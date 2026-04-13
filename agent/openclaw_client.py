"""
OpenClaw Agent 客户端模块

封装 OpenClaw CLI 和 WebSocket API 调用。
将用户的语音识别文本发送给 OpenClaw AI Agent，获取回复。
"""

import asyncio
import json
import time
from typing import Optional

from utils.logger import get_logger

logger = get_logger("agent")


class OpenClawClient:
    """
    OpenClaw Agent 客户端。

    支持两种调用方式:
    - CLI: 通过 `openclaw agent --message` 命令行调用
    - WebSocket: 通过 Gateway WebSocket API 调用 (TODO: 未来实现)

    Parameters
    ----------
    method : str
        调用方式: "cli" 或 "websocket"
    cli_path : str
        openclaw CLI 可执行文件路径
    session_id : str
        会话 ID，用于保持多轮对话上下文
    thinking : str
        思考级别
    timeout : int
        单次调用超时（秒）
    local : bool
        是否使用 --local 跳过 Gateway 直接本地执行
    gateway_url : str
        Gateway WebSocket 地址（websocket 模式时使用）
    """

    def __init__(
        self,
        method: str = "cli",
        cli_path: str = "openclaw",
        session_id: str = "voice-assistant",
        thinking: str = "medium",
        timeout: int = 120,
        local: bool = False,
        gateway_url: str = "ws://127.0.0.1:18789",
    ):
        self.method = method
        self.cli_path = cli_path
        self.session_id = session_id
        self.thinking = thinking
        self.timeout = timeout
        self.local = local
        self.gateway_url = gateway_url

    async def send_message(self, message: str) -> str:
        """
        向 OpenClaw Agent 发送消息并获取回复。

        Parameters
        ----------
        message : str
            用户消息文本

        Returns
        -------
        str
            Agent 回复文本
        """
        if not message or not message.strip():
            logger.warning("收到空消息，跳过发送")
            return ""

        logger.info("发送到 OpenClaw: %s", message)
        start_time = time.time()

        if self.method == "cli":
            result = await self._send_via_cli(message)
        elif self.method == "websocket":
            result = await self._send_via_websocket(message)
        else:
            logger.error("不支持的调用方式: %s", self.method)
            return f"错误: 不支持的调用方式 '{self.method}'"

        elapsed = time.time() - start_time

        if result:
            # 截断日志中的长文本
            display = result[:200] + "..." if len(result) > 200 else result
            logger.info("OpenClaw 回复 (%.2fs): %s", elapsed, display)
        else:
            logger.warning("OpenClaw 无回复 (%.2fs)", elapsed)

        return result

    async def _send_via_cli(self, message: str) -> str:
        """
        通过 CLI 命令调用 OpenClaw Agent。

        命令格式:
            openclaw agent --message "..." --session-id <id> --thinking <level> --json
        """
        cmd = [
            self.cli_path,
            "agent",
            "--message", message,
            "--session-id", self.session_id,
            "--thinking", self.thinking,
            "--json",
        ]
        if self.local:
            cmd.append("--local")

        logger.debug("执行命令: %s", " ".join(cmd))

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "OpenClaw CLI 调用超时 (%ds)，正在终止进程...",
                    self.timeout,
                )
                process.kill()
                await process.wait()
                return "抱歉，AI 处理超时了，请再试一次。"

            if process.returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace").strip()
                logger.error(
                    "OpenClaw CLI 返回错误 (code=%d): %s",
                    process.returncode, stderr_text,
                )
                return "抱歉，AI 处理出错了，请稍后再试。"

            stdout_text = stdout.decode("utf-8", errors="replace").strip()

            if not stdout_text:
                logger.warning("OpenClaw CLI 返回空输出")
                return ""

            # 尝试解析 JSON 输出
            return self._parse_cli_output(stdout_text)

        except FileNotFoundError:
            logger.critical(
                "找不到 openclaw 命令 (%s)，请确认已安装并在 PATH 中",
                self.cli_path,
            )
            return "错误: 找不到 openclaw 命令，请确认安装。"
        except Exception as e:
            logger.error("OpenClaw CLI 调用异常: %s", e, exc_info=True)
            return "抱歉，调用 AI 助手时出错了。"

    def _parse_cli_output(self, output: str) -> str:
        """
        解析 openclaw agent --json 的输出。

        OpenClaw --json 输出结构:
        {
            "payloads": [{"text": "...", "mediaUrl": null}],
            "meta": {
                "durationMs": 1234,
                "agentMeta": {...},
                "stopReason": "completed" | "error",
            }
        }
        """
        try:
            data = json.loads(output)

            # 检查是否有错误
            meta = data.get("meta", {})
            stop_reason = meta.get("stopReason", "")
            if stop_reason == "error":
                agent_meta = meta.get("agentMeta", {})
                error_msg = agent_meta.get("error", "")
                if error_msg:
                    logger.error("OpenClaw 返回错误: %s", error_msg)
                    return f"AI 处理出错: {error_msg}"

            # 从 payloads 数组提取文本
            payloads = data.get("payloads", [])
            if payloads:
                texts = [p.get("text", "") for p in payloads if p.get("text")]
                if texts:
                    return "\n".join(texts)

            # 兼容其他格式: 直接取 text / summary
            text = data.get("text", "") or data.get("summary", "")
            if text:
                return text

            # 尝试取 payload (单数)
            payload = data.get("payload", {})
            if isinstance(payload, dict):
                return payload.get("text", "") or payload.get("summary", "")

            logger.debug("JSON 输出结构: %s", list(data.keys()))
            return output  # fallback: 返回原始输出
        except json.JSONDecodeError:
            # 非 JSON 输出，直接返回文本
            logger.debug("非 JSON 输出，直接使用原始文本")
            return output

    async def _send_via_websocket(self, message: str) -> str:
        """
        通过 WebSocket API 调用 OpenClaw Gateway。

        协议:
        1. 连接 ws://127.0.0.1:18789
        2. 发送 connect 帧
        3. 发送 agent 请求
        4. 接收流式响应
        5. 返回最终结果

        TODO: 完整实现 WebSocket 调用（当前 CLI 方式足够使用）
        """
        logger.warning(
            "WebSocket 调用方式尚未完整实现，回退到 CLI 方式"
        )
        return await self._send_via_cli(message)

    async def check_available(self) -> bool:
        """
        检查 OpenClaw 是否可用。

        Returns
        -------
        bool
            True 表示可用
        """
        try:
            process = await asyncio.create_subprocess_exec(
                self.cli_path, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=5,
            )
            version = stdout.decode("utf-8", errors="replace").strip()
            logger.info("OpenClaw 可用, 版本: %s", version)
            return True
        except FileNotFoundError:
            logger.error("找不到 openclaw 命令: %s", self.cli_path)
            return False
        except asyncio.TimeoutError:
            logger.error("检查 OpenClaw 版本超时")
            return False
        except Exception as e:
            logger.error("检查 OpenClaw 可用性失败: %s", e)
            return False
