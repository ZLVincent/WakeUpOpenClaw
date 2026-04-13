"""
唤醒词检测器工厂模块

根据配置动态创建对应的唤醒词检测器实例。
使用延迟导入确保：用 Porcupine 时不需要安装 Snowboy，反之亦然。
"""

from utils.logger import get_logger
from wake_up.base import BaseWakeWordDetector

logger = get_logger("wake_up")

# 支持的引擎名称
SUPPORTED_ENGINES = ("porcupine", "snowboy")


def create_detector(config: dict) -> BaseWakeWordDetector:
    """
    根据配置创建唤醒词检测器。

    Parameters
    ----------
    config : dict
        config.yaml 中 wake_up 部分的完整字典，例如:
        {
            "engine": "snowboy",
            "porcupine": { "access_key": "...", ... },
            "snowboy": { "resource_path": "...", ... },
        }

    Returns
    -------
    BaseWakeWordDetector
        对应引擎的检测器实例（未初始化，需调用 initialize()）

    Raises
    ------
    ValueError
        不支持的引擎名称
    """
    engine = config.get("engine", "snowboy").lower().strip()
    logger.info("唤醒词引擎: %s", engine)

    if engine == "porcupine":
        return _create_porcupine(config.get("porcupine", {}))

    elif engine == "snowboy":
        return _create_snowboy(config.get("snowboy", {}))

    else:
        logger.critical(
            "不支持的唤醒词引擎: '%s' (可选: %s)",
            engine, ", ".join(SUPPORTED_ENGINES),
        )
        raise ValueError(
            f"不支持的唤醒词引擎: '{engine}'，"
            f"可选: {', '.join(SUPPORTED_ENGINES)}"
        )


def _create_porcupine(cfg: dict) -> BaseWakeWordDetector:
    """创建 Porcupine 检测器。"""
    from wake_up.porcupine_detector import PorcupineDetector

    return PorcupineDetector(
        access_key=cfg.get("access_key", ""),
        keyword_path=cfg.get("keyword_path", "./models/wakeword.ppn"),
        sensitivity=cfg.get("sensitivity", 0.5),
    )


def _create_snowboy(cfg: dict) -> BaseWakeWordDetector:
    """创建 Snowboy 检测器。"""
    from wake_up.snowboy_detector import SnowboyDetector

    return SnowboyDetector(
        resource_path=cfg.get("resource_path", "./snowboy/resources/common.res"),
        model_path=cfg.get("model_path", "./snowboy/resources/models/snowboy.umdl"),
        sensitivity=cfg.get("sensitivity", 0.5),
        audio_gain=cfg.get("audio_gain", 1.0),
        apply_frontend=cfg.get("apply_frontend", False),
        snowboy_lib_path=cfg.get("snowboy_lib_path", "./snowboy"),
    )
