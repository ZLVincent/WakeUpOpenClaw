"""
免打扰控制器 — 管理 Do-Not-Disturb 时段逻辑。
"""

import datetime

from utils.logger import get_logger

logger = get_logger("dnd")


class DNDController:
    """
    免打扰时段控制器。

    通过配置的 start/end 时间判断当前是否处于免打扰时段。
    支持跨午夜时段（如 22:30 ~ 07:30）。
    """

    def __init__(self, dnd_cfg: dict):
        self.enabled = dnd_cfg.get("enabled", False)
        self.start = self._parse_time(dnd_cfg.get("start", "22:30"))
        self.end = self._parse_time(dnd_cfg.get("end", "07:30"))

    @staticmethod
    def _parse_time(time_str: str) -> datetime.time:
        try:
            parts = time_str.strip().split(":")
            return datetime.time(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            logger.warning("时间格式无效: '%s'，使用默认值 00:00", time_str)
            return datetime.time(0, 0)

    def is_active(self, now: datetime.time = None) -> bool:
        if not self.enabled:
            return False
        if now is None:
            now = datetime.datetime.now().time()

        if self.start <= self.end:
            return self.start <= now <= self.end
        else:
            return now >= self.start or now <= self.end

    def format_start(self) -> str:
        return self.start.strftime("%H:%M")

    def format_end(self) -> str:
        return self.end.strftime("%H:%M")
