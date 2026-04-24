"""
定时器管理模块

管理多个倒计时定时器，到时通过 TTS 语音播报和可选的微信推送提醒。
纯内存管理（asyncio 任务），不持久化到数据库，重启后定时器丢失。
"""

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from utils.logger import get_logger

logger = get_logger("timer")


@dataclass
class Timer:
    """一个定时器实例。"""
    id: int                          # 唯一 ID
    label: str                       # 标签/描述（如"关火"）
    duration_seconds: int            # 总时长（秒）
    created_at: float = 0.0          # 创建时间戳
    task: Optional[asyncio.Task] = field(default=None, repr=False)

    @property
    def remaining_seconds(self) -> int:
        """剩余秒数。"""
        elapsed = time.time() - self.created_at
        remaining = self.duration_seconds - elapsed
        return max(0, int(remaining))

    @property
    def is_active(self) -> bool:
        """是否仍在倒计时。"""
        return self.task is not None and not self.task.done()


def format_duration(seconds: int) -> str:
    """将秒数格式化为口语化的时间描述。"""
    if seconds <= 0:
        return "0秒"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if hours > 0:
        parts.append(f"{hours}小时")
    if minutes > 0:
        parts.append(f"{minutes}分钟")
    if secs > 0 and hours == 0:  # 有小时的时候不报秒
        parts.append(f"{secs}秒")
    return "".join(parts)


def parse_duration(text: str) -> tuple[int, str]:
    """
    从用户输入中解析时长和标签。

    支持的格式:
    - "5分钟后提醒我"          → (300, "")
    - "5分钟后提醒我关火"      → (300, "关火")
    - "1小时后叫我"            → (3600, "")
    - "1个半小时后提醒我"      → (5400, "")
    - "90秒后提醒我"           → (90, "")
    - "半小时后提醒我"         → (1800, "")
    - "10分钟闹钟"             → (600, "")
    - "定个5分钟的定时器"      → (300, "")

    Returns
    -------
    tuple[int, str]
        (时长秒数, 标签)。解析失败返回 (0, "")
    """
    text = text.strip()
    total_seconds = 0

    # 匹配 "半小时"
    if "半小时" in text:
        total_seconds += 1800
        text = text.replace("半小时", "")

    # 匹配 "N个半小时" / "N小时半"
    m = re.search(r"(\d+)\s*个半小时", text)
    if m:
        total_seconds += int(m.group(1)) * 3600 + 1800
        text = text[:m.start()] + text[m.end():]
    else:
        m = re.search(r"(\d+)\s*小时半", text)
        if m:
            total_seconds += int(m.group(1)) * 3600 + 1800
            text = text[:m.start()] + text[m.end():]

    # 匹配 "N小时"
    m = re.search(r"(\d+)\s*小时", text)
    if m:
        total_seconds += int(m.group(1)) * 3600
        text = text[:m.start()] + text[m.end():]

    # 匹配 "N分钟" / "N分"
    m = re.search(r"(\d+)\s*分钟?", text)
    if m:
        total_seconds += int(m.group(1)) * 60
        text = text[:m.start()] + text[m.end():]

    # 匹配 "N秒"
    m = re.search(r"(\d+)\s*秒", text)
    if m:
        total_seconds += int(m.group(1))
        text = text[:m.start()] + text[m.end():]

    # 提取标签：去掉常见的前后缀词
    label = text
    for word in ("后", "提醒我", "叫我", "提醒", "定个", "定一个",
                 "设个", "设一个", "的", "闹钟", "定时器", "计时器",
                 "倒计时", "帮我", "请"):
        label = label.replace(word, "")
    # 去除标点
    label = re.sub(r'[。，！？、；：.!?,;:\s]+', '', label).strip()

    return total_seconds, label


class TimerManager:
    """
    定时器管理器。

    管理多个并行的倒计时定时器，到期时通过回调函数通知。

    Parameters
    ----------
    on_expire : callable
        定时器到期时的回调函数，签名: async on_expire(timer: Timer)
    """

    def __init__(self, on_expire: Optional[Callable] = None):
        self._timers: dict[int, Timer] = {}
        self._next_id = 1
        self._on_expire = on_expire

    @property
    def active_timers(self) -> list[Timer]:
        """所有活跃的定时器。"""
        return [t for t in self._timers.values() if t.is_active]

    @property
    def count(self) -> int:
        """活跃定时器数量。"""
        return len(self.active_timers)

    def create(self, duration_seconds: int, label: str = "") -> Timer:
        """
        创建一个新的定时器。

        Parameters
        ----------
        duration_seconds : int
            倒计时总秒数
        label : str
            标签/描述

        Returns
        -------
        Timer
        """
        timer = Timer(
            id=self._next_id,
            label=label,
            duration_seconds=duration_seconds,
            created_at=time.time(),
        )
        self._next_id += 1

        # 启动 asyncio 倒计时任务
        timer.task = asyncio.create_task(self._countdown(timer))
        self._timers[timer.id] = timer

        logger.info(
            "定时器 #%d 已创建: %s%s",
            timer.id,
            format_duration(duration_seconds),
            f" ({label})" if label else "",
        )
        return timer

    def cancel(self, timer_id: int = None) -> Optional[Timer]:
        """
        取消定时器。

        Parameters
        ----------
        timer_id : int, optional
            指定 ID。不传则取消最近创建的活跃定时器。

        Returns
        -------
        Timer | None
            被取消的定时器，找不到返回 None
        """
        if timer_id:
            timer = self._timers.get(timer_id)
        else:
            # 取消最近的活跃定时器
            active = self.active_timers
            timer = active[-1] if active else None

        if timer and timer.is_active:
            timer.task.cancel()
            logger.info("定时器 #%d 已取消", timer.id)
            return timer
        return None

    def cancel_all(self) -> int:
        """取消所有活跃定时器，返回取消的数量。"""
        count = 0
        for timer in self.active_timers:
            timer.task.cancel()
            count += 1
        if count:
            logger.info("已取消全部 %d 个定时器", count)
        return count

    async def _countdown(self, timer: Timer) -> None:
        """倒计时协程，到期后调用回调。"""
        try:
            await asyncio.sleep(timer.duration_seconds)
            logger.info(
                "定时器 #%d 到期: %s",
                timer.id,
                timer.label or format_duration(timer.duration_seconds),
            )
            if self._on_expire:
                await self._on_expire(timer)
        except asyncio.CancelledError:
            logger.debug("定时器 #%d 已被取消", timer.id)
        finally:
            # 清理已完成的定时器
            self._timers.pop(timer.id, None)
