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
import datetime
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
from skills.router import SkillRouter
from storage.database import ChatDatabase
from tts.edge_tts_engine import EdgeTTSEngine
from utils.config_resolver import resolve_config
from utils.logger import get_logger, setup_logging
from wake_up.factory import create_detector
from web.server import WebServer

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

    def __init__(self, config: dict, config_path: str = "config.yaml"):
        self.config = config
        self.config_path = config_path
        self._state = State.IDLE
        self._running = False
        self._conversation_round = 0

        # 当前活跃对话信息
        self._current_conversation: Optional[dict] = None

        # ---- 初始化各组件 ----

        # 数据库
        db_cfg = config.get("database", {})
        self.db = ChatDatabase(
            host=db_cfg.get("host", "localhost"),
            port=db_cfg.get("port", 3306),
            user=db_cfg.get("user", "root"),
            password=db_cfg.get("password", ""),
            database=db_cfg.get("database", "wakeup_openclaw"),
            pool_size=db_cfg.get("pool_size", 5),
        )

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

        # Web 服务
        web_cfg = config.get("web", {})
        self.web_enabled = web_cfg.get("enabled", True)
        self.web_server = WebServer(
            agent_client=self.agent_client,
            tts_engine=self.tts_engine,
            host=web_cfg.get("host", "0.0.0.0"),
            port=web_cfg.get("port", 8084),
            tts_on_web=web_cfg.get("tts_on_web", False),
            config_path=self.config_path,
            database=self.db,
        ) if self.web_enabled else None

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
        self.max_history_rounds = conv_cfg.get("max_history_rounds", 30)
        self.barge_in = conv_cfg.get("barge_in", False)
        self.streaming_tts = conv_cfg.get("streaming_tts", False)

        # 免打扰配置
        dnd_cfg = conv_cfg.get("do_not_disturb", {})
        self.dnd_enabled = dnd_cfg.get("enabled", False)
        self.dnd_start = self._parse_time(dnd_cfg.get("start", "22:30"))
        self.dnd_end = self._parse_time(dnd_cfg.get("end", "07:30"))

        # 技能路由
        skills_cfg = config.get("skills", {})
        self.skill_router = SkillRouter(
            commands=skills_cfg.get("commands", []),
            enabled=skills_cfg.get("enabled", True),
        )

    def _set_state(self, new_state: State) -> None:
        """切换状态并记录日志。"""
        old_state = self._state
        self._state = new_state
        logger.info("状态切换: %s -> %s", old_state.value, new_state.value)
        # 广播状态到 Web 客户端
        if self.web_server and self.web_server._ws_clients:
            asyncio.ensure_future(
                self.web_server.broadcast_status(new_state.value)
            )

    def _is_dnd_active(self) -> bool:
        """
        检查当前是否在免打扰时段内。

        支持跨午夜时段（如 22:30 ~ 07:30）。
        """
        if not self.dnd_enabled:
            return False

        now = datetime.datetime.now().time()

        if self.dnd_start <= self.dnd_end:
            # 不跨午夜: 如 08:00 ~ 18:00
            return self.dnd_start <= now <= self.dnd_end
        else:
            # 跨午夜: 如 22:30 ~ 07:30
            return now >= self.dnd_start or now <= self.dnd_end

    @staticmethod
    def _parse_time(time_str: str) -> datetime.time:
        """将 'HH:MM' 字符串解析为 datetime.time 对象。"""
        try:
            parts = time_str.strip().split(":")
            return datetime.time(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            logger.warning("时间格式无效: '%s'，使用默认值 00:00", time_str)
            return datetime.time(0, 0)

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

        # 0. 初始化数据库
        logger.info("[0/4] 初始化数据库...")
        try:
            await self.db.initialize()
        except Exception as e:
            logger.warning("数据库初始化失败，对话历史将不会持久化: %s", e)

        # 加载或创建活跃对话
        try:
            self._current_conversation = await self.db.get_or_create_active_conversation("voice")
            # 将数据库中的 session-id 同步到 OpenClaw 客户端
            self.agent_client.session_id = self._current_conversation["session_id"]
            logger.info(
                "当前对话 #%d (session=%s, 轮次=%d)",
                self._current_conversation["id"],
                self._current_conversation["session_id"],
                self._current_conversation["round_count"],
            )
        except Exception as e:
            logger.warning("加载对话失败: %s", e)

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

        # 启动 Web 服务
        if self.web_server:
            self.web_server._assistant = self
            await self.web_server.start()

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
        免打扰时段内暂停语音唤醒（Web 端不受影响）。
        """
        # 免打扰检查
        if self._is_dnd_active():
            logger.info(
                "当前处于免打扰时段 (%s ~ %s)，语音唤醒已暂停",
                self.dnd_start.strftime("%H:%M"),
                self.dnd_end.strftime("%H:%M"),
            )
            if self.web_server and self.web_server._ws_clients:
                await self.web_server.broadcast_status("dnd")
            while self._running and self._is_dnd_active():
                await asyncio.sleep(60)
            if not self._running:
                return
            logger.info("免打扰时段结束，恢复语音唤醒")

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

            # ---- 技能匹配：本地快速响应 ----
            skill_result = await self.skill_router.match(recognized_text)
            if skill_result:
                logger.info("技能命中: action=%s", skill_result.action)
                # 保存到数据库
                await self._save_message("user", recognized_text, "voice")
                await self._save_message("assistant", skill_result.text, "voice")

                # 特殊动作：新建对话
                if skill_result.action == "new_conversation":
                    await self.start_new_conversation("voice")

                # TTS 播报技能回复
                if skill_result.text:
                    self._set_state(State.SPEAKING)
                    await self.tts_engine.speak(skill_result.text)
                continue

            # 保存用户消息到数据库
            await self._save_message("user", recognized_text, "voice")

            # ---- THINKING: 调用 OpenClaw ----
            self._set_state(State.THINKING)
            start_time = time.time()
            reply = await self.agent_client.send_message(recognized_text)
            duration_ms = int((time.time() - start_time) * 1000)

            if not reply:
                logger.warning("OpenClaw 无回复或超时，退出对话")
                if self.prompt_sound:
                    await self._play_sound(self.sound_done)
                break

            # 保存 AI 回复到数据库
            await self._save_message("assistant", reply, "voice", duration_ms)

            # 检查是否需要自动新建对话
            await self._check_auto_new_conversation()

            # ---- SPEAKING: TTS 合成并播放 ----
            self._set_state(State.SPEAKING)
            barged_in = await self._speak_with_barge_in(reply)

            if barged_in:
                # 唤醒词打断了播放，直接进入下一轮（已检测到唤醒词）
                logger.info("语音打断！跳过等待，直接进入下一轮")
                if self.prompt_sound:
                    await self._play_sound(self.sound_wake)
                continue

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

    async def _save_message(
        self, role: str, content: str, source: str, duration_ms: int = None,
    ) -> None:
        """保存消息到数据库。"""
        try:
            if self._current_conversation:
                conv_id = self._current_conversation["id"]
                await self.db.add_message(conv_id, role, content, source, duration_ms)

                # 更新对话标题（取第一条用户消息）
                if role == "user" and not self._current_conversation.get("title"):
                    title = content[:50]
                    await self.db.update_conversation_title(conv_id, title)
                    self._current_conversation["title"] = title

                # 增加轮次计数
                if role == "assistant":
                    count = await self.db.increment_round_count(conv_id)
                    self._current_conversation["round_count"] = count
        except Exception as e:
            logger.debug("保存消息到数据库失败: %s", e)

    async def _check_auto_new_conversation(self) -> None:
        """检查是否超过最大历史轮次，自动新建对话。"""
        try:
            if not self._current_conversation:
                return
            round_count = self._current_conversation.get("round_count", 0)
            if round_count >= self.max_history_rounds:
                logger.info(
                    "对话已达 %d 轮，自动开启新对话",
                    round_count,
                )
                self._current_conversation = await self.db.start_new_conversation("voice")
                self.agent_client.session_id = self._current_conversation["session_id"]
                # 语音提示
                await self.tts_engine.speak("当前对话已较长，已为您开启新对话")
        except Exception as e:
            logger.debug("自动新建对话失败: %s", e)

    async def start_new_conversation(self, source: str = "voice") -> dict:
        """
        手动开启新对话（供 Web 和语音技能调用）。

        Returns
        -------
        dict
            新对话信息
        """
        try:
            self._current_conversation = await self.db.start_new_conversation(source)
            self.agent_client.session_id = self._current_conversation["session_id"]
            return self._current_conversation
        except Exception as e:
            logger.error("开启新对话失败: %s", e)
            return {}

    async def _speak_with_barge_in(self, text: str) -> bool:
        """
        TTS 合成并播放，同时支持语音打断。

        如果 barge_in 配置为 True，在播放期间后台运行唤醒词检测。
        检测到唤醒词时立即中断播放。

        Parameters
        ----------
        text : str
            要朗读的文本

        Returns
        -------
        bool
            True 表示被唤醒词打断了
        """
        if not self.barge_in:
            # 不支持打断，直接播放（支持流式）
            if self.streaming_tts:
                await self.tts_engine.speak_streaming(text)
            else:
                await self.tts_engine.speak(text)
            return False

        # 先合成
        audio_file = await self.tts_engine.synthesize(text)
        if not audio_file:
            return False

        # 启动异步播放
        play_proc = await self.tts_engine.play_async(audio_file)
        if not play_proc:
            self.tts_engine._remove_file(audio_file)
            return False

        # 同时启动唤醒词检测
        barged = False
        try:
            wake_future = asyncio.get_running_loop().run_in_executor(
                None, lambda: self._blocking_wake_listen(),
            )
            play_future = asyncio.ensure_future(play_proc.wait())

            done, pending = await asyncio.wait(
                [wake_future, play_future],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if wake_future in done and wake_future.result():
                # 唤醒词检测到了，中断播放
                logger.info("检测到唤醒词，中断 TTS 播放")
                play_proc.kill()
                await play_proc.wait()
                barged = True
            else:
                # 播放自然结束
                logger.info("语音播放完成")

            # 取消未完成的任务
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        finally:
            self.tts_engine._remove_file(audio_file)

        return barged

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

        # 停止 Web 服务（异步操作，在事件循环中调度）
        if self.web_server:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.web_server.stop())
                else:
                    loop.run_until_complete(self.web_server.stop())
            except Exception as e:
                logger.debug("停止 Web 服务时出错: %s", e)

        # 释放资源
        self.recorder.close()
        self.detector.cleanup()
        self.tts_engine.cleanup()

        # 关闭数据库（异步）
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.db.close())
            else:
                loop.run_until_complete(self.db.close())
        except Exception as e:
            logger.debug("关闭数据库时出错: %s", e)

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

    # 初始化日志（在解析环境变量前，确保日志系统可用）
    log_cfg = config.get("logging", {})
    setup_logging(log_cfg)

    # 解析配置中的 ${ENV_VAR} 引用
    config = resolve_config(config)

    logger.info("配置文件已加载: %s", config_path)

    # 创建助手实例
    assistant = VoiceAssistant(config, config_path=config_path)

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
