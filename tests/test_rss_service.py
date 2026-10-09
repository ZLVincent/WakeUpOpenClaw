"""RSS 播客服务的行为契约测试。"""

import asyncio
from unittest.mock import AsyncMock


def run(coro):
    return asyncio.run(coro)


RSS_WITH_AUDIO_AND_TEXT = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>节目 A</title>
  <item><title>最新音频</title><description>AI 和科技</description>
    <pubDate>Wed, 08 Oct 2026 12:00:00 +0000</pubDate>
    <enclosure url="https://cdn.example/a.mp3" type="audio/mpeg" />
  </item>
  <item><title>只有文字</title><description>不应出现</description>
    <pubDate>Thu, 09 Oct 2026 12:00:00 +0000</pubDate></item>
</channel></rss>"""

ATOM_WITH_AUDIO = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>节目 B</title>
  <entry><title>Atom 音频</title><summary>商业新闻</summary>
    <updated>2026-10-09T08:00:00Z</updated>
    <link rel="enclosure" type="audio/mpeg" href="https://cdn.example/b.mp3" />
  </entry>
</feed>"""


class FakeDatabase:
    def __init__(self):
        self.subscriptions = [
            {"id": 1, "name": "节目 A", "rss_url": "https://feeds.example/a.xml", "enabled": 1},
            {"id": 2, "name": "节目 B", "rss_url": "https://feeds.example/b.xml", "enabled": 1},
        ]

    async def list_rss_subscriptions(self, enabled_only=True):
        return self.subscriptions


def test_fetch_latest_parses_rss_and_atom_filters_non_audio_and_sorts():
    """只保留带 enclosure 的节目，并将不同订阅按发布时间倒序合并。"""
    from skills.rss_service import RssService

    db = FakeDatabase()
    service = RssService(
        database=db,
        config={"request_timeout": 3, "max_results": 3},
    )
    service._fetch_feed = AsyncMock(side_effect=[RSS_WITH_AUDIO_AND_TEXT, ATOM_WITH_AUDIO])

    episodes = run(service.fetch_latest())

    assert [episode["title"] for episode in episodes] == ["Atom 音频", "最新音频"]
    assert [episode["media_url"] for episode in episodes] == [
        "https://cdn.example/b.mp3", "https://cdn.example/a.mp3",
    ]
    assert episodes[0]["subscription_name"] == "节目 B"


def test_fetch_latest_skips_broken_feeds_but_reports_no_episodes_when_all_fail():
    """单个坏 XML 不应阻断其他订阅；所有来源不可解析时不应抛出异常。"""
    from skills.rss_service import RssService

    service = RssService(database=FakeDatabase(), config={"request_timeout": 3})
    service._fetch_feed = AsyncMock(side_effect=["<rss>", RSS_WITH_AUDIO_AND_TEXT])
    assert [episode["title"] for episode in run(service.fetch_latest())] == ["最新音频"]

    service._fetch_feed = AsyncMock(side_effect=TimeoutError("network timeout"))
    assert run(service.fetch_latest()) == []


def test_ai_selection_uses_isolated_session_and_falls_back_to_local_keyword_matching():
    """AI 只可在 rss-skill session 筛选；坏返回和超时退回本地匹配。"""
    from skills.rss_service import RssService

    episodes = [
        {"id": "one", "title": "芯片趋势", "description": "AI 硬件", "published_at": "2026-10-08T00:00:00Z"},
        {"id": "two", "title": "经济观察", "description": "宏观市场", "published_at": "2026-10-09T00:00:00Z"},
    ]
    agent = type("Agent", (), {"send_message": AsyncMock(return_value='{"episode_ids": ["one"]}')})()
    service = RssService(
        database=FakeDatabase(),
        agent_client=agent,
        config={"ai_session_id": "rss-skill", "max_results": 3},
    )

    selected = run(service.select_episodes("找 AI 播客", episodes))
    assert [episode["id"] for episode in selected] == ["one"]
    assert agent.send_message.await_args.kwargs["session_id"] == "rss-skill"

    agent.send_message = AsyncMock(return_value="not json")
    fallback = run(service.select_episodes("AI", episodes))
    assert [episode["id"] for episode in fallback] == ["one"]

