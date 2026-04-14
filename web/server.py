"""
Web 界面服务模块

提供一个简洁的聊天 Web 页面，作为语音交互的备用方式。
用户可以通过浏览器以文本形式和 OpenClaw 对话。
支持 TTS 播报 Web 端收到的 AI 回复（可选）。

基于 aiohttp（edge-tts 已依赖，无需额外安装）。
"""

import asyncio
import json
import os
import time
from typing import Optional

from aiohttp import web

from agent.openclaw_client import OpenClawClient
from tts.edge_tts_engine import EdgeTTSEngine
from utils.logger import get_logger

logger = get_logger("web")


class WebServer:
    """
    聊天 Web 服务器。

    Parameters
    ----------
    agent_client : OpenClawClient
        OpenClaw 客户端实例（与语音助手共享）
    tts_engine : EdgeTTSEngine | None
        TTS 引擎实例，用于播报 Web 端的 AI 回复（可选）
    host : str
        监听地址
    port : int
        监听端口
    tts_on_web : bool
        Web 端的 AI 回复是否也通过扬声器播报
    """

    def __init__(
        self,
        agent_client: OpenClawClient,
        tts_engine: Optional[EdgeTTSEngine] = None,
        host: str = "0.0.0.0",
        port: int = 8080,
        tts_on_web: bool = False,
    ):
        self.agent_client = agent_client
        self.tts_engine = tts_engine
        self.host = host
        self.port = port
        self.tts_on_web = tts_on_web
        self._history: list[dict] = []
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None

    async def start(self) -> None:
        """启动 Web 服务器（非阻塞）。"""
        self._app = web.Application()
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_post("/api/chat", self._handle_chat)
        self._app.router.add_get("/api/history", self._handle_history)
        self._app.router.add_post("/api/clear", self._handle_clear)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info("Web 服务已启动: http://%s:%d", self.host, self.port)

    async def stop(self) -> None:
        """停止 Web 服务器。"""
        if self._runner:
            await self._runner.cleanup()
            logger.info("Web 服务已停止")

    # ------------------------------------------------------------------
    # 路由处理
    # ------------------------------------------------------------------

    async def _handle_index(self, request: web.Request) -> web.Response:
        """返回聊天页面 HTML。"""
        template_path = os.path.join(
            os.path.dirname(__file__), "templates", "chat.html"
        )
        with open(template_path, "r", encoding="utf-8") as f:
            html = f.read()
        return web.Response(text=html, content_type="text/html")

    async def _handle_chat(self, request: web.Request) -> web.Response:
        """处理聊天消息。"""
        try:
            data = await request.json()
            message = data.get("message", "").strip()
        except Exception:
            return web.json_response({"error": "无效的请求"}, status=400)

        if not message:
            return web.json_response({"error": "消息不能为空"}, status=400)

        logger.info("Web 收到消息: %s", message)

        # 记录用户消息
        self._history.append({
            "role": "user",
            "text": message,
            "time": time.strftime("%H:%M:%S"),
        })

        # 调用 OpenClaw
        start_time = time.time()
        reply = await self.agent_client.send_message(message)
        elapsed = time.time() - start_time

        if not reply:
            reply = "抱歉，AI 没有返回有效回复。"

        # 记录 AI 回复
        self._history.append({
            "role": "assistant",
            "text": reply,
            "time": time.strftime("%H:%M:%S"),
            "duration": round(elapsed, 1),
        })

        logger.info("Web AI 回复 (%.1fs): %s", elapsed, reply[:100])

        # 可选：通过扬声器播报
        if self.tts_on_web and self.tts_engine:
            asyncio.create_task(self.tts_engine.speak(reply))

        return web.json_response({
            "reply": reply,
            "duration": round(elapsed, 1),
        })

    async def _handle_history(self, request: web.Request) -> web.Response:
        """返回聊天历史。"""
        return web.json_response({"history": self._history})

    async def _handle_clear(self, request: web.Request) -> web.Response:
        """清空聊天历史。"""
        self._history.clear()
        logger.info("Web 聊天历史已清空")
        return web.json_response({"status": "ok"})
