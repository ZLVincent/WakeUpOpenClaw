"""
天气技能动作 Mixin

包含 weather 技能的所有操作处理器：
心知天气查询（今天/明天/后天/三天）。
"""

import asyncio
import json
import urllib.parse
from typing import Optional
from utils.logger import get_logger

logger = get_logger("skills")


class WeatherActionsMixin:
    """天气技能动作。"""

    _DAY_LABELS = ["今天", "明天", "后天"]

    def _extract_weather_days(self, text: str) -> list:
        """从用户输入提取要查询的天数索引列表。"""
        clean = self._PUNCTUATION_RE.sub("", text)
        if any(w in clean for w in ("三天", "未来三天", "三日")):
            return [0, 1, 2]
        result = [i for i, label in enumerate(self._DAY_LABELS) if label in clean]
        return result if result else [0]

    def _extract_weather_location(self, text: str, keywords: list) -> str:
        """从用户输入提取地名。"""
        clean = self._PUNCTUATION_RE.sub("", text.strip())
        for w in ("今天", "明天", "后天", "三天", "未来三天", "三日"):
            clean = clean.replace(w, "")
        for kw in sorted(keywords, key=len, reverse=True):
            clean = clean.replace(kw, "")
        clean = clean.strip()
        return clean if 0 < len(clean) <= 10 else ""

    @staticmethod
    def _analyze_weather(code: int, suggestion_brief: str) -> str:
        """根据天气 code 和运动建议生成口语化建议。"""
        if code <= 8:
            return "天气不错，空气清新，适合出门运动哦" if "适宜" in suggestion_brief else "空气质量比较一般，建议减少出行"
        elif 10 <= code <= 15:
            return "出门记得带伞哦"
        elif code in range(16, 19) or code in range(25, 30) or code in range(34, 37):
            return "极端天气来临，尽量待在屋里"
        elif code == 38:
            return "天气炎热，记得多补充水分哦"
        elif code == 37:
            return "好冷的天，记得穿厚一点哦"
        return ""

    async def _fetch_seniverse(self, url: str, api_key: str, location: str, proxy: str = "") -> Optional[dict]:
        """调用心知天气 API，返回解析后的 JSON 或 None。"""
        params = f"key={urllib.parse.quote(api_key)}&location={urllib.parse.quote(location)}&language=zh-Hans&unit=c"
        full_url = f"{url}?{params}"
        try:
            cmd = ["curl", "-s", "--max-time", "8"]
            if proxy:
                cmd += ["-x", proxy]
            cmd.append(full_url)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                return json.loads(stdout.decode("utf-8", errors="replace").strip())
        except asyncio.TimeoutError:
            logger.warning("心知天气 API 超时: %s", url)
        except Exception as e:
            logger.warning("心知天气 API 请求失败: %s", e)
        return None

    async def _action_query_weather(self, skill, action, user_text=""):
        """查询心知天气（今天/明天/后天/三天）。"""
        api_key = skill.options.get("api_key", "")
        default_location = skill.options.get("location", "上海")
        proxy = skill.options.get("proxy", "")

        if not api_key or api_key.startswith("${"):
            return self._make_result(
                "天气查询未配置 API Key，请设置环境变量 SENIVERSE_API_KEY",
                "query_weather", "weather",
            )

        location = self._extract_weather_location(user_text, action.keywords) or default_location
        days = self._extract_weather_days(user_text)

        DAILY_API = "https://api.seniverse.com/v3/weather/daily.json"
        data = await self._fetch_seniverse(DAILY_API, api_key, location, proxy=proxy)
        if not data or "results" not in data:
            logger.warning("心知天气返回无效数据: %s", data)
            return self._make_result("抱歉，获取不到天气数据，请稍后再试", "query_weather", "weather")

        daily = data["results"][0]["daily"]

        suggestion_text = ""
        SUGGESTION_API = "https://api.seniverse.com/v3/life/suggestion.json"
        sug_data = await self._fetch_seniverse(SUGGESTION_API, api_key, location, proxy=proxy)
        if sug_data and "results" in sug_data:
            try:
                suggestion_text = sug_data["results"][0]["suggestion"]["sport"]["brief"]
            except (KeyError, IndexError):
                pass

        parts = [f"{location}天气，"]
        for idx in days:
            if idx >= len(daily):
                break
            d = daily[idx]
            parts.append(f"{self._DAY_LABELS[idx]}{d.get('text_day', '未知')}，{d.get('low', '?')}到{d.get('high', '?')}度。")

        if suggestion_text and days:
            first_idx = days[0]
            if first_idx < len(daily):
                advice = self._analyze_weather(int(daily[first_idx].get("code_day", 0)), suggestion_text)
                if advice:
                    parts.append(f"{advice}。")

        return self._make_result("".join(parts), "query_weather", "weather")
