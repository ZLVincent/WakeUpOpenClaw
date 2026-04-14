"""
技能路由模块

在发送给 OpenClaw 之前进行本地关键词匹配。
匹配到的指令直接本地执行，不走 AI，响应更快。
"""

import asyncio
import datetime
from dataclasses import dataclass, field
from typing import Callable, Optional

from utils.logger import get_logger

logger = get_logger("skills")


@dataclass
class SkillResult:
    """技能执行结果。"""
    text: str = ""           # 回复文本（TTS 朗读）
    action: str = ""         # 动作标识
    handled: bool = True     # 是否已处理（False 表示交给 AI）


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
    """

    def __init__(self, commands: list[dict] = None, enabled: bool = True):
        self.enabled = enabled
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
        }

    async def match(self, text: str) -> Optional[SkillResult]:
        """
        匹配用户输入，返回执行结果或 None（交给 AI）。

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

        text_lower = text.strip().lower()

        for cmd in self.commands:
            for keyword in cmd.keywords:
                if keyword.lower() in text_lower:
                    logger.info(
                        "技能匹配: '%s' -> action=%s (keyword='%s')",
                        text, cmd.action, keyword,
                    )
                    return await self._execute(cmd)

        return None

    async def _execute(self, cmd: SkillCommand) -> SkillResult:
        """执行匹配到的技能。"""
        handler = self._action_handlers.get(cmd.action)
        if handler:
            result = await handler(cmd)
            return result

        # 没有 handler，只返回配置的回复文本
        if cmd.reply:
            return SkillResult(text=cmd.reply, action=cmd.action)

        return SkillResult(text="好的", action=cmd.action)

    # ------------------------------------------------------------------
    # 内置动作处理器
    # ------------------------------------------------------------------

    async def _action_volume_up(self, cmd: SkillCommand) -> SkillResult:
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

    async def _action_volume_down(self, cmd: SkillCommand) -> SkillResult:
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

    async def _action_volume_set(self, cmd: SkillCommand) -> SkillResult:
        """设置音量到指定百分比（从 reply 中解析）。"""
        return SkillResult(text=cmd.reply or "好的", action="volume_set")

    async def _action_stop_playback(self, cmd: SkillCommand) -> SkillResult:
        """停止所有 mpv 播放。"""
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

    async def _action_current_time(self, cmd: SkillCommand) -> SkillResult:
        """报时。"""
        now = datetime.datetime.now()
        time_str = now.strftime("现在是%H点%M分")
        return SkillResult(text=time_str, action="current_time")

    async def _action_new_conversation(self, cmd: SkillCommand) -> SkillResult:
        """
        新建对话。

        注意：实际的新建操作由 main.py 的 _conversation_loop 根据
        action="new_conversation" 来执行。
        """
        return SkillResult(
            text=cmd.reply or "好的，已开启新对话",
            action="new_conversation",
        )
