import os
import sys
import json
import logging
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
import database as db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)-25s %(levelname)s %(message)s")
logger = logging.getLogger("destillo.deadurls")

DISCORD_WEBHOOK = os.environ.get("DESTILLO_DIGEST_WEBHOOK", "")

def check_urls():
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM items WHERE status='done' ORDER BY processed_at DESC"
        ).fetchall()

    dead = []
    for item in rows:
        url = item["url"]
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status >= 400:
                    dead.append((item["title"] or "Untitled", url, resp.status))
        except Exception as e:
            dead.append((item["title"] or "Untitled", url, str(e)[:60]))

    if dead:
        lines = ["**Destillo — Dead URLs Detected**", ""]
        for title, url, reason in dead:
            lines.append(f"- **{title}** — {reason}")
            lines.append(f"  {url}")

        payload = json.dumps({"content": "\n".join(lines)[:2000]}).encode()
        if DISCORD_WEBHOOK:
            req = urllib.request.Request(
                DISCORD_WEBHOOK,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            try:
                urllib.request.urlopen(req, timeout=10)
                logger.info(f"Dead URL alert posted ({len(dead)} dead)")
            except Exception as e:
                logger.warning(f"Failed to post alert: {e}")
        logger.warning(f"Dead URLs: {len(dead)}")
    else:
        logger.info("All URLs alive")

    return dead

if __name__ == "__main__":
    dead = check_urls()
    print(f"{len(dead)} dead URLs found")
