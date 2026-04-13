"""
Porcupine 唤醒词检测模块

使用 Picovoice Porcupine 进行本地唤醒词检测。
支持自定义 .ppn 唤醒词模型文件。

需要安装: pip install pvporcupine
需要注册获取 Access Key: https://console.picovoice.ai/
"""

import os

from utils.logger import get_logger
from wake_up.base import BaseWakeWordDetector

logger = get_logger("wake_up")


class PorcupineDetector(BaseWakeWordDetector):
    """
    基于 Picovoice Porcupine 的唤醒词检测器。

    Parameters
    ----------
    access_key : str
        Picovoice Access Key
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
        super().__init__()
        self.access_key = access_key
        self.keyword_path = keyword_path
        self.sensitivity = sensitivity
        self._porcupine = None

    def initialize(self) -> None:
        """初始化 Porcupine 引擎。"""
        try:
            import pvporcupine
        except ImportError:
            logger.critical(
                "pvporcupine 未安装，请执行: pip install pvporcupine"
            )
            raise ImportError(
                "pvporcupine 未安装。请执行: pip install pvporcupine"
            )

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
        if self._porcupine is None:
            raise RuntimeError("Porcupine 未初始化，请先调用 initialize()")
        return self._porcupine.frame_length

    @property
    def sample_rate(self) -> int:
        if self._porcupine is None:
            raise RuntimeError("Porcupine 未初始化，请先调用 initialize()")
        return self._porcupine.sample_rate

    def process_frame(self, audio_frame: list[int]) -> bool:
        if self._porcupine is None:
            raise RuntimeError("Porcupine 未初始化，请先调用 initialize()")

        keyword_index = self._porcupine.process(audio_frame)
        if keyword_index >= 0:
            logger.info("检测到唤醒词! (keyword_index=%d)", keyword_index)
            return True
        return False

    def cleanup(self) -> None:
        if self._porcupine is not None:
            self._porcupine.delete()
            self._porcupine = None
            logger.info("Porcupine 资源已释放")
