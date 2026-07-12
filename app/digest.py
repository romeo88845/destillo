import os
import sys
import json
import logging
import urllib.request
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import database as db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)-25s %(levelname)s %(message)s")
logger = logging.getLogger("destillo.digest")

DISCORD_WEBHOOK = os.environ.get("DESTILLO_DIGEST_WEBHOOK", "")

def run_digest(days: int = 7):
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM items WHERE status='done' AND processed_at >= ? ORDER BY processed_at DESC",
            (cutoff,)
        ).fetchall()

    if not rows:
        msg = f"No captures in the last {days} days."
        logger.info(msg)
        return msg

    by_area = {}
    for r in rows:
        area = r["subject_area"] or "misc"
        by_area.setdefault(area, []).append(dict(r))

    lines = [f"**Destillo Digest — Last {days} Days**", f"**{len(rows)} captures**\n"]
    for area, items in sorted(by_area.items()):
        lines.append(f"**{area}** ({len(items)})")
        for item in items:
            title = item["title"] or "Untitled"
            lines.append(f"- {title}")
        lines.append("")

    if DISCORD_WEBHOOK:
        payload = json.dumps({"content": "\n".join(lines)[:2000]}).encode()
        req = urllib.request.Request(
            DISCORD_WEBHOOK,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            logger.info(f"Digest posted to Discord ({len(rows)} items)")
        except Exception as e:
            logger.warning(f"Failed to post digest: {e}")

    return "\n".join(lines)

if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    print(run_digest(days))
