"""
日志工具模块

提供统一的日志配置和彩色终端输出。
各模块通过 get_logger(name) 获取独立的 logger 实例。
"""

import logging
import logging.handlers
import os
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# 彩色终端 Formatter
# ---------------------------------------------------------------------------

class ColorFormatter(logging.Formatter):
    """为终端日志输出添加 ANSI 颜色。"""

    # ANSI 颜色码
    COLORS = {
        logging.DEBUG:    "\033[36m",   # 青色
        logging.INFO:     "\033[32m",   # 绿色
        logging.WARNING:  "\033[33m",   # 黄色
        logging.ERROR:    "\033[31m",   # 红色
        logging.CRITICAL: "\033[1;31m", # 粗体红色
    }
    RESET = "\033[0m"

    # 模块名颜色 (循环使用)
    MODULE_COLORS = [
        "\033[34m",   # 蓝色
        "\033[35m",   # 紫色
        "\033[36m",   # 青色
        "\033[33m",   # 黄色
        "\033[32m",   # 绿色
        "\033[94m",   # 亮蓝
        "\033[95m",   # 亮紫
    ]
    _module_color_map: dict[str, str] = {}
    _color_index = 0

    @classmethod
    def _get_module_color(cls, name: str) -> str:
        if name not in cls._module_color_map:
            cls._module_color_map[name] = cls.MODULE_COLORS[
                cls._color_index % len(cls.MODULE_COLORS)
            ]
            cls._color_index += 1
        return cls._module_color_map[name]

    def format(self, record: logging.LogRecord) -> str:
        level_color = self.COLORS.get(record.levelno, self.RESET)
        module_color = self._get_module_color(record.name)

        # 格式化级别名，固定宽度
        level_name = record.levelname.ljust(8)

        # 格式化模块名，固定宽度
        module_name = record.name.ljust(10)

        # 时间戳
        timestamp = self.formatTime(record, self.datefmt)

        # 组装带颜色的日志行
        msg = record.getMessage()
        line = (
            f"\033[90m{timestamp}\033[0m "
            f"{level_color}[{level_name}]\033[0m "
            f"{module_color}[{module_name}]\033[0m "
            f"{msg}"
        )

        # 附加异常信息
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            line += f"\n{level_color}{record.exc_text}{self.RESET}"

        return line


class PlainFormatter(logging.Formatter):
    """不带颜色的日志格式，用于文件输出。"""

    def format(self, record: logging.LogRecord) -> str:
        level_name = record.levelname.ljust(8)
        module_name = record.name.ljust(10)
        timestamp = self.formatTime(record, self.datefmt)
        msg = record.getMessage()
        line = f"{timestamp} [{level_name}] [{module_name}] {msg}"

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            line += f"\n{record.exc_text}"

        return line


# ---------------------------------------------------------------------------
# 解析文件大小字符串
# ---------------------------------------------------------------------------

def _parse_file_size(size_str: str) -> int:
    """将 '10MB', '500KB' 等字符串转换为字节数。"""
    size_str = size_str.strip().upper()
    multipliers = {
        "KB": 1024,
        "MB": 1024 * 1024,
        "GB": 1024 * 1024 * 1024,
    }
    for suffix, multiplier in multipliers.items():
        if size_str.endswith(suffix):
            return int(float(size_str[:-len(suffix)]) * multiplier)
    # 纯数字，当做字节
    return int(size_str)


# ---------------------------------------------------------------------------
# 全局初始化
# ---------------------------------------------------------------------------

_initialized = False


def setup_logging(config: dict) -> None:
    """
    根据配置字典初始化全局日志系统。

    Parameters
    ----------
    config : dict
        config.yaml 中 logging 部分的字典，例如:
        {
            "level": "INFO",
            "console": True,
            "console_color": True,
            "file": "logs/assistant.log",
            "max_file_size": "10MB",
            "backup_count": 5,
        }
    """
    global _initialized
    if _initialized:
        return

    level_str = config.get("level", "INFO").upper()
    level = getattr(logging, level_str, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    datefmt = "%Y-%m-%d %H:%M:%S"

    # ---- 终端 handler ----
    if config.get("console", True):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        if config.get("console_color", True) and _supports_color():
            formatter = ColorFormatter(datefmt=datefmt)
        else:
            formatter = PlainFormatter(datefmt=datefmt)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    # ---- 文件 handler ----
    log_file = config.get("file", "")
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        max_bytes = _parse_file_size(config.get("max_file_size", "10MB"))
        backup_count = config.get("backup_count", 5)

        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(PlainFormatter(datefmt=datefmt))
        root.addHandler(file_handler)

    _initialized = True

    # 降低第三方库日志级别，避免刷屏
    for noisy in ("websockets", "asyncio", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _supports_color() -> bool:
    """检测终端是否支持 ANSI 颜色。"""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if sys.platform == "win32":
        # Windows Terminal / WT 支持颜色
        return (
            os.environ.get("WT_SESSION") is not None
            or os.environ.get("TERM_PROGRAM") == "vscode"
            or "ANSICON" in os.environ
        )
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def get_logger(name: str) -> logging.Logger:
    """
    获取指定模块的 logger。

    Parameters
    ----------
    name : str
        模块名称，如 "wake_up", "asr", "agent", "tts", "audio", "main"

    Returns
    -------
    logging.Logger
    """
    return logging.getLogger(name)
