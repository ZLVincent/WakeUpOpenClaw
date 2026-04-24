"""
WakeUpOpenClaw Calendar MCP Server

通过 MCP 协议向 OpenClaw Agent 暴露日程操作工具。
Agent 可以调用这些工具来查询、创建、修改和删除日程。

通信方式: stdio (JSON-RPC)
内部调用: HTTP -> localhost:8084/api/events

注册方式 (一次性):
    openclaw mcp set calendar '{"command":"/path/to/venv/bin/python3","args":["/path/to/mcp/calendar_server.py"]}'

依赖:
    pip install mcp httpx
"""

import asyncio
import json
import os
import sys
from datetime import date, timedelta
from typing import Optional

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# API 基础地址，可通过环境变量覆盖
API_BASE = os.environ.get("WAKEUP_API_BASE", "http://localhost:8084")

# 创建 MCP Server 实例
app = Server("calendar")


# ---------------------------------------------------------------------------
# HTTP 客户端辅助
# ---------------------------------------------------------------------------

async def api_get(path: str, params: dict = None) -> dict:
    """GET 请求本地 API。"""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{API_BASE}{path}", params=params)
        resp.raise_for_status()
        return resp.json()


async def api_post(path: str, data: dict) -> dict:
    """POST 请求本地 API。"""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{API_BASE}{path}", json=data)
        resp.raise_for_status()
        return resp.json()


async def api_put(path: str, data: dict) -> dict:
    """PUT 请求本地 API。"""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.put(f"{API_BASE}{path}", json=data)
        resp.raise_for_status()
        return resp.json()


async def api_delete(path: str) -> dict:
    """DELETE 请求本地 API。"""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.delete(f"{API_BASE}{path}")
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# 日程格式化辅助
# ---------------------------------------------------------------------------

def format_event(ev: dict) -> str:
    """将单个日程格式化为可读文本。"""
    date_str = ev.get("date", "")
    title = ev.get("title", "")
    if ev.get("all_day"):
        time_str = "全天"
    elif ev.get("start_time"):
        time_str = ev["start_time"]
        if ev.get("end_time"):
            time_str += f"-{ev['end_time']}"
    else:
        time_str = ""
    parts = [p for p in [date_str, time_str, title] if p]
    desc = ev.get("description", "")
    line = " | ".join(parts)
    if desc:
        line += f" ({desc})"
    return line


def format_event_list(events: list, label: str = "") -> str:
    """将日程列表格式化为可读文本。"""
    if not events:
        return f"{label}没有日程安排。" if label else "没有日程安排。"
    lines = [f"{label}共有 {len(events)} 个日程：" if label else f"共有 {len(events)} 个日程："]
    for ev in events:
        lines.append(f"  - {format_event(ev)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP Tools 定义
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[Tool]:
    """返回可用工具列表。"""
    return [
        Tool(
            name="query_events",
            description="查询指定日期范围内的日程事件。需要提供开始日期和结束日期，格式为 YYYY-MM-DD。",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "开始日期，格式 YYYY-MM-DD",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期，格式 YYYY-MM-DD",
                    },
                },
                "required": ["start_date", "end_date"],
            },
        ),
        Tool(
            name="query_today_events",
            description="查询今天的所有日程事件。无需参数。",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="query_tomorrow_events",
            description="查询明天的所有日程事件。无需参数。",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="query_week_events",
            description="查询本周（周一到周日）的所有日程事件。无需参数。",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="create_event",
            description="创建一个新的日程事件。必须提供标题和日期。时间可选，不提供则为全天事件。",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "日程标题",
                    },
                    "date": {
                        "type": "string",
                        "description": "日期，格式 YYYY-MM-DD",
                    },
                    "start_time": {
                        "type": "string",
                        "description": "开始时间，格式 HH:MM（可选，不提供则为全天事件）",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "结束时间，格式 HH:MM（可选）",
                    },
                    "description": {
                        "type": "string",
                        "description": "日程描述/备注（可选）",
                    },
                    "category": {
                        "type": "string",
                        "description": "分类：工作/生活/会议/提醒（可选）",
                    },
                    "remind_minutes": {
                        "type": "integer",
                        "description": "提前提醒分钟数，默认5分钟，设0不提醒",
                    },
                },
                "required": ["title", "date"],
            },
        ),
        Tool(
            name="update_event",
            description="修改一个已有的日程事件。必须提供事件ID，以及要修改的字段。",
            inputSchema={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "integer",
                        "description": "要修改的日程事件ID",
                    },
                    "title": {
                        "type": "string",
                        "description": "新标题（可选）",
                    },
                    "date": {
                        "type": "string",
                        "description": "新日期，格式 YYYY-MM-DD（可选）",
                    },
                    "start_time": {
                        "type": "string",
                        "description": "新开始时间，格式 HH:MM（可选）",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "新结束时间，格式 HH:MM（可选）",
                    },
                    "description": {
                        "type": "string",
                        "description": "新描述（可选）",
                    },
                },
                "required": ["event_id"],
            },
        ),
        Tool(
            name="delete_event",
            description="删除一个日程事件。必须提供事件ID。建议先查询获取事件ID再删除。",
            inputSchema={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "integer",
                        "description": "要删除的日程事件ID",
                    },
                },
                "required": ["event_id"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """处理工具调用。"""
    try:
        result = await _dispatch_tool(name, arguments)
        return [TextContent(type="text", text=result)]
    except httpx.ConnectError:
        return [TextContent(
            type="text",
            text=f"错误：无法连接到 WakeUpOpenClaw 服务 ({API_BASE})，请确认服务已启动。",
        )]
    except Exception as e:
        return [TextContent(type="text", text=f"操作失败：{str(e)}")]


async def _dispatch_tool(name: str, args: dict) -> str:
    """分发工具调用到具体实现。"""

    if name == "query_events":
        data = await api_get("/api/events", {
            "start": args["start_date"],
            "end": args["end_date"],
        })
        return format_event_list(data.get("events", []))

    elif name == "query_today_events":
        today = date.today().strftime("%Y-%m-%d")
        data = await api_get("/api/events", {"start": today, "end": today})
        return format_event_list(data.get("events", []), "今天")

    elif name == "query_tomorrow_events":
        tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        data = await api_get("/api/events", {"start": tomorrow, "end": tomorrow})
        return format_event_list(data.get("events", []), "明天")

    elif name == "query_week_events":
        today = date.today()
        monday = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
        sunday = (today + timedelta(days=6 - today.weekday())).strftime("%Y-%m-%d")
        data = await api_get("/api/events", {"start": monday, "end": sunday})
        return format_event_list(data.get("events", []), "本周")

    elif name == "create_event":
        event_data = {
            "title": args["title"],
            "date": args["date"],
            "start_time": args.get("start_time"),
            "end_time": args.get("end_time"),
            "all_day": args.get("start_time") is None,
            "description": args.get("description", ""),
            "category": args.get("category", ""),
            "remind_minutes": args.get("remind_minutes", 5),
        }
        data = await api_post("/api/events", event_data)
        ev = data.get("event", {})
        return f"日程已创建（ID: {ev.get('id', '?')}）：{format_event(ev)}"

    elif name == "update_event":
        event_id = args.pop("event_id")
        # 只传有值的字段
        update_data = {k: v for k, v in args.items() if v is not None}
        data = await api_put(f"/api/events/{event_id}", update_data)
        ev = data.get("event", {})
        return f"日程已更新（ID: {event_id}）：{format_event(ev)}"

    elif name == "delete_event":
        event_id = args["event_id"]
        await api_delete(f"/api/events/{event_id}")
        return f"日程已删除（ID: {event_id}）"

    else:
        return f"未知工具：{name}"


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

async def main():
    """启动 MCP Server（stdio 模式）。"""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream)


if __name__ == "__main__":
    asyncio.run(main())
