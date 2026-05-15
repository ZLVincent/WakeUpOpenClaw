"""
日程提醒服务 — 后台循环检查日程并播报。
"""

import asyncio
import os

from storage.database import ChatDatabase
from tts.edge_tts_engine import EdgeTTSEngine
from utils.logger import get_logger

logger = get_logger("reminder")


class ReminderService:
    """
    日程提醒后台服务。

    每隔 check_interval 秒检查一次即将到来的日程，
    通过 TTS 语音播报（免打扰时段内跳过语音但仍发微信）。
    """

    def __init__(
        self,
        db: ChatDatabase,
        tts_engine: EdgeTTSEngine,
        cli_path: str,
        remind_enabled: bool,
        check_interval: int,
        wechat_cfg: dict,
        is_dnd_active_cb,
    ):
        self.db = db
        self.tts_engine = tts_engine
        self.cli_path = cli_path
        self.remind_enabled = remind_enabled
        self.check_interval = check_interval
        self.wechat_cfg = wechat_cfg
        self._is_dnd_active_cb = is_dnd_active_cb
        self._running = False

    async def run_loop(self) -> None:
        self._running = True
        while self._running:
            try:
                events = await self.db.get_upcoming_reminders()
                for ev in events:
                    title = ev.get("title", "")
                    remind_min = ev.get("remind_minutes", 5)
                    msg = f"提醒您，{remind_min}分钟后有日程：{title}"

                    logger.info("日程提醒: %s", msg)

                    if not self._is_dnd_active_cb():
                        await self.tts_engine.speak(msg)

                    await self._send_wechat_remind(msg)
                    await self.db.mark_event_reminded(ev["id"])

            except Exception as e:
                logger.warning("提醒循环出错: %s", e)

            await asyncio.sleep(self.check_interval)

    def stop(self):
        self._running = False

    async def _send_wechat_remind(self, message: str) -> None:
        cfg = self.wechat_cfg
        if not cfg.get("enabled", False):
            return

        target = cfg.get("target", "")
        channel = cfg.get("channel", "openclaw-weixin")
        if not target:
            logger.warning("微信提醒未配置 target 参数，跳过")
            return

        cmd = [
            self.cli_path,
            "message", "send",
            "--channel", channel,
            "--target", target,
            "--message", message,
        ]
        logger.info("发送微信提醒: %s (target=%s)", message[:50], target[:20])
        try:
            env = os.environ.copy()
            for key in ("https_proxy", "http_proxy", "all_proxy",
                        "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
                env.pop(key, None)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace").strip()
                logger.error("微信提醒发送失败 (code=%d): %s", proc.returncode, stderr_text[:200])
            else:
                logger.info("微信提醒发送成功")
        except Exception as e:
            logger.error("发送微信提醒异常: %s", e)
