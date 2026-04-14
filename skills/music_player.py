"""
本地音乐播放器模块

管理播放列表、当前播放状态和 mpv 子进程。
支持单曲播放、列表顺序播放、上一首/下一首/停止。
"""

import asyncio
import os
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.database import ChatDatabase

from utils.logger import get_logger

logger = get_logger("music")


class MusicPlayer:
    """
    本地音乐播放器。

    Parameters
    ----------
    database : ChatDatabase
        数据库实例，用于查询 zlpi_music 表
    player : str
        播放器命令（默认 mpv）
    player_args : list[str]
        播放器参数
    """

    def __init__(
        self,
        database,
        player: str = "mpv",
        player_args: Optional[list[str]] = None,
    ):
        self.db = database
        self.player = player
        self.player_args = player_args or ["--no-terminal", "--really-quiet"]
        self._playlist: list[dict] = []
        self._current_index: int = -1
        self._process: Optional[asyncio.subprocess.Process] = None
        self._play_task: Optional[asyncio.Task] = None
        self._stopped = False

    @property
    def is_playing(self) -> bool:
        """是否正在播放。"""
        return (
            self._process is not None
            and self._process.returncode is None
        )

    @property
    def current_track(self) -> Optional[dict]:
        """当前播放的歌曲信息。"""
        if 0 <= self._current_index < len(self._playlist):
            return self._playlist[self._current_index]
        return None

    @property
    def playlist_info(self) -> str:
        """播放列表摘要信息。"""
        total = len(self._playlist)
        if total == 0:
            return "播放列表为空"
        current = self._current_index + 1
        return f"第 {current}/{total} 首"

    async def play_single(self, keyword: str) -> Optional[dict]:
        """
        搜索并播放单首歌曲。

        先停止当前播放，然后搜索歌曲，加载为单曲播放列表。

        Parameters
        ----------
        keyword : str
            搜索关键词（歌名或歌手）

        Returns
        -------
        dict | None
            找到的歌曲信息，未找到返回 None
        """
        music = await self.db.search_music(keyword)
        if not music:
            return None

        file_path = music.get("file_path", "")
        if not file_path or not os.path.exists(file_path):
            logger.warning("歌曲文件不存在: %s", file_path)
            return None

        await self.stop()
        self._playlist = [music]
        self._current_index = 0
        self._stopped = False
        self._play_task = asyncio.create_task(self._play_loop())
        logger.info("单曲播放: %s - %s", music.get("singer", ""), music.get("name", ""))
        return music

    async def play_all(self, favorite_only: bool = False) -> Optional[dict]:
        """
        加载播放列表并开始顺序播放。

        Parameters
        ----------
        favorite_only : bool
            是否只播放收藏歌曲

        Returns
        -------
        dict | None
            第一首歌曲信息，列表为空返回 None
        """
        songs = await self.db.get_all_music(favorite_only=favorite_only)
        if not songs:
            return None

        # 过滤掉文件不存在的
        valid_songs = [s for s in songs if s.get("file_path") and os.path.exists(s["file_path"])]
        if not valid_songs:
            logger.warning("播放列表中没有有效的歌曲文件")
            return None

        await self.stop()
        self._playlist = valid_songs
        self._current_index = 0
        self._stopped = False
        self._play_task = asyncio.create_task(self._play_loop())
        logger.info(
            "列表播放: 共 %d 首%s",
            len(valid_songs),
            "（收藏）" if favorite_only else "",
        )
        return valid_songs[0]

    async def next_track(self) -> Optional[dict]:
        """
        跳到下一首。

        Returns
        -------
        dict | None
            下一首歌曲信息，已是最后一首返回 None
        """
        if not self._playlist:
            return None

        if self._current_index >= len(self._playlist) - 1:
            logger.info("已是最后一首")
            return None

        # kill 当前播放，_play_loop 会自动播下一首
        self._current_index += 1
        await self._kill_current()
        logger.info("下一首: %s", self._playlist[self._current_index].get("name", ""))
        return self._playlist[self._current_index]

    async def prev_track(self) -> Optional[dict]:
        """
        跳到上一首。

        Returns
        -------
        dict | None
            上一首歌曲信息，已是第一首返回 None
        """
        if not self._playlist:
            return None

        if self._current_index <= 0:
            logger.info("已是第一首")
            return None

        # 回退索引，kill 当前播放，_play_loop 会播放新索引
        self._current_index -= 1
        await self._kill_current()
        logger.info("上一首: %s", self._playlist[self._current_index].get("name", ""))
        return self._playlist[self._current_index]

    async def stop(self):
        """停止播放，清空播放列表。"""
        self._stopped = True
        await self._kill_current()
        if self._play_task and not self._play_task.done():
            self._play_task.cancel()
            try:
                await self._play_task
            except asyncio.CancelledError:
                pass
        self._play_task = None
        self._playlist = []
        self._current_index = -1
        logger.info("播放已停止")

    async def _play_loop(self):
        """
        后台播放循环。

        从 _current_index 开始逐首播放，直到列表结束或被停止。
        """
        while (
            not self._stopped
            and 0 <= self._current_index < len(self._playlist)
        ):
            track = self._playlist[self._current_index]
            file_path = track.get("file_path", "")
            name = track.get("name", "未知")
            singer = track.get("singer", "")

            logger.info(
                "正在播放 [%d/%d]: %s - %s",
                self._current_index + 1, len(self._playlist), singer, name,
            )

            try:
                cmd = [self.player] + self.player_args + [file_path]
                self._process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await self._process.wait()
            except asyncio.CancelledError:
                await self._kill_current()
                return
            except Exception as e:
                logger.warning("播放失败 (%s): %s", name, e)

            self._process = None

            if self._stopped:
                return

            # 如果是 next_track/prev_track 主动 kill 的，索引已经更新过了，
            # 不需要再 +1；如果是自然播完，才 +1
            # 判断方式：当前索引的歌曲是否和刚播放的一致
            if (
                0 <= self._current_index < len(self._playlist)
                and self._playlist[self._current_index] is track
            ):
                self._current_index += 1

        if not self._stopped:
            logger.info("播放列表播放完毕")
            self._playlist = []
            self._current_index = -1

    async def _kill_current(self):
        """终止当前 mpv 进程。"""
        if self._process and self._process.returncode is None:
            try:
                self._process.kill()
                await self._process.wait()
            except Exception:
                pass
            self._process = None
