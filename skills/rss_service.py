"""RSS 播客订阅、拉取与 AI 筛选服务。"""

import asyncio
import datetime as dt
import ipaddress
import json
import re
import secrets
import socket
from email.utils import parsedate_to_datetime
from hashlib import sha1
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from aiohttp import web
from aiohttp.abc import AbstractResolver
import feedparser

from utils.logger import get_logger

logger = get_logger("rss")


class RssDatabaseUnavailableError(RuntimeError):
    """RSS 所需的订阅数据库不可用。"""


class _PublicResolver(AbstractResolver):
    """将每个 HTTP 连接绑定到已验证的公网 IP，阻断 DNS rebinding。"""

    async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
        records = await asyncio.get_running_loop().getaddrinfo(
            host, port, family=family, type=socket.SOCK_STREAM,
        )
        answers = []
        for resolved_family, _, _, _, sockaddr in records:
            address = sockaddr[0]
            if not ipaddress.ip_address(address).is_global:
                raise OSError("RSS 主机不能指向本机或私有网络")
            answers.append({"hostname": host, "host": address, "port": port,
                            "family": resolved_family, "proto": 0, "flags": 0})
        if not answers:
            raise OSError("RSS 主机没有可用公网地址")
        return answers

    async def close(self):
        return None


class _RssMediaProxy:
    """只允许已登记 URL 的本地流式代理，供 mpv 安全播放。"""

    def __init__(self, timeout: float):
        self.timeout = timeout
        self._urls: dict[str, str] = {}
        self._runner = None
        self._site = None
        self._port = None

    async def register(self, media_url: str) -> str:
        if not self._runner:
            app = web.Application()
            app.router.add_get("/media/{token}", self._stream)
            self._runner = web.AppRunner(app)
            await self._runner.setup()
            self._site = web.TCPSite(self._runner, "127.0.0.1", 0)
            await self._site.start()
            sockets = self._site._server.sockets
            self._port = sockets[0].getsockname()[1]
        token = secrets.token_urlsafe(24)
        self._urls[token] = media_url
        return f"http://127.0.0.1:{self._port}/media/{token}"

    async def _stream(self, request):
        media_url = self._urls.get(request.match_info["token"])
        if not media_url:
            raise web.HTTPNotFound()
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=self.timeout, sock_read=self.timeout)
        connector = aiohttp.TCPConnector(resolver=_PublicResolver(), ttl_dns_cache=0)
        try:
            async with aiohttp.ClientSession(timeout=timeout, connector=connector, trust_env=False) as session:
                async with session.get(media_url, allow_redirects=True, max_redirects=3) as upstream:
                    upstream.raise_for_status()
                    headers = {key: value for key, value in upstream.headers.items()
                               if key.lower() in {"content-type", "content-length", "accept-ranges"}}
                    response = web.StreamResponse(status=200, headers=headers)
                    await response.prepare(request)
                    async for chunk in upstream.content.iter_chunked(64 * 1024):
                        await response.write(chunk)
                    await response.write_eof()
                    return response
        except Exception as exc:
            logger.warning("RSS 音频代理失败: %s", exc)
            raise web.HTTPBadGateway()

    async def close(self):
        if self._runner:
            await self._runner.cleanup()
            self._runner = self._site = self._port = None
            self._urls.clear()


class RssService:
    """处理可播放 Podcast RSS 的存储、查询和筛选。"""

    def __init__(self, database, agent_client=None, config: Optional[dict] = None):
        self.db = database
        self.agent_client = agent_client
        self.config = config or {}
        self.request_timeout = float(self.config.get("request_timeout", 15))
        self.max_results = int(self.config.get("max_results", 3))
        self.max_response_bytes = int(self.config.get("max_response_bytes", 2_000_000))
        self.ai_session_id = self.config.get("ai_session_id", "rss-skill")
        self._media_proxy = _RssMediaProxy(self.request_timeout)

    @staticmethod
    def is_safe_public_url(value: str) -> bool:
        """仅接受 http(s) URL，并拒绝显式的本机/私网 IP。"""
        parsed = urlparse(value or "")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        hostname = parsed.hostname.lower()
        if hostname in {"localhost", "localhost.localdomain"}:
            return False
        if hostname.isdecimal():
            return False
        try:
            return ipaddress.ip_address(hostname).is_global
        except ValueError:
            return True

    async def _fetch_feed(self, url: str) -> str:
        if not self.is_safe_public_url(url):
            raise ValueError("RSS 地址必须是公网 http/https URL")
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        connector = aiohttp.TCPConnector(resolver=_PublicResolver(), ttl_dns_cache=0)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector, trust_env=False) as session:
            async with session.get(url, allow_redirects=True, max_redirects=3,
                                   headers={"User-Agent": "WakeUpOpenClaw-RSS/1.0"}) as response:
                response.raise_for_status()
                content = await response.content.read(self.max_response_bytes + 1)
        if len(content) > self.max_response_bytes:
            raise ValueError("RSS 响应过大")
        return content.decode("utf-8", errors="replace")

    @staticmethod
    def _published_at(entry) -> dt.datetime:
        for key in ("published", "updated", "created"):
            value = entry.get(key)
            if not value:
                continue
            try:
                parsed = parsedate_to_datetime(value)
            except (TypeError, ValueError, IndexError):
                try:
                    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.astimezone(dt.timezone.utc)
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)

    def _episodes_from_feed(self, subscription: dict, content: str) -> list[dict]:
        parsed = feedparser.parse(content)
        if getattr(parsed, "bozo", False) and not parsed.entries:
            raise ValueError("RSS XML 无法解析")
        episodes = []
        for entry in parsed.entries:
            media_url = ""
            for enclosure in entry.get("enclosures", []) or []:
                candidate = enclosure.get("href") or enclosure.get("url")
                content_type = (enclosure.get("type") or "").lower()
                if candidate and (content_type.startswith("audio/") or not content_type):
                    media_url = candidate
                    break
            if not media_url:
                for link in entry.get("links", []) or []:
                    if link.get("rel") == "enclosure" and (link.get("type") or "").lower().startswith("audio/"):
                        media_url = link.get("href")
                        break
            if not media_url or not self.is_safe_public_url(media_url):
                continue
            published = self._published_at(entry)
            title = (entry.get("title") or "未命名节目").strip()
            description = re.sub(r"<[^>]+>", "", entry.get("summary") or entry.get("description") or "").strip()
            raw_id = entry.get("id") or entry.get("guid") or entry.get("link") or media_url
            episode_id = sha1(f"{subscription.get('rss_url', '')}|{raw_id}".encode("utf-8")).hexdigest()
            episodes.append({"id": episode_id, "title": title, "description": description,
                             "media_url": media_url, "published_at": published.isoformat(),
                             "subscription_id": subscription.get("id"),
                             "subscription_name": subscription.get("name", "播客")})
        return episodes

    async def fetch_latest(self) -> list[dict]:
        if not self.db:
            return []
        try:
            subscriptions = await self.db.list_rss_subscriptions(enabled_only=True)
        except Exception as exc:
            logger.warning("读取 RSS 订阅失败: %s", exc)
            raise RssDatabaseUnavailableError("RSS 订阅数据库不可用") from exc
        episodes = []
        for subscription in subscriptions:
            try:
                content = await self._fetch_feed(subscription.get("rss_url", ""))
                episodes.extend(self._episodes_from_feed(subscription, content))
            except Exception as exc:
                logger.warning("RSS 拉取失败 (%s): %s", subscription.get("name", ""), exc)
        episodes.sort(key=lambda item: item["published_at"], reverse=True)
        return episodes

    @staticmethod
    def _json_object(value: str) -> dict:
        match = re.search(r"\{.*\}", value or "", re.S)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _local_select(self, query: str, episodes: list[dict]) -> list[dict]:
        ignored = {"播客", "rss", "最新", "帮我", "查询", "搜索", "找"}
        terms = [term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]+", query or "") if term.lower() not in ignored]
        if not terms:
            return episodes[:self.max_results]
        matches = [episode for episode in episodes if any(
            term in f"{episode.get('title', '')} {episode.get('description', '')} {episode.get('subscription_name', '')}".lower()
            for term in terms)]
        return (matches or episodes)[:self.max_results]

    def _topic_matches(self, topic: str, episodes: list[dict]) -> list[dict]:
        """只返回主题实际命中的条目，不把回退逻辑混入匹配结果。"""
        ignored = {"播客", "rss", "最新", "帮我", "查询", "搜索", "找", "播放", "听"}
        terms = [term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]+", topic or "")
                 if term.lower() not in ignored]
        if not terms:
            return episodes[:self.max_results]
        return [episode for episode in episodes if any(
            term in f"{episode.get('title', '')} {episode.get('description', '')} {episode.get('subscription_name', '')}".lower()
            for term in terms)]

    async def latest_for_topic(self, topic: str, max_results: int = None) -> tuple[list[dict], bool]:
        """确定性地按主题取最新条目；无命中时返回全局最新作为回退。"""
        episodes = await self.fetch_latest()
        limit = max(1, min(int(max_results or self.max_results), 10))
        if not topic.strip():
            return episodes[:limit], False
        matches = self._topic_matches(topic, episodes)
        if matches:
            return matches[:limit], True
        return episodes[:limit], False

    async def select_episodes(self, query: str, episodes: list[dict]) -> list[dict]:
        if not episodes:
            return []
        if not self.agent_client:
            return self._local_select(query, episodes)
        candidates = [{"id": item["id"], "title": item.get("title", ""),
                       "description": item.get("description", "")[:300], "published_at": item.get("published_at", ""),
                       "source": item.get("subscription_name", "")} for item in episodes[:30]]
        prompt = ("你是 RSS 播客筛选器。只能从给定条目中选择，不得编造节目。"
                  "返回纯 JSON：{\"episode_ids\":[\"id\"],\"summary\":\"简短中文摘要\"}。"
                  f"用户请求：{query}\n候选条目：{json.dumps(candidates, ensure_ascii=False)}")
        try:
            response = await self.agent_client.send_message(prompt, session_id=self.ai_session_id)
            response_data = self._json_object(response)
            selected_ids = response_data.get("episode_ids", [])
            if isinstance(selected_ids, list):
                by_id = {item["id"]: item for item in episodes}
                selected = [dict(by_id[item_id]) for item_id in selected_ids if item_id in by_id]
                if selected:
                    summary = response_data.get("summary")
                    if isinstance(summary, str) and summary.strip():
                        selected[0]["selection_summary"] = summary.strip()[:300]
                    return selected[:self.max_results]
        except Exception as exc:
            logger.warning("RSS AI 筛选失败，改用本地匹配: %s", exc)
        return self._local_select(query, episodes)

    async def search(self, query: str) -> list[dict]:
        return await self.select_episodes(query, await self.fetch_latest())

    async def list_subscriptions(self) -> list[dict]:
        if not self.db:
            raise RssDatabaseUnavailableError("RSS 订阅数据库不可用")
        try:
            return await self.db.list_rss_subscriptions(enabled_only=False)
        except Exception as exc:
            raise RssDatabaseUnavailableError("RSS 订阅数据库不可用") from exc

    async def add_subscription(self, name: str, rss_url: str) -> bool:
        if not self.db:
            raise RssDatabaseUnavailableError("RSS 订阅数据库不可用")
        try:
            return bool(await self.db.add_rss_subscription(name=name, rss_url=rss_url))
        except Exception as exc:
            raise RssDatabaseUnavailableError("RSS 订阅数据库不可用") from exc

    async def remove_subscription(self, subscription_id: int) -> bool:
        if not self.db:
            raise RssDatabaseUnavailableError("RSS 订阅数据库不可用")
        try:
            return bool(await self.db.remove_rss_subscription(subscription_id))
        except Exception as exc:
            raise RssDatabaseUnavailableError("RSS 订阅数据库不可用") from exc

    async def prepare_media_url(self, value: str) -> str:
        """登记安全流式代理；mpv 只会访问本机受控端点。"""
        if not self.is_safe_public_url(value):
            raise ValueError("音频地址必须是公网 http/https URL")
        return await self._media_proxy.register(value)

    async def close(self) -> None:
        await self._media_proxy.close()

    async def find_subscription_candidates(self, query: str) -> list[dict]:
        if not self.agent_client or not query:
            return []
        prompt = ("根据用户想订阅的中文播客，寻找公开 Podcast RSS 地址。"
                  "返回纯 JSON：{\"candidates\":[{\"name\":\"节目名\",\"rss_url\":\"https://...\"}]}。"
                  "不知道可靠地址时返回空数组，不能编造。用户请求：" + query)
        try:
            candidates = self._json_object(await self.agent_client.send_message(prompt, session_id=self.ai_session_id)).get("candidates", [])
        except Exception as exc:
            logger.warning("RSS 订阅候选查询失败: %s", exc)
            return []
        valid = []
        for candidate in candidates if isinstance(candidates, list) else []:
            if not isinstance(candidate, dict):
                continue
            name, rss_url = str(candidate.get("name", "")).strip(), str(candidate.get("rss_url", "")).strip()
            if not name or not self.is_safe_public_url(rss_url):
                continue
            try:
                if self._episodes_from_feed({"name": name, "rss_url": rss_url}, await self._fetch_feed(rss_url)):
                    valid.append({"name": name, "rss_url": rss_url})
            except Exception as exc:
                logger.info("候选 RSS 未通过校验 (%s): %s", name, exc)
        return valid[:3]

    async def seed_defaults(self) -> None:
        defaults = self.config.get("seed_subscriptions", [])
        if self.db and defaults:
            await self.db.seed_rss_subscriptions(defaults)
