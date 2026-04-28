"""
日程技能动作 Mixin

包含 calendar 技能的所有操作处理器：
今天/明天/本周/下周/剩余日程查询。
"""

import datetime
from utils.logger import get_logger

logger = get_logger("skills")


class CalendarActionsMixin:
    """日程技能动作。"""

    _WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    @staticmethod
    def _format_time_for_speech(time_str: str) -> str:
        """将时间字符串转换为适合语音播报的格式。"""
        try:
            parts = time_str.strip().split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            return f"{hour}点" if minute == 0 else f"{hour}点{minute:02d}分"
        except (ValueError, IndexError):
            return time_str

    def _format_event_line(self, ev: dict, prefix: str = "") -> str:
        """将单个日程格式化为一行播报文本。"""
        if ev.get("all_day"):
            return f"{prefix}全天，{ev['title']}。"
        elif ev.get("start_time"):
            t = self._format_time_for_speech(ev["start_time"])
            return f"{prefix}{t}，{ev['title']}。"
        else:
            return f"{prefix}{ev['title']}。"

    async def _query_events_for_date(self, date: datetime.date, label: str):
        """查询指定日期的日程。"""
        if not self.db:
            return self._make_result("日程功能暂不可用", "query_events", "calendar")
        try:
            events = await self.db.get_events_by_date(date.strftime("%Y-%m-%d"))
        except Exception as e:
            logger.warning("查询日程失败: %s", e)
            return self._make_result("查询日程时出错了", "query_events", "calendar")
        if not events:
            return self._make_result(f"{label}没有日程安排", "query_events", "calendar")

        lines = [f"{label}有{len(events)}个日程。"]
        for ev in events:
            lines.append(self._format_event_line(ev))
        return self._make_result("\n".join(lines), "query_events", "calendar")

    async def _action_query_today_events(self, skill, action, user_text=""):
        return await self._query_events_for_date(datetime.date.today(), "今天")

    async def _action_query_tomorrow_events(self, skill, action, user_text=""):
        return await self._query_events_for_date(
            datetime.date.today() + datetime.timedelta(days=1), "明天"
        )

    async def _action_query_week_events(self, skill, action, user_text=""):
        """查询本周日程。"""
        if not self.db:
            return self._make_result("日程功能暂不可用", "query_week", "calendar")
        today = datetime.date.today()
        monday = today - datetime.timedelta(days=today.weekday())
        sunday = monday + datetime.timedelta(days=6)
        try:
            events = await self.db.get_events_by_range(
                monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d"),
            )
        except Exception as e:
            logger.warning("查询本周日程失败: %s", e)
            return self._make_result("查询日程时出错了", "query_week", "calendar")
        if not events:
            return self._make_result("本周没有日程安排", "query_week", "calendar")

        lines = [f"本周共有{len(events)}个日程。"]
        for ev in events:
            day = datetime.date.fromisoformat(ev["date"])
            lines.append(self._format_event_line(ev, f"{self._WEEKDAY_NAMES[day.weekday()]}，"))
        return self._make_result("\n".join(lines), "query_week", "calendar")

    async def _action_query_next_week_events(self, skill, action, user_text=""):
        """查询下周日程。"""
        if not self.db:
            return self._make_result("日程功能暂不可用", "query_next_week", "calendar")
        today = datetime.date.today()
        next_monday = today + datetime.timedelta(days=(7 - today.weekday()))
        next_sunday = next_monday + datetime.timedelta(days=6)
        try:
            events = await self.db.get_events_by_range(
                next_monday.strftime("%Y-%m-%d"), next_sunday.strftime("%Y-%m-%d"),
            )
        except Exception as e:
            logger.warning("查询下周日程失败: %s", e)
            return self._make_result("查询日程时出错了", "query_next_week", "calendar")
        if not events:
            return self._make_result("下周没有日程安排", "query_next_week", "calendar")

        lines = [f"下周共有{len(events)}个日程。"]
        for ev in events:
            day = datetime.date.fromisoformat(ev["date"])
            lines.append(self._format_event_line(ev, f"{self._WEEKDAY_NAMES[day.weekday()]}，"))
        return self._make_result("\n".join(lines), "query_next_week", "calendar")

    async def _action_query_upcoming_events(self, skill, action, user_text=""):
        """查询本周剩余未完成的日程。"""
        if not self.db:
            return self._make_result("日程功能暂不可用", "query_upcoming", "calendar")
        today = datetime.date.today()
        sunday = today + datetime.timedelta(days=(6 - today.weekday()))
        try:
            events = await self.db.get_upcoming_events_in_range(
                today.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d"),
            )
        except Exception as e:
            logger.warning("查询剩余日程失败: %s", e)
            return self._make_result("查询日程时出错了", "query_upcoming", "calendar")
        if not events:
            return self._make_result("本周剩余没有待完成的日程", "query_upcoming", "calendar")

        lines = [f"本周还有{len(events)}个未完成的日程。"]
        for ev in events:
            day = datetime.date.fromisoformat(ev["date"])
            delta = (day - today).days
            if delta == 0:
                label = "今天"
            elif delta == 1:
                label = "明天"
            elif delta == 2:
                label = "后天"
            else:
                label = self._WEEKDAY_NAMES[day.weekday()]
            lines.append(self._format_event_line(ev, f"{label}，"))
        return self._make_result("\n".join(lines), "query_upcoming", "calendar")
