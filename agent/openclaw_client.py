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
    system_prompt : str
        系统提示词，拼接在用户消息前面发送给 Agent
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
        system_prompt: str = "",
    ):
        self.method = method
        self.cli_path = cli_path
        self.session_id = session_id
        self.thinking = thinking
        self.timeout = timeout
        self.local = local
        self.gateway_url = gateway_url
        self.system_prompt = system_prompt.strip()

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

        使用实时逐行读取 stdout，一旦检测到完整 JSON 响应立即返回，
        不等待进程退出（OpenClaw 进程退出前可能有十几秒的清理时间）。
        """
        # 拼接系统提示词
        full_message = message
        if self.system_prompt:
            full_message = f"[系统指令] {self.system_prompt}\n\n[用户问题] {message}"

        cmd = [
            self.cli_path,
            "agent",
            "--message", full_message,
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

            # 实时逐行读取 stdout，检测到 JSON 就立即返回
            stdout_lines = []
            try:
                result = await asyncio.wait_for(
                    self._read_until_json(process, stdout_lines),
                    timeout=self.timeout,
                )
                if result:
                    return result
            except asyncio.TimeoutError:
                logger.error(
                    "OpenClaw CLI 调用超时 (%ds)，正在终止进程...",
                    self.timeout,
                )
                process.kill()
                await process.wait()
                return ""

            # 没有从流式读取中提取到 JSON，尝试用已收集的行解析
            stdout_text = "\n".join(stdout_lines).strip()
            logger.debug("OpenClaw stdout 完整输出 (%d字符): %s", len(stdout_text), stdout_text[:500])

            if process.returncode is not None and process.returncode != 0:
                stderr_data = await process.stderr.read() if process.stderr else b""
                stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
                logger.error(
                    "OpenClaw CLI 返回错误 (code=%d): %s",
                    process.returncode, stderr_text,
                )
                return ""

            if stdout_text:
                return self._parse_cli_output(stdout_text)
            return ""

        except FileNotFoundError:
            logger.critical(
                "找不到 openclaw 命令 (%s)，请确认已安装并在 PATH 中",
                self.cli_path,
            )
            return "错误: 找不到 openclaw 命令，请确认安装。"
        except Exception as e:
            logger.error("OpenClaw CLI 调用异常: %s", e, exc_info=True)
            return ""

    async def _read_until_json(
        self,
        process: asyncio.subprocess.Process,
        stdout_lines: list,
    ) -> str:
        """
        实时逐行读取进程 stdout，一旦检测到完整 JSON 响应立即解析返回。

        OpenClaw --json 输出的 JSON 以 '{' 开头，可能跨多行（pretty-printed）。
        当检测到 JSON 开始后，持续收集行直到 JSON 完整（可被 json.loads 解析）。
        """
        json_buffer = ""
        in_json = False
        brace_depth = 0

        while True:
            line = await process.stdout.readline()
            if not line:
                # EOF，进程 stdout 关闭
                break

            decoded = line.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
            stdout_lines.append(decoded)

            if not in_json:
                stripped = decoded.strip()
                if stripped.startswith("{"):
                    in_json = True
                    json_buffer = ""
                else:
                    # 非 JSON 行（日志等），跳过
                    continue

            if in_json:
                json_buffer += decoded + "\n"
                # 简单的花括号深度计数（忽略字符串内的括号，但对于
                # OpenClaw 的 JSON 输出足够可靠）
                for ch in decoded:
                    if ch == "{":
                        brace_depth += 1
                    elif ch == "}":
                        brace_depth -= 1

                if brace_depth <= 0:
                    # JSON 对象应该已完整，尝试解析
                    json_buffer = json_buffer.strip()
                    try:
                        json.loads(json_buffer)
                        logger.debug(
                            "实时捕获到完整 JSON (%d字符)",
                            len(json_buffer),
                        )
                        result = self._parse_cli_output(json_buffer)
                        # 不等进程退出，后台让它自行结束
                        asyncio.create_task(self._cleanup_process(process))
                        return result
                    except json.JSONDecodeError:
                        # 花括号计数不准，继续收集
                        brace_depth = 0
                        continue

        return ""

    @staticmethod
    async def _cleanup_process(process: asyncio.subprocess.Process) -> None:
        """后台等待进程退出，避免僵尸进程。"""
        try:
            await asyncio.wait_for(process.wait(), timeout=30)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    # OpenClaw 输出中可能混入的非回复内容关键词
    _NOISE_PATTERNS = (
        "completed",
        "gateway agent failed",
        "falling back to embedded",
        "[agent]",
    )

    def _parse_cli_output(self, output: str) -> str:
        """
        解析 openclaw agent 的输出。

        OpenClaw stdout 有两种可能格式:
        1. --json 模式下的 JSON（可能混有非 JSON 前缀行）:
           Gateway agent failed; falling back to embedded: ...
           {"payloads":[{"text":"回复内容"}],"meta":{...}}
        2. 纯文本（Gateway 模式下可能直接输出文本）

        需要同时处理两种情况。
        """
        # 第一步: 尝试提取并解析 JSON
        json_str = self._extract_json(output)
        if json_str:
            result = self._parse_json_response(json_str)
            if result:
                return result

        # 第二步: 非 JSON 输出，按纯文本处理
        # 过滤掉非回复内容的行（日志、状态信息等）
        lines = output.strip().split("\n")
        content_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # 跳过明显的噪音行
            lower = stripped.lower()
            if any(noise in lower for noise in self._NOISE_PATTERNS):
                continue
            # 跳过以 [ 开头的日志行
            if stripped.startswith("["):
                continue
            content_lines.append(stripped)

        if content_lines:
            result = "\n".join(content_lines)
            logger.debug("从纯文本输出提取到回复: %s", result[:200])
            return result

        logger.warning("OpenClaw 输出中未找到有效回复内容")
        return ""

    def _parse_json_response(self, json_str: str) -> str:
        """
        解析 JSON 格式的 OpenClaw 响应，提取回复文本。

        OpenClaw --json 输出有两种结构:

        Gateway 模式 (嵌套在 result 中):
        {
            "runId": "...",
            "status": "ok",
            "summary": "completed",
            "result": {
                "payloads": [{"text": "回复内容"}],
                "meta": {"stopReason": "completed", ...}
            }
        }

        Embedded/--local 模式 (顶层):
        {
            "payloads": [{"text": "回复内容"}],
            "meta": {"stopReason": "completed", ...}
        }
        """
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.debug("JSON 解析失败: %s", json_str[:200])
            return ""

        # 判断结构: 如果有 result 字段，取 result 作为实际数据
        if "result" in data and isinstance(data["result"], dict):
            payload_data = data["result"]
        else:
            payload_data = data

        # 检查是否有错误
        meta = payload_data.get("meta", {})
        stop_reason = meta.get("stopReason", "")
        if stop_reason == "error":
            # 优先从 payloads 取错误文本
            payloads = payload_data.get("payloads", [])
            if payloads:
                err_text = payloads[0].get("text", "")
                if err_text:
                    logger.error("OpenClaw 返回错误: %s", err_text)
                    return f"AI 处理出错: {err_text}"
            # 其次从 agentMeta 取
            agent_meta = meta.get("agentMeta", {})
            error_msg = agent_meta.get("error", "")
            if error_msg:
                logger.error("OpenClaw 返回错误: %s", error_msg)
                return f"AI 处理出错: {error_msg}"

        # 从 payloads 数组提取文本
        payloads = payload_data.get("payloads", [])
        if payloads:
            texts = [p.get("text", "") for p in payloads if p.get("text")]
            if texts:
                return "\n".join(texts)

        # 兼容: 直接取 text 字段
        text = payload_data.get("text", "")
        if text:
            return text

        logger.warning(
            "OpenClaw JSON 中未找到有效文本，顶层keys: %s, payload_data keys: %s",
            list(data.keys()), list(payload_data.keys()),
        )
        return ""

    @staticmethod
    def _extract_json(text: str) -> str:
        """
        从可能混有非 JSON 行的文本中提取最后一个完整的 JSON 对象。

        从后往前扫描，找到以 '{' 开头的行，尝试解析。
        """
        # 先尝试整体解析
        text = text.strip()
        if text.startswith("{"):
            return text

        # 从后往前逐行查找 JSON 起始
        lines = text.split("\n")
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i].strip()
            if line.startswith("{"):
                candidate = "\n".join(lines[i:]).strip()
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    continue

        return ""

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
