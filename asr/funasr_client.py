"""
FunASR WebSocket 客户端模块

连接本地 FunASR Docker 服务进行实时语音识别。
协议: FunASR WebSocket 2pass 在线/离线识别协议。

流程:
  1. 建立 WebSocket 连接
  2. 发送 JSON 配置帧 (首帧)
  3. 持续发送 PCM 音频数据帧
  4. 发送结束帧
  5. 接收识别结果
"""

import asyncio
import json
import ssl
import time
from typing import Callable, Optional

import websockets

from utils.logger import get_logger

logger = get_logger("asr")


class FunASRClient:
    """
    FunASR WebSocket 客户端。

    Parameters
    ----------
    server_url : str
        FunASR 服务的 WebSocket 地址。
        - 离线服务 (offline): ws://localhost:10095 (无 SSL)
        - 实时服务 (online/2pass): wss://localhost:10096 (有 SSL)
    mode : str
        识别模式: "offline" / "online" / "2pass"
    hotwords : str
        热词，空格分隔
    use_itn : bool
        是否使用逆文本归一化
    ssl_enabled : bool
        是否启用 SSL。离线服务默认关闭，实时服务默认开启。
    """

    def __init__(
        self,
        server_url: str = "ws://localhost:10095",
        mode: str = "offline",
        hotwords: str = "",
        use_itn: bool = True,
        ssl_enabled: bool = False,
    ):
        self.server_url = server_url
        self.mode = mode
        self.hotwords = hotwords
        self.use_itn = use_itn
        self.ssl_enabled = ssl_enabled

    def _get_ssl_context(self) -> ssl.SSLContext | None:
        """构造 SSL 上下文。FunASR 默认使用自签名证书，需跳过验证。"""
        if not self.ssl_enabled:
            return None
        if not self.server_url.startswith("wss://"):
            return None
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        return ssl_context

    async def recognize(
        self,
        audio_generator,
        sample_rate: int = 16000,
        on_partial: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        对音频流进行语音识别。

        Parameters
        ----------
        audio_generator : async generator
            异步生成器，每次 yield bytes 类型的 PCM 音频块。
            当生成器结束时，表示录音完成。
        sample_rate : int
            音频采样率
        on_partial : callable, optional
            收到中间识别结果时的回调函数 on_partial(text: str)

        Returns
        -------
        str
            最终识别结果文本
        """
        final_text = ""
        start_time = time.time()

        logger.info("连接 FunASR 服务: %s", self.server_url)

        try:
            async with websockets.connect(
                self.server_url,
                ping_interval=None,
                max_size=None,
                ssl=self._get_ssl_context(),
            ) as ws:
                logger.debug("FunASR WebSocket 连接已建立")

                # ---- 发送首帧 (JSON 配置) ----
                first_frame = json.dumps({
                    "mode": self.mode,
                    "chunk_size": [5, 10, 5],  # [lookback, chunk, lookahead] in 60ms
                    "chunk_interval": 10,
                    "wav_name": "voice_assistant",
                    "wav_format": "pcm",
                    "is_speaking": True,
                    "hotwords": self.hotwords,
                    "itn": self.use_itn,
                })
                await ws.send(first_frame)
                logger.debug("已发送首帧配置: mode=%s", self.mode)

                # ---- 启动接收任务 ----
                result_holder = {"final": "", "partial": ""}
                receive_done = asyncio.Event()

                async def receive_results():
                    """持续接收识别结果。"""
                    nonlocal final_text
                    try:
                        async for message in ws:
                            try:
                                result = json.loads(message)
                            except json.JSONDecodeError:
                                logger.warning(
                                    "FunASR 返回了非 JSON 消息: %s",
                                    message[:200],
                                )
                                continue

                            mode = result.get("mode", "")
                            text = result.get("text", "")
                            is_final = result.get("is_final", False)

                            if mode == "2pass-online" and text:
                                # 在线识别中间结果
                                result_holder["partial"] = text
                                logger.info("FunASR 中间结果: %s", text)
                                if on_partial:
                                    on_partial(text)

                            elif mode == "2pass-offline" and text:
                                # 2pass 离线纠错最终结果
                                result_holder["final"] = text
                                logger.info("FunASR 最终结果: %s", text)
                                receive_done.set()
                                return

                            elif mode == "offline" and text:
                                # 纯离线模式结果 — 收到即完成
                                result_holder["final"] = text
                                logger.info("FunASR 离线结果: %s", text)
                                receive_done.set()
                                return

                            elif mode == "online" and text:
                                # 纯在线模式，持续更新
                                result_holder["partial"] = text
                                if on_partial:
                                    on_partial(text)

                            if is_final:
                                logger.debug("FunASR 标记 is_final=True")
                                receive_done.set()
                                return

                    except websockets.ConnectionClosed:
                        logger.debug("FunASR WebSocket 连接关闭")
                    finally:
                        receive_done.set()

                recv_task = asyncio.create_task(receive_results())

                # ---- 发送音频数据 ----
                total_bytes = 0
                chunk_count = 0
                try:
                    async for audio_chunk in audio_generator:
                        if audio_chunk:
                            await ws.send(audio_chunk)
                            total_bytes += len(audio_chunk)
                            chunk_count += 1

                            # 每 50 个块输出一次统计 (约 1.6 秒 @ 512 帧)
                            if chunk_count % 50 == 0:
                                logger.debug(
                                    "已发送 %d 个音频块, 共 %.1f KB",
                                    chunk_count, total_bytes / 1024,
                                )
                except Exception as e:
                    logger.error("发送音频数据时出错: %s", e)

                logger.info(
                    "音频发送完成 (共 %d 块, %.1f KB)",
                    chunk_count, total_bytes / 1024,
                )

                # ---- 发送结束帧 ----
                end_frame = json.dumps({
                    "is_speaking": False,
                })
                await ws.send(end_frame)
                logger.debug("已发送结束帧")

                # ---- 等待最终结果 ----
                try:
                    await asyncio.wait_for(receive_done.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning("等待 FunASR 最终结果超时 (10s)")

                # 取消接收任务
                recv_task.cancel()
                try:
                    await recv_task
                except asyncio.CancelledError:
                    pass

                # 优先使用最终结果，否则用中间结果
                final_text = result_holder["final"] or result_holder["partial"]

        except websockets.InvalidURI as e:
            logger.error("FunASR 服务地址无效: %s", e)
        except (ConnectionRefusedError, OSError) as e:
            logger.error(
                "无法连接 FunASR 服务 (%s)，请确认 Docker 服务已启动: %s",
                self.server_url, e,
            )
        except Exception as e:
            logger.error("FunASR 识别过程出错: %s", e, exc_info=True)

        elapsed = time.time() - start_time
        if final_text:
            logger.info("识别完成 (耗时 %.2fs): %s", elapsed, final_text)
        else:
            logger.warning("识别完成但无结果 (耗时 %.2fs)", elapsed)

        return final_text

    async def check_connection(self) -> bool:
        """
        检查 FunASR 服务是否可连接。

        Returns
        -------
        bool
            True 表示服务可用
        """
        try:
            async with websockets.connect(
                self.server_url,
                ping_interval=None,
                open_timeout=3,
                ssl=self._get_ssl_context(),
            ) as ws:
                await ws.close()
            logger.info("FunASR 服务连接正常: %s", self.server_url)
            return True
        except Exception as e:
            logger.error("FunASR 服务不可用 (%s): %s", self.server_url, e)
            return False
