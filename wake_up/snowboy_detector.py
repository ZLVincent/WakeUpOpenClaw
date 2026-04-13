"""
Snowboy 唤醒词检测模块

使用 Snowboy (seasalt-ai fork) 进行本地唤醒词检测。
支持 .umdl (通用模型) 和 .pmdl (个人模型)。

Snowboy 不能通过 pip 安装，需要从源码编译:
  1. sudo apt install swig libatlas-base-dev sox
  2. git clone https://github.com/seasalt-ai/snowboy.git
  3. cd snowboy/swig/Python3 && make
  4. 将编译产物 (_snowboydetect.so, snowboydetect.py) 和 resources/ 目录
     复制到项目的 snowboy/ 目录下

目录结构示例:
  WakeUpOpenClaw/
  └── snowboy/
      ├── _snowboydetect.so      # 编译产物
      ├── snowboydetect.py        # 编译产物
      └── resources/
          ├── common.res          # 必需的资源文件
          └── models/
              └── snowboy.umdl    # 唤醒词模型
"""

import os
import struct
import sys

from utils.logger import get_logger
from wake_up.base import BaseWakeWordDetector

logger = get_logger("wake_up")

# Snowboy 固定参数
_SNOWBOY_SAMPLE_RATE = 16000
_SNOWBOY_DEFAULT_FRAME_LENGTH = 2048  # 每帧样本数 (4096 bytes / 2)


class SnowboyDetector(BaseWakeWordDetector):
    """
    基于 Snowboy 的唤醒词检测器。

    Parameters
    ----------
    resource_path : str
        Snowboy common.res 资源文件路径
    model_path : str
        唤醒词模型文件路径 (.umdl 或 .pmdl)
    sensitivity : float
        检测灵敏度 0.0 ~ 1.0
    audio_gain : float
        音频增益，默认 1.0
    apply_frontend : bool
        是否启用前端音频处理 (降噪等)
    snowboy_lib_path : str
        snowboydetect.so 所在目录路径
    """

    def __init__(
        self,
        resource_path: str,
        model_path: str,
        sensitivity: float = 0.5,
        audio_gain: float = 1.0,
        apply_frontend: bool = False,
        snowboy_lib_path: str = "./snowboy",
    ):
        super().__init__()
        self.resource_path = resource_path
        self.model_path = model_path
        self.sensitivity = sensitivity
        self.audio_gain = audio_gain
        self.apply_frontend = apply_frontend
        self.snowboy_lib_path = snowboy_lib_path
        self._detector = None

    def initialize(self) -> None:
        """初始化 Snowboy 检测引擎。"""
        # 检查文件是否存在
        if not os.path.exists(self.resource_path):
            logger.critical(
                "Snowboy 资源文件不存在: %s\n"
                "请确认 snowboy 已正确编译并将 resources/common.res 复制到指定路径",
                self.resource_path,
            )
            raise FileNotFoundError(
                f"Snowboy 资源文件不存在: {self.resource_path}"
            )

        if not os.path.exists(self.model_path):
            logger.critical(
                "Snowboy 唤醒词模型不存在: %s\n"
                "可使用 seasalt-ai/snowboy 仓库中 resources/models/ 下的 .umdl 文件",
                self.model_path,
            )
            raise FileNotFoundError(
                f"Snowboy 唤醒词模型不存在: {self.model_path}"
            )

        # 将 snowboy 库路径加入 Python 搜索路径
        lib_path = os.path.abspath(self.snowboy_lib_path)
        if lib_path not in sys.path:
            sys.path.insert(0, lib_path)
            logger.debug("已将 Snowboy 库路径加入 sys.path: %s", lib_path)

        # 尝试导入 snowboydetect
        try:
            import snowboydetect
        except ImportError as e:
            logger.critical(
                "无法导入 snowboydetect 模块。请按以下步骤编译:\n"
                "  1. sudo apt install swig libatlas-base-dev sox\n"
                "  2. git clone https://github.com/seasalt-ai/snowboy.git\n"
                "  3. cd snowboy/swig/Python3 && make\n"
                "  4. 将 _snowboydetect.so 和 snowboydetect.py 复制到: %s\n"
                "原始错误: %s",
                lib_path, e,
            )
            raise ImportError(
                f"snowboydetect 模块不可用，请从源码编译安装。详见日志。"
            ) from e

        logger.info("正在初始化 Snowboy 唤醒词引擎...")
        logger.debug("  资源文件: %s", self.resource_path)
        logger.debug("  模型路径: %s", self.model_path)
        logger.debug("  灵敏度: %.2f", self.sensitivity)
        logger.debug("  音频增益: %.1f", self.audio_gain)
        logger.debug("  前端处理: %s", self.apply_frontend)

        try:
            self._detector = snowboydetect.SnowboyDetect(
                resource_filename=os.path.abspath(self.resource_path).encode(),
                model_str=os.path.abspath(self.model_path).encode(),
            )
            self._detector.SetSensitivity(str(self.sensitivity).encode())
            self._detector.SetAudioGain(self.audio_gain)
            self._detector.ApplyFrontend(self.apply_frontend)

            logger.info(
                "Snowboy 初始化成功 (采样率: %d, 声道: %d, 位深: %d)",
                self._detector.SampleRate(),
                self._detector.NumChannels(),
                self._detector.BitsPerSample(),
            )
        except Exception as e:
            logger.critical("Snowboy 初始化失败: %s", e)
            raise

    @property
    def frame_length(self) -> int:
        """每帧音频样本数。Snowboy 无固定要求，使用 2048 样本 (~128ms@16kHz)。"""
        return _SNOWBOY_DEFAULT_FRAME_LENGTH

    @property
    def sample_rate(self) -> int:
        """Snowboy 固定使用 16000 Hz。"""
        return _SNOWBOY_SAMPLE_RATE

    def process_frame(self, audio_frame: list[int]) -> bool:
        """
        处理一帧音频数据。

        Snowboy 的 RunDetection() 接受原始 PCM bytes。
        这里将 list[int] 转换为 bytes 后传入。

        RunDetection 返回值:
          -2: 静默
          -1: 错误
           0: 有语音但未匹配唤醒词
          >0: 检测到唤醒词 (值为关键词索引，从 1 开始)
        """
        if self._detector is None:
            raise RuntimeError("Snowboy 未初始化，请先调用 initialize()")

        # list[int] -> bytes (16-bit little-endian PCM)
        audio_bytes = struct.pack(f"{len(audio_frame)}h", *audio_frame)

        result = self._detector.RunDetection(audio_bytes)

        if result == -1:
            logger.warning("Snowboy RunDetection 返回错误")
            return False

        if result > 0:
            logger.info("检测到唤醒词! (keyword_index=%d)", result)
            return True

        return False

    def cleanup(self) -> None:
        """释放 Snowboy 资源。"""
        # snowboydetect.SnowboyDetect 没有显式的 delete/close 方法，
        # Python GC 会处理。置空引用即可。
        if self._detector is not None:
            self._detector = None
            logger.info("Snowboy 资源已释放")
