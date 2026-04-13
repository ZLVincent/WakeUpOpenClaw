"""
Porcupine 唤醒词检测模块

使用 Picovoice Porcupine 进行本地唤醒词检测。
支持自定义 .ppn 唤醒词模型文件。
"""

import os
from typing import Callable, Optional

import pvporcupine

from audio.recorder import AudioRecorder
from utils.logger import get_logger

logger = get_logger("wake_up")


class WakeWordDetector:
    """
    唤醒词检测器，封装 Porcupine 引擎。

    Parameters
    ----------
    access_key : str
        Picovoice Access Key (https://console.picovoice.ai/ 免费注册获取)
    keyword_path : str
        自定义唤醒词 .ppn 模型文件路径
    sensitivity : float
        检测灵敏度 0.0 ~ 1.0
    """

    def __init__(
        self,
        access_key: str,
        keyword_path: str,
        sensitivity: float = 0.5,
    ):
        self.access_key = access_key
        self.keyword_path = keyword_path
        self.sensitivity = sensitivity

        self._porcupine: Optional[pvporcupine.Porcupine] = None
        self._is_running = False

    def initialize(self) -> None:
        """初始化 Porcupine 引擎。"""
        if not os.path.exists(self.keyword_path):
            logger.critical(
                "唤醒词模型文件不存在: %s (请在 Picovoice Console 训练后下载)",
                self.keyword_path,
            )
            raise FileNotFoundError(
                f"唤醒词模型文件不存在: {self.keyword_path}"
            )

        logger.info("正在初始化 Porcupine 唤醒词引擎...")
        logger.debug("  模型路径: %s", self.keyword_path)
        logger.debug("  灵敏度: %.2f", self.sensitivity)

        try:
            self._porcupine = pvporcupine.create(
                access_key=self.access_key,
                keyword_paths=[self.keyword_path],
                sensitivities=[self.sensitivity],
            )
            logger.info(
                "Porcupine 初始化成功 (帧长: %d, 采样率: %d)",
                self._porcupine.frame_length,
                self._porcupine.sample_rate,
            )
        except pvporcupine.PorcupineError as e:
            logger.critical("Porcupine 初始化失败: %s", e)
            raise

    @property
    def frame_length(self) -> int:
        """Porcupine 需要的音频帧长度。"""
        if self._porcupine is None:
            raise RuntimeError("Porcupine 未初始化，请先调用 initialize()")
        return self._porcupine.frame_length

    @property
    def sample_rate(self) -> int:
        """Porcupine 需要的采样率。"""
        if self._porcupine is None:
            raise RuntimeError("Porcupine 未初始化，请先调用 initialize()")
        return self._porcupine.sample_rate

    def process_frame(self, audio_frame: list[int]) -> bool:
        """
        处理一帧音频数据，检测是否包含唤醒词。

        Parameters
        ----------
        audio_frame : list[int]
            int16 音频样本列表，长度必须等于 frame_length

        Returns
        -------
        bool
            True 表示检测到唤醒词
        """
        if self._porcupine is None:
            raise RuntimeError("Porcupine 未初始化，请先调用 initialize()")

        keyword_index = self._porcupine.process(audio_frame)
        if keyword_index >= 0:
            logger.info("检测到唤醒词! (keyword_index=%d)", keyword_index)
            return True
        return False

    def listen(
        self,
        recorder: AudioRecorder,
        on_wake: Optional[Callable[[], None]] = None,
    ) -> None:
        """
        持续监听麦克风，直到检测到唤醒词。

        这是一个阻塞方法，检测到唤醒词后返回。
        可通过设置 self._is_running = False 来中断。

        Parameters
        ----------
        recorder : AudioRecorder
            已打开的音频录音器
        on_wake : callable, optional
            检测到唤醒词时的回调函数
        """
        if self._porcupine is None:
            raise RuntimeError("Porcupine 未初始化，请先调用 initialize()")

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

    def cleanup(self) -> None:
        """释放 Porcupine 资源。"""
        if self._porcupine is not None:
            self._porcupine.delete()
            self._porcupine = None
            logger.info("Porcupine 资源已释放")

    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        return False
