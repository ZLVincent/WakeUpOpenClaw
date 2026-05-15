"""
对话管理器 — 管理多轮对话状态、保存消息、自动换对话。
"""

from typing import Optional

from storage.database import ChatDatabase
from utils.logger import get_logger

logger = get_logger("conversation")


class ConversationManager:
    """
    管理当前对话的状态，包括会话信息、消息持久化、自动建新对话。
    """

    def __init__(self, db: ChatDatabase, max_history_rounds: int = 30):
        self.db = db
        self.max_history_rounds = max_history_rounds
        self._current_conversation: Optional[dict] = None

    @property
    def current_conversation(self) -> Optional[dict]:
        return self._current_conversation

    @current_conversation.setter
    def current_conversation(self, value: Optional[dict]):
        self._current_conversation = value

    async def load_or_create(self, source: str = "voice") -> dict:
        try:
            self._current_conversation = await self.db.get_or_create_active_conversation(source)
            logger.info(
                "当前对话 #%d (session=%s, 轮次=%d)",
                self._current_conversation["id"],
                self._current_conversation["session_id"],
                self._current_conversation["round_count"],
            )
        except Exception as e:
            logger.warning("加载对话失败: %s", e)
        return self._current_conversation or {}

    async def start_new(self, source: str = "voice") -> dict:
        try:
            self._current_conversation = await self.db.start_new_conversation(source)
            return self._current_conversation
        except Exception as e:
            logger.error("开启新对话失败: %s", e)
            return {}

    async def save_message(
        self, role: str, content: str, source: str, duration_ms: int = None,
    ) -> None:
        try:
            if self._current_conversation:
                conv_id = self._current_conversation["id"]
                await self.db.add_message(conv_id, role, content, source, duration_ms)

                if role == "user" and not self._current_conversation.get("title"):
                    title = content[:50]
                    await self.db.update_conversation_title(conv_id, title)
                    self._current_conversation["title"] = title

                if role == "assistant":
                    count = await self.db.increment_round_count(conv_id)
                    self._current_conversation["round_count"] = count
        except Exception as e:
            logger.debug("保存消息到数据库失败: %s", e)

    async def check_auto_new(self) -> None:
        try:
            if not self._current_conversation:
                return
            round_count = self._current_conversation.get("round_count", 0)
            if round_count >= self.max_history_rounds:
                logger.info("对话已达 %d 轮，自动开启新对话", round_count)
                self._current_conversation = await self.db.start_new_conversation("voice")
                return True
        except Exception as e:
            logger.debug("自动新建对话失败: %s", e)
        return False

    def get_session_id(self) -> str:
        return (self._current_conversation or {}).get("session_id", "")
