from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass
class FollowerRecord:
    user_id: str
    username: str
    full_name: str = ""
    is_private: Optional[bool] = None
    is_verified: Optional[bool] = None
    profile_pic_url: str = ""

    @classmethod
    def from_user_short(cls, user) -> "FollowerRecord":
        return cls(
            user_id=str(getattr(user, "pk", "")),
            username=str(getattr(user, "username", "")),
            full_name=str(getattr(user, "full_name", "") or ""),
            is_private=getattr(user, "is_private", None),
            is_verified=getattr(user, "is_verified", None),
            profile_pic_url=str(getattr(user, "profile_pic_url", "") or ""),
        )


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS followers (
                target_username TEXT NOT NULL,
                target_user_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                full_name TEXT,
                is_private INTEGER,
                is_verified INTEGER,
                profile_pic_url TEXT,
                scraped_at INTEGER NOT NULL,
                PRIMARY KEY (target_user_id, user_id)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scrape_state (
                target_user_id TEXT PRIMARY KEY,
                target_username TEXT NOT NULL,
                cursor TEXT NOT NULL DEFAULT '',
                completed INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            )
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def reset_target(self, target_user_id: str) -> None:
        self.conn.execute("DELETE FROM followers WHERE target_user_id = ?", (target_user_id,))
        self.conn.execute("DELETE FROM scrape_state WHERE target_user_id = ?", (target_user_id,))
        self.conn.commit()

    def get_cursor(self, target_user_id: str) -> str:
        row = self.conn.execute(
            "SELECT cursor FROM scrape_state WHERE target_user_id = ?", (target_user_id,)
        ).fetchone()
        return row[0] if row else ""

    def is_completed(self, target_user_id: str) -> bool:
        row = self.conn.execute(
            "SELECT completed FROM scrape_state WHERE target_user_id = ?", (target_user_id,)
        ).fetchone()
        return bool(row[0]) if row else False

    def save_page(
        self,
        target_username: str,
        target_user_id: str,
        records: Iterable[FollowerRecord],
        next_cursor: str,
        completed: bool,
    ) -> int:
        now = int(time.time())
        rows = []
        for record in records:
            rows.append(
                (
                    target_username,
                    target_user_id,
                    record.user_id,
                    record.username,
                    record.full_name,
                    int(record.is_private) if record.is_private is not None else None,
                    int(record.is_verified) if record.is_verified is not None else None,
                    record.profile_pic_url,
                    now,
                )
            )
        before = self.conn.total_changes
        with self.conn:
            self.conn.executemany(
                """
                INSERT OR IGNORE INTO followers (
                    target_username, target_user_id, user_id, username, full_name,
                    is_private, is_verified, profile_pic_url, scraped_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self.conn.execute(
                """
                INSERT INTO scrape_state (target_user_id, target_username, cursor, completed, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(target_user_id) DO UPDATE SET
                    target_username=excluded.target_username,
                    cursor=excluded.cursor,
                    completed=excluded.completed,
                    updated_at=excluded.updated_at
                """,
                (target_user_id, target_username, next_cursor or "", int(completed), now),
            )
        return self.conn.total_changes - before

    def count(self, target_user_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM followers WHERE target_user_id = ?", (target_user_id,)
        ).fetchone()
        return int(row[0])

    def export_csv(self, target_user_id: str, destination: Path, limit: int = 0) -> int:
        destination.parent.mkdir(parents=True, exist_ok=True)
        query = """
            SELECT user_id, username, full_name, is_private, is_verified, profile_pic_url, scraped_at
            FROM followers
            WHERE target_user_id = ?
            ORDER BY rowid
        """
        params: list[object] = [target_user_id]
        if limit > 0:
            query += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        with destination.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "user_id",
                    "username",
                    "full_name",
                    "is_private",
                    "is_verified",
                    "profile_pic_url",
                    "scraped_at",
                ]
            )
            writer.writerows(rows)
        return len(rows)

    def export_jsonl(self, target_user_id: str, destination: Path, limit: int = 0) -> int:
        destination.parent.mkdir(parents=True, exist_ok=True)
        query = """
            SELECT user_id, username, full_name, is_private, is_verified, profile_pic_url, scraped_at
            FROM followers
            WHERE target_user_id = ?
            ORDER BY rowid
        """
        params: list[object] = [target_user_id]
        if limit > 0:
            query += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        fields = [
            "user_id",
            "username",
            "full_name",
            "is_private",
            "is_verified",
            "profile_pic_url",
            "scraped_at",
        ]
        with destination.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(dict(zip(fields, row)), ensure_ascii=False) + "\n")
        return len(rows)


def build_client(session_path: Path, username: str, password: str, proxy: str | None):
    try:
        from instagrapi import Client
    except ImportError as exc:
        raise SystemExit("instagrapi is not installed. Run: pip install -r requirements.txt") from exc

    cl = Client()
    if proxy:
        cl.set_proxy(proxy)
    cl.delay_range = [1, 3]

    if session_path.exists():
        try:
            cl.load_settings(session_path)
        except Exception as exc:
            print(f"warning: could not load saved session: {exc}", file=sys.stderr)

    if not username or not password:
        raise SystemExit("Set IG_USERNAME and IG_PASSWORD (or pass --username/--password).")

    cl.login(username, password)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    cl.dump_settings(session_path)
    return cl


def scrape_followers(
    cl,
    store: Store,
    target_username: str,
    page_size: int,
    limit: int,
    min_delay: float,
    max_delay: float,
    fresh: bool,
) -> tuple[str, int, bool]:
    target_id = str(cl.user_id_from_username(target_username))
    if fresh:
        store.reset_target(target_id)

    if store.is_completed(target_id) and not fresh:
        return target_id, store.count(target_id), True

    cursor = store.get_cursor(target_id)
    total = store.count(target_id)

    while True:
        remaining = max(limit - total, 0) if limit > 0 else 0
        if limit > 0 and remaining == 0:
            break

        amount = min(page_size, remaining) if remaining > 0 else page_size
        users, next_cursor = cl.user_followers_v1_chunk(
            target_id,
            max_amount=amount,
            max_id=cursor,
        )
        records = [FollowerRecord.from_user_short(user) for user in users]
        completed = not bool(next_cursor)
        store.save_page(target_username, target_id, records, next_cursor or "", completed)
        total = store.count(target_id)
        print(f"saved {len(records)} from page; unique total={total}")

        if completed or (limit > 0 and total >= limit):
            break

        if next_cursor == cursor:
            raise RuntimeError("Instagram returned the same pagination cursor; aborting to avoid a loop.")
        cursor = next_cursor
        time.sleep(random.uniform(min_delay, max_delay))

    return target_id, total, store.is_completed(target_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export followers for an Instagram account using an authenticated instagrapi session."
    )
    parser.add_argument("target", help="Instagram username whose followers should be exported")
    parser.add_argument("--username", default=os.getenv("IG_USERNAME", ""), help="Login username")
    parser.add_argument("--password", default=os.getenv("IG_PASSWORD", ""), help="Login password")
    parser.add_argument("--proxy", default=os.getenv("IG_PROXY", ""), help="Optional single HTTP/SOCKS proxy")
    parser.add_argument("--limit", type=int, default=0, help="Maximum unique followers to save (0 = all)")
    parser.add_argument("--page-size", type=int, default=100, help="Followers requested per page")
    parser.add_argument("--min-delay", type=float, default=2.0, help="Minimum delay between pages, seconds")
    parser.add_argument("--max-delay", type=float, default=5.0, help="Maximum delay between pages, seconds")
    parser.add_argument("--state-dir", default="state", help="Directory for session and SQLite state")
    parser.add_argument("--output-dir", default="output", help="Directory for CSV/JSONL exports")
    parser.add_argument("--format", choices=("csv", "jsonl", "both"), default="csv")
    parser.add_argument("--fresh", action="store_true", help="Discard stored progress for this target")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.page_size < 1 or args.page_size > 200:
        raise SystemExit("--page-size must be between 1 and 200")
    if args.min_delay < 0 or args.max_delay < args.min_delay:
        raise SystemExit("Invalid delay range")
    if args.limit < 0:
        raise SystemExit("--limit cannot be negative")

    target = args.target.strip().lstrip("@").lower()
    state_dir = Path(args.state_dir)
    output_dir = Path(args.output_dir)
    store = Store(state_dir / "followers.sqlite3")

    try:
        cl = build_client(
            session_path=state_dir / "session.json",
            username=args.username,
            password=args.password,
            proxy=args.proxy or None,
        )
        target_id, total, completed = scrape_followers(
            cl=cl,
            store=store,
            target_username=target,
            page_size=args.page_size,
            limit=args.limit,
            min_delay=args.min_delay,
            max_delay=args.max_delay,
            fresh=args.fresh,
        )

        exported = 0
        if args.format in ("csv", "both"):
            exported = store.export_csv(target_id, output_dir / f"{target}_followers.csv", args.limit)
        if args.format in ("jsonl", "both"):
            exported = store.export_jsonl(target_id, output_dir / f"{target}_followers.jsonl", args.limit)

        print(
            json.dumps(
                {
                    "target": target,
                    "target_user_id": target_id,
                    "stored": total,
                    "exported": exported,
                    "complete": completed,
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
