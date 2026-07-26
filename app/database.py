import sqlite3
import json
import os
from typing import List, Dict, Optional

DB_PATH = "/opt/destillo/data/destillo.db"


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id      TEXT,
                title         TEXT,
                url           TEXT NOT NULL,
                channel       TEXT,
                subject_area  TEXT,
                file_path     TEXT,
                processed_at  TEXT,
                source        TEXT DEFAULT 'manual',
                status        TEXT DEFAULT 'queued',
                status_message TEXT,
                error_message TEXT,
                summary       TEXT,
                tags          TEXT,
                created_at    TEXT DEFAULT (datetime('now'))
            )
        """)
        try:
            conn.execute("ALTER TABLE items ADD COLUMN status_message TEXT")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE items ADD COLUMN favorite INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE items ADD COLUMN updated_at TEXT")
            conn.commit()
        except Exception:
            pass
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_url ON items(url)")
        conn.commit()


def add_item(url: str, source: str = "manual", subject_area_override: str = None, status: str = "queued") -> int:
    with get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO items (url, source, status, subject_area) VALUES (?, ?, ?, ?)",
                (url, source, status, subject_area_override)
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            row = conn.execute("SELECT id FROM items WHERE url = ?", (url,)).fetchone()
            return row["id"] if row else -1


def get_deferred_items() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM items WHERE status = 'deferred' ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def process_deferred() -> int:
    count = 0
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE items SET status = 'queued' WHERE status = 'deferred'"
        )
        count = cur.rowcount
        conn.commit()
    return count


def update_item(item_id: int, **kwargs):
    if not kwargs:
        return
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [item_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE items SET {fields} WHERE id = ?", values)
        conn.commit()


def get_queued_items() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM items WHERE status = 'queued' ORDER BY created_at ASC LIMIT 5"
        ).fetchall()
        return [dict(r) for r in rows]


def get_items(limit: int = 20, offset: int = 0,
              subject_area: str = None, search: str = None,
              include_active: bool = False,
              favorite: int = None,
              tag: str = None, channel: str = None) -> List[Dict]:
    if include_active:
        q = "SELECT * FROM items WHERE 1=1"
    else:
        q = "SELECT * FROM items WHERE status NOT IN ('queued', 'processing')"
    p = []
    if subject_area:
        q += " AND subject_area = ?"; p.append(subject_area)
    if favorite is not None:
        q += " AND favorite = ?"; p.append(favorite)
    if tag:
        q += " AND tags LIKE ?"; p.append(f'%"{tag}"%')
    if channel:
        q += " AND channel = ?"; p.append(channel)
    if search:
        q += " AND (title LIKE ? OR summary LIKE ? OR tags LIKE ? OR channel LIKE ?)"
        s = f"%{search}%"; p.extend([s, s, s, s])
    q += " ORDER BY COALESCE(processed_at, created_at) DESC LIMIT ? OFFSET ?"
    p.extend([limit, offset])
    with get_conn() as conn:
        rows = conn.execute(q, p).fetchall()
        result = []
        for r in rows:
            item = dict(r)
            if item.get("tags") and isinstance(item["tags"], str):
                try:    item["tags"] = json.loads(item["tags"])
                except: item["tags"] = []
            result.append(item)
        return result


def get_item(item_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        if item.get("tags") and isinstance(item["tags"], str):
            try:    item["tags"] = json.loads(item["tags"])
            except: item["tags"] = []
        return item


def get_stats() -> Dict:
    with get_conn() as conn:
        total   = conn.execute("SELECT COUNT(*) FROM items WHERE status='done'").fetchone()[0]
        queued  = conn.execute("SELECT COUNT(*) FROM items WHERE status='queued'").fetchone()[0]
        deferred = conn.execute("SELECT COUNT(*) FROM items WHERE status='deferred'").fetchone()[0]
        proc    = conn.execute("SELECT COUNT(*) FROM items WHERE status='processing'").fetchone()[0]
        errors  = conn.execute("SELECT COUNT(*) FROM items WHERE status='error'").fetchone()[0]
        by_area = conn.execute(
            "SELECT subject_area, COUNT(*) as count FROM items "
            "WHERE status='done' GROUP BY subject_area ORDER BY count DESC"
        ).fetchall()
        return {"total": total, "queued": queued, "deferred": deferred, "processing": proc,
                "errors": errors, "by_subject_area": [dict(r) for r in by_area]}


def get_tags() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT tags FROM items WHERE status='done' AND tags IS NOT NULL AND tags != '[]'"
        ).fetchall()
        counts = {}
        for row in rows:
            try:
                t = json.loads(row["tags"])
                if isinstance(t, list):
                    for tag in t:
                        counts[tag] = counts.get(tag, 0) + 1
            except Exception:
                pass
        return sorted([{"tag": k, "count": v} for k, v in counts.items()], key=lambda x: -x["count"])


def get_channels() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT channel, COUNT(*) as count FROM items WHERE status='done' "
            "AND channel IS NOT NULL AND channel != '' "
            "GROUP BY channel ORDER BY count DESC"
        ).fetchall()
        return [{"channel": r["channel"], "count": r["count"]} for r in rows]


def delete_item(item_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.commit()
