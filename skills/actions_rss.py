"""RSS 播客技能动作。"""

import re
import time


class RssActionsMixin:
    """通过 SkillRouter 注入的 RSS 操作处理器。"""

    def _rss_service_or_result(self, action):
        if not getattr(self, "rss_service", None):
            return self._make_result("RSS 播客功能不可用，请检查数据库和网络配置", action.name, "rss")
        return None

    @staticmethod
    def _rss_index(text: str):
        match = re.search(r"第\s*(\d+)\s*个?", text or "")
        if match:
            return int(match.group(1)) - 1

        chinese_match = re.search(r"第\s*([一二三四五六七八九十]+)\s*个", text or "")
        if not chinese_match:
            return None
        numeral = chinese_match.group(1)
        digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if numeral == "十":
            return 9
        if len(numeral) == 1 and numeral in digits:
            return digits[numeral] - 1
        if len(numeral) == 2 and numeral[0] == "十" and numeral[1] in digits:
            return 10 + digits[numeral[1]] - 1
        if len(numeral) == 2 and numeral[0] in digits and numeral[1] == "十":
            return digits[numeral[0]] * 10 - 1
        if len(numeral) == 3 and numeral[0] in digits and numeral[1] == "十" and numeral[2] in digits:
            return digits[numeral[0]] * 10 + digits[numeral[2]] - 1
        return None

    def _rss_query_after_keyword(self, text: str, keywords: list[str]) -> str:
        for keyword in sorted(keywords, key=len, reverse=True):
            pos = text.lower().find(keyword.lower())
            if pos >= 0:
                return text[pos + len(keyword):].strip(" ：:，,。")
        return text.strip()

    def _rss_pending_valid(self, key: str, ttl: int) -> bool:
        return bool(getattr(self, key, None)) and time.monotonic() - getattr(self, key + "_at", 0) <= ttl

    async def _action_list_rss_subscriptions(self, skill, action, user_text=""):
        unavailable = self._rss_service_or_result(action)
        if unavailable:
            return unavailable
        try:
            subscriptions = await self.rss_service.list_subscriptions()
        except Exception:
            return self._make_result("RSS 订阅数据库不可用", action.name, "rss")
        names = [item.get("name", "未命名") for item in subscriptions if item.get("enabled", 1)]
        return self._make_result("当前订阅：" + "、".join(names) if names else "当前没有播客订阅", action.name, "rss")

    async def _action_query_rss(self, skill, action, user_text=""):
        unavailable = self._rss_service_or_result(action)
        if unavailable:
            return unavailable
        try:
            episodes = await self.rss_service.search(self._rss_query_after_keyword(user_text, action.keywords))
        except Exception:
            return self._make_result("RSS 订阅数据库不可用", action.name, "rss")
        if not episodes:
            return self._make_result("没有找到可播放的最新播客节目", action.name, "rss")
        self._rss_last_results, self._rss_last_results_at = episodes, time.monotonic()
        lines = ["找到以下最新节目："]
        for index, episode in enumerate(episodes, 1):
            source = episode.get("subscription_name")
            lines.append(f"{index}. {episode.get('title', '未命名')}" + (f"，来自{source}" if source else ""))
        summary = episodes[0].get("selection_summary")
        if summary:
            lines.append("摘要：" + summary)
        lines.append("请说播放第几个")
        return self._make_result("\n".join(lines), action.name, "rss")

    async def _action_add_rss_subscription(self, skill, action, user_text=""):
        unavailable = self._rss_service_or_result(action)
        if unavailable:
            return unavailable
        query = self._rss_query_after_keyword(user_text, action.keywords)
        if not query:
            return self._make_result("请说出想订阅的播客名称", action.name, "rss")
        candidates = await self.rss_service.find_subscription_candidates(query)
        if not candidates:
            return self._make_result("没有找到可验证的 RSS 地址，请换个节目名称再试", action.name, "rss")
        self._rss_pending_add, self._rss_pending_add_at = candidates, time.monotonic()
        lines = ["找到以下可订阅节目："] + [f"{index}. {item['name']}" for index, item in enumerate(candidates, 1)]
        lines.append("请说确认订阅第 1 个")
        return self._make_result("\n".join(lines), action.name, "rss")

    async def _action_confirm_rss_subscription(self, skill, action, user_text=""):
        ttl, index = int(skill.options.get("confirmation_ttl_seconds", 300)), self._rss_index(user_text)
        if not self._rss_pending_valid("_rss_pending_add", ttl) or index is None or not 0 <= index < len(self._rss_pending_add):
            return self._make_result("没有有效的待确认订阅，请先搜索播客", action.name, "rss")
        candidate = self._rss_pending_add[index]
        try:
            saved = await self.rss_service.add_subscription(candidate["name"], candidate["rss_url"])
        except Exception:
            saved = False
        if saved:
            self._rss_pending_add = []
            return self._make_result(f"已订阅{candidate['name']}", action.name, "rss")
        return self._make_result("订阅保存失败，请检查数据库", action.name, "rss")

    async def _action_remove_rss_subscription(self, skill, action, user_text=""):
        unavailable = self._rss_service_or_result(action)
        if unavailable:
            return unavailable
        query = self._rss_query_after_keyword(user_text, action.keywords).lower()
        try:
            subscriptions = await self.rss_service.list_subscriptions()
        except Exception:
            return self._make_result("RSS 订阅数据库不可用", action.name, "rss")
        matches = [item for item in subscriptions if query and query in item.get("name", "").lower()]
        if not matches:
            return self._make_result("没有找到匹配的播客订阅", action.name, "rss")
        self._rss_pending_remove, self._rss_pending_remove_at = matches, time.monotonic()
        lines = ["请选择要删除的订阅："] + [f"{index}. {item.get('name', '未命名')}" for index, item in enumerate(matches, 1)]
        lines.append("请说确认删除订阅第 1 个")
        return self._make_result("\n".join(lines), action.name, "rss")

    async def _action_confirm_remove_rss_subscription(self, skill, action, user_text=""):
        ttl, index = int(skill.options.get("confirmation_ttl_seconds", 300)), self._rss_index(user_text)
        if not self._rss_pending_valid("_rss_pending_remove", ttl) or index is None or not 0 <= index < len(self._rss_pending_remove):
            return self._make_result("没有有效的待删除订阅，请先选择播客", action.name, "rss")
        candidate = self._rss_pending_remove[index]
        try:
            removed = await self.rss_service.remove_subscription(candidate["id"])
        except Exception:
            removed = False
        if removed:
            self._rss_pending_remove = []
            return self._make_result(f"已删除订阅{candidate.get('name', '')}", action.name, "rss")
        return self._make_result("删除订阅失败，请检查数据库", action.name, "rss")

    async def _action_play_rss_result(self, skill, action, user_text=""):
        index, ttl = self._rss_index(user_text), int(skill.options.get("result_ttl_seconds", 600))
        if not self._rss_pending_valid("_rss_last_results", ttl) or index is None or not 0 <= index < len(self._rss_last_results):
            return self._make_result("请先查询播客，再说播放第几个", action.name, "rss")
        if not self.music_player:
            return self._make_result("本地播放器不可用", action.name, "rss")
        episode = self._rss_last_results[index]
        proxy_builder = getattr(self.rss_service, "prepare_media_url", None)
        media_url = episode["media_url"]
        if proxy_builder:
            try:
                media_url = await proxy_builder(media_url)
            except Exception:
                return self._make_result("该播客音频地址不安全或无法访问", action.name, "rss")
        if self.music_player.is_playing:
            await self.music_player.stop()
        await self.music_player.play_url(media_url, title=episode.get("title", "播客节目"))
        return self._make_result(f"正在播放{episode.get('title', '播客节目')}", action.name, "rss")
