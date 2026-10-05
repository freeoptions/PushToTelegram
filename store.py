from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from settings import DB_PATH, ensure_data_dir


@dataclass(slots=True)
class SentVideo:
    platform: str
    uid: str
    up_name: str
    bvid: str
    title: str
    video_url: str
    published_at: int


class SentVideoStore:
    def __init__(self) -> None:
        ensure_data_dir()
        self._conn = sqlite3.connect(DB_PATH)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sent_videos (
                platform TEXT NOT NULL DEFAULT 'bilibili',
                uid TEXT NOT NULL,
                up_name TEXT NOT NULL,
                bvid TEXT NOT NULL,
                title TEXT NOT NULL,
                video_url TEXT NOT NULL,
                published_at INTEGER NOT NULL,
                sent_at INTEGER NOT NULL,
                PRIMARY KEY (uid, bvid)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_checkpoints (
                platform TEXT NOT NULL,
                uid TEXT NOT NULL,
                last_check_started_at INTEGER NOT NULL,
                PRIMARY KEY (platform, uid)
            )
            """
        )
        self._ensure_platform_column()
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _ensure_platform_column(self) -> None:
        columns = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(sent_videos)").fetchall()
        }
        if "platform" in columns:
            return
        self._conn.execute("ALTER TABLE sent_videos ADD COLUMN platform TEXT NOT NULL DEFAULT 'bilibili'")
        self._conn.commit()

    def has_any_for_uid(self, uid: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sent_videos WHERE uid = ? LIMIT 1",
            (uid,),
        ).fetchone()
        return row is not None

    def was_sent(self, uid: str, bvid: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sent_videos WHERE uid = ? AND bvid = ? LIMIT 1",
            (uid, bvid),
        ).fetchone()
        return row is not None

    def get_sent_bvids(self, uid: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT bvid FROM sent_videos WHERE uid = ?",
            (uid,),
        ).fetchall()
        return {str(row[0]) for row in rows if row[0]}

    def get_checkpoint(self, platform: str, uid: str) -> int | None:
        row = self._conn.execute(
            """
            SELECT last_check_started_at
            FROM sync_checkpoints
            WHERE platform = ? AND uid = ?
            LIMIT 1
            """,
            (platform, uid),
        ).fetchone()
        if row is None:
            return None
        return int(row[0])

    def save_checkpoint(self, platform: str, uid: str, check_started_at: int) -> None:
        self._conn.execute(
            """
            INSERT INTO sync_checkpoints (platform, uid, last_check_started_at)
            VALUES (?, ?, ?)
            ON CONFLICT(platform, uid) DO UPDATE SET
                last_check_started_at = excluded.last_check_started_at
            """,
            (platform, uid, int(check_started_at)),
        )
        self._conn.commit()

    def save_sent(self, item: SentVideo) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO sent_videos (
                platform, uid, up_name, bvid, title, video_url, published_at, sent_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.platform,
                item.uid,
                item.up_name,
                item.bvid,
                item.title,
                item.video_url,
                item.published_at,
                int(time.time()),
            ),
        )
        self._conn.commit()

    def list_sent_videos(self) -> list[SentVideo]:
        rows = self._conn.execute(
            """
            SELECT platform, uid, up_name, bvid, title, video_url, published_at
            FROM sent_videos
            ORDER BY published_at DESC, uid ASC, bvid ASC
            """
        ).fetchall()
        return [
            SentVideo(
                platform=row[0],
                uid=row[1],
                up_name=row[2],
                bvid=row[3],
                title=row[4],
                video_url=row[5],
                published_at=row[6],
            )
            for row in rows
        ]
