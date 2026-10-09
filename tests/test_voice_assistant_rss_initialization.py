"""VoiceAssistant 初始化 RSS 默认订阅的静态回归测试。"""

import ast
from pathlib import Path


def _initialize_node():
    main_path = Path(__file__).resolve().parents[1] / "main.py"
    module = ast.parse(main_path.read_text(encoding="utf-8"))
    voice_assistant = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "VoiceAssistant"
    )
    return next(
        node for node in voice_assistant.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "initialize"
    )


def test_initialize_reads_rss_enabled_from_self_config_not_init_local():
    """RSS seed 开关必须在 initialize 内从 self.config 读取。"""
    initialize = _initialize_node()

    bare_skills_cfg_reads = [
        node for node in ast.walk(initialize)
        if isinstance(node, ast.Name) and node.id == "skills_cfg" and isinstance(node.ctx, ast.Load)
    ]
    assert not bare_skills_cfg_reads

    self_config_reads = [
        node for node in ast.walk(initialize)
        if isinstance(node, ast.Attribute)
        and node.attr == "config"
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ]
    constants = {node.value for node in ast.walk(initialize) if isinstance(node, ast.Constant)}
    assert self_config_reads
    assert {"skills", "rss", "enabled"}.issubset(constants)
