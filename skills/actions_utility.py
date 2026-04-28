"""
通用工具技能动作 Mixin

包含 conversation 和 utility 技能的所有操作处理器：
新建对话、报时、系统重启、系统状态、IP 地址、网络状态、晨间简报。
"""

import asyncio
import datetime
import subprocess
from utils.logger import get_logger

logger = get_logger("skills")


class UtilityActionsMixin:
    """通用工具技能动作。"""

    # --- conversation ---

    async def _action_new_conversation(self, skill, action, user_text=""):
        """新建对话（实际操作由 main.py 根据 action 名执行）。"""
        return self._make_result(action.reply or "好的，已开启新对话", "new_conversation", "conversation")

    # --- utility ---

    async def _action_current_time(self, skill, action, user_text=""):
        """报时。"""
        now = datetime.datetime.now()
        return self._make_result(now.strftime("现在是%H点%M分"), "current_time", "utility")

    async def _action_reboot(self, skill, action, user_text=""):
        """重启系统（仅提示，需二次确认）。"""
        return self._make_result(
            action.reply or "确认要重启系统吗？请说确认重启来执行",
            "reboot", "utility",
        )

    async def _action_confirm_reboot(self, skill, action, user_text=""):
        """确认重启系统（真正执行）。"""
        logger.info("收到确认重启系统指令，即将执行 sudo reboot")
        subprocess.Popen(
            ["sudo", "reboot"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return self._make_result(action.reply or "好的，系统即将重启", "confirm_reboot", "utility")

    async def _action_system_status(self, skill, action, user_text=""):
        """查看系统状态（CPU、内存、温度、磁盘、运行时间）。"""
        from utils.system_info import get_system_info

        info = get_system_info()
        lines = ["当前系统状态。"]
        lines.append(f"CPU使用率{info['cpu_percent']:.0f}%。")
        if info["cpu_temp"] is not None:
            lines.append(f"CPU温度{info['cpu_temp']:.0f}度。")

        mem_used_gb = info["mem_used_bytes"] / (1024 ** 3)
        mem_total_gb = info["mem_total_bytes"] / (1024 ** 3)
        lines.append(f"内存已使用{mem_used_gb:.1f}G，共{mem_total_gb:.1f}G，使用率{info['mem_percent']:.0f}%。")

        disk_used_gb = info["disk_used_bytes"] / (1024 ** 3)
        disk_total_gb = info["disk_total_bytes"] / (1024 ** 3)
        lines.append(f"磁盘已使用{disk_used_gb:.0f}G，共{disk_total_gb:.0f}G，使用率{info['disk_percent']:.0f}%。")

        uptime = info["uptime_seconds"]
        days = int(uptime // 86400)
        hours = int((uptime % 86400) // 3600)
        minutes = int((uptime % 3600) // 60)
        lines.append(f"系统已运行{days}天{hours}小时。" if days > 0 else f"系统已运行{hours}小时{minutes}分钟。")

        return self._make_result("\n".join(lines), "system_status", "utility")

    async def _action_ip_address(self, skill, action, user_text=""):
        """查询本机 IP 地址。"""
        from utils.system_info import get_ip_info

        info = get_ip_info()
        lines = ["当前网络地址。"]
        if info["interfaces"]:
            for iface in info["interfaces"]:
                lines.append(f"{iface['name']}，{iface['ip']}。")
        else:
            lines.append("未检测到有效的局域网IP。")
        if info["hostname"]:
            lines.append(f"主机名，{info['hostname']}。")
        return self._make_result("\n".join(lines), "ip_address", "utility")

    async def _action_network_status(self, skill, action, user_text=""):
        """检查网络连通性（百度 ping，谷歌 curl + 代理）。"""
        from utils.system_info import check_network

        proxy = skill.options.get("proxy", "")
        results = await check_network(proxy=proxy)
        lines = ["网络连通性检测。"]
        for r in results:
            name = r["name"]
            if r["reachable"]:
                ms = f"延迟{r['latency_ms']:.0f}毫秒" if r["latency_ms"] is not None else ""
                lines.append(f"{name}，正常{'，' + ms if ms else ''}。")
            else:
                lines.append(f"{name}，{r.get('error', '不通')}。")
        return self._make_result("\n".join(lines), "network_status", "utility")

    async def _action_morning_briefing(self, skill, action, user_text=""):
        """晨间简报：天气 + 今日头条 + 财经 + 娱乐 + 笑话。"""
        if not self.agent_client:
            return self._make_result("晨间简报功能不可用，AI 未配置", "morning_briefing", "utility")

        default_city = skill.options.get("city", "上海")
        city = default_city
        cleaned = self._PUNCTUATION_RE.sub("", user_text)
        for kw in action.keywords:
            if kw in cleaned:
                remainder = cleaned.replace(kw, "", 1).strip()
                if remainder and len(remainder) <= 10:
                    city = remainder
                break

        weather_text = await self._fetch_weather(city)

        today = datetime.date.today().strftime("%Y年%m月%d日")
        weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][datetime.date.today().weekday()]
        prompt = (
            f"现在是早晨的问候时间。请用中文为用户生成一份简洁的晨间简报，适合语音播报。\n"
            f"今天是{today}，{weekday}，{city}天气：{weather_text}。\n\n"
            f"请严格按以下顺序和格式生成内容（每个类别不超过200字）：\n"
            f"1. 今日头条：前5条国内热点新闻，每条一句话简介\n"
            f"2. 财经新闻：5条最新财经要点，每条一句话\n"
            f"3. 娱乐新闻：5条娱乐圈动态，每条一句话\n"
            f"4. 最后讲一个简短的笑话\n\n"
            f"要求：开头先说\"早上好主人\"并报告今日日期和天气；"
            f"用口语化自然语言，不要 Markdown 标记；每个类别清晰分段；整体控制在1000字以内"
        )

        logger.info("发送晨间简报请求到 AI (city=%s)", city)
        try:
            reply = await self.agent_client.send_message(prompt, session_id=self.agent_client.session_id)
        except Exception as e:
            logger.error("晨间简报 AI 调用失败: %s", e)
            reply = ""

        if not reply:
            fallback = f"早上好！今天是{today}，{city}天气{weather_text}。AI 暂时不可用，稍后再试。"
            return self._make_result(fallback, "morning_briefing", "utility")
        return self._make_result(reply, "morning_briefing", "utility")

    async def _fetch_weather(self, city: str) -> str:
        """从 wttr.in 获取指定城市的天气简报。"""
        import urllib.parse
        city_encoded = urllib.parse.quote(city)
        url = f"https://wttr.in/{city_encoded}?format=%C+%t&lang=zh"
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-s", "--max-time", "10", url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=12)
            if proc.returncode == 0:
                text = stdout.decode("utf-8", errors="replace").strip()
                if text and "Unknown location" not in text and len(text) < 100:
                    return text
        except asyncio.TimeoutError:
            logger.warning("获取天气超时 (%s)", city)
        except Exception as e:
            logger.warning("获取天气失败 (%s): %s", city, e)
        return "未知"

    async def _action_system_status_web(self, skill, action, user_text=""):
        """系统状态（别名）。"""
        return await self._action_system_status(skill, action, user_text)
