"""
唤醒词检测模块

支持多种唤醒词引擎，通过配置文件切换:
- snowboy: Snowboy (seasalt-ai fork)，需从源码编译
- porcupine: Picovoice Porcupine，需要 Access Key

使用 create_detector(config) 工厂函数创建检测器实例。
"""

from wake_up.base import BaseWakeWordDetector
from wake_up.factory import create_detector

__all__ = ["BaseWakeWordDetector", "create_detector"]
