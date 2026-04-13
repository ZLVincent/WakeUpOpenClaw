"""
WakeUpOpenClaw — 语音唤醒 AI 助手主程序

流程:
  1. 持续监听麦克风，等待唤醒词
  2. 检测到唤醒词后，播放提示音
  3. 进入多轮对话循环:
     a. 录音 + FunASR 实时识别
     b. 将识别结果发送给 OpenClaw Agent
     c. 用 edge-tts 合成回复语音并播放
     d. 等待下一轮输入（静默超时则退出）
  4. 超时退出后回到唤醒词监听

状态机:
  IDLE -> LISTENING -> THINKING -> SPEAKING -> LISTENING (循环)
                                           -> IDLE (超时退出)
"""

import asyncio
import enum
import os
import signal
import struct
import sys
import time
from typing import Optional

import yaml

from agent.openclaw_client import OpenClawClient
from asr.funasr_client import FunASRClient
from audio.recorder import AudioRecorder
from tts.edge_tts_engine import EdgeTTSEngine
from utils.logger import get_logger, setup_logging
from wake_up.factory import create_detector

logger = get_logger("main")


# ---------------------------------------------------------------------------
# 状态机定义
# ---------------------------------------------------------------------------

class State(enum.Enum):
    """助手运行状态。"""
    IDLE = "idle"            # 等待唤醒词
    LISTENING = "listening"  # 正在录音识别
    THINKING = "thinking"    # 等待 AI 回复
    SPEAKING = "speaking"    # 播放语音回复
    SHUTDOWN = "shutdown"    # 关闭中


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

def load_config(config_path: str = "config.yaml") -> dict:
    """加载 YAML 配置文件。"""
    if not os.path.exists(config_path):
        print(f"[ERROR] 配置文件不存在: {config_path}")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    return config


# ---------------------------------------------------------------------------
# 音频流生成器 (用于 FunASR)
# ---------------------------------------------------------------------------

class AudioStreamGenerator:
    """
    异步音频流生成器。

    从麦克风持续读取音频数据，通过 VAD (静默检测) 判断说话结束。
    使用简单的能量阈值进行端点检测。
    """

    def __init__(
        self,
        recorder: AudioRecorder,
        silence_timeout: float = 3.0,
        energy_threshold: int = 300,
    ):
        self.recorder = recorder
        self.silence_timeout = silence_timeout
        self.energy_threshold = energy_threshold
        self._stop = False

    def stop(self):
        self._stop = True

    async def __aiter__(self):
        """异步迭代器，yield PCM 音频块。"""
        silence_start: Optional[float] = None
        has_speech = False

        logger.debug(
            "音频流生成器启动 (静默超时: %.1fs, 能量阈值: %d)",
            self.silence_timeout, self.energy_threshold,
        )

        while not self._stop:
            # 在事件循环中异步读取（避免阻塞）
            raw_data = await asyncio.get_running_loop().run_in_executor(
                None, self.recorder.read_raw,
            )

            if not raw_data:
                continue

            # 计算音频帧能量（简单 RMS）
            samples = struct.unpack(f"{len(raw_data) // 2}h", raw_data)
            energy = (sum(s * s for s in samples) / len(samples)) ** 0.5

            if energy > self.energy_threshold:
                # 检测到语音活动
                has_speech = True
                silence_start = None
            else:
                # 静默
                if has_speech and silence_start is None:
                    silence_start = time.time()
                    logger.debug("检测到静默开始")

            # 无论有无语音都发送音频数据（让 FunASR 服务端做 VAD）
            yield raw_data

            # 检查静默超时
            if has_speech and silence_start is not None:
                silence_duration = time.time() - silence_start
                if silence_duration >= self.silence_timeout:
                    logger.info(
                        "静默持续 %.1fs，判定说话结束",
                        silence_duration,
                    )
                    break

            # 短暂让出控制权
            await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# 主助手类
# ---------------------------------------------------------------------------

class VoiceAssistant:
    """
    语音助手主控制器。

    整合唤醒词检测、语音识别、AI Agent、语音合成。
    """

    def __init__(self, config: dict):
        self.config = config
        self._state = State.IDLE
        self._running = False
        self._conversation_round = 0

        # ---- 初始化各组件 ----

        # 音频录音器
        audio_cfg = config.get("audio", {})
        self.recorder = AudioRecorder(
            sample_rate=audio_cfg.get("sample_rate", 16000),
            channels=audio_cfg.get("channels", 1),
            chunk_size=audio_cfg.get("chunk_size", 512),
            input_device_index=audio_cfg.get("input_device_index"),
        )

        # 唤醒词检测器 (通过工厂函数根据 engine 配置创建)
        wake_cfg = config.get("wake_up", {})
        self.detector = create_detector(wake_cfg)

        # FunASR 客户端
        asr_cfg = config.get("asr", {})
        self.asr_client = FunASRClient(
            server_url=asr_cfg.get("server_url", "ws://localhost:10095"),
            mode=asr_cfg.get("mode", "offline"),
            hotwords=asr_cfg.get("hotwords", ""),
            use_itn=asr_cfg.get("use_itn", True),
            ssl_enabled=asr_cfg.get("ssl_enabled", False),
        )

        # OpenClaw Agent 客户端
        agent_cfg = config.get("agent", {})
        self.agent_client = OpenClawClient(
            method=agent_cfg.get("method", "cli"),
            cli_path=agent_cfg.get("cli_path", "openclaw"),
            session_id=agent_cfg.get("session_id", "voice-assistant"),
            thinking=agent_cfg.get("thinking", "medium"),
            timeout=agent_cfg.get("timeout", 120),
            local=agent_cfg.get("local", False),
            gateway_url=agent_cfg.get("gateway_url", "ws://127.0.0.1:18789"),
            system_prompt=agent_cfg.get("system_prompt", ""),
        )

        # TTS 引擎
        tts_cfg = config.get("tts", {})
        self.tts_engine = EdgeTTSEngine(
            voice=tts_cfg.get("voice", "zh-CN-XiaoxiaoNeural"),
            rate=tts_cfg.get("rate", "+0%"),
            volume=tts_cfg.get("volume", "+0%"),
            player=tts_cfg.get("player", "mpv"),
            player_args=tts_cfg.get("player_args", ["--no-terminal", "--really-quiet"]),
            proxy=tts_cfg.get("proxy"),
        )

        # 对话配置
        conv_cfg = config.get("conversation", {})
        self.conversation_mode = conv_cfg.get("mode", "single")  # "single" or "multi"
        self.silence_timeout = conv_cfg.get("silence_timeout", 15)
        self.max_rounds = conv_cfg.get("max_rounds", 20)
        self.prompt_sound = conv_cfg.get("prompt_sound", True)
        self.prompt_text = conv_cfg.get("prompt_text", "我在")
        self.sound_wake = conv_cfg.get("sound_wake", "./static/beep_hi.wav")
        self.sound_done = conv_cfg.get("sound_done", "./static/beep_lo.wav")
        self.vad_silence_timeout = conv_cfg.get("vad_silence_timeout", 1.5)
        self.vad_energy_threshold = conv_cfg.get("vad_energy_threshold", 500)
        self.continue_wait_timeout = conv_cfg.get("continue_wait_timeout", 5.0)

    def _set_state(self, new_state: State) -> None:
        """切换状态并记录日志。"""
        old_state = self._state
        self._state = new_state
        logger.info("状态切换: %s -> %s", old_state.value, new_state.value)

    async def _play_sound(self, sound_path: str) -> None:
        """
        播放提示音文件。

        Parameters
        ----------
        sound_path : str
            音频文件路径 (.wav / .mp3 等)
        """
        if not sound_path:
            return
        if not os.path.exists(sound_path):
            logger.debug("提示音文件不存在: %s", sound_path)
            return
        logger.debug("播放提示音: %s", sound_path)
        await self.tts_engine.play(sound_path)

    async def initialize(self) -> bool:
        """
        初始化所有组件，执行可用性检查。

        Returns
        -------
        bool
            True 表示所有组件初始化成功
        """
        logger.info("=" * 60)
        logger.info("WakeUpOpenClaw 语音助手启动中...")
        logger.info("=" * 60)

        # 1. 检查 OpenClaw
        logger.info("[1/4] 检查 OpenClaw...")
        if not await self.agent_client.check_available():
            logger.critical("OpenClaw 不可用，请检查安装")
            return False

        # 2. 检查 FunASR
        logger.info("[2/4] 检查 FunASR 服务...")
        if not await self.asr_client.check_connection():
            logger.critical("FunASR 服务不可用，请检查 Docker 容器是否已启动")
            return False

        # 3. 检查 TTS
        logger.info("[3/4] 检查 TTS 引擎...")
        if not await self.tts_engine.check_available():
            logger.warning("TTS 引擎检查失败，语音播放可能异常")

        # 4. 初始化唤醒词检测
        logger.info("[4/4] 初始化唤醒词检测...")
        try:
            self.detector.initialize()
        except Exception as e:
            logger.critical("唤醒词检测初始化失败: %s", e)
            return False

        # 打开麦克风 (使用唤醒词引擎要求的帧大小)
        self.recorder.chunk_size = self.detector.frame_length
        self.recorder.open()

        logger.info("=" * 60)
        logger.info("所有组件初始化完成，助手已就绪!")
        logger.info("=" * 60)
        return True

    async def run(self) -> None:
        """运行主循环。"""
        self._running = True

        while self._running:
            try:
                if self._state == State.IDLE:
                    await self._handle_idle()
                elif self._state == State.SHUTDOWN:
                    break
            except KeyboardInterrupt:
                logger.info("收到键盘中断，正在退出...")
                break
            except Exception as e:
                logger.error("主循环异常: %s", e, exc_info=True)
                logger.info("3 秒后重试...")
                await asyncio.sleep(3)
                self._set_state(State.IDLE)

    async def _handle_idle(self) -> None:
        """
        IDLE 状态：持续监听唤醒词。
        检测到唤醒词后进入多轮对话。
        """
        logger.info("等待唤醒词... (说出唤醒词来激活)")

        # 在线程中运行阻塞的唤醒词监听
        detected = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: self._blocking_wake_listen(),
        )

        if detected and self._running:
            # 播放唤醒提示音
            if self.prompt_sound:
                if self.sound_wake and os.path.exists(self.sound_wake):
                    await self._play_sound(self.sound_wake)
                else:
                    # 回退: 用 TTS 朗读提示语
                    self._set_state(State.SPEAKING)
                    await self.tts_engine.speak(self.prompt_text)

            # 进入多轮对话
            await self._conversation_loop()

    def _blocking_wake_listen(self) -> bool:
        """阻塞式唤醒词监听（在线程中运行）。"""
        try:
            self.detector.listen(self.recorder)
            return True
        except Exception as e:
            logger.error("唤醒词监听出错: %s", e)
            return False

    async def _conversation_loop(self) -> None:
        """
        多轮对话循环。

        流程: LISTENING -> THINKING -> SPEAKING -> (等待用户继续) -> LISTENING -> ...
        超时或达到最大轮次时退出回到 IDLE。
        """
        self._conversation_round = 0

        while self._running and self._conversation_round < self.max_rounds:
            self._conversation_round += 1
            logger.info(
                "--- 对话轮次 %d/%d ---",
                self._conversation_round, self.max_rounds,
            )

            # ---- LISTENING: 录音 + 语音识别 ----
            self._set_state(State.LISTENING)
            recognized_text = await self._listen_and_recognize()

            if not recognized_text:
                logger.info("未识别到有效语音内容，退出对话")
                break

            # 播放录音结束提示音
            if self.prompt_sound:
                await self._play_sound(self.sound_done)

            # ---- THINKING: 调用 OpenClaw ----
            self._set_state(State.THINKING)
            reply = await self.agent_client.send_message(recognized_text)

            if not reply:
                logger.warning("OpenClaw 无回复或超时，退出对话")
                # 播放低音提示用户本轮结束
                if self.prompt_sound:
                    await self._play_sound(self.sound_done)
                break

            # ---- SPEAKING: TTS 合成并播放 ----
            self._set_state(State.SPEAKING)
            await self.tts_engine.speak(reply)

            # 单轮模式：AI 回复后直接退出
            if self.conversation_mode == "single":
                logger.info("单轮对话模式，回复完毕，退出对话")
                break

            # 多轮模式：等待用户是否继续说话
            if not await self._wait_for_speech(self.continue_wait_timeout):
                logger.info("用户无继续说话意图，退出对话")
                break

            # 用户有说话意图，播放提示音进入下一轮
            if self.prompt_sound:
                await self._play_sound(self.sound_wake)

        # 对话结束
        logger.info(
            "多轮对话结束 (共 %d 轮)",
            self._conversation_round,
        )
        self._set_state(State.IDLE)

    async def _wait_for_speech(self, timeout: float = 5.0) -> bool:
        """
        短暂监听麦克风，判断用户是否有继续说话的意图。

        在 AI 回复播完后调用。如果在 timeout 内检测到语音活动，
        返回 True 表示用户想继续对话；否则返回 False 退出多轮对话。

        Parameters
        ----------
        timeout : float
            等待用户开口的最大时间（秒），默认 5 秒

        Returns
        -------
        bool
            True 表示检测到语音活动，用户想继续说话
        """
        logger.info("等待用户继续说话... (%.1fs 内无语音将退出对话)", timeout)
        start = time.time()

        while time.time() - start < timeout:
            raw_data = await asyncio.get_running_loop().run_in_executor(
                None, self.recorder.read_raw,
            )
            if not raw_data:
                continue

            samples = struct.unpack(f"{len(raw_data) // 2}h", raw_data)
            energy = (sum(s * s for s in samples) / len(samples)) ** 0.5

            if energy > self.vad_energy_threshold:
                logger.info("检测到语音活动，继续多轮对话")
                return True

        return False

    async def _listen_and_recognize(self) -> str:
        """
        录音并通过 FunASR 识别。

        Returns
        -------
        str
            识别结果文本，空字符串表示无结果
        """
        logger.info("开始录音，请说话...")

        # 创建音频流生成器，使用配置中的 VAD 参数
        audio_stream = AudioStreamGenerator(
            recorder=self.recorder,
            silence_timeout=self.vad_silence_timeout,
            energy_threshold=self.vad_energy_threshold,
        )

        # 使用带总超时的包装器
        recognized_text = ""
        try:
            recognized_text = await asyncio.wait_for(
                self.asr_client.recognize(
                    audio_generator=audio_stream,
                    sample_rate=self.recorder.sample_rate,
                ),
                timeout=self.silence_timeout,
            )
        except asyncio.TimeoutError:
            logger.info("录音超时 (%ds)，无语音输入", self.silence_timeout)
            audio_stream.stop()

        return recognized_text.strip() if recognized_text else ""

    def shutdown(self) -> None:
        """优雅关闭。"""
        if self._state == State.SHUTDOWN:
            return  # 避免重复关闭

        logger.info("正在关闭助手...")
        self._running = False
        self._set_state(State.SHUTDOWN)
        self.detector.stop()

        # 释放资源
        self.recorder.close()
        self.detector.cleanup()
        self.tts_engine.cleanup()

        logger.info("助手已关闭")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    """程序入口。"""
    # 确定配置文件路径
    config_path = "config.yaml"
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    # 加载配置
    config = load_config(config_path)

    # 初始化日志
    log_cfg = config.get("logging", {})
    setup_logging(log_cfg)

    logger.info("配置文件已加载: %s", config_path)

    # 创建助手实例
    assistant = VoiceAssistant(config)

    # 注册信号处理
    def signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("收到信号 %s，正在关闭...", sig_name)
        assistant.shutdown()

    signal.signal(signal.SIGINT, signal_handler)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, signal_handler)

    # 运行异步主循环
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        # 初始化
        init_ok = loop.run_until_complete(assistant.initialize())
        if not init_ok:
            logger.critical("初始化失败，程序退出")
            sys.exit(1)

        # 运行主循环
        loop.run_until_complete(assistant.run())

    except KeyboardInterrupt:
        logger.info("收到键盘中断")
    finally:
        assistant.shutdown()
        # 清理异步任务
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
        loop.close()
        logger.info("程序退出完成")


if __name__ == "__main__":
    main()
