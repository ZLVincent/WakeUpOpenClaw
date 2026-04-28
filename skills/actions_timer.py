"""
定时器技能动作 Mixin

包含 timer 技能的所有操作处理器：
设定/查询/取消定时器。
"""

from utils.logger import get_logger

logger = get_logger("skills")


class TimerActionsMixin:
    """定时器技能动作。"""

    async def _action_set_timer(self, skill, action, user_text=""):
        """设定定时器。从用户输入中解析时长和标签。"""
        from skills.timer import parse_duration, format_duration

        if not self.timer_manager:
            return self._make_result("定时器功能不可用", "set_timer", "timer")

        duration, label = parse_duration(user_text)
        if duration <= 0:
            return self._make_result("没有识别到有效的时间，请说类似5分钟后提醒我", "set_timer", "timer")

        self.timer_manager.create(duration, label)
        duration_str = format_duration(duration)
        text = f"好的，{duration_str}后提醒您"
        if label:
            text += f"，{label}"
        return self._make_result(text, "set_timer", "timer")

    async def _action_query_timer(self, skill, action, user_text=""):
        """查询定时器状态。"""
        from skills.timer import format_duration

        if not self.timer_manager:
            return self._make_result("定时器功能不可用", "query_timer", "timer")

        active = self.timer_manager.active_timers
        if not active:
            return self._make_result("当前没有定时器", "query_timer", "timer")

        lines = [f"当前有{len(active)}个定时器。"]
        for t in active:
            remaining = format_duration(t.remaining_seconds)
            lines.append(f"还剩{remaining}，{t.label}。" if t.label else f"还剩{remaining}。")
        return self._make_result("\n".join(lines), "query_timer", "timer")

    async def _action_cancel_timer(self, skill, action, user_text=""):
        """取消定时器。"""
        if not self.timer_manager:
            return self._make_result("定时器功能不可用", "cancel_timer", "timer")

        if self.timer_manager.count == 0:
            return self._make_result("当前没有定时器", "cancel_timer", "timer")

        if self.timer_manager.count > 1:
            count = self.timer_manager.cancel_all()
            return self._make_result(f"已取消全部{count}个定时器", "cancel_timer", "timer")

        timer = self.timer_manager.cancel()
        return self._make_result("好的，定时器已取消" if timer else "取消失败", "cancel_timer", "timer")
