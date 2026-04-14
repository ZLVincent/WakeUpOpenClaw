"""
配置值解析器

递归遍历配置字典，将 ${ENV_VAR_NAME} 语法替换为对应的环境变量值。

用法:
    config = load_config("config.yaml")
    config = resolve_config(config)

支持:
    - 整个值是变量引用: "password: ${MYSQL_PASSWORD}" → 取环境变量值
    - 嵌入在字符串中: "jdbc:mysql://${DB_HOST}:3306" → 部分替换
    - 环境变量未设置时: 整个值引用返回原始字符串不替换，嵌入引用替换为空串并警告
    - 非字符串值（int, bool, list, dict）递归处理
"""

import os
import re
from typing import Any

from utils.logger import get_logger

logger = get_logger("config")

# 匹配 ${VAR_NAME}，变量名由字母、数字、下划线组成
_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def resolve_config(config: dict) -> dict:
    """
    递归解析配置中的 ${ENV_VAR} 引用。

    Parameters
    ----------
    config : dict
        原始配置字典

    Returns
    -------
    dict
        解析后的配置字典（新对象，不修改原始）
    """
    resolved = _resolve_value(config)
    return resolved


def _resolve_value(value: Any) -> Any:
    """递归解析单个配置值。"""
    if isinstance(value, str):
        return _resolve_string(value)
    elif isinstance(value, dict):
        return {k: _resolve_value(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_value(item) for item in value]
    return value


def _resolve_string(value: str) -> Any:
    """
    解析字符串中的环境变量引用。

    - 整个值就是 "${VAR}": 返回环境变量值，未设置则保留原始字符串
    - 含有 "${VAR}" 嵌入: 替换匹配部分，未设置的变量替换为空串
    """
    # 快速跳过不含变量引用的字符串
    if "${" not in value:
        return value

    # 整个值就是一个变量引用: "${VAR_NAME}"
    match = re.fullmatch(r"\$\{(\w+)\}", value.strip())
    if match:
        var_name = match.group(1)
        env_val = os.environ.get(var_name)
        if env_val is not None:
            logger.debug("配置变量解析: ${%s} -> (已设置)", var_name)
            return env_val
        else:
            logger.warning(
                "环境变量 %s 未设置，配置值保留为 '%s'",
                var_name, value,
            )
            return value

    # 嵌入在字符串中: "prefix_${VAR}_suffix"
    def replacer(m: re.Match) -> str:
        var_name = m.group(1)
        env_val = os.environ.get(var_name)
        if env_val is not None:
            logger.debug("配置变量解析: ${%s} -> (已设置)", var_name)
            return env_val
        else:
            logger.warning("环境变量 %s 未设置，替换为空字符串", var_name)
            return ""

    return _ENV_PATTERN.sub(replacer, value)
