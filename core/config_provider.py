"""
配置加载 — 从 YAML 文件加载配置。
"""

import os
import sys

import yaml


def load_config(config_path: str = "config.yaml") -> dict:
    """加载 YAML 配置文件。"""
    if not os.path.exists(config_path):
        print(f"[ERROR] 配置文件不存在: {config_path}")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    return config
