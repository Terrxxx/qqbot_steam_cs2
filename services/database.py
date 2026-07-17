from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from utils.logger import logger

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "qqbot.db"


class Database:
    """SQLite 数据库（单例）"""

    _instance: Optional["Database"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "Database":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_db()
        return cls._instance

    def _init_db(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")  # 读写并发
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()
        logger.info(f"数据库已初始化: {DB_PATH}")

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS steam_profiles (
                steam_id    TEXT PRIMARY KEY,
                steam_name  TEXT DEFAULT '',
                display_name TEXT DEFAULT '',
                group_openid TEXT DEFAULT '',
                is_active   INTEGER DEFAULT 1,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS game_sessions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                steam_id        TEXT NOT NULL,
                game_name       TEXT DEFAULT '',
                start_time      TEXT NOT NULL,
                end_time        TEXT,
                duration_minutes INTEGER DEFAULT 0,
                notified_start  INTEGER DEFAULT 0,
                notified_end    INTEGER DEFAULT 0,
                FOREIGN KEY (steam_id) REFERENCES steam_profiles(steam_id)
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_steam_id
                ON game_sessions(steam_id, start_time);

            CREATE TABLE IF NOT EXISTS daily_cache (
                user_id  TEXT NOT NULL,
                date     TEXT NOT NULL,
                type     TEXT NOT NULL,
                data     TEXT NOT NULL,
                PRIMARY KEY (user_id, date, type)
            );
        """)
        self._conn.commit()

    # ==================== Steam Profiles CRUD ====================

    def add_profile(self, steam_id: str, display_name: str, group_openid: str) -> bool:
        """添加或更新 Steam 监控"""
        try:
            self._conn.execute(
                """INSERT INTO steam_profiles (steam_id, display_name, group_openid, is_active)
                   VALUES (?, ?, ?, 1)
                   ON CONFLICT(steam_id) DO UPDATE SET
                       display_name=excluded.display_name,
                       group_openid=excluded.group_openid,
                       is_active=1""",
                (steam_id, display_name, group_openid),
            )
            self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"添加 Steam 失败: {e}")
            return False

    def remove_profile(self, steam_id: str, group_openid: str = "") -> bool:
        """软删除"""
        try:
            if group_openid:
                self._conn.execute(
                    "UPDATE steam_profiles SET is_active=0 WHERE steam_id=? AND group_openid=?",
                    (steam_id, group_openid),
                )
            else:
                self._conn.execute(
                    "UPDATE steam_profiles SET is_active=0 WHERE steam_id=?",
                    (steam_id,),
                )
            self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"移除 Steam 失败: {e}")
            return False

    def get_active_profiles(self, group_openid: str = "") -> list[dict]:
        """获取活跃监控列表（可按群过滤）"""
        if group_openid:
            rows = self._conn.execute(
                "SELECT steam_id, display_name, group_openid, steam_name "
                "FROM steam_profiles WHERE is_active=1 AND group_openid=?",
                (group_openid,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT steam_id, display_name, group_openid, steam_name "
                "FROM steam_profiles WHERE is_active=1",
            ).fetchall()
        return [dict(zip(["steam_id", "display_name", "group_openid", "steam_name"], r)) for r in rows]

    def get_all_profiles(self) -> list[dict]:
        return self.get_active_profiles()

    def update_steam_name(self, steam_id: str, steam_name: str) -> None:
        self._conn.execute(
            "UPDATE steam_profiles SET steam_name=? WHERE steam_id=?",
            (steam_name, steam_id),
        )
        self._conn.commit()

    def update_display_name(self, steam_id: str, display_name: str) -> None:
        self._conn.execute(
            "UPDATE steam_profiles SET display_name=? WHERE steam_id=?",
            (display_name, steam_id),
        )
        self._conn.commit()

    # ==================== Game Sessions ====================

    def start_session(self, steam_id: str, game_name: str, start_time: datetime) -> int:
        """记录游戏开始，返回 session id"""
        cur = self._conn.execute(
            "INSERT INTO game_sessions (steam_id, game_name, start_time) VALUES (?, ?, ?)",
            (steam_id, game_name, start_time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        self._conn.commit()
        return cur.lastrowid

    def end_session(self, session_id: int, end_time: datetime) -> int:
        """记录游戏结束，返回游玩分钟数"""
        row = self._conn.execute(
            "SELECT start_time FROM game_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        if not row:
            return 0

        start = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        minutes = int((end_time - start).total_seconds() / 60)

        self._conn.execute(
            "UPDATE game_sessions SET end_time=?, duration_minutes=?, notified_end=1 WHERE id=?",
            (end_time.strftime("%Y-%m-%d %H:%M:%S"), minutes, session_id),
        )
        self._conn.commit()
        return minutes

    # ==================== Daily Cache ====================

    def get_daily_cache(self, user_id: str, date: str, cache_type: str) -> str | None:
        row = self._conn.execute(
            "SELECT data FROM daily_cache WHERE user_id=? AND date=? AND type=?",
            (user_id, date, cache_type),
        ).fetchone()
        return row[0] if row else None

    def set_daily_cache(self, user_id: str, date: str, cache_type: str, data: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO daily_cache (user_id, date, type, data) VALUES (?, ?, ?, ?)",
            (user_id, date, cache_type, data),
        )
        self._conn.commit()

    def get_recent_sessions(self, steam_id: str, limit: int = 10) -> list[dict]:
        rows = self._conn.execute(
            "SELECT game_name, start_time, end_time, duration_minutes "
            "FROM game_sessions WHERE steam_id=? ORDER BY start_time DESC LIMIT ?",
            (steam_id, limit),
        ).fetchall()
        return [dict(zip(["game", "start", "end", "mins"], r)) for r in rows]
