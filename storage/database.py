"""
数据持久化模块 — MySQL 存储

使用 aiomysql 异步连接池，自动建库建表。
存储对话会话、消息记录和日程事件。
"""

import asyncio
import datetime
import time
import uuid
from typing import Optional

import aiomysql

from utils.logger import get_logger

logger = get_logger("storage")

# 建表 SQL
_CREATE_CONVERSATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS conversations (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(64) NOT NULL,
    title           VARCHAR(200) DEFAULT '',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    is_active       TINYINT(1) DEFAULT 1,
    round_count     INT DEFAULT 0,
    source          VARCHAR(20) DEFAULT 'voice',
    INDEX idx_session_id (session_id),
    INDEX idx_is_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_CREATE_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS messages (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    conversation_id BIGINT NOT NULL,
    role            VARCHAR(20) NOT NULL,
    content         TEXT NOT NULL,
    source          VARCHAR(20) DEFAULT 'voice',
    duration_ms     INT DEFAULT NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_conversation_id (conversation_id),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_CREATE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    title           VARCHAR(200) NOT NULL,
    description     TEXT DEFAULT '',
    date            DATE NOT NULL,
    start_time      TIME DEFAULT NULL,
    end_time        TIME DEFAULT NULL,
    all_day         TINYINT(1) DEFAULT 0,
    color           VARCHAR(20) DEFAULT '#0f3460',
    category        VARCHAR(50) DEFAULT '',
    remind_minutes  INT DEFAULT 5,
    reminded        TINYINT(1) DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_date (date),
    INDEX idx_remind (date, start_time, reminded, remind_minutes)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def generate_session_id() -> str:
    """生成新的 session-id (UUID)。"""
    return str(uuid.uuid4())


class ChatDatabase:
    """
    对话历史数据库操作层。

    Parameters
    ----------
    host : str
    port : int
    user : str
    password : str
    database : str
    pool_size : int
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 3306,
        user: str = "root",
        password: str = "",
        database: str = "wakeup_openclaw",
        pool_size: int = 5,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.pool_size = pool_size
        self._pool: Optional[aiomysql.Pool] = None

    async def initialize(self) -> None:
        """初始化连接池，自动建库建表。"""
        # 先连接 MySQL（不指定数据库），创建数据库
        try:
            conn = await aiomysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                charset="utf8mb4",
            )
            async with conn.cursor() as cur:
                await cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.database}` "
                    f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            conn.close()
            logger.info("数据库 '%s' 已就绪", self.database)
        except Exception as e:
            logger.error("创建数据库失败: %s", e)
            raise

        # 创建连接池
        self._pool = await aiomysql.create_pool(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            db=self.database,
            charset="utf8mb4",
            minsize=1,
            maxsize=self.pool_size,
            autocommit=True,
        )
        logger.info("MySQL 连接池已创建 (pool_size=%d)", self.pool_size)

        # 建表
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(_CREATE_CONVERSATIONS_TABLE)
                await cur.execute(_CREATE_MESSAGES_TABLE)
                try:
                    await cur.execute(_CREATE_EVENTS_TABLE)
                except Exception as e:
                    logger.error("创建 events 表失败: %s", e)
                    # 尝试不带索引的简化版本建表
                    await cur.execute("""
                        CREATE TABLE IF NOT EXISTS events (
                            id              BIGINT AUTO_INCREMENT PRIMARY KEY,
                            title           VARCHAR(200) NOT NULL,
                            description     TEXT,
                            date            DATE NOT NULL,
                            start_time      TIME DEFAULT NULL,
                            end_time        TIME DEFAULT NULL,
                            all_day         TINYINT(1) DEFAULT 0,
                            color           VARCHAR(20) DEFAULT '#0f3460',
                            category        VARCHAR(50) DEFAULT '',
                            remind_minutes  INT DEFAULT 5,
                            reminded        TINYINT(1) DEFAULT 0,
                            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                            updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                    """)
                    logger.info("events 表已通过简化 SQL 创建")
        logger.info("数据表已就绪")

    async def close(self) -> None:
        """关闭连接池。"""
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            logger.info("MySQL 连接池已关闭")

    # ------------------------------------------------------------------
    # 对话管理
    # ------------------------------------------------------------------

    async def create_conversation(
        self, session_id: str, source: str = "voice"
    ) -> int:
        """
        创建新对话。

        Returns
        -------
        int
            新对话的 ID
        """
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO conversations (session_id, source) VALUES (%s, %s)",
                    (session_id, source),
                )
                conversation_id = cur.lastrowid
        logger.info(
            "新建对话 #%d (session=%s, source=%s)",
            conversation_id, session_id, source,
        )
        return conversation_id

    async def get_active_conversation(self) -> Optional[dict]:
        """获取当前活跃的对话。"""
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM conversations WHERE is_active = 1 "
                    "ORDER BY updated_at DESC LIMIT 1"
                )
                return await cur.fetchone()

    async def archive_conversation(self, conversation_id: int) -> None:
        """归档对话（设为非活跃）。"""
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE conversations SET is_active = 0 WHERE id = %s",
                    (conversation_id,),
                )
        logger.info("对话 #%d 已归档", conversation_id)

    async def delete_conversation(self, conversation_id: int) -> None:
        """删除对话及其所有消息。"""
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                # messages 有外键 ON DELETE CASCADE，删 conversation 即可
                await cur.execute(
                    "DELETE FROM conversations WHERE id = %s",
                    (conversation_id,),
                )
        logger.info("对话 #%d 已删除", conversation_id)

    async def archive_all_conversations(self) -> None:
        """归档所有活跃对话。"""
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE conversations SET is_active = 0 WHERE is_active = 1"
                )

    async def update_conversation_title(
        self, conversation_id: int, title: str
    ) -> None:
        """更新对话标题。"""
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE conversations SET title = %s WHERE id = %s",
                    (title[:200], conversation_id),
                )

    async def increment_round_count(self, conversation_id: int) -> int:
        """增加对话轮次计数，返回新值。"""
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE conversations SET round_count = round_count + 1 "
                    "WHERE id = %s",
                    (conversation_id,),
                )
                await cur.execute(
                    "SELECT round_count FROM conversations WHERE id = %s",
                    (conversation_id,),
                )
                row = await cur.fetchone()
                return row[0] if row else 0

    async def list_conversations(self, limit: int = 50) -> list:
        """列出对话，最近的在前。"""
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, session_id, title, created_at, updated_at, "
                    "is_active, round_count, source "
                    "FROM conversations ORDER BY updated_at DESC LIMIT %s",
                    (limit,),
                )
                rows = await cur.fetchall()
                # datetime 转字符串
                for r in rows:
                    for k in ("created_at", "updated_at"):
                        if r.get(k):
                            r[k] = r[k].strftime("%Y-%m-%d %H:%M:%S")
                return rows

    # ------------------------------------------------------------------
    # 消息管理
    # ------------------------------------------------------------------

    async def add_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        source: str = "voice",
        duration_ms: Optional[int] = None,
    ) -> int:
        """
        添加一条消息。

        Returns
        -------
        int
            消息 ID
        """
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO messages "
                    "(conversation_id, role, content, source, duration_ms) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (conversation_id, role, content, source, duration_ms),
                )
                return cur.lastrowid

    async def get_messages(
        self,
        conversation_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> list:
        """获取某个对话的消息列表。"""
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, role, content, source, duration_ms, created_at "
                    "FROM messages WHERE conversation_id = %s "
                    "ORDER BY created_at ASC LIMIT %s OFFSET %s",
                    (conversation_id, limit, offset),
                )
                rows = await cur.fetchall()
                for r in rows:
                    if r.get("created_at"):
                        r["created_at"] = r["created_at"].strftime("%H:%M:%S")
                return rows

    async def get_or_create_active_conversation(
        self, source: str = "voice"
    ) -> dict:
        """
        获取当前活跃对话，如果没有则新建一个。

        Returns
        -------
        dict
            包含 id, session_id 等字段的对话字典
        """
        conv = await self.get_active_conversation()
        if conv:
            return conv

        session_id = generate_session_id()
        conv_id = await self.create_conversation(session_id, source)
        return {
            "id": conv_id,
            "session_id": session_id,
            "title": "",
            "round_count": 0,
            "source": source,
            "is_active": 1,
        }

    async def start_new_conversation(self, source: str = "voice") -> dict:
        """
        归档所有活跃对话，创建新对话。

        Returns
        -------
        dict
            新对话信息
        """
        await self.archive_all_conversations()
        session_id = generate_session_id()
        conv_id = await self.create_conversation(session_id, source)
        logger.info("已开启新对话 #%d (session=%s)", conv_id, session_id)
        return {
            "id": conv_id,
            "session_id": session_id,
            "title": "",
            "round_count": 0,
            "source": source,
            "is_active": 1,
        }

    # ------------------------------------------------------------------
    # 日程事件管理
    # ------------------------------------------------------------------

    def _format_event_row(self, row: dict) -> dict:
        """格式化日程查询结果的日期/时间字段为字符串。"""
        for k in ("created_at", "updated_at"):
            if row.get(k) and isinstance(row[k], datetime.datetime):
                row[k] = row[k].strftime("%Y-%m-%d %H:%M:%S")
        if row.get("date") and isinstance(row["date"], datetime.date):
            row["date"] = row["date"].strftime("%Y-%m-%d")
        for k in ("start_time", "end_time"):
            if row.get(k) and isinstance(row[k], datetime.timedelta):
                total_sec = int(row[k].total_seconds())
                hours, remainder = divmod(total_sec, 3600)
                minutes, _ = divmod(remainder, 60)
                row[k] = f"{hours:02d}:{minutes:02d}"
            elif row.get(k) and isinstance(row[k], datetime.time):
                row[k] = row[k].strftime("%H:%M")
            elif row.get(k) is None:
                row[k] = None
        return row

    async def create_event(
        self,
        title: str,
        date: str,
        start_time: str = None,
        end_time: str = None,
        all_day: bool = False,
        color: str = "#0f3460",
        category: str = "",
        description: str = "",
        remind_minutes: int = 5,
    ) -> int:
        """创建日程事件，返回事件 ID。"""
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO events "
                    "(title, description, date, start_time, end_time, all_day, "
                    "color, category, remind_minutes) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (title, description, date, start_time, end_time,
                     1 if all_day else 0, color, category, remind_minutes),
                )
                event_id = cur.lastrowid
        logger.info("新建日程 #%d: %s (%s)", event_id, title, date)
        return event_id

    async def update_event(self, event_id: int, **fields) -> None:
        """更新日程事件。只更新传入的字段。"""
        if not fields:
            return
        set_parts = []
        values = []
        allowed = {
            "title", "description", "date", "start_time", "end_time",
            "all_day", "color", "category", "remind_minutes", "reminded",
        }
        for k, v in fields.items():
            if k in allowed:
                set_parts.append(f"{k} = %s")
                values.append(v)
        if not set_parts:
            return
        values.append(event_id)
        sql = f"UPDATE events SET {', '.join(set_parts)} WHERE id = %s"
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(values))
        logger.info("更新日程 #%d: %s", event_id, list(fields.keys()))

    async def delete_event(self, event_id: int) -> None:
        """删除日程事件。"""
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM events WHERE id = %s", (event_id,))
        logger.info("删除日程 #%d", event_id)

    async def get_event(self, event_id: int) -> Optional[dict]:
        """获取单个日程事件。"""
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM events WHERE id = %s", (event_id,))
                row = await cur.fetchone()
                return self._format_event_row(row) if row else None

    async def get_events_by_range(self, start: str, end: str) -> list:
        """获取日期范围内的日程列表。"""
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM events WHERE date >= %s AND date <= %s "
                    "ORDER BY date ASC, all_day DESC, start_time ASC",
                    (start, end),
                )
                rows = await cur.fetchall()
                return [self._format_event_row(r) for r in rows]

    async def get_events_by_date(self, date: str) -> list:
        """获取某一天的日程列表。"""
        return await self.get_events_by_range(date, date)

    async def get_upcoming_reminders(self) -> list:
        """获取需要提醒的即将到来的日程。"""
        now = datetime.datetime.now()
        today = now.strftime("%Y-%m-%d")
        current_time = now.strftime("%H:%M:%S")
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM events "
                    "WHERE date = %s AND reminded = 0 AND remind_minutes > 0 "
                    "AND all_day = 0 AND start_time IS NOT NULL "
                    "AND SUBTIME(start_time, SEC_TO_TIME(remind_minutes * 60)) <= %s",
                    (today, current_time),
                )
                rows = await cur.fetchall()
                return [self._format_event_row(r) for r in rows]

    async def mark_event_reminded(self, event_id: int) -> None:
        """标记日程已提醒。"""
        await self.update_event(event_id, reminded=1)

    async def reset_daily_reminders(self) -> None:
        """重置今天所有事件的提醒状态。"""
        today = datetime.date.today().strftime("%Y-%m-%d")
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE events SET reminded = 0 WHERE date = %s",
                    (today,),
                )
