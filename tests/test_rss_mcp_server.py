"""播放播客 MCP 的最小行为契约。"""

import asyncio
from pathlib import Path
import re


def run(coro):
    return asyncio.run(coro)


def test_rss_mcp_advertises_subscription_query_and_safe_play_tools():
    """MCP 仅暴露按主题播放，绝不能接受调用方给出的音频 URL。"""
    from mcp import rss_server

    tools = {tool.name: tool for tool in run(rss_server.list_tools())}
    assert {"list_rss_subscriptions", "query_latest_rss", "play_latest_rss"} <= tools.keys()

    play_schema = tools["play_latest_rss"].inputSchema
    assert "topic" in play_schema["properties"]
    assert "media_url" not in play_schema["properties"]


def test_play_latest_rss_forwards_only_topic_to_local_http_api(monkeypatch):
    """服务端决定“主题命中或全局最新”回退；MCP 不接收可控媒体地址。"""
    from mcp import rss_server

    calls = []

    async def fake_post(path, data):
        calls.append((path, data))
        return {
            "episode": {"title": "全球最新早间节目", "subscription_name": "新闻台"},
            "matched": False,
        }

    monkeypatch.setattr(rss_server, "api_post", fake_post)
    result = run(rss_server._dispatch_tool("play_latest_rss", {
        "topic": "早间新闻",
        "media_url": "https://attacker.example/untrusted.mp3",
    }))

    assert calls and calls[0][1] == {"topic": "早间新闻"}
    assert "全球最新早间节目" in result


def test_rss_mcp_queries_subscriptions_and_latest_entries_via_local_http(monkeypatch):
    """订阅和最新节目查询均应委托主服务的本机 HTTP API。"""
    from mcp import rss_server

    paths = []

    async def fake_get(path, params=None):
        paths.append((path, params))
        if "subscription" in path:
            return {"subscriptions": [{"name": "忽左忽右", "rss_url": "https://example.test/feed"}]}
        return {"episodes": [{"title": "最新一期", "subscription_name": "忽左忽右"}]}

    monkeypatch.setattr(rss_server, "api_get", fake_get)
    subscriptions = run(rss_server._dispatch_tool("list_rss_subscriptions", {}))
    latest = run(rss_server._dispatch_tool("query_latest_rss", {"topic": "科技"}))

    assert "忽左忽右" in subscriptions
    assert "最新一期" in latest
    assert len(paths) == 2


def test_play_morning_news_is_not_captured_by_generic_local_music_keyword():
    """普通自然语言播放请求应交由 OpenClaw 决定是否调用播客 MCP。"""
    from skills.router import SkillRouter

    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    config_text = config_path.read_text(encoding="utf-8")
    music_section = re.search(
        r"^  music:\n(.*?)(?=^  [a-z_]+:\n)", config_text, re.MULTILINE | re.DOTALL,
    ).group(1)
    music_keywords = re.findall(r"^        - (.+)$", music_section, re.MULTILINE)
    router = SkillRouter(skills_config={
        "music": {"actions": {"play": {"keywords": music_keywords}}},
    })

    assert run(router.match("播放早间新闻")) is None
