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
import os
import signal
import struct
import sys
import time
from typing import Optional

from agent.openclaw_client import OpenClawClient
from asr.funasr_client import FunASRClient
from audio.recorder import AudioRecorder
from core.config_provider import load_config
from core.conversation_manager import ConversationManager
from core.dnd_controller import DNDController
from core.reminder_service import ReminderService
from core.state import State
from skills.music_player import MusicPlayer
from skills.router import SkillRouter
from skills.timer import TimerManager, format_duration
from storage.database import ChatDatabase
from tts.edge_tts_engine import EdgeTTSEngine
from utils.config_resolver import resolve_config
from utils.logger import get_logger, setup_logging
from wake_up.factory import create_detector
from web.server import WebServer

logger = get_logger("main")


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
        silence_start: Optional[float] = None
        has_speech = False

        logger.debug(
            "音频流生成器启动 (静默超时: %.1fs, 能量阈值: %d)",
            self.silence_timeout, self.energy_threshold,
        )

        while not self._stop:
            raw_data = await asyncio.get_running_loop().run_in_executor(
                None, self.recorder.read_raw,
            )

            if not raw_data:
                continue

            samples = struct.unpack(f"{len(raw_data) // 2}h", raw_data)
            energy = (sum(s * s for s in samples) / len(samples)) ** 0.5

            if energy > self.energy_threshold:
                has_speech = True
                silence_start = None
            else:
                if has_speech and silence_start is None:
                    silence_start = time.time()
                    logger.debug("检测到静默开始")

            yield raw_data

            if has_speech and silence_start is not None:
                silence_duration = time.time() - silence_start
                if silence_duration >= self.silence_timeout:
                    logger.info(
                        "静默持续 %.1fs，判定说话结束",
                        silence_duration,
                    )
                    break

            await asyncio.sleep(0)


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

        db_cfg = config.get("database", {})
        self.db = ChatDatabase(
            host=db_cfg.get("host", "localhost"),
            port=db_cfg.get("port", 3306),
            user=db_cfg.get("user", "root"),
            password=db_cfg.get("password", ""),
            database=db_cfg.get("database", "wakeup_openclaw"),
            pool_size=db_cfg.get("pool_size", 5),
        )

        audio_cfg = config.get("audio", {})
        self.recorder = AudioRecorder(
            sample_rate=audio_cfg.get("sample_rate", 16000),
            channels=audio_cfg.get("channels", 1),
            chunk_size=audio_cfg.get("chunk_size", 512),
            input_device_index=audio_cfg.get("input_device_index"),
        )

        wake_cfg = config.get("wake_up", {})
        self.detector = create_detector(wake_cfg)

        asr_cfg = config.get("asr", {})
        self.asr_client = FunASRClient(
            server_url=asr_cfg.get("server_url", "ws://localhost:10095"),
            mode=asr_cfg.get("mode", "offline"),
            hotwords=asr_cfg.get("hotwords", ""),
            use_itn=asr_cfg.get("use_itn", True),
            ssl_enabled=asr_cfg.get("ssl_enabled", False),
        )

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

        tts_cfg = config.get("tts", {})
        self.tts_engine = EdgeTTSEngine(
            voice=tts_cfg.get("voice", "zh-CN-XiaoxiaoNeural"),
            rate=tts_cfg.get("rate", "+0%"),
            volume=tts_cfg.get("volume", "+0%"),
            player=tts_cfg.get("player", "mpv"),
            player_args=tts_cfg.get("player_args", ["--no-terminal", "--really-quiet"]),
            proxy=tts_cfg.get("proxy"),
        )

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

        conv_cfg = config.get("conversation", {})
        self.conversation_mode = conv_cfg.get("mode", "single")
        self.silence_timeout = conv_cfg.get("silence_timeout", 15)
        self.max_rounds = conv_cfg.get("max_rounds", 20)
        self.prompt_sound = conv_cfg.get("prompt_sound", True)
        self.prompt_text = conv_cfg.get("prompt_text", "我在")
        self.sound_wake = conv_cfg.get("sound_wake", "./static/beep_hi.wav")
        self.sound_done = conv_cfg.get("sound_done", "./static/beep_lo.wav")
        self.vad_silence_timeout = conv_cfg.get("vad_silence_timeout", 1.5)
        self.vad_energy_threshold = conv_cfg.get("vad_energy_threshold", 500)
        self.continue_wait_timeout = conv_cfg.get("continue_wait_timeout", 5.0)
        self.barge_in = conv_cfg.get("barge_in", False)
        self.streaming_tts = conv_cfg.get("streaming_tts", False)

        self.dnd_controller = DNDController(
            conv_cfg.get("do_not_disturb", {}),
        )

        tts_cfg = config.get("tts", {})
        self.music_player = MusicPlayer(
            database=self.db,
            player=tts_cfg.get("player", "mpv"),
            player_args=tts_cfg.get("player_args", ["--no-terminal", "--really-quiet"]),
        )

        self.timer_manager = TimerManager(
            on_expire=self._on_timer_expire,
        )

        skills_cfg = config.get("skills", {})
        skills_groups = {k: v for k, v in skills_cfg.items() if k != "enabled" and isinstance(v, dict)}
        self.skill_router = SkillRouter(
            skills_config=skills_groups,
            enabled=skills_cfg.get("enabled", True),
            database=self.db,
            music_player=self.music_player,
            timer_manager=self.timer_manager,
            agent_client=self.agent_client,
        )

        self.conversation_manager = ConversationManager(
            db=self.db,
            max_history_rounds=conv_cfg.get("max_history_rounds", 30),
        )

        cal_cfg = config.get("calendar", {})
        self.reminder_service = ReminderService(
            db=self.db,
            tts_engine=self.tts_engine,
            cli_path=agent_cfg.get("cli_path", "openclaw"),
            remind_enabled=cal_cfg.get("remind_enabled", True),
            check_interval=cal_cfg.get("remind_check_interval", 60),
            wechat_cfg=cal_cfg.get("wechat_remind", {}),
            is_dnd_active_cb=self.dnd_controller.is_active,
        )

        if cal_cfg.get("wechat_remind", {}).get("enabled"):
            logger.info(
                "微信提醒已启用 (target=%s)",
                cal_cfg.get("wechat_remind", {}).get("target", "")[:20],
            )
        else:
            logger.info("微信提醒未启用")

    def _set_state(self, new_state: State) -> None:
        old_state = self._state
        self._state = new_state
        logger.info("状态切换: %s -> %s", old_state.value, new_state.value)
        if self.web_server and self.web_server._ws_clients:
            asyncio.ensure_future(
                self.web_server.broadcast_status(new_state.value)
            )

    async def _play_sound(self, sound_path: str) -> None:
        if not sound_path:
            return
        if not os.path.exists(sound_path):
            logger.debug("提示音文件不存在: %s", sound_path)
            return
        logger.debug("播放提示音: %s", sound_path)
        await self.tts_engine.play(sound_path)

    async def initialize(self) -> bool:
        logger.info("=" * 60)
        logger.info("WakeUpOpenClaw 语音助手启动中...")
        logger.info("=" * 60)

        logger.info("[0/4] 初始化数据库...")
        try:
            await self.db.initialize()
            logger.info("数据库初始化成功")
        except Exception as e:
            logger.warning("数据库初始化失败，对话历史和日程将不会持久化: %s", e, exc_info=True)

        await self.conversation_manager.load_or_create("voice")

        logger.info("[1/4] 检查 OpenClaw...")
        if not await self.agent_client.check_available():
            logger.critical("OpenClaw 不可用，请检查安装")
            return False

        logger.info("[2/4] 检查 FunASR 服务...")
        if not await self.asr_client.check_connection():
            logger.critical("FunASR 服务不可用，请检查 Docker 容器是否已启动")
            return False

        logger.info("[3/4] 检查 TTS 引擎...")
        if not await self.tts_engine.check_available():
            logger.warning("TTS 引擎检查失败，语音播放可能异常")

        logger.info("[4/4] 初始化唤醒词检测...")
        try:
            self.detector.initialize()
        except Exception as e:
            logger.critical("唤醒词检测初始化失败: %s", e)
            return False

        self.recorder.chunk_size = self.detector.frame_length
        self.recorder.open()

        if self.web_server:
            self.web_server._assistant = self
            await self.web_server.start()

        logger.info("=" * 60)
        logger.info("所有组件初始化完成，助手已就绪!")
        logger.info("=" * 60)
        return True

    async def run(self) -> None:
        self._running = True

        reminder_task = None
        if self.reminder_service.remind_enabled:
            reminder_task = asyncio.create_task(self.reminder_service.run_loop())
            logger.info("日程提醒后台任务已启动 (间隔: %ds)", self.reminder_service.check_interval)

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
        if self.dnd_controller.is_active():
            logger.info(
                "当前处于免打扰时段 (%s ~ %s)，语音唤醒已暂停",
                self.dnd_controller.format_start(),
                self.dnd_controller.format_end(),
            )
            if self.web_server and self.web_server._ws_clients:
                await self.web_server.broadcast_status("dnd")
            while self._running and self.dnd_controller.is_active():
                await asyncio.sleep(60)
            if not self._running:
                return
            logger.info("免打扰时段结束，恢复语音唤醒")

        logger.info("等待唤醒词... (说出唤醒词来激活)")

        detected = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: self._blocking_wake_listen(),
        )

        if detected and self._running:
            if self.dnd_controller.is_active():
                logger.info("检测到唤醒词，但当前处于免打扰时段，忽略")
                return

            if self.prompt_sound:
                if self.sound_wake and os.path.exists(self.sound_wake):
                    await self._play_sound(self.sound_wake)
                else:
                    self._set_state(State.SPEAKING)
                    await self.tts_engine.speak(self.prompt_text)

            await self._conversation_loop()

    def _blocking_wake_listen(self) -> bool:
        try:
            self.detector.listen(self.recorder)
            return True
        except Exception as e:
            logger.error("唤醒词监听出错: %s", e)
            return False

    async def _conversation_loop(self) -> None:
        self._conversation_round = 0

        while self._running and self._conversation_round < self.max_rounds:
            self._conversation_round += 1
            logger.info(
                "--- 对话轮次 %d/%d ---",
                self._conversation_round, self.max_rounds,
            )

            self._set_state(State.LISTENING)
            recognized_text = await self._listen_and_recognize()

            if not recognized_text:
                logger.info("未识别到有效语音内容，退出对话")
                break

            if self.prompt_sound:
                await self._play_sound(self.sound_done)

            skill_result = await self.skill_router.match(recognized_text)
            if skill_result:
                logger.info("技能命中: action=%s", skill_result.action)
                await self.conversation_manager.save_message("user", recognized_text, "voice")
                await self.conversation_manager.save_message("assistant", skill_result.text, "voice")

                if skill_result.action == "new_conversation":
                    await self.start_new_conversation("voice")

                if skill_result.text:
                    self._set_state(State.SPEAKING)
                    await self.tts_engine.speak(skill_result.text)

                if self.conversation_mode == "single":
                    break
                continue

            await self.conversation_manager.save_message("user", recognized_text, "voice")

            self._set_state(State.THINKING)
            start_time = time.time()
            current_sid = self.conversation_manager.get_session_id()
            reply = await self.agent_client.send_message(recognized_text, session_id=current_sid)
            duration_ms = int((time.time() - start_time) * 1000)

            if not reply:
                logger.warning("OpenClaw 无回复或超时，退出对话")
                if self.prompt_sound:
                    await self._play_sound(self.sound_done)
                break

            await self.conversation_manager.save_message("assistant", reply, "voice", duration_ms)

            auto_new = await self.conversation_manager.check_auto_new()
            if auto_new:
                await self.tts_engine.speak("当前对话已较长，已为您开启新对话")

            self._set_state(State.SPEAKING)
            barged_in = await self._speak_with_barge_in(reply)

            if barged_in:
                logger.info("语音打断！跳过等待，直接进入下一轮")
                if self.prompt_sound:
                    await self._play_sound(self.sound_wake)
                continue

            if self.conversation_mode == "single":
                logger.info("单轮对话模式，回复完毕，退出对话")
                break

            if not await self._wait_for_speech(self.continue_wait_timeout):
                logger.info("用户无继续说话意图，退出对话")
                break

            if self.prompt_sound:
                await self._play_sound(self.sound_wake)

        logger.info(
            "多轮对话结束 (共 %d 轮)",
            self._conversation_round,
        )
        self._set_state(State.IDLE)

    async def start_new_conversation(self, source: str = "voice") -> dict:
        return await self.conversation_manager.start_new(source)

    async def _speak_with_barge_in(self, text: str) -> bool:
        if not self.barge_in:
            if self.streaming_tts:
                await self.tts_engine.speak_streaming(text)
            else:
                await self.tts_engine.speak(text)
            return False

        audio_file = await self.tts_engine.synthesize(text)
        if not audio_file:
            return False

        play_proc = await self.tts_engine.play_async(audio_file)
        if not play_proc:
            self.tts_engine._remove_file(audio_file)
            return False

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
                logger.info("检测到唤醒词，中断 TTS 播放")
                play_proc.kill()
                await play_proc.wait()
                barged = True
            else:
                logger.info("语音播放完成")

            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            self.detector.stop()
        finally:
            self.tts_engine._remove_file(audio_file)

        return barged

    async def _wait_for_speech(self, timeout: float = 5.0) -> bool:
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
        logger.info("开始录音，请说话...")

        audio_stream = AudioStreamGenerator(
            recorder=self.recorder,
            silence_timeout=self.vad_silence_timeout,
            energy_threshold=self.vad_energy_threshold,
        )

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

    async def _on_timer_expire(self, timer) -> None:
        label = timer.label
        duration_str = format_duration(timer.duration_seconds)

        if label:
            msg = f"定时器到了，{label}"
        else:
            msg = f"{duration_str}定时器到了"

        logger.info("定时器到期: %s", msg)

        try:
            await self.tts_engine.speak(msg)
        except Exception as e:
            logger.warning("定时器 TTS 播报失败: %s", e)

        await self.reminder_service._send_wechat_remind(msg)

    def shutdown(self) -> None:
        if self._state == State.SHUTDOWN:
            return

        logger.info("正在关闭助手...")
        self._running = False
        self._set_state(State.SHUTDOWN)
        self.detector.stop()
        self.reminder_service.stop()

        self.recorder.close()
        self.detector.cleanup()
        self.tts_engine.cleanup()

        logger.info("助手已关闭")


def main():
    config_path = "config.yaml"
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    config = load_config(config_path)

    log_cfg = config.get("logging", {})
    setup_logging(log_cfg)

    config = resolve_config(config)

    logger.info("配置文件已加载: %s", config_path)

    assistant = VoiceAssistant(config, config_path=config_path)

    def signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("收到信号 %s，正在关闭...", sig_name)
        assistant.shutdown()

    signal.signal(signal.SIGINT, signal_handler)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, signal_handler)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        init_ok = loop.run_until_complete(assistant.initialize())
        if not init_ok:
            logger.critical("初始化失败，程序退出")
            sys.exit(1)

        loop.run_until_complete(assistant.run())

    except KeyboardInterrupt:
        logger.info("收到键盘中断")
    finally:
        assistant.shutdown()
        async def _async_cleanup():
            try:
                if assistant.web_server:
                    await assistant.web_server.stop()
            except Exception as e:
                logger.debug("停止 Web 服务时出错: %s", e)
            try:
                await assistant.db.close()
            except Exception as e:
                logger.debug("关闭数据库时出错: %s", e)
        try:
            loop.run_until_complete(_async_cleanup())
        except Exception:
            pass
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
