"""openWakeWord 唤醒词检测适配器。

模型文件由使用者自行训练或准备，并通过 ``config.yaml`` 指定。只有选择
``openwakeword`` 引擎时才会导入该可选依赖。
"""

from pathlib import Path
from typing import Any, Optional

from utils.logger import get_logger
from wake_up.base import BaseWakeWordDetector

logger = get_logger("wake_up")


class OpenWakeWordDetector(BaseWakeWordDetector):
    """使用 openWakeWord 自定义模型进行本地唤醒词检测。"""

    _SAMPLE_RATE = 16000
    _FRAME_LENGTH = 1280

    def __init__(
        self,
        model_path: str,
        model_name: str,
        threshold: float = 0.5,
        inference_framework: Optional[str] = None,
    ):
        super().__init__()
        if not model_path:
            raise ValueError("openWakeWord 的 model_path 不能为空")
        if not model_name:
            raise ValueError("openWakeWord 的 model_name 不能为空")
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("openWakeWord 的 threshold 必须在 0.0 到 1.0 之间")
        if inference_framework not in (None, "onnx", "tflite"):
            raise ValueError(
                "openWakeWord 的 inference_framework 仅支持 onnx 或 tflite"
            )

        self.model_path = model_path
        self.model_name = model_name
        self.threshold = threshold
        self.inference_framework = (
            inference_framework or self._infer_inference_framework(model_path)
        )
        self._model: Any = None
        self._numpy: Any = None

    def initialize(self) -> None:
        """延迟加载 openWakeWord 和模型，避免影响其他唤醒词引擎。"""
        if not Path(self.model_path).exists():
            logger.critical("openWakeWord 模型文件不存在: %s", self.model_path)
            raise FileNotFoundError(
                f"openWakeWord 模型文件不存在: {self.model_path}"
            )

        try:
            import numpy
            from openwakeword.model import Model
        except ImportError as exc:
            logger.critical(
                "openWakeWord 或 numpy 未安装，请执行: pip install openwakeword numpy"
            )
            raise ImportError(
                "openWakeWord 或 numpy 未安装。请执行: pip install openwakeword numpy"
            ) from exc

        logger.info("正在初始化 openWakeWord 唤醒词引擎...")
        logger.debug("  模型路径: %s", self.model_path)
        logger.debug("  模型名称: %s", self.model_name)
        logger.debug("  阈值: %.2f", self.threshold)
        logger.debug("  推理后端: %s", self.inference_framework)

        self._model = Model(
            wakeword_models=[self.model_path],
            inference_framework=self.inference_framework,
        )
        self._numpy = numpy
        logger.info(
            "openWakeWord 初始化成功 (帧长: %d, 采样率: %d)",
            self._FRAME_LENGTH,
            self._SAMPLE_RATE,
        )

    @property
    def frame_length(self) -> int:
        self._require_initialized()
        return self._FRAME_LENGTH

    @property
    def sample_rate(self) -> int:
        self._require_initialized()
        return self._SAMPLE_RATE

    def process_frame(self, audio_frame: list[int]) -> bool:
        self._require_initialized()
        if not audio_frame:
            return False
        if len(audio_frame) != self._FRAME_LENGTH:
            logger.warning(
                "openWakeWord 音频帧长度不匹配: 期望 %d，实际 %d",
                self._FRAME_LENGTH,
                len(audio_frame),
            )
            return False

        scores = self._model.predict(
            self._numpy.asarray(audio_frame, dtype=self._numpy.int16)
        )
        score = scores.get(self.model_name)
        if score is None:
            logger.debug("模型未返回唤醒词 '%s' 的分数", self.model_name)
            return False
        if score >= self.threshold:
            logger.info(
                "检测到唤醒词! (model=%s, score=%.3f, threshold=%.3f)",
                self.model_name,
                score,
                self.threshold,
            )
            return True
        return False

    def cleanup(self) -> None:
        self._model = None
        self._numpy = None
        logger.info("openWakeWord 资源已释放")

    def _require_initialized(self) -> None:
        if self._model is None or self._numpy is None:
            raise RuntimeError("openWakeWord 未初始化，请先调用 initialize()")

    @staticmethod
    def _infer_inference_framework(model_path: str) -> str:
        suffix = Path(model_path).suffix.lower()
        if suffix == ".onnx":
            return "onnx"
        if suffix == ".tflite":
            return "tflite"
        raise ValueError(
            "无法从模型扩展名推断推理后端；请使用 .onnx/.tflite，"
            "或显式配置 inference_framework"
        )
