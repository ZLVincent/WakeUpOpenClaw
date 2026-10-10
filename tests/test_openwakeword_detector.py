"""openWakeWord 唤醒词适配器的行为契约测试。"""

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _stub_pyaudio(monkeypatch):
    """避免测试仅因本机没有音频驱动库而无法导入 wake_up。"""
    monkeypatch.setitem(sys.modules, "pyaudio", types.ModuleType("pyaudio"))


def _openwakeword_module():
    return importlib.import_module("wake_up.openwakeword_detector")


def _install_fake_openwakeword(monkeypatch, model):
    package = types.ModuleType("openwakeword")
    model_module = types.ModuleType("openwakeword.model")
    model_module.Model = model
    monkeypatch.setitem(sys.modules, "openwakeword", package)
    monkeypatch.setitem(sys.modules, "openwakeword.model", model_module)


def _mark_model_path_existing(monkeypatch):
    """让测试不依赖仓库外的真实自定义模型文件。"""
    monkeypatch.setattr(Path, "exists", lambda _path: True)


def test_factory_creates_openwakeword_detector_from_named_configuration():
    """engine=openwakeword 必须创建尚未初始化的适配器。"""
    factory = importlib.import_module("wake_up.factory")
    detector_module = _openwakeword_module()

    detector = factory.create_detector(
        {
            "engine": "openwakeword",
            "openwakeword": {
                "model_path": "./models/hey_vincent.onnx",
                "model_name": "hey_vincent",
                "threshold": 0.63,
            },
        }
    )

    assert isinstance(detector, detector_module.OpenWakeWordDetector)
    assert detector.model_path == "./models/hey_vincent.onnx"
    assert detector.model_name == "hey_vincent"
    assert detector.threshold == 0.63


def test_openwakeword_dependency_is_loaded_only_during_initialize(monkeypatch):
    """未调用 initialize 时，不应要求安装 openwakeword。"""
    monkeypatch.delitem(sys.modules, "openwakeword", raising=False)
    monkeypatch.delitem(sys.modules, "openwakeword.model", raising=False)
    detector_module = _openwakeword_module()

    detector = detector_module.OpenWakeWordDetector(
        model_path="./models/hey_vincent.onnx",
        model_name="hey_vincent",
    )

    assert detector._model is None
    assert "openwakeword" not in sys.modules


def test_initialize_imports_model_lazily_and_exposes_openwakeword_audio_contract(monkeypatch):
    """初始化时加载模型，且固定使用 openWakeWord 的 16 kHz / 1280 样本帧。"""
    model = Mock()
    _install_fake_openwakeword(monkeypatch, model)
    _mark_model_path_existing(monkeypatch)
    detector_module = _openwakeword_module()
    detector = detector_module.OpenWakeWordDetector(
        model_path="./models/hey_vincent.onnx",
        model_name="hey_vincent",
    )

    detector.initialize()

    model.assert_called_once_with(
        wakeword_models=["./models/hey_vincent.onnx"],
        inference_framework="onnx",
    )
    assert detector.sample_rate == 16000
    assert detector.frame_length == 1280


def test_access_before_initialize_raises_runtime_error():
    """与现有检测器一致，初始化前不能读取引擎属性或处理音频。"""
    detector_module = _openwakeword_module()
    detector = detector_module.OpenWakeWordDetector(
        model_path="./models/hey_vincent.onnx",
        model_name="hey_vincent",
    )

    with pytest.raises(RuntimeError):
        _ = detector.sample_rate
    with pytest.raises(RuntimeError):
        _ = detector.frame_length
    with pytest.raises(RuntimeError):
        detector.process_frame([0] * 1280)


def test_process_frame_passes_int16_samples_and_compares_named_model_score(monkeypatch):
    """模型只按所选 model_name 的分数与阈值判定唤醒。"""
    model = Mock()
    instance = model.return_value
    instance.predict.return_value = {"hey_vincent": 0.75, "other": 0.99}
    _install_fake_openwakeword(monkeypatch, model)
    _mark_model_path_existing(monkeypatch)
    detector_module = _openwakeword_module()
    detector = detector_module.OpenWakeWordDetector(
        model_path="./models/hey_vincent.onnx",
        model_name="hey_vincent",
        threshold=0.75,
    )
    detector.initialize()

    assert detector.process_frame([-32768, 0, 32767] + [1] * 1277) is True

    passed_frame = instance.predict.call_args.args[0]
    assert isinstance(passed_frame, np.ndarray)
    assert passed_frame.dtype == np.int16
    assert passed_frame.tolist()[:3] == [-32768, 0, 32767]


def test_score_below_threshold_unknown_model_and_empty_frame_do_not_wake(monkeypatch):
    """不足阈值、模型未返回选定名称或空帧都必须安全地视为未唤醒。"""
    model = Mock()
    instance = model.return_value
    _install_fake_openwakeword(monkeypatch, model)
    _mark_model_path_existing(monkeypatch)
    detector_module = _openwakeword_module()
    detector = detector_module.OpenWakeWordDetector(
        model_path="./models/hey_vincent.onnx",
        model_name="hey_vincent",
        threshold=0.75,
    )
    detector.initialize()

    instance.predict.return_value = {"hey_vincent": 0.74}
    assert detector.process_frame([0] * 1280) is False

    instance.predict.return_value = {"another_model": 1.0}
    assert detector.process_frame([0] * 1280) is False

    instance.predict.reset_mock()
    assert detector.process_frame([]) is False
    instance.predict.assert_not_called()


def test_initialize_respects_explicit_onnx_inference_framework(monkeypatch):
    """显式指定后端时，必须原样传给 openWakeWord。"""
    model = Mock()
    _install_fake_openwakeword(monkeypatch, model)
    _mark_model_path_existing(monkeypatch)
    detector_module = _openwakeword_module()
    detector = detector_module.OpenWakeWordDetector(
        model_path="./models/hey_vincent.tflite",
        model_name="hey_vincent",
        inference_framework="onnx",
    )

    detector.initialize()

    model.assert_called_once_with(
        wakeword_models=["./models/hey_vincent.tflite"],
        inference_framework="onnx",
    )


def test_initialize_infers_tflite_inference_framework_from_model_path(monkeypatch):
    """未显式指定后端时，.tflite 模型应选择 TFLite 推理。"""
    model = Mock()
    _install_fake_openwakeword(monkeypatch, model)
    _mark_model_path_existing(monkeypatch)
    detector_module = _openwakeword_module()
    detector = detector_module.OpenWakeWordDetector(
        model_path="./models/hey_vincent.tflite",
        model_name="hey_vincent",
    )

    detector.initialize()

    model.assert_called_once_with(
        wakeword_models=["./models/hey_vincent.tflite"],
        inference_framework="tflite",
    )


def test_initialize_raises_file_not_found_for_missing_model_path(monkeypatch):
    """不存在的自定义模型必须在加载依赖前给出明确错误。"""
    model = Mock()
    _install_fake_openwakeword(monkeypatch, model)
    monkeypatch.setattr(Path, "exists", lambda _path: False)
    detector_module = _openwakeword_module()
    detector = detector_module.OpenWakeWordDetector(
        model_path="./models/missing.onnx",
        model_name="hey_vincent",
    )

    with pytest.raises(FileNotFoundError, match="missing.onnx"):
        detector.initialize()

    model.assert_not_called()
