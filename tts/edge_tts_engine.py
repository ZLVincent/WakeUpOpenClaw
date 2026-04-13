"""
edge-tts 语音合成引擎模块

使用微软 Edge TTS 将文本转换为语音并播放。
免费、无需 API Key、中文效果好。
"""

import asyncio
import os
import re
import tempfile
import time
from typing import Optional

import edge_tts

from utils.logger import get_logger

logger = get_logger("tts")


class EdgeTTSEngine:
    """
    基于 edge-tts 的语音合成引擎。

    Parameters
    ----------
    voice : str
        语音名称，如 "zh-CN-XiaoxiaoNeural"
    rate : str
        语速调整，如 "+10%", "-20%", "+0%"
    volume : str
        音量调整，如 "+10%", "-20%", "+0%"
    player : str
        播放器命令，如 "mpv", "aplay"
    player_args : list[str]
        播放器额外参数
    proxy : str | None
        HTTP/SOCKS 代理地址，如 "http://127.0.0.1:7890"
    """

    def __init__(
        self,
        voice: str = "zh-CN-XiaoxiaoNeural",
        rate: str = "+0%",
        volume: str = "+0%",
        player: str = "mpv",
        player_args: Optional[list[str]] = None,
        proxy: Optional[str] = None,
    ):
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.player = player
        self.player_args = player_args or ["--no-terminal", "--really-quiet"]
        self.proxy = proxy

        if self.proxy:
            logger.info("TTS 使用代理: %s", self.proxy)

        # 临时文件目录
        self._temp_dir = tempfile.mkdtemp(prefix="wakeup_tts_")
        logger.debug("TTS 临时目录: %s", self._temp_dir)

    async def synthesize(self, text: str) -> Optional[str]:
        """
        将文本合成为语音文件。

        Parameters
        ----------
        text : str
            要合成的文本

        Returns
        -------
        str | None
            合成的音频文件路径，失败返回 None
        """
        if not text or not text.strip():
            logger.warning("合成文本为空，跳过")
            return None

        # 清理 Markdown 和特殊符号，使文本适合语音朗读
        text = self._clean_for_speech(text)

        start_time = time.time()
        text_preview = text[:50] + "..." if len(text) > 50 else text
        logger.info("开始合成语音: \"%s\" (共%d字)", text_preview, len(text))

        # 生成临时文件路径
        temp_file = os.path.join(
            self._temp_dir,
            f"tts_{int(time.time() * 1000)}.mp3",
        )

        try:
            communicate = edge_tts.Communicate(
                text=text,
                voice=self.voice,
                rate=self.rate,
                volume=self.volume,
                proxy=self.proxy,
            )
            await communicate.save(temp_file)

            file_size = os.path.getsize(temp_file)
            elapsed = time.time() - start_time
            logger.info(
                "语音合成完成 (%.2fs, %.1f KB): %s",
                elapsed, file_size / 1024, temp_file,
            )
            return temp_file

        except Exception as e:
            logger.error("语音合成失败: %s", e, exc_info=True)
            return None

    async def play(self, audio_file: str) -> bool:
        """
        播放音频文件。

        Parameters
        ----------
        audio_file : str
            音频文件路径

        Returns
        -------
        bool
            播放是否成功
        """
        if not os.path.exists(audio_file):
            logger.error("音频文件不存在: %s", audio_file)
            return False

        cmd = [self.player] + self.player_args + [audio_file]
        logger.debug("播放命令: %s", " ".join(cmd))
        logger.info("开始播放语音...")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()

            if process.returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace").strip()
                logger.error(
                    "播放器返回错误 (code=%d): %s",
                    process.returncode, stderr_text,
                )
                return False

            logger.info("语音播放完成")
            return True

        except FileNotFoundError:
            logger.error(
                "播放器 '%s' 不可用，请安装: sudo apt install %s",
                self.player, self.player,
            )
            return False
        except Exception as e:
            logger.error("播放音频时出错: %s", e, exc_info=True)
            return False

    async def speak(self, text: str) -> bool:
        """
        合成语音并播放（一步到位）。

        Parameters
        ----------
        text : str
            要朗读的文本

        Returns
        -------
        bool
            是否成功
        """
        audio_file = await self.synthesize(text)
        if audio_file is None:
            return False

        success = await self.play(audio_file)

        # 播放完成后清理临时文件
        try:
            os.remove(audio_file)
            logger.debug("已清理临时文件: %s", audio_file)
        except OSError as e:
            logger.debug("清理临时文件失败: %s", e)

        return success

    async def check_available(self) -> bool:
        """
        检查 TTS 引擎和播放器是否可用。

        Returns
        -------
        bool
            True 表示可用
        """
        # 检查播放器
        try:
            process = await asyncio.create_subprocess_exec(
                self.player, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(process.communicate(), timeout=3)
            logger.info("播放器 '%s' 可用", self.player)
        except FileNotFoundError:
            logger.error("播放器 '%s' 不可用", self.player)
            return False
        except Exception as e:
            logger.warning("检查播放器时出错: %s", e)
            return False

        # 快速测试 edge-tts
        try:
            voices = await edge_tts.list_voices(proxy=self.proxy)
            voice_names = [v["Name"] for v in voices]
            if self.voice in voice_names:
                logger.info("TTS 语音 '%s' 可用", self.voice)
            else:
                logger.warning(
                    "TTS 语音 '%s' 不在可用列表中，可能导致合成失败",
                    self.voice,
                )
                # 列出部分中文语音供参考
                zh_voices = [v for v in voice_names if v.startswith("zh-CN")]
                if zh_voices:
                    logger.info("可用的中文语音: %s", ", ".join(zh_voices[:5]))
            return True
        except Exception as e:
            logger.error("edge-tts 不可用: %s", e)
            return False

    def cleanup(self) -> None:
        """清理临时文件目录。"""
        try:
            import shutil
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            logger.debug("已清理 TTS 临时目录: %s", self._temp_dir)
        except Exception as e:
            logger.debug("清理临时目录失败: %s", e)

    @staticmethod
    def _clean_for_speech(text: str) -> str:
        """
        清理文本中不适合语音朗读的符号和格式。

        去除 Markdown 格式符号、emoji、多余空白等，
        使文本更适合 TTS 语音合成。
        """
        # 去除 Markdown 加粗/斜体: **text** -> text, *text* -> text
        text = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", text)
        # 去除 Markdown 删除线: ~~text~~ -> text
        text = re.sub(r"~~(.+?)~~", r"\1", text)
        # 去除 Markdown 行内代码: `code` -> code
        text = re.sub(r"`(.+?)`", r"\1", text)
        # 去除 Markdown 标题符号: ### title -> title
        text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
        # 去除 Markdown 列表符号: - item / * item -> item
        text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
        # 去除有序列表数字: 1. item -> item
        text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
        # 去除 Markdown 链接: [text](url) -> text
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        # 去除 Markdown 图片: ![alt](url) -> alt
        text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
        # 去除 HTML 标签: <br> <br/> 等
        text = re.sub(r"<[^>]+>", "", text)
        # 去除 emoji (Unicode emoji 范围)
        text = re.sub(
            r"[\U0001F300-\U0001F9FF\U00002702-\U000027B0"
            r"\U0000FE00-\U0000FE0F\U0000200D]+",
            "", text,
        )
        # 合并多个空行为一个
        text = re.sub(r"\n{3,}", "\n\n", text)
        # 去除行首尾多余空白
        text = "\n".join(line.strip() for line in text.split("\n"))

        return text.strip()
