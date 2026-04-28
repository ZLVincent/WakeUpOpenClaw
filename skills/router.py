"""
技能路由模块

将技能按业务分组（音乐、日程、对话、工具、天气、定时器），每个技能包含多个操作。
在发送给 OpenClaw 之前进行本地关键词匹配，命中则本地执行，不走 AI。

Handler 实现拆分到各 actions_*.py 文件中，通过 Mixin 多重继承组合。
"""

import re
from dataclasses import dataclass, field
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.database import ChatDatabase
    from skills.music_player import MusicPlayer
    from skills.timer import TimerManager
    from agent.openclaw_client import OpenClawClient

from utils.logger import get_logger
from skills.actions_music import MusicActionsMixin
from skills.actions_calendar import CalendarActionsMixin
from skills.actions_utility import UtilityActionsMixin
from skills.actions_weather import WeatherActionsMixin
from skills.actions_timer import TimerActionsMixin

logger = get_logger("skills")


# Skill 中文显示名
SKILL_DISPLAY_NAMES = {
    "music": "本地音乐播放",
    "calendar": "日程管理",
    "conversation": "对话管理",
    "utility": "通用工具",
    "weather": "天气查询",
    "timer": "定时器",
}


@dataclass
class SkillResult:
    """技能执行结果。"""
    text: str = ""
    action: str = ""
    skill: str = ""
    handled: bool = True
    extra: dict = field(default_factory=dict)


@dataclass
class SkillAction:
    """一个技能内的具体操作。"""
    name: str = ""
    keywords: list[str] = field(default_factory=list)
    reply: str = ""


@dataclass
class Skill:
    """一个完整的技能。"""
    name: str = ""
    enabled: bool = True
    options: dict = field(default_factory=dict)
    actions: list[SkillAction] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return SKILL_DISPLAY_NAMES.get(self.name, self.name)


class SkillRouter(
    MusicActionsMixin,
    CalendarActionsMixin,
    UtilityActionsMixin,
    WeatherActionsMixin,
    TimerActionsMixin,
):
    """
    技能路由器。

    通过多重继承从各 Mixin 引入所有 action handler。
    自身只负责：初始化、关键词匹配、handler 注册和分发。

    Parameters
    ----------
    skills_config : dict
        config.yaml 中 skills 段（不含 enabled 字段）的技能分组配置
    enabled : bool
        全局开关
    database : ChatDatabase | None
    music_player : MusicPlayer | None
    timer_manager : TimerManager | None
    agent_client : OpenClawClient | None
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
        timer_manager=None,
        agent_client=None,
    ):
        self.enabled = enabled
        self.db = database
        self.music_player = music_player
        self.timer_manager = timer_manager
        self.agent_client = agent_client
        self.skills: dict[str, Skill] = {}
        self._action_handlers: dict[str, Callable] = {}

        self._register_builtin_actions()

        if skills_config:
            for skill_name, skill_cfg in skills_config.items():
                if not isinstance(skill_cfg, dict):
                    continue
                skill = Skill(
                    name=skill_name,
                    enabled=skill_cfg.get("enabled", True),
                    options=skill_cfg.get("options", {}),
                )
                for action_name, action_cfg in (skill_cfg.get("actions") or {}).items():
                    if not isinstance(action_cfg, dict):
                        continue
                    skill.actions.append(SkillAction(
                        name=action_name,
                        keywords=action_cfg.get("keywords", []),
                        reply=action_cfg.get("reply", ""),
                    ))
                self.skills[skill_name] = skill

        if self.enabled:
            total = sum(len(s.actions) for s in self.skills.values())
            active = sum(1 for s in self.skills.values() if s.enabled)
            logger.info("技能路由已启用: %d 个技能 (%d 启用), %d 个操作", len(self.skills), active, total)

    def _register_builtin_actions(self) -> None:
        """注册所有内置动作处理器。"""
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
            "reboot": self._action_reboot,
            "confirm_reboot": self._action_confirm_reboot,
            "system_status": self._action_system_status,
            "ip_address": self._action_ip_address,
            "network_status": self._action_network_status,
            "morning_briefing": self._action_morning_briefing,
            # weather
            "query_weather": self._action_query_weather,
            # timer
            "set_timer": self._action_set_timer,
            "query_timer": self._action_query_timer,
            "cancel_timer": self._action_cancel_timer,
        }

    async def match(self, text: str) -> Optional[SkillResult]:
        """
        匹配用户输入，返回执行结果或 None（交给 AI）。
        遍历所有启用的技能及其操作，去除标点后匹配关键词。
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

    async def _execute(self, skill: Skill, action: SkillAction, user_text: str = "") -> SkillResult:
        """执行匹配到的操作。"""
        handler = self._action_handlers.get(action.name)
        if handler:
            return await handler(skill, action, user_text)
        return SkillResult(text=action.reply or "好的", action=action.name, skill=skill.name)

    def _make_result(self, text: str, action: str, skill: str) -> SkillResult:
        """便捷方法：创建 SkillResult。"""
        return SkillResult(text=text, action=action, skill=skill)
