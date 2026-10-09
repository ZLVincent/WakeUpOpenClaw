"""RSS 技能的确认流程、播放和路由契约。"""

import asyncio
from unittest.mock import AsyncMock


def run(coro):
    return asyncio.run(coro)


def rss_config():
    return {
        "music": {"actions": {"play": {"keywords": ["播放"]}}},
        "rss": {
            "enabled": True,
            "options": {"confirmation_ttl_seconds": 600},
            "actions": {
                "query_rss": {"keywords": ["播客", "RSS"]},
                "add_rss_subscription": {"keywords": ["订阅播客", "添加 RSS"]},
                "confirm_rss_subscription": {"keywords": ["确认订阅"]},
                "remove_rss_subscription": {"keywords": ["删除播客"]},
                "confirm_remove_rss_subscription": {"keywords": ["确认删除订阅"]},
                "play_rss_result": {"keywords": ["播放第"]},
            },
        },
    }


def test_rss_selection_index_accepts_chinese_ordinals_and_keeps_arabic_numbers():
    """所有 RSS 确认/播放命令应共享同一套一基序号到零基索引解析。"""
    from skills.actions_rss import RssActionsMixin

    assert RssActionsMixin._rss_index("确认订阅第一个") == 0
    assert RssActionsMixin._rss_index("确认删除订阅第二个") == 1
    assert RssActionsMixin._rss_index("播放第十个") == 9
    assert RssActionsMixin._rss_index("确认订阅第 2 个") == 1
    assert RssActionsMixin._rss_index("播放第10个") == 9


def test_longest_keyword_wins_over_generic_music_play_for_rss_result():
    """“播放第 2 个”必须命中 RSS，而不是 music 的通用“播放”。"""
    from skills.router import SkillRouter

    router = SkillRouter(skills_config=rss_config())

    async def play_result(skill, action, text):
        return router._make_result("正在播放", action.name, skill.name)

    router._action_handlers["play_rss_result"] = play_result
    result = run(router.match("播放第 2 个"))

    assert result.skill == "rss"
    assert result.action == "play_rss_result"


def test_subscription_add_and_delete_require_explicit_confirmation():
    """候选订阅和删除项只在对应确认指令后持久化。"""
    from skills.router import SkillRouter

    service = type("Service", (), {})()
    service.find_subscription_candidates = AsyncMock(return_value=[
        {"name": "测试播客", "rss_url": "https://feeds.example/show.xml"},
    ])
    service.add_subscription = AsyncMock()
    service.list_subscriptions = AsyncMock(return_value=[
        {"id": 8, "name": "测试播客", "rss_url": "https://feeds.example/show.xml"},
    ])
    service.remove_subscription = AsyncMock()

    router = SkillRouter(skills_config=rss_config())
    router.rss_service = service

    proposed = run(router.match("订阅播客 测试播客"))
    assert "确认订阅第 1 个" in proposed.text
    service.add_subscription.assert_not_awaited()

    confirmed = run(router.match("确认订阅第 1 个"))
    assert confirmed.skill == "rss"
    service.add_subscription.assert_awaited_once_with("测试播客", "https://feeds.example/show.xml")

    proposed_delete = run(router.match("删除播客 测试播客"))
    assert "确认删除订阅第 1 个" in proposed_delete.text
    service.remove_subscription.assert_not_awaited()

    run(router.match("确认删除订阅第 1 个"))
    service.remove_subscription.assert_awaited_once_with(8)


def test_query_cache_allows_playing_selected_number_and_stops_music():
    """查询后十分钟内可按序号播放，同时先停止本地音乐。"""
    from skills.router import SkillRouter

    service = type("Service", (), {})()
    service.search = AsyncMock(return_value=[
        {"title": "第一期", "media_url": "https://cdn.example/1.mp3"},
        {"title": "第二期", "media_url": "https://cdn.example/2.mp3"},
    ])
    player = type("Player", (), {"is_playing": True, "stop": AsyncMock(), "play_url": AsyncMock()})()
    router = SkillRouter(skills_config=rss_config(), music_player=player)
    router.rss_service = service

    listed = run(router.match("帮我找 RSS 播客"))
    assert "1." in listed.text and "2." in listed.text

    played = run(router.match("播放第 2 个"))
    assert "第二期" in played.text
    player.stop.assert_awaited_once()
    player.play_url.assert_awaited_once_with("https://cdn.example/2.mp3", title="第二期")


def test_stop_action_stops_rss_playback_even_without_local_playlist():
    """统一的停止指令应终止 mpv，即使播放器没有本地歌曲列表。"""
    from skills.router import SkillRouter

    player = type("Player", (), {"is_playing": True, "stop": AsyncMock()})()
    router = SkillRouter(
        skills_config={"music": {"actions": {"stop": {"keywords": ["停止播放"]}}}},
        music_player=player,
    )
    result = run(router.match("停止播放"))
    assert result.action == "stop"
    player.stop.assert_awaited_once()
