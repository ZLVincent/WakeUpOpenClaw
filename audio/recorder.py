"""
麦克风音频录制模块

提供两种模式：
- 帧模式 (frame mode): 为 Porcupine 唤醒词检测提供固定帧长的 int16 音频块
- 流模式 (stream mode): 为 FunASR 提供持续的 PCM 音频流
"""

import struct
from typing import Optional

import pyaudio

from utils.logger import get_logger

logger = get_logger("audio")


class AudioRecorder:
    """
    基于 PyAudio 的麦克风录音器。

    Parameters
    ----------
    sample_rate : int
        采样率，默认 16000 Hz
    channels : int
        声道数，默认 1（单声道）
    chunk_size : int
        每次读取的帧数，默认 512
    input_device_index : int | None
        输入设备索引，None 表示使用系统默认设备
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 512,
        input_device_index: Optional[int] = None,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self.input_device_index = input_device_index

        self._pa: Optional[pyaudio.PyAudio] = None
        self._stream: Optional[pyaudio.Stream] = None
        self._is_open = False

    def open(self) -> None:
        """打开音频流。"""
        if self._is_open:
            logger.warning("音频流已经打开，跳过重复打开")
            return

        logger.info(
            "正在打开音频设备 (采样率: %dHz, 声道: %d, 帧大小: %d)",
            self.sample_rate, self.channels, self.chunk_size,
        )

        self._pa = pyaudio.PyAudio()

        # 列出可用设备信息 (DEBUG 级别)
        if logger.isEnabledFor(10):  # DEBUG
            info = self._pa.get_host_api_info_by_index(0)
            num_devices = info.get("deviceCount", 0)
            for i in range(num_devices):
                dev_info = self._pa.get_device_info_by_host_api_device_index(0, i)
                if dev_info.get("maxInputChannels", 0) > 0:
                    logger.debug(
                        "  输入设备 [%d]: %s (最大输入声道: %d)",
                        i, dev_info["name"], dev_info["maxInputChannels"],
                    )

        try:
            self._stream = self._pa.open(
                rate=self.sample_rate,
                channels=self.channels,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=self.chunk_size,
                input_device_index=self.input_device_index,
            )
            self._is_open = True
            logger.info("音频设备已打开")
        except Exception as e:
            logger.critical("无法打开音频设备: %s", e)
            self.close()
            raise

    def close(self) -> None:
        """关闭音频流并释放资源。"""
        if self._stream is not None:
            try:
                if self._stream.is_active():
                    self._stream.stop_stream()
                self._stream.close()
            except Exception as e:
                logger.warning("关闭音频流时出错: %s", e)
            self._stream = None

        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception as e:
                logger.warning("终止 PyAudio 时出错: %s", e)
            self._pa = None

        self._is_open = False
        logger.info("音频设备已关闭")

    def read_frame(self) -> list[int]:
        """
        读取一帧音频数据，返回 int16 列表。

        用于 Porcupine 唤醒词检测（需要 int16 数组）。

        Returns
        -------
        list[int]
            帧长度为 chunk_size 的 int16 音频样本列表
        """
        if not self._is_open or self._stream is None:
            raise RuntimeError("音频流未打开，请先调用 open()")

        try:
            raw_data = self._stream.read(self.chunk_size, exception_on_overflow=False)
            return list(struct.unpack(f"{self.chunk_size}h", raw_data))
        except IOError as e:
            logger.warning("读取音频帧时发生 IO 错误 (可能是溢出): %s", e)
            return [0] * self.chunk_size

    def read_raw(self, num_frames: Optional[int] = None) -> bytes:
        """
        读取原始 PCM 字节数据。

        用于 FunASR 语音识别（需要原始 PCM bytes）。

        Parameters
        ----------
        num_frames : int | None
            读取的帧数，默认为 chunk_size

        Returns
        -------
        bytes
            原始 PCM 音频数据 (16-bit little-endian)
        """
        if not self._is_open or self._stream is None:
            raise RuntimeError("音频流未打开，请先调用 open()")

        frames = num_frames or self.chunk_size
        try:
            return self._stream.read(frames, exception_on_overflow=False)
        except IOError as e:
            logger.warning("读取原始音频数据时发生 IO 错误: %s", e)
            return b"\x00" * (frames * 2)  # 16-bit = 2 bytes per sample

    @property
    def is_open(self) -> bool:
        """音频流是否已打开。"""
        return self._is_open

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
