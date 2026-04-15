"""
技能路由模块

将技能按业务分组（音乐、日程、对话、工具），每个技能包含多个操作。
在发送给 OpenClaw 之前进行本地关键词匹配，命中则本地执行，不走 AI。
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


# Skill 中文显示名
SKILL_DISPLAY_NAMES = {
    "music": "本地音乐播放",
    "calendar": "日程管理",
    "conversation": "对话管理",
    "utility": "通用工具",
}


@dataclass
class SkillResult:
    """技能执行结果。"""
    text: str = ""           # 回复文本（TTS 朗读）
    action: str = ""         # 动作标识 (如 "play_music", "volume_up")
    skill: str = ""          # 所属技能名 (如 "music", "calendar")
    handled: bool = True     # 是否已处理（False 表示交给 AI）
    extra: dict = field(default_factory=dict)


@dataclass
class SkillAction:
    """一个技能内的具体操作。"""
    name: str = ""                                    # 操作名如 "play", "volume_up"
    keywords: list[str] = field(default_factory=list) # 触发关键词列表
    reply: str = ""                                   # 默认回复文本


@dataclass
class Skill:
    """一个完整的技能。"""
    name: str = ""                                    # 技能名如 "music"
    enabled: bool = True                              # 是否启用
    options: dict = field(default_factory=dict)        # 自定义配置参数
    actions: list[SkillAction] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return SKILL_DISPLAY_NAMES.get(self.name, self.name)


class SkillRouter:
    """
    技能路由器。

    根据分组配置中的关键词匹配用户输入，
    匹配成功则执行对应的内置动作。

    Parameters
    ----------
    skills_config : dict
        config.yaml 中 skills 段（不含 enabled 字段）的技能分组配置
    enabled : bool
        全局开关
    database : ChatDatabase | None
        数据库实例
    music_player : MusicPlayer | None
        音乐播放器实例
    """

    # 用于去除标点的正则（中英文标点 + 空白）
    _PUNCTUATION_RE = re.compile(
        r'[。，！？、；：""''（）【】《》\s.!?,;:\'"()\[\]{}\-~…]+'
    )

    def __init__(
        self,
        skills_config: dict = None,
        enabled: bool = True,
        database=None,
        music_player=None,
    ):
        self.enabled = enabled
        self.db = database
        self.music_player = music_player
        self.skills: dict[str, Skill] = {}
        self._action_handlers: dict[str, Callable] = {}

        # 注册内置动作处理器
        self._register_builtin_actions()

        # 解析分组配置
        if skills_config:
            for skill_name, skill_cfg in skills_config.items():
                if not isinstance(skill_cfg, dict):
                    continue
                skill = Skill(
                    name=skill_name,
                    enabled=skill_cfg.get("enabled", True),
                    options=skill_cfg.get("options", {}),
                )
                actions_cfg = skill_cfg.get("actions", {})
                if isinstance(actions_cfg, dict):
                    for action_name, action_cfg in actions_cfg.items():
                        if not isinstance(action_cfg, dict):
                            continue
                        skill.actions.append(SkillAction(
                            name=action_name,
                            keywords=action_cfg.get("keywords", []),
                            reply=action_cfg.get("reply", ""),
                        ))
                self.skills[skill_name] = skill

        if self.enabled:
            total_actions = sum(len(s.actions) for s in self.skills.values())
            active_skills = sum(1 for s in self.skills.values() if s.enabled)
            logger.info(
                "技能路由已启用: %d 个技能 (%d 启用), %d 个操作",
                len(self.skills), active_skills, total_actions,
            )

    def _register_builtin_actions(self) -> None:
        """注册内置动作处理器。按 action name 注册。"""
        self._action_handlers = {
            # music
            "play": self._action_play_music,
            "play_favorite": self._action_play_favorite_music,
            "next_track": self._action_next_track,
            "prev_track": self._action_prev_track,
            "stop": self._action_stop_playback,
            "volume_up": self._action_volume_up,
            "volume_down": self._action_volume_down,
            # calendar
            "query_today": self._action_query_today_events,
            "query_tomorrow": self._action_query_tomorrow_events,
            "query_week": self._action_query_week_events,
            "query_next_week": self._action_query_next_week_events,
            "query_upcoming": self._action_query_upcoming_events,
            # conversation
            "new_conversation": self._action_new_conversation,
            # utility
            "current_time": self._action_current_time,
        }

    async def match(self, text: str) -> Optional[SkillResult]:
        """
        匹配用户输入，返回执行结果或 None（交给 AI）。

        遍历所有启用的技能及其操作，去除标点后匹配关键词。

        Parameters
        ----------
        text : str
            用户语音识别结果

        Returns
        -------
        SkillResult | None
        """
        if not self.enabled or not text:
            return None

        text_clean = self._PUNCTUATION_RE.sub("", text.strip().lower())

        for skill in self.skills.values():
            if not skill.enabled:
                continue
            for action in skill.actions:
                for keyword in action.keywords:
                    if keyword.lower() in text_clean:
                        logger.info(
                            "技能匹配: '%s' -> skill=%s, action=%s (keyword='%s')",
                            text, skill.name, action.name, keyword,
                        )
                        return await self._execute(skill, action, text)

        return None

    async def _execute(
        self, skill: Skill, action: SkillAction, user_text: str = ""
    ) -> SkillResult:
        """执行匹配到的操作。"""
        handler = self._action_handlers.get(action.name)
        if handler:
            return await handler(skill, action, user_text)

        # 没有 handler，返回配置的回复文本
        return SkillResult(
            text=action.reply or "好的",
            action=action.name,
            skill=skill.name,
        )

    # ------------------------------------------------------------------
    # music 技能动作
    # ------------------------------------------------------------------

    async def _action_volume_up(
        self, skill: Skill, action: SkillAction, user_text: str = ""
    ) -> SkillResult:
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
        return SkillResult(
            text=action.reply or "好的，已调大音量",
            action="volume_up", skill="music",
        )

    async def _action_volume_down(
        self, skill: Skill, action: SkillAction, user_text: str = ""
    ) -> SkillResult:
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
        return SkillResult(
            text=action.reply or "好的，已调小音量",
            action="volume_down", skill="music",
        )

    async def _action_stop_playback(
        self, skill: Skill, action: SkillAction, user_text: str = ""
    ) -> SkillResult:
        """停止播放。"""
        if self.music_player and self.music_player.is_playing:
            await self.music_player.stop()
            return SkillResult(
                text=action.reply or "好的，已停止播放",
                action="stop", skill="music",
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                "pkill", "-f", "mpv",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except Exception:
            pass
        return SkillResult(
            text=action.reply or "好的，已停止播放",
            action="stop", skill="music",
        )

    def _extract_music_query(self, user_text: str, keywords: list[str]) -> str:
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

    async def _action_play_music(
        self, skill: Skill, action: SkillAction, user_text: str = ""
    ) -> SkillResult:
        """播放音乐：有搜索词则单曲，无则列表播放。"""
        if not self.music_player:
            return SkillResult(text="音乐播放功能不可用", action="play", skill="music")

        search_term = self._extract_music_query(user_text, action.keywords)

        if search_term:
            track = await self.music_player.play_single(search_term)
            if not track:
                return SkillResult(
                    text=f"没有找到「{search_term}」相关的歌曲",
                    action="play", skill="music",
                )
            singer = track.get("singer", "")
            name = track.get("name", "")
            text = f"正在播放{singer}的{name}" if singer else f"正在播放{name}"
            return SkillResult(text=text, action="play", skill="music")
        else:
            track = await self.music_player.play_all()
            if not track:
                return SkillResult(text="本地没有可播放的歌曲", action="play", skill="music")
            total = len(self.music_player._playlist)
            return SkillResult(text=f"开始播放，共{total}首歌曲", action="play", skill="music")

    async def _action_play_favorite_music(
        self, skill: Skill, action: SkillAction, user_text: str = ""
    ) -> SkillResult:
        """播放收藏歌曲。"""
        if not self.music_player:
            return SkillResult(text="音乐播放功能不可用", action="play_favorite", skill="music")
        track = await self.music_player.play_all(favorite_only=True)
        if not track:
            return SkillResult(text="没有收藏的歌曲", action="play_favorite", skill="music")
        total = len(self.music_player._playlist)
        return SkillResult(text=f"开始播放收藏歌曲，共{total}首", action="play_favorite", skill="music")

    async def _action_next_track(
        self, skill: Skill, action: SkillAction, user_text: str = ""
    ) -> SkillResult:
        """下一首。"""
        if not self.music_player or not self.music_player.is_playing:
            return SkillResult(text="当前没有在播放歌曲", action="next_track", skill="music")
        track = await self.music_player.next_track()
        if not track:
            return SkillResult(text="已经是最后一首了", action="next_track", skill="music")
        singer = track.get("singer", "")
        name = track.get("name", "")
        text = f"正在播放{singer}的{name}" if singer else f"正在播放{name}"
        return SkillResult(text=text, action="next_track", skill="music")

    async def _action_prev_track(
        self, skill: Skill, action: SkillAction, user_text: str = ""
    ) -> SkillResult:
        """上一首。"""
        if not self.music_player or not self.music_player.is_playing:
            return SkillResult(text="当前没有在播放歌曲", action="prev_track", skill="music")
        track = await self.music_player.prev_track()
        if not track:
            return SkillResult(text="已经是第一首了", action="prev_track", skill="music")
        singer = track.get("singer", "")
        name = track.get("name", "")
        text = f"正在播放{singer}的{name}" if singer else f"正在播放{name}"
        return SkillResult(text=text, action="prev_track", skill="music")

    # ------------------------------------------------------------------
    # calendar 技能动作
    # ------------------------------------------------------------------

    async def _action_query_today_events(
        self, skill: Skill, action: SkillAction, user_text: str = ""
    ) -> SkillResult:
        """查询今天的日程。"""
        return await self._query_events_for_date(datetime.date.today(), "今天")

    async def _action_query_tomorrow_events(
        self, skill: Skill, action: SkillAction, user_text: str = ""
    ) -> SkillResult:
        """查询明天的日程。"""
        return await self._query_events_for_date(
            datetime.date.today() + datetime.timedelta(days=1), "明天"
        )

    async def _query_events_for_date(
        self, date: datetime.date, label: str
    ) -> SkillResult:
        """查询指定日期的日程。"""
        if not self.db:
            return SkillResult(text="日程功能暂不可用", action="query_events", skill="calendar")
        try:
            events = await self.db.get_events_by_date(date.strftime("%Y-%m-%d"))
        except Exception as e:
            logger.warning("查询日程失败: %s", e)
            return SkillResult(text="查询日程时出错了", action="query_events", skill="calendar")
        if not events:
            return SkillResult(text=f"{label}没有日程安排", action="query_events", skill="calendar")

        lines = [f"{label}有{len(events)}个日程。"]
        for ev in events:
            if ev.get("all_day"):
                lines.append(f"全天，{ev['title']}。")
            elif ev.get("start_time"):
                t = self._format_time_for_speech(ev['start_time'])
                lines.append(f"{t}，{ev['title']}。")
            else:
                lines.append(f"{ev['title']}。")
        reply = "\n".join(lines)
        return SkillResult(text=reply, action="query_events", skill="calendar")

    _WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    @staticmethod
    def _format_time_for_speech(time_str: str) -> str:
        """
        将时间字符串转换为适合语音播报的格式。

        "14:00" → "14点"
        "14:30" → "14点30分"
        "09:05" → "9点05分"
        """
        try:
            parts = time_str.strip().split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            if minute == 0:
                return f"{hour}点"
            else:
                return f"{hour}点{minute:02d}分"
        except (ValueError, IndexError):
            return time_str

    async def _action_query_week_events(
        self, skill: Skill, action: SkillAction, user_text: str = ""
    ) -> SkillResult:
        """查询本周日程。"""
        if not self.db:
            return SkillResult(text="日程功能暂不可用", action="query_week", skill="calendar")

        today = datetime.date.today()
        monday = today - datetime.timedelta(days=today.weekday())
        sunday = monday + datetime.timedelta(days=6)

        try:
            events = await self.db.get_events_by_range(
                monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d"),
            )
        except Exception as e:
            logger.warning("查询本周日程失败: %s", e)
            return SkillResult(text="查询日程时出错了", action="query_week", skill="calendar")

        if not events:
            return SkillResult(text="本周没有日程安排", action="query_week", skill="calendar")

        lines = [f"本周共有{len(events)}个日程。"]
        for ev in events:
            day = datetime.date.fromisoformat(ev["date"])
            weekday = self._WEEKDAY_NAMES[day.weekday()]
            if ev.get("all_day"):
                lines.append(f"{weekday}，全天，{ev['title']}。")
            elif ev.get("start_time"):
                t = self._format_time_for_speech(ev['start_time'])
                lines.append(f"{weekday}，{t}，{ev['title']}。")
            else:
                lines.append(f"{weekday}，{ev['title']}。")

        return SkillResult(text="\n".join(lines), action="query_week", skill="calendar")

    async def _action_query_next_week_events(
        self, skill: Skill, action: SkillAction, user_text: str = ""
    ) -> SkillResult:
        """查询下周日程。"""
        if not self.db:
            return SkillResult(text="日程功能暂不可用", action="query_next_week", skill="calendar")

        today = datetime.date.today()
        next_monday = today + datetime.timedelta(days=(7 - today.weekday()))
        next_sunday = next_monday + datetime.timedelta(days=6)

        try:
            events = await self.db.get_events_by_range(
                next_monday.strftime("%Y-%m-%d"), next_sunday.strftime("%Y-%m-%d"),
            )
        except Exception as e:
            logger.warning("查询下周日程失败: %s", e)
            return SkillResult(text="查询日程时出错了", action="query_next_week", skill="calendar")

        if not events:
            return SkillResult(text="下周没有日程安排", action="query_next_week", skill="calendar")

        lines = [f"下周共有{len(events)}个日程。"]
        for ev in events:
            day = datetime.date.fromisoformat(ev["date"])
            weekday = self._WEEKDAY_NAMES[day.weekday()]
            if ev.get("all_day"):
                lines.append(f"{weekday}，全天，{ev['title']}。")
            elif ev.get("start_time"):
                t = self._format_time_for_speech(ev['start_time'])
                lines.append(f"{weekday}，{t}，{ev['title']}。")
            else:
                lines.append(f"{weekday}，{ev['title']}。")

        return SkillResult(text="\n".join(lines), action="query_next_week", skill="calendar")

    async def _action_query_upcoming_events(
        self, skill: Skill, action: SkillAction, user_text: str = ""
    ) -> SkillResult:
        """查询本周剩余未完成的日程。"""
        if not self.db:
            return SkillResult(text="日程功能暂不可用", action="query_upcoming", skill="calendar")

        today = datetime.date.today()
        sunday = today + datetime.timedelta(days=(6 - today.weekday()))

        try:
            events = await self.db.get_upcoming_events_in_range(
                today.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d"),
            )
        except Exception as e:
            logger.warning("查询剩余日程失败: %s", e)
            return SkillResult(text="查询日程时出错了", action="query_upcoming", skill="calendar")

        if not events:
            return SkillResult(text="本周剩余没有待完成的日程", action="query_upcoming", skill="calendar")

        lines = [f"本周还有{len(events)}个未完成的日程。"]
        for ev in events:
            day = datetime.date.fromisoformat(ev["date"])
            # 用相对日期描述
            delta = (day - today).days
            if delta == 0:
                day_label = "今天"
            elif delta == 1:
                day_label = "明天"
            elif delta == 2:
                day_label = "后天"
            else:
                day_label = self._WEEKDAY_NAMES[day.weekday()]

            if ev.get("all_day"):
                lines.append(f"{day_label}，全天，{ev['title']}。")
            elif ev.get("start_time"):
                t = self._format_time_for_speech(ev['start_time'])
                lines.append(f"{day_label}，{t}，{ev['title']}。")
            else:
                lines.append(f"{day_label}，{ev['title']}。")

        return SkillResult(text="\n".join(lines), action="query_upcoming", skill="calendar")

    # ------------------------------------------------------------------
    # conversation 技能动作
    # ------------------------------------------------------------------

    async def _action_new_conversation(
        self, skill: Skill, action: SkillAction, user_text: str = ""
    ) -> SkillResult:
        """新建对话（实际操作由 main.py 根据 action 名执行）。"""
        return SkillResult(
            text=action.reply or "好的，已开启新对话",
            action="new_conversation", skill="conversation",
        )

    # ------------------------------------------------------------------
    # utility 技能动作
    # ------------------------------------------------------------------

    async def _action_current_time(
        self, skill: Skill, action: SkillAction, user_text: str = ""
    ) -> SkillResult:
        """报时。"""
        now = datetime.datetime.now()
        return SkillResult(
            text=now.strftime("现在是%H点%M分"),
            action="current_time", skill="utility",
        )
