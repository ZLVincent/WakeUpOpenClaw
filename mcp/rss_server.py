"""WakeUpOpenClaw RSS MCP Server.

向 OpenClaw 暴露订阅查询和按主题播放播客的安全工具。所有实际
RSS 选择与播放器控制均在本机 WakeUpOpenClaw 服务中完成。
"""

import asyncio
import os

import httpx
from mcp.server import Server, InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ServerCapabilities

API_BASE = os.environ.get("WAKEUP_API_BASE", "http://localhost:8084")
app = Server("rss")


async def api_get(path: str, params: dict = None) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{API_BASE}{path}", params=params)
        response.raise_for_status()
        return response.json()


async def api_post(path: str, data: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"{API_BASE}{path}", json=data)
        response.raise_for_status()
        return response.json()


def _format_episodes(episodes: list) -> str:
    if not episodes:
        return "没有找到可播放的播客节目。"
    lines = []
    for index, episode in enumerate(episodes, 1):
        source = episode.get("subscription_name", "未知来源")
        lines.append(f"{index}. {episode.get('title', '未命名')}（{source}）")
    return "\n".join(lines)


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="list_rss_subscriptions",
            description="列出 WakeUpOpenClaw 当前已订阅的 Podcast RSS 源。",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="query_latest_rss",
            description="按主题查询订阅 RSS 中的最新可播放播客。主题可由用户自然语言意图提炼而来。",
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "节目主题或关键词，可选"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "description": "最多返回条数"},
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="play_latest_rss",
            description="按主题播放订阅 RSS 中最新匹配的播客；无匹配时由本机服务播放全局最新节目。仅传主题，不能传媒体 URL。",
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "用户要收听的新闻或播客主题"},
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        return [TextContent(type="text", text=await _dispatch_tool(name, arguments or {}))]
    except httpx.ConnectError:
        return [TextContent(type="text", text=f"错误：无法连接到 WakeUpOpenClaw 服务 ({API_BASE})。")]
    except Exception as exc:
        return [TextContent(type="text", text=f"操作失败：{exc}")]


async def _dispatch_tool(name: str, args: dict) -> str:
    if name == "list_rss_subscriptions":
        data = await api_get("/api/rss/subscriptions")
        subscriptions = data.get("subscriptions", [])
        if not subscriptions:
            return "当前没有 RSS 播客订阅。"
        return "当前订阅：\n" + "\n".join(
            f"- {item.get('name', '未命名')}" for item in subscriptions if item.get("enabled", 1)
        )
    if name == "query_latest_rss":
        topic = str(args.get("topic", "")).strip()
        max_results = max(1, min(int(args.get("max_results", 3)), 10))
        data = await api_get("/api/rss/episodes", {"topic": topic, "max_results": max_results})
        return _format_episodes(data.get("episodes", []))
    if name == "play_latest_rss":
        # 刻意忽略任何未声明字段，特别是调用方传入的 media_url。
        topic = str(args.get("topic", "")).strip()
        data = await api_post("/api/rss/play", {"topic": topic})
        episode = data.get("episode", {})
        if not episode:
            return "没有可播放的最新播客节目。"
        fallback = "；未找到主题匹配，已改播全局最新节目" if not data.get("matched", True) else ""
        return f"正在播放：{episode.get('title', '未命名')}（{episode.get('subscription_name', '未知来源')}）{fallback}"
    return f"未知工具：{name}"


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            InitializationOptions(server_name="rss", server_version="1.0.0", capabilities=ServerCapabilities()),
        )


if __name__ == "__main__":
    asyncio.run(main())
