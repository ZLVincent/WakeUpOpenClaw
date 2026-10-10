"""RSS 条件性多轮跟进的行为契约。"""

import asyncio
import ast
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from pathlib import Path


def run(coro):
    return asyncio.run(coro)


def rss_config():
    return {
        "rss": {
            "enabled": True,
            "options": {
                "confirmation_ttl_seconds": 300,
                "result_ttl_seconds": 600,
                "followup_wait_timeout": 20,
            },
            "actions": {
                "list_rss_subscriptions": {"keywords": ["RSS列表"]},
                "query_rss": {"keywords": ["搜索播客"]},
                "add_rss_subscription": {"keywords": ["订阅播客"]},
                "confirm_rss_subscription": {"keywords": ["确认订阅"]},
                "remove_rss_subscription": {"keywords": ["删除播客"]},
                "confirm_remove_rss_subscription": {"keywords": ["确认删除订阅"]},
                "play_rss_result": {"keywords": ["播放第"]},
                "cancel_rss_followup": {"keywords": ["取消"]},
            },
        },
    }


def test_only_rss_actions_that_need_another_utterance_request_followup():
    """搜索、添加候选和删除候选提示应进入短暂的 await-followup 状态。"""
    from skills.router import SkillRouter

    service = SimpleNamespace(
        search=AsyncMock(return_value=[{"title": "第一期", "media_url": "https://example.test/1.mp3"}]),
        find_subscription_candidates=AsyncMock(return_value=[{
            "name": "测试播客", "rss_url": "https://example.test/feed.xml",
        }]),
        list_subscriptions=AsyncMock(return_value=[{
            "id": 1, "name": "测试播客", "rss_url": "https://example.test/feed.xml",
        }]),
    )
    config = rss_config()
    config["music"] = {"actions": {"stop": {"keywords": ["停止播放"]}}}
    router = SkillRouter(skills_config=config, rss_service=service)

    search = run(router.match("搜索播客 科技"))
    add = run(router.match("订阅播客 测试播客"))
    remove = run(router.match("删除播客 测试播客"))

    for result in (search, add, remove):
        assert result.extra.get("await_followup") is True
        assert result.extra.get("followup_timeout") == 20


def test_rss_finished_failed_and_regular_actions_do_not_request_followup():
    """完成、失败或不需用户选择的动作保持单轮行为。"""
    from skills.router import SkillRouter

    service = SimpleNamespace(
        list_subscriptions=AsyncMock(return_value=[]),
        search=AsyncMock(return_value=[]),
        find_subscription_candidates=AsyncMock(return_value=[]),
    )
    config = rss_config()
    config["music"] = {"actions": {"stop": {"keywords": ["停止播放"]}}}
    router = SkillRouter(skills_config=config, rss_service=service)

    listed = run(router.match("RSS列表"))
    no_episodes = run(router.match("搜索播客 科技"))
    failed_confirmation = run(router.match("确认订阅第 1 个"))
    failed_play = run(router.match("播放第 1 个"))
    ordinary_music = run(router.match("停止播放"))

    for result in (listed, no_episodes, failed_confirmation, failed_play, ordinary_music):
        assert not result.extra.get("await_followup", False)


def test_single_mode_loop_recognizes_await_followup_before_breaking():
    """主循环必须在 single 模式的 break 判定中检查技能跟进信号。"""
    main_path = Path(__file__).resolve().parents[1] / "main.py"
    module = ast.parse(main_path.read_text(encoding="utf-8"))
    voice_assistant = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "VoiceAssistant")
    conversation_loop = next(
        node for node in voice_assistant.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_conversation_loop"
    )

    loop_source = ast.unparse(conversation_loop)
    assert "await_followup" in loop_source
    assert "skill_result.extra" in loop_source


def test_cancel_consumes_an_active_short_lived_rss_followup():
    """有效的 20 秒 RSS follow-up 窗口内，取消应清除临时上下文。"""
    from skills.router import SkillRouter

    service = SimpleNamespace()
    active_router = SkillRouter(skills_config=rss_config(), rss_service=service)
    active_router._rss_last_results = [{"title": "第一期", "media_url": "https://example.test/1.mp3"}]
    active_router._rss_last_results_at = time.monotonic()
    active_router._rss_followup_at = time.monotonic()

    cancelled = run(active_router.match("取消"))
    assert cancelled.skill == "rss"
    assert cancelled.action == "cancel_followup"
    assert not cancelled.extra.get("await_followup", False)


def test_cancel_does_not_consume_valid_rss_cache_after_followup_window_expires():
    """10 分钟节目缓存仍有效也不能延长 20 秒 follow-up 的取消拦截。"""
    from skills.router import SkillRouter

    service = SimpleNamespace()
    expired_router = SkillRouter(skills_config=rss_config(), rss_service=service)
    expired_router._rss_last_results = [{"title": "第一期", "media_url": "https://example.test/1.mp3"}]
    expired_router._rss_last_results_at = time.monotonic()
    expired_router._rss_followup_at = time.monotonic() - 21

    expired_cancel = run(expired_router.match("取消"))
    assert expired_cancel is None or expired_cancel.skill != "rss"


def test_cancel_without_rss_followup_is_not_consumed():
    """完全没有 RSS 待处理上下文时，取消留给 OpenClaw 或其他技能。"""
    from skills.router import SkillRouter

    service = SimpleNamespace()
    idle_router = SkillRouter(skills_config=rss_config(), rss_service=service)
    idle_cancel = run(idle_router.match("取消"))
    assert idle_cancel is None or idle_cancel.skill != "rss"
