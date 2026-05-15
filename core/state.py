"""
状态机定义 — 助手运行状态枚举。
"""

import enum


class State(enum.Enum):
    """助手运行状态。"""
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    SHUTDOWN = "shutdown"
