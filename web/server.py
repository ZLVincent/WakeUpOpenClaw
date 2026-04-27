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
from storage.database import ChatDatabase
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
    database : ChatDatabase | None
        数据库实例，用于对话持久化
    """

    def __init__(
        self,
        agent_client: OpenClawClient,
        tts_engine: Optional[EdgeTTSEngine] = None,
        host: str = "0.0.0.0",
        port: int = 8084,
        tts_on_web: bool = False,
        config_path: str = "config.yaml",
        database: Optional[ChatDatabase] = None,
    ):
        self.agent_client = agent_client
        self.tts_engine = tts_engine
        self.host = host
        self.port = port
        self.tts_on_web = tts_on_web
        self.config_path = config_path
        self.db = database
        self._assistant = None  # 由 main.py 设置，引用 VoiceAssistant 实例
        self._ws_clients: set[web.WebSocketResponse] = set()
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None

    async def start(self) -> None:
        """启动 Web 服务器（非阻塞）。"""
        self._app = web.Application()

        # 聊天页面
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_post("/api/chat", self._handle_chat)

        # 对话管理 API
        self._app.router.add_get("/api/conversations", self._handle_conversations_list)
        self._app.router.add_post("/api/conversations/new", self._handle_conversation_new)
        self._app.router.add_get("/api/conversations/{id}/messages", self._handle_conversation_messages)
        self._app.router.add_post("/api/conversations/{id}/archive", self._handle_conversation_archive)
        self._app.router.add_delete("/api/conversations/{id}", self._handle_conversation_delete)

        # 配置管理页面
        self._app.router.add_get("/config", self._handle_config_page)
        self._app.router.add_get("/api/config", self._handle_config_get)
        self._app.router.add_put("/api/config", self._handle_config_update)
        self._app.router.add_get("/api/config/{section}", self._handle_config_section_get)
        self._app.router.add_put("/api/config/{section}", self._handle_config_section_update)

        # 系统管理 API
        self._app.router.add_get("/api/system/version", self._handle_system_version)
        self._app.router.add_get("/api/system/update/check", self._handle_update_check)
        self._app.router.add_post("/api/system/update/apply", self._handle_update_apply)
        self._app.router.add_post("/api/system/restart", self._handle_restart)

        # 状态面板 WebSocket
        self._app.router.add_get("/ws/status", self._handle_ws_status)

        # 音量控制 API
        self._app.router.add_get("/api/volume", self._handle_volume_get)
        self._app.router.add_put("/api/volume", self._handle_volume_set)

        # 日志查看页面
        self._app.router.add_get("/logs", self._handle_logs_page)
        self._app.router.add_get("/api/logs", self._handle_logs_get)

        # 日程日历页面
        self._app.router.add_get("/calendar", self._handle_calendar_page)
        self._app.router.add_get("/api/events", self._handle_events_list)
        self._app.router.add_post("/api/events", self._handle_event_create)
        self._app.router.add_put("/api/events/{id}", self._handle_event_update)
        self._app.router.add_delete("/api/events/{id}", self._handle_event_delete)

        # 系统状态监控页面
        self._app.router.add_get("/status", self._handle_status_page)
        self._app.router.add_get("/api/status/system", self._handle_status_system)
        self._app.router.add_get("/api/status/ip", self._handle_status_ip)
        self._app.router.add_get("/api/status/network", self._handle_status_network)

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

        # 获取或创建活跃对话
        conv = None
        if self.db:
            try:
                conv = await self.db.get_or_create_active_conversation("web")
            except Exception as e:
                logger.debug("获取对话失败: %s", e)

        # 保存用户消息
        if self.db and conv:
            try:
                await self.db.add_message(conv["id"], "user", message, "web")
                if not conv.get("title"):
                    await self.db.update_conversation_title(conv["id"], message[:50])
            except Exception as e:
                logger.debug("保存用户消息失败: %s", e)

        # 先尝试技能路由匹配（与语音端一致）
        if self._assistant and self._assistant.skill_router:
            skill_result = await self._assistant.skill_router.match(message)
            if skill_result:
                reply = skill_result.text or ""
                logger.info("Web 技能命中: action=%s, reply=%s", skill_result.action, reply[:50])

                # 特殊动作处理
                if skill_result.action == "new_conversation":
                    await self._assistant.start_new_conversation("web")

                # 保存 AI 回复
                if self.db and conv:
                    try:
                        await self.db.add_message(conv["id"], "assistant", reply, "web")
                    except Exception:
                        pass

                # 可选 TTS 播报
                if self.tts_on_web and self.tts_engine and reply:
                    asyncio.create_task(self.tts_engine.speak(reply))

                return web.json_response({
                    "reply": reply,
                    "duration": 0,
                    "conversation_id": conv["id"] if conv else None,
                    "skill": skill_result.action,
                })

        # 技能未命中，调用 OpenClaw AI
        start_time = time.time()
        conv_sid = conv["session_id"] if conv else ""
        reply = await self.agent_client.send_message(message, session_id=conv_sid)
        elapsed = time.time() - start_time
        duration_ms = int(elapsed * 1000)

        if not reply:
            reply = "抱歉，AI 没有返回有效回复。"

        # 保存 AI 回复
        if self.db and conv:
            try:
                await self.db.add_message(conv["id"], "assistant", reply, "web", duration_ms)
                await self.db.increment_round_count(conv["id"])
            except Exception as e:
                logger.debug("保存 AI 回复失败: %s", e)

        logger.info("Web AI 回复 (%.1fs): %s", elapsed, reply[:100])

        if self.tts_on_web and self.tts_engine:
            asyncio.create_task(self.tts_engine.speak(reply))

        return web.json_response({
            "reply": reply,
            "duration": round(elapsed, 1),
            "conversation_id": conv["id"] if conv else None,
        })

    # ------------------------------------------------------------------
    # 对话管理路由
    # ------------------------------------------------------------------

    async def _handle_conversations_list(self, request: web.Request) -> web.Response:
        """列出所有对话。"""
        if not self.db:
            return web.json_response({"conversations": []})
        try:
            conversations = await self.db.list_conversations(limit=50)
            return web.json_response({"conversations": conversations})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_conversation_new(self, request: web.Request) -> web.Response:
        """新建对话。"""
        if self._assistant:
            conv = await self._assistant.start_new_conversation("web")
        elif self.db:
            conv = await self.db.start_new_conversation("web")
        else:
            return web.json_response({"error": "数据库不可用"}, status=500)
        return web.json_response({"conversation": conv})

    async def _handle_conversation_messages(self, request: web.Request) -> web.Response:
        """获取某个对话的消息。"""
        if not self.db:
            return web.json_response({"messages": []})
        try:
            conv_id = int(request.match_info["id"])
            messages = await self.db.get_messages(conv_id, limit=200)
            return web.json_response({"messages": messages})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_conversation_archive(self, request: web.Request) -> web.Response:
        """归档对话。"""
        if not self.db:
            return web.json_response({"error": "数据库不可用"}, status=500)
        try:
            conv_id = int(request.match_info["id"])
            await self.db.archive_conversation(conv_id)
            return web.json_response({"status": "ok"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_conversation_delete(self, request: web.Request) -> web.Response:
        """删除对话及其所有消息。"""
        if not self.db:
            return web.json_response({"error": "数据库不可用"}, status=500)
        try:
            conv_id = int(request.match_info["id"])
            # 如果删除的是当前活跃对话，需要清除引用
            if (self._assistant
                    and self._assistant._current_conversation
                    and self._assistant._current_conversation.get("id") == conv_id):
                self._assistant._current_conversation = None
            await self.db.delete_conversation(conv_id)
            return web.json_response({"status": "ok"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

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

        # 对比变更的段名
        old_config = self._read_config() or {}
        success = self._write_config(new_config)
        if not success:
            return web.json_response({"error": "写入配置文件失败"}, status=500)

        changed = self._diff_config_sections(old_config, new_config)
        logger.info("配置已通过 Web 更新 (完整)")

        # 自动推送到远程
        pushed = await self._git_commit_and_push(
            "config.yaml",
            f"chore: update config via web ({', '.join(changed) if changed else 'all'})",
        )
        msg = "配置已保存并推送到远程" if pushed else "配置已保存，但推送到远程失败"
        return web.json_response({"status": "ok", "message": msg, "pushed": pushed})

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

        pushed = await self._git_commit_and_push(
            "config.yaml",
            f"chore: update config via web ({section})",
        )
        msg = f"配置段 '{section}' 已保存并推送到远程" if pushed else f"配置段 '{section}' 已保存，但推送到远程失败"
        return web.json_response({"status": "ok", "message": msg, "pushed": pushed})

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

    async def _git_commit_and_push(self, filepath: str, message: str) -> bool:
        """git add + commit + push，成功返回 True。"""
        cwd = os.path.dirname(os.path.abspath(self.config_path)) or "."
        logger.info("开始推送配置变更: %s (cwd=%s)", message, cwd)

        # git add
        ok, _ = await self._run_cmd_checked("git", "add", filepath, cwd=cwd)
        if not ok:
            logger.warning("git add %s 失败", filepath)
            return False

        # git commit
        ok, out = await self._run_cmd_checked("git", "commit", "-m", message, cwd=cwd)
        if not ok:
            logger.warning("git commit 失败: %s", out[:200])
            return False

        # git push
        ok, out = await self._run_cmd_checked("git", "push", "origin", cwd=cwd)
        if not ok:
            logger.warning("git push 失败: %s", out[:200])
            return False

        logger.info("配置已推送到远程: %s", message)
        return True

    @staticmethod
    def _diff_config_sections(old: dict, new: dict) -> list[str]:
        """对比两份配置，返回有变更的段名列表。"""
        changed = []
        all_keys = set(list(old.keys()) + list(new.keys()))
        for key in all_keys:
            if old.get(key) != new.get(key):
                changed.append(key)
        return changed

    # ------------------------------------------------------------------
    # 系统管理路由
    # ------------------------------------------------------------------

    async def _handle_system_version(self, request: web.Request) -> web.Response:
        """获取当前版本信息。"""
        version = await self._run_cmd("git", "log", "-1", "--format=%h %s (%ci)")
        branch = await self._run_cmd("git", "rev-parse", "--abbrev-ref", "HEAD")
        return web.json_response({
            "version": version.strip() if version else "unknown",
            "branch": branch.strip() if branch else "unknown",
        })

    async def _handle_update_check(self, request: web.Request) -> web.Response:
        """检查远程是否有更新。"""
        # fetch 最新
        await self._run_cmd("git", "fetch", "origin")
        # 比较本地和远程
        branch = (await self._run_cmd("git", "rev-parse", "--abbrev-ref", "HEAD") or "main").strip()
        log = await self._run_cmd(
            "git", "log", f"HEAD..origin/{branch}", "--oneline",
        )
        commits = [l.strip() for l in (log or "").strip().split("\n") if l.strip()]
        return web.json_response({
            "has_update": len(commits) > 0,
            "commits": commits,
            "branch": branch,
        })

    async def _handle_update_apply(self, request: web.Request) -> web.Response:
        """拉取更新并重启。"""
        # git pull
        pull_output = await self._run_cmd("git", "pull", "origin")
        if pull_output is None:
            return web.json_response({"error": "git pull 失败"}, status=500)

        logger.info("OTA 更新: git pull 完成: %s", pull_output.strip()[:200])

        # 延迟 1 秒后执行重启（确保 HTTP 响应先返回）
        asyncio.get_running_loop().call_later(
            1.0,
            lambda: asyncio.ensure_future(
                self._run_cmd("sudo", "supervisorctl", "restart", "WakeUpOpenClaw")
            ),
        )
        logger.info("OTA 更新: 重启命令将在 1 秒后执行")

        return web.json_response({
            "status": "ok",
            "pull": pull_output.strip()[:500],
            "message": "更新已拉取，服务即将重启",
        })

    async def _handle_restart(self, request: web.Request) -> web.Response:
        """仅重启服务（不拉取更新）。"""
        asyncio.get_running_loop().call_later(
            1.0,
            lambda: asyncio.ensure_future(
                self._run_cmd("sudo", "supervisorctl", "restart", "WakeUpOpenClaw")
            ),
        )
        logger.info("手动重启命令将在 1 秒后执行")
        return web.json_response({
            "status": "ok",
            "message": "重启命令已调度，服务即将重启",
        })

    # ------------------------------------------------------------------
    # 音量控制路由
    # ------------------------------------------------------------------

    async def _handle_volume_get(self, request: web.Request) -> web.Response:
        """获取当前系统音量。"""
        volume = await self._get_system_volume()
        return web.json_response({"volume": volume})

    async def _handle_volume_set(self, request: web.Request) -> web.Response:
        """设置系统音量。"""
        try:
            data = await request.json()
            volume = int(data.get("volume", 50))
        except Exception:
            return web.json_response({"error": "无效的请求"}, status=400)

        volume = max(0, min(100, volume))
        output = await self._run_cmd("amixer", "set", "Master", f"{volume}%")
        if output is None:
            return web.json_response({"error": "设置音量失败"}, status=500)

        logger.info("Web 设置音量: %d%%", volume)
        actual = await self._get_system_volume()
        return web.json_response({"volume": actual})

    async def _get_system_volume(self) -> int:
        """获取当前系统音量百分比。"""
        output = await self._run_cmd("amixer", "get", "Master")
        if not output:
            return 50  # fallback
        # 解析 amixer 输出中的百分比，如 [75%]
        import re
        match = re.search(r"\[(\d+)%\]", output)
        return int(match.group(1)) if match else 50

    # ------------------------------------------------------------------
    # 日志查看路由
    # ------------------------------------------------------------------

    async def _handle_logs_page(self, request: web.Request) -> web.Response:
        """返回日志查看页面 HTML。"""
        return self._serve_template("logs.html")

    async def _handle_logs_get(self, request: web.Request) -> web.Response:
        """
        获取日志文件内容。

        Query params:
            lines: 返回最后多少行 (默认 500, 最大 5000)
            level: 过滤日志级别 (DEBUG/INFO/WARNING/ERROR/CRITICAL)
            module: 过滤模块名 (wake_up/asr/agent/tts/main 等)
            file: 日志文件名 (默认 assistant.log)
        """
        # 解析参数
        max_lines = min(int(request.query.get("lines", "500")), 5000)
        level_filter = request.query.get("level", "").upper()
        module_filter = request.query.get("module", "").lower()
        log_filename = request.query.get("file", "assistant.log")

        # 安全检查: 只允许读取 logs/ 目录下的文件, 防止路径穿越
        log_dir = os.path.join(os.path.dirname(self.config_path), "logs")
        if ".." in log_filename or "/" in log_filename or "\\" in log_filename:
            return web.json_response({"error": "无效的文件名"}, status=400)

        log_path = os.path.join(log_dir, log_filename)

        # 列出可用日志文件
        available_files = []
        try:
            if os.path.isdir(log_dir):
                for f in sorted(os.listdir(log_dir)):
                    if f.startswith("assistant.log"):
                        available_files.append(f)
        except OSError:
            pass

        # 读取日志文件
        if not os.path.exists(log_path):
            return web.json_response({
                "lines": [],
                "total": 0,
                "file": log_filename,
                "available_files": available_files,
            })

        try:
            lines = self._read_last_lines(log_path, max_lines)
        except Exception as e:
            logger.error("读取日志文件失败: %s", e)
            return web.json_response({"error": str(e)}, status=500)

        total = len(lines)

        # 后端过滤
        if level_filter:
            lines = [l for l in lines if f"[{level_filter}" in l]
        if module_filter:
            lines = [l for l in lines if f"[{module_filter}" in l.lower()]

        return web.json_response({
            "lines": lines,
            "total": total,
            "file": log_filename,
            "available_files": available_files,
        })

    @staticmethod
    def _read_last_lines(filepath: str, n: int) -> list[str]:
        """高效读取文件最后 n 行（从末尾向前读取）。"""
        lines = []
        with open(filepath, "rb") as f:
            # 跳到文件末尾
            f.seek(0, 2)
            file_size = f.tell()
            if file_size == 0:
                return []

            # 从末尾向前读取，每次 8KB
            pos = file_size
            buffer = b""
            while pos > 0 and len(lines) <= n:
                chunk_size = min(8192, pos)
                pos -= chunk_size
                f.seek(pos)
                buffer = f.read(chunk_size) + buffer
                lines = buffer.split(b"\n")

            # 取最后 n 行（去掉可能的空末行）
            result = [l.decode("utf-8", errors="replace") for l in lines if l]
            return result[-n:]

    # ------------------------------------------------------------------
    # 日程日历路由
    # ------------------------------------------------------------------

    async def _handle_calendar_page(self, request: web.Request) -> web.Response:
        """返回日历页面 HTML。"""
        return self._serve_template("calendar.html")

    async def _handle_events_list(self, request: web.Request) -> web.Response:
        """获取日期范围内的日程。"""
        if not self.db:
            return web.json_response({"events": []})
        start = request.query.get("start", "")
        end = request.query.get("end", "")
        if not start or not end:
            return web.json_response({"error": "需要 start 和 end 参数"}, status=400)
        try:
            events = await self.db.get_events_by_range(start, end)
            return web.json_response({"events": events})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_event_create(self, request: web.Request) -> web.Response:
        """创建日程。"""
        if not self.db:
            return web.json_response({"error": "数据库不可用"}, status=500)
        try:
            data = await request.json()
            event_id = await self.db.create_event(
                title=data.get("title", ""),
                date=data.get("date", ""),
                start_time=data.get("start_time"),
                end_time=data.get("end_time"),
                all_day=data.get("all_day", False),
                color=data.get("color", "#0f3460"),
                category=data.get("category", ""),
                description=data.get("description", ""),
                remind_minutes=data.get("remind_minutes", 5),
            )
            event = await self.db.get_event(event_id)
            return web.json_response({"event": event})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_event_update(self, request: web.Request) -> web.Response:
        """更新日程。"""
        if not self.db:
            return web.json_response({"error": "数据库不可用"}, status=500)
        try:
            event_id = int(request.match_info["id"])
            data = await request.json()
            # 只传需要更新的字段
            fields = {}
            for k in ("title", "description", "date", "start_time", "end_time",
                       "all_day", "color", "category", "remind_minutes"):
                if k in data:
                    fields[k] = data[k]
            if fields:
                # 修改内容后重置提醒状态
                fields["reminded"] = 0
                await self.db.update_event(event_id, **fields)
            event = await self.db.get_event(event_id)
            return web.json_response({"event": event})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_event_delete(self, request: web.Request) -> web.Response:
        """删除日程。"""
        if not self.db:
            return web.json_response({"error": "数据库不可用"}, status=500)
        try:
            event_id = int(request.match_info["id"])
            await self.db.delete_event(event_id)
            return web.json_response({"status": "ok"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # ------------------------------------------------------------------
    # 系统状态监控路由
    # ------------------------------------------------------------------

    async def _handle_status_page(self, request: web.Request) -> web.Response:
        """返回系统状态页面 HTML。"""
        return self._serve_template("status.html")

    async def _handle_status_system(self, request: web.Request) -> web.Response:
        """获取系统状态（CPU/内存/磁盘/温度/运行时间）。"""
        from utils.system_info import get_system_info
        try:
            info = get_system_info()
            return web.json_response(info)
        except Exception as e:
            logger.error("获取系统状态失败: %s", e)
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_status_ip(self, request: web.Request) -> web.Response:
        """获取本机 IP 地址。"""
        from utils.system_info import get_ip_info
        try:
            info = get_ip_info()
            return web.json_response(info)
        except Exception as e:
            logger.error("获取 IP 失败: %s", e)
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_status_network(self, request: web.Request) -> web.Response:
        """检查网络连通性（ping 百度和 Google）。"""
        from utils.system_info import check_network
        try:
            results = await check_network()
            return web.json_response({"targets": results})
        except Exception as e:
            logger.error("网络检测失败: %s", e)
            return web.json_response({"error": str(e)}, status=500)

    @staticmethod
    async def _run_cmd(*args: str) -> Optional[str]:
        """运行系统命令，返回 stdout 或 None。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                logger.warning("命令 %s 返回 code=%d: %s", args, proc.returncode,
                             stderr.decode("utf-8", errors="replace")[:200])
            return stdout.decode("utf-8", errors="replace")
        except Exception as e:
            logger.error("执行命令 %s 失败: %s", args, e)
            return None

    @staticmethod
    async def _run_cmd_checked(*args: str, cwd: str = None) -> tuple:
        """
        运行命令并检查返回码。

        Returns
        -------
        tuple[bool, str]
            (是否成功, stdout 文本)
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            if proc.returncode != 0:
                logger.warning(
                    "命令 %s 失败 (code=%d): %s",
                    " ".join(args), proc.returncode, stderr_text[:300],
                )
                return False, stderr_text
            return True, stdout_text
        except asyncio.TimeoutError:
            logger.error("命令 %s 超时 (60s)", " ".join(args))
            return False, "timeout"
        except Exception as e:
            logger.error("命令 %s 异常: %s", " ".join(args), e)
            return False, str(e)

    # ------------------------------------------------------------------
    # WebSocket 状态面板
    # ------------------------------------------------------------------

    async def _handle_ws_status(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket 状态推送端点。"""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.add(ws)
        logger.debug("WebSocket 状态客户端已连接 (共 %d)", len(self._ws_clients))

        try:
            # 发送当前状态
            await ws.send_json(self._get_status_snapshot())
            # 保持连接，等待客户端关闭
            async for msg in ws:
                pass  # 不需要接收客户端消息
        finally:
            self._ws_clients.discard(ws)
            logger.debug("WebSocket 状态客户端已断开 (剩余 %d)", len(self._ws_clients))

        return ws

    def _get_status_snapshot(self) -> dict:
        """获取当前状态快照。"""
        state = "unknown"
        conv_round = 0
        if self._assistant:
            state = self._assistant._state.value
            conv_round = self._assistant._conversation_round
        return {
            "type": "status",
            "state": state,
            "conversation_round": conv_round,
        }

    async def broadcast_status(self, state: str, **extra) -> None:
        """
        向所有 WebSocket 客户端广播状态更新。

        Parameters
        ----------
        state : str
            当前状态 (idle / listening / thinking / speaking)
        **extra
            额外字段，如 text, duration 等
        """
        if not self._ws_clients:
            return

        msg = {"type": "status", "state": state, **extra}
        dead = set()
        for ws in self._ws_clients:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead
