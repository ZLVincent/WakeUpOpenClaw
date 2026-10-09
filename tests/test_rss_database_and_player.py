"""RSS 订阅持久化和媒体 URL 播放契约。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock


def run(coro):
    return asyncio.run(coro)


def test_database_exposes_subscription_crud_and_idempotent_seed():
    """数据库层负责 RSS URL 去重及默认订阅的幂等初始化。"""
    from storage.database import ChatDatabase

    db = ChatDatabase({"host": "localhost", "database": "test"})
    db._pool = MagicMock()
    db.list_rss_subscriptions = AsyncMock(return_value=[])
    db.add_rss_subscription = AsyncMock(return_value=True)

    defaults = [
        {"name": "忽左忽右", "rss_url": "https://justpodmedia.com/rss/left-right.xml"},
        {"name": "不明白播客", "rss_url": "https://feeds.acast.com/public/shows/68004395b4ef799a7a410371"},
    ]
    run(db.seed_rss_subscriptions(defaults))
    assert db.add_rss_subscription.await_count == 2

    db.list_rss_subscriptions = AsyncMock(return_value=[defaults[0]])
    db.add_rss_subscription.reset_mock()
    run(db.seed_rss_subscriptions(defaults))
    db.add_rss_subscription.assert_awaited_once_with(**defaults[1])


def test_play_url_uses_configured_mpv_and_replaces_existing_playback(monkeypatch):
    """网络音频与本地音乐共用同一个受控 mpv 进程。"""
    from skills.music_player import MusicPlayer

    player = MusicPlayer(database=None, player="mpv-test", player_args=["--quiet"])
    player.stop = AsyncMock()
    scheduled = []
    monkeypatch.setattr("skills.music_player.asyncio.create_task", lambda coro: scheduled.append(coro) or MagicMock(done=lambda: True))

    track = run(player.play_url("https://cdn.example/episode.mp3", title="RSS 第一期"))
    assert track == {"name": "RSS 第一期", "file_path": "https://cdn.example/episode.mp3", "source": "rss"}
    player.stop.assert_awaited_once()
    assert len(scheduled) == 1
    scheduled[0].close()

