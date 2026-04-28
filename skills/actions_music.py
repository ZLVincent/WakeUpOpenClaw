"""
音乐技能动作 Mixin

包含 music 技能的所有操作处理器：
音量调节、停止播放、播放音乐（单曲/列表/收藏）、上下首。
"""

import asyncio
from utils.logger import get_logger

logger = get_logger("skills")


class MusicActionsMixin:
    """音乐技能动作。通过 Mixin 注入 SkillRouter。"""

    async def _action_volume_up(self, skill, action, user_text=""):
        """调大音量。"""
        step = skill.options.get("volume_step", "10%")
        try:
            proc = await asyncio.create_subprocess_exec(
                "amixer", "set", "Master", f"{step}+",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            logger.info("音量已调大 (%s)", step)
        except Exception as e:
            logger.warning("调大音量失败: %s", e)
        return self._make_result(action.reply or "好的，已调大音量", "volume_up", "music")

    async def _action_volume_down(self, skill, action, user_text=""):
        """调小音量。"""
        step = skill.options.get("volume_step", "10%")
        try:
            proc = await asyncio.create_subprocess_exec(
                "amixer", "set", "Master", f"{step}-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            logger.info("音量已调小 (%s)", step)
        except Exception as e:
            logger.warning("调小音量失败: %s", e)
        return self._make_result(action.reply or "好的，已调小音量", "volume_down", "music")

    async def _action_stop_playback(self, skill, action, user_text=""):
        """停止播放。"""
        if self.music_player and self.music_player.is_playing:
            await self.music_player.stop()
            return self._make_result(action.reply or "好的，已停止播放", "stop", "music")
        try:
            proc = await asyncio.create_subprocess_exec(
                "pkill", "-f", "mpv",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except Exception:
            pass
        return self._make_result(action.reply or "好的，已停止播放", "stop", "music")

    def _extract_music_query(self, user_text, keywords):
        """从用户输入中提取音乐搜索关键词。"""
        text = user_text.strip()
        for kw in sorted(keywords, key=len, reverse=True):
            idx = text.lower().find(kw.lower())
            if idx != -1:
                remainder = text[idx + len(kw):].strip()
                for prefix in ("歌曲", "歌", "音乐", "本地", "一首", "首"):
                    if remainder.startswith(prefix):
                        remainder = remainder[len(prefix):].strip()
                remainder = self._PUNCTUATION_RE.sub("", remainder)
                return remainder
        return ""

    async def _action_play_music(self, skill, action, user_text=""):
        """播放音乐：有搜索词则单曲，无则列表播放。"""
        if not self.music_player:
            return self._make_result("音乐播放功能不可用", "play", "music")

        search_term = self._extract_music_query(user_text, action.keywords)
        if search_term:
            track = await self.music_player.play_single(search_term)
            if not track:
                return self._make_result(f"没有找到「{search_term}」相关的歌曲", "play", "music")
            singer = track.get("singer", "")
            name = track.get("name", "")
            text = f"正在播放{singer}的{name}" if singer else f"正在播放{name}"
            return self._make_result(text, "play", "music")
        else:
            track = await self.music_player.play_all()
            if not track:
                return self._make_result("本地没有可播放的歌曲", "play", "music")
            total = len(self.music_player._playlist)
            return self._make_result(f"开始播放，共{total}首歌曲", "play", "music")

    async def _action_play_favorite_music(self, skill, action, user_text=""):
        """播放收藏歌曲。"""
        if not self.music_player:
            return self._make_result("音乐播放功能不可用", "play_favorite", "music")
        track = await self.music_player.play_all(favorite_only=True)
        if not track:
            return self._make_result("没有收藏的歌曲", "play_favorite", "music")
        total = len(self.music_player._playlist)
        return self._make_result(f"开始播放收藏歌曲，共{total}首", "play_favorite", "music")

    async def _action_next_track(self, skill, action, user_text=""):
        """下一首。"""
        if not self.music_player or not self.music_player.is_playing:
            return self._make_result("当前没有在播放歌曲", "next_track", "music")
        track = await self.music_player.next_track()
        if not track:
            return self._make_result("已经是最后一首了", "next_track", "music")
        singer = track.get("singer", "")
        name = track.get("name", "")
        text = f"正在播放{singer}的{name}" if singer else f"正在播放{name}"
        return self._make_result(text, "next_track", "music")

    async def _action_prev_track(self, skill, action, user_text=""):
        """上一首。"""
        if not self.music_player or not self.music_player.is_playing:
            return self._make_result("当前没有在播放歌曲", "prev_track", "music")
        track = await self.music_player.prev_track()
        if not track:
            return self._make_result("已经是第一首了", "prev_track", "music")
        singer = track.get("singer", "")
        name = track.get("name", "")
        text = f"正在播放{singer}的{name}" if singer else f"正在播放{name}"
        return self._make_result(text, "prev_track", "music")
