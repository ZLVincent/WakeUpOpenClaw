"""
技能路由模块

在发送给 OpenClaw 之前进行本地关键词匹配。
匹配到的指令直接本地执行，不走 AI，响应更快。
"""

import asyncio
import datetime
import re
from dataclasses import dataclass, field
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.database import ChatDatabase
    from skills.music_player import MusicPlayer

from utils.logger import get_logger

logger = get_logger("skills")


@dataclass
class SkillResult:
    """技能执行结果。"""
    text: str = ""           # 回复文本（TTS 朗读）
    action: str = ""         # 动作标识
    handled: bool = True     # 是否已处理（False 表示交给 AI）
    extra: dict = field(default_factory=dict)  # 附加数据


@dataclass
class SkillCommand:
    """一条技能指令定义。"""
    keywords: list[str] = field(default_factory=list)
    action: str = ""
    reply: str = ""


class SkillRouter:
    """
    技能路由器。

    根据配置中的关键词列表匹配用户输入，
    匹配成功则执行对应的内置动作。

    Parameters
    ----------
    commands : list[dict]
        技能指令配置列表
    enabled : bool
        是否启用技能路由
    database : ChatDatabase | None
        数据库实例，用于日程查询
    music_player : MusicPlayer | None
        音乐播放器实例
    """

    def __init__(self, commands: list[dict] = None, enabled: bool = True,
                 database=None, music_player=None):
        self.enabled = enabled
        self.db = database
        self.music_player = music_player
        self.commands: list[SkillCommand] = []
        self._action_handlers: dict[str, Callable] = {}

        # 注册内置动作处理器
        self._register_builtin_actions()

        # 解析配置
        if commands:
            for cmd in commands:
                self.commands.append(SkillCommand(
                    keywords=cmd.get("keywords", []),
                    action=cmd.get("action", ""),
                    reply=cmd.get("reply", ""),
                ))

        if self.enabled:
            logger.info("技能路由已启用，共 %d 条指令", len(self.commands))

    def _register_builtin_actions(self) -> None:
        """注册内置动作处理器。"""
        self._action_handlers = {
            "volume_up": self._action_volume_up,
            "volume_down": self._action_volume_down,
            "volume_set": self._action_volume_set,
            "stop_playback": self._action_stop_playback,
            "current_time": self._action_current_time,
            "new_conversation": self._action_new_conversation,
            "query_today_events": self._action_query_today_events,
            "query_tomorrow_events": self._action_query_tomorrow_events,
            "play_music": self._action_play_music,
            "play_favorite_music": self._action_play_favorite_music,
            "next_track": self._action_next_track,
            "prev_track": self._action_prev_track,
        }

    # 用于去除标点的正则（中英文标点 + 空白）
    _PUNCTUATION_RE = re.compile(
        r'[。，！？、；：""''（）【】《》\s.!?,;:\'"()\[\]{}\-~…]+'
    )

    async def match(self, text: str) -> Optional[SkillResult]:
        """
        匹配用户输入，返回执行结果或 None（交给 AI）。

        对用户输入去除标点后再匹配关键词，避免 FunASR 识别结果中的
        标点符号（如"播放本地歌曲。"中的"。"）干扰匹配。

        Parameters
        ----------
        text : str
            用户语音识别结果

        Returns
        -------
        SkillResult | None
            匹配到则返回执行结果，否则 None
        """
        if not self.enabled or not text:
            return None

        # 去除标点后用于匹配
        text_clean = self._PUNCTUATION_RE.sub("", text.strip().lower())

        for cmd in self.commands:
            for keyword in cmd.keywords:
                if keyword.lower() in text_clean:
                    logger.info(
                        "技能匹配: '%s' -> action=%s (keyword='%s')",
                        text, cmd.action, keyword,
                    )
                    return await self._execute(cmd, text)  # 传原始文本

        return None

    async def _execute(self, cmd: SkillCommand, user_text: str = "") -> SkillResult:
        """执行匹配到的技能。"""
        handler = self._action_handlers.get(cmd.action)
        if handler:
            return await handler(cmd, user_text)

        # 没有 handler，只返回配置的回复文本
        if cmd.reply:
            return SkillResult(text=cmd.reply, action=cmd.action)

        return SkillResult(text="好的", action=cmd.action)

    # ------------------------------------------------------------------
    # 内置动作处理器
    # ------------------------------------------------------------------

    async def _action_volume_up(self, cmd: SkillCommand, user_text: str = "") -> SkillResult:
        """调大音量。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "amixer", "set", "Master", "10%+",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            logger.info("音量已调大")
        except Exception as e:
            logger.warning("调大音量失败: %s", e)
        return SkillResult(text=cmd.reply or "好的，已调大音量", action="volume_up")

    async def _action_volume_down(self, cmd: SkillCommand, user_text: str = "") -> SkillResult:
        """调小音量。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "amixer", "set", "Master", "10%-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            logger.info("音量已调小")
        except Exception as e:
            logger.warning("调小音量失败: %s", e)
        return SkillResult(text=cmd.reply or "好的，已调小音量", action="volume_down")

    async def _action_volume_set(self, cmd: SkillCommand, user_text: str = "") -> SkillResult:
        """设置音量到指定百分比（从 reply 中解析）。"""
        return SkillResult(text=cmd.reply or "好的", action="volume_set")

    async def _action_stop_playback(self, cmd: SkillCommand, user_text: str = "") -> SkillResult:
        """停止所有 mpv 播放。"""
        # 如果有音乐播放器在播放，用它来停止（会清空播放列表）
        if self.music_player and self.music_player.is_playing:
            await self.music_player.stop()
            return SkillResult(text=cmd.reply or "好的，已停止播放", action="stop_playback")
        # 否则直接 kill mpv
        try:
            proc = await asyncio.create_subprocess_exec(
                "pkill", "-f", "mpv",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            logger.info("已停止播放")
        except Exception as e:
            logger.debug("停止播放失败: %s", e)
        return SkillResult(text=cmd.reply or "好的，已停止播放", action="stop_playback")

    async def _action_current_time(self, cmd: SkillCommand, user_text: str = "") -> SkillResult:
        """报时。"""
        now = datetime.datetime.now()
        time_str = now.strftime("现在是%H点%M分")
        return SkillResult(text=time_str, action="current_time")

    async def _action_new_conversation(self, cmd: SkillCommand, user_text: str = "") -> SkillResult:
        """
        新建对话。

        注意：实际的新建操作由 main.py 的 _conversation_loop 根据
        action="new_conversation" 来执行。
        """
        return SkillResult(
            text=cmd.reply or "好的，已开启新对话",
            action="new_conversation",
        )

    async def _action_query_today_events(self, cmd: SkillCommand, user_text: str = "") -> SkillResult:
        """查询今天的日程。"""
        return await self._query_events_for_date(
            datetime.date.today(), "今天"
        )

    async def _action_query_tomorrow_events(self, cmd: SkillCommand, user_text: str = "") -> SkillResult:
        """查询明天的日程。"""
        return await self._query_events_for_date(
            datetime.date.today() + datetime.timedelta(days=1), "明天"
        )

    async def _query_events_for_date(
        self, date: datetime.date, label: str
    ) -> SkillResult:
        """查询指定日期的日程并生成语音回复。"""
        if not self.db:
            return SkillResult(text="日程功能暂不可用", action="query_events")

        try:
            events = await self.db.get_events_by_date(date.strftime("%Y-%m-%d"))
        except Exception as e:
            logger.warning("查询日程失败: %s", e)
            return SkillResult(text="查询日程时出错了", action="query_events")

        if not events:
            return SkillResult(
                text=f"{label}没有日程安排",
                action="query_events",
            )

        parts = []
        for ev in events:
            if ev.get("all_day"):
                parts.append(f"全天，{ev['title']}")
            elif ev.get("start_time"):
                t = ev["start_time"]
                parts.append(f"{t}，{ev['title']}")
            else:
                parts.append(ev["title"])

        reply = f"{label}有{len(events)}个日程：" + "；".join(parts)
        return SkillResult(text=reply, action="query_events")

    # ------------------------------------------------------------------
    # 音乐播放动作
    # ------------------------------------------------------------------

    def _extract_music_query(self, user_text: str, keywords: list[str]) -> str:
        """
        从用户输入中提取音乐搜索关键词。

        例如: "播放歌曲雨爱" + keywords=["播放歌曲"] → "雨爱"
              "播放杨丞琳的歌" + keywords=["播放"] → "杨丞琳的歌"
        """
        text = user_text.strip()
        for kw in sorted(keywords, key=len, reverse=True):
            # 按关键词长度降序匹配，优先去掉最长的前缀
            idx = text.lower().find(kw.lower())
            if idx != -1:
                remainder = text[idx + len(kw):].strip()
                # 去掉常见连接词
                for prefix in ("歌曲", "歌", "音乐", "本地", "一首", "首"):
                    if remainder.startswith(prefix):
                        remainder = remainder[len(prefix):].strip()
                # 去除 FunASR 识别结果中的标点符号
                remainder = re.sub(
                    r'[。，！？、；：""''（）【】《》\s.!?,;:\'"()\[\]{}\-~…]+',
                    '', remainder,
                )
                return remainder
        return ""

    async def _action_play_music(self, cmd: SkillCommand, user_text: str = "") -> SkillResult:
        """
        播放音乐（统一入口）。

        有搜索关键词 → 单曲搜索播放
        无搜索关键词 → 列表顺序播放
        """
        if not self.music_player:
            return SkillResult(text="音乐播放功能不可用", action="play_music")

        search_term = self._extract_music_query(user_text, cmd.keywords)

        if search_term:
            # 单曲搜索播放
            track = await self.music_player.play_single(search_term)
            if not track:
                return SkillResult(
                    text=f"没有找到「{search_term}」相关的歌曲",
                    action="play_music",
                )
            singer = track.get("singer", "")
            name = track.get("name", "")
            text = f"正在播放{singer}的{name}" if singer else f"正在播放{name}"
            return SkillResult(text=text, action="play_music")
        else:
            # 列表顺序播放
            track = await self.music_player.play_all()
            if not track:
                return SkillResult(text="本地没有可播放的歌曲", action="play_music")
            total = len(self.music_player._playlist)
            return SkillResult(
                text=f"开始播放，共{total}首歌曲",
                action="play_music",
            )

    async def _action_play_favorite_music(self, cmd: SkillCommand, user_text: str = "") -> SkillResult:
        """播放收藏歌曲。"""
        if not self.music_player:
            return SkillResult(text="音乐播放功能不可用", action="play_favorite_music")

        track = await self.music_player.play_all(favorite_only=True)
        if not track:
            return SkillResult(text="没有收藏的歌曲", action="play_favorite_music")
        total = len(self.music_player._playlist)
        return SkillResult(
            text=f"开始播放收藏歌曲，共{total}首",
            action="play_favorite_music",
        )

    async def _action_next_track(self, cmd: SkillCommand, user_text: str = "") -> SkillResult:
        """下一首。"""
        if not self.music_player or not self.music_player.is_playing:
            return SkillResult(text="当前没有在播放歌曲", action="next_track")

        track = await self.music_player.next_track()
        if not track:
            return SkillResult(text="已经是最后一首了", action="next_track")
        singer = track.get("singer", "")
        name = track.get("name", "")
        text = f"正在播放{singer}的{name}" if singer else f"正在播放{name}"
        return SkillResult(text=text, action="next_track")

    async def _action_prev_track(self, cmd: SkillCommand, user_text: str = "") -> SkillResult:
        """上一首。"""
        if not self.music_player or not self.music_player.is_playing:
            return SkillResult(text="当前没有在播放歌曲", action="prev_track")

        track = await self.music_player.prev_track()
        if not track:
            return SkillResult(text="已经是第一首了", action="prev_track")
        singer = track.get("singer", "")
        name = track.get("name", "")
        text = f"正在播放{singer}的{name}" if singer else f"正在播放{name}"
        return SkillResult(text=text, action="prev_track")
