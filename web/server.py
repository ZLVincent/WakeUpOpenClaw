"""
Web 界面服务模块

提供聊天页面和配置管理页面。
- 聊天页面：文本形式和 OpenClaw 对话
- 配置页面：实时查看和修改 config.yaml

基于 aiohttp（edge-tts 已依赖，无需额外安装）。
"""

import asyncio
import json
import os
import time
from typing import Optional

import yaml
from aiohttp import web

from agent.openclaw_client import OpenClawClient
from tts.edge_tts_engine import EdgeTTSEngine
from utils.logger import get_logger

logger = get_logger("web")


class WebServer:
    """
    Web 服务器，提供聊天和配置管理界面。

    Parameters
    ----------
    agent_client : OpenClawClient
        OpenClaw 客户端实例（与语音助手共享）
    tts_engine : EdgeTTSEngine | None
        TTS 引擎实例
    host : str
        监听地址
    port : int
        监听端口
    tts_on_web : bool
        Web 端的 AI 回复是否也通过扬声器播报
    config_path : str
        config.yaml 文件路径，用于配置管理
    """

    def __init__(
        self,
        agent_client: OpenClawClient,
        tts_engine: Optional[EdgeTTSEngine] = None,
        host: str = "0.0.0.0",
        port: int = 8084,
        tts_on_web: bool = False,
        config_path: str = "config.yaml",
    ):
        self.agent_client = agent_client
        self.tts_engine = tts_engine
        self.host = host
        self.port = port
        self.tts_on_web = tts_on_web
        self.config_path = config_path
        self._history: list[dict] = []
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None

    async def start(self) -> None:
        """启动 Web 服务器（非阻塞）。"""
        self._app = web.Application()

        # 聊天页面
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_post("/api/chat", self._handle_chat)
        self._app.router.add_get("/api/history", self._handle_history)
        self._app.router.add_post("/api/clear", self._handle_clear)

        # 配置管理页面
        self._app.router.add_get("/config", self._handle_config_page)
        self._app.router.add_get("/api/config", self._handle_config_get)
        self._app.router.add_put("/api/config", self._handle_config_update)
        self._app.router.add_get("/api/config/{section}", self._handle_config_section_get)
        self._app.router.add_put("/api/config/{section}", self._handle_config_section_update)

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
    # 聊天页面路由
    # ------------------------------------------------------------------

    async def _handle_index(self, request: web.Request) -> web.Response:
        """返回聊天页面 HTML。"""
        return self._serve_template("chat.html")

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

        self._history.append({
            "role": "user",
            "text": message,
            "time": time.strftime("%H:%M:%S"),
        })

        start_time = time.time()
        reply = await self.agent_client.send_message(message)
        elapsed = time.time() - start_time

        if not reply:
            reply = "抱歉，AI 没有返回有效回复。"

        self._history.append({
            "role": "assistant",
            "text": reply,
            "time": time.strftime("%H:%M:%S"),
            "duration": round(elapsed, 1),
        })

        logger.info("Web AI 回复 (%.1fs): %s", elapsed, reply[:100])

        if self.tts_on_web and self.tts_engine:
            asyncio.create_task(self.tts_engine.speak(reply))

        return web.json_response({
            "reply": reply,
            "duration": round(elapsed, 1),
        })

    async def _handle_history(self, request: web.Request) -> web.Response:
        return web.json_response({"history": self._history})

    async def _handle_clear(self, request: web.Request) -> web.Response:
        self._history.clear()
        logger.info("Web 聊天历史已清空")
        return web.json_response({"status": "ok"})

    # ------------------------------------------------------------------
    # 配置管理路由
    # ------------------------------------------------------------------

    async def _handle_config_page(self, request: web.Request) -> web.Response:
        """返回配置管理页面 HTML。"""
        return self._serve_template("config.html")

    async def _handle_config_get(self, request: web.Request) -> web.Response:
        """获取完整配置。"""
        config = self._read_config()
        if config is None:
            return web.json_response({"error": "无法读取配置文件"}, status=500)
        return web.json_response({"config": config})

    async def _handle_config_section_get(self, request: web.Request) -> web.Response:
        """获取某个配置段。"""
        section = request.match_info["section"]
        config = self._read_config()
        if config is None:
            return web.json_response({"error": "无法读取配置文件"}, status=500)
        if section not in config:
            return web.json_response({"error": f"配置段 '{section}' 不存在"}, status=404)
        return web.json_response({"section": section, "config": config[section]})

    async def _handle_config_update(self, request: web.Request) -> web.Response:
        """更新完整配置。"""
        try:
            data = await request.json()
            new_config = data.get("config")
        except Exception:
            return web.json_response({"error": "无效的请求"}, status=400)

        if not new_config or not isinstance(new_config, dict):
            return web.json_response({"error": "配置内容无效"}, status=400)

        success = self._write_config(new_config)
        if not success:
            return web.json_response({"error": "写入配置文件失败"}, status=500)

        logger.info("配置已通过 Web 更新 (完整)")
        return web.json_response({"status": "ok", "message": "配置已保存，部分配置需要重启生效"})

    async def _handle_config_section_update(self, request: web.Request) -> web.Response:
        """更新某个配置段。"""
        section = request.match_info["section"]
        try:
            data = await request.json()
            section_config = data.get("config")
        except Exception:
            return web.json_response({"error": "无效的请求"}, status=400)

        if section_config is None:
            return web.json_response({"error": "配置内容无效"}, status=400)

        config = self._read_config()
        if config is None:
            return web.json_response({"error": "无法读取配置文件"}, status=500)

        config[section] = section_config
        success = self._write_config(config)
        if not success:
            return web.json_response({"error": "写入配置文件失败"}, status=500)

        logger.info("配置已通过 Web 更新 (段: %s)", section)
        return web.json_response({"status": "ok", "message": f"配置段 '{section}' 已保存，部分配置需要重启生效"})

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _serve_template(self, filename: str) -> web.Response:
        """读取并返回模板文件。"""
        template_path = os.path.join(
            os.path.dirname(__file__), "templates", filename
        )
        with open(template_path, "r", encoding="utf-8") as f:
            html = f.read()
        return web.Response(text=html, content_type="text/html")

    def _read_config(self) -> Optional[dict]:
        """读取 config.yaml。"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error("读取配置文件失败: %s", e)
            return None

    def _write_config(self, config: dict) -> bool:
        """写入 config.yaml，保留注释格式尽量友好。"""
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    config, f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            return True
        except Exception as e:
            logger.error("写入配置文件失败: %s", e)
            return False
