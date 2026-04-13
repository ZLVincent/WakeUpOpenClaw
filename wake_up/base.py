"""
唤醒词检测器抽象基类

定义统一的接口规范，所有唤醒词引擎（Porcupine、Snowboy 等）必须实现这些方法。
"""

from abc import ABC, abstractmethod
from typing import Callable, Optional

from audio.recorder import AudioRecorder
from utils.logger import get_logger

logger = get_logger("wake_up")


class BaseWakeWordDetector(ABC):
    """
    唤醒词检测器抽象基类。

    所有唤醒词引擎必须实现以下方法:
    - initialize(): 初始化引擎
    - frame_length: 每帧音频样本数
    - sample_rate: 音频采样率
    - process_frame(): 处理一帧音频并判断是否检测到唤醒词
    - listen(): 阻塞式持续监听
    - stop(): 停止监听
    - cleanup(): 释放资源
    """

    def __init__(self):
        self._is_running = False

    @abstractmethod
    def initialize(self) -> None:
        """初始化唤醒词引擎。初始化失败应抛出异常。"""
        ...

    @property
    @abstractmethod
    def frame_length(self) -> int:
        """引擎需要的每帧音频样本数。"""
        ...

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """引擎需要的音频采样率 (Hz)。"""
        ...

    @abstractmethod
    def process_frame(self, audio_frame: list[int]) -> bool:
        """
        处理一帧音频数据，检测是否包含唤醒词。

        Parameters
        ----------
        audio_frame : list[int]
            int16 音频样本列表，长度应等于 frame_length

        Returns
        -------
        bool
            True 表示检测到唤醒词
        """
        ...

    def listen(
        self,
        recorder: AudioRecorder,
        on_wake: Optional[Callable[[], None]] = None,
    ) -> None:
        """
        持续监听麦克风，直到检测到唤醒词。

        这是一个阻塞方法，检测到唤醒词后返回。
        可通过 stop() 中断。

        Parameters
        ----------
        recorder : AudioRecorder
            已打开的音频录音器
        on_wake : callable, optional
            检测到唤醒词时的回调函数
        """
        self._is_running = True
        logger.info("开始监听唤醒词... (说出唤醒词来激活)")

        while self._is_running:
            audio_frame = recorder.read_frame()
            if self.process_frame(audio_frame):
                if on_wake:
                    on_wake()
                return

    def stop(self) -> None:
        """停止监听。"""
        self._is_running = False
        logger.debug("唤醒词监听已请求停止")

    @abstractmethod
    def cleanup(self) -> None:
        """释放引擎资源。"""
        ...

    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        return False
