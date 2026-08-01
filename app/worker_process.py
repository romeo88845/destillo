import sys
import os
import json
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-25s %(levelname)s %(message)s"
)
logger = logging.getLogger("destillo.worker")

import database as db
from config_manager import load_config
from parser import classify_and_parse, distill_transcript
from storage import write_markdown
from transcriber import get_transcript, get_chapters, split_transcript_by_chapters
from gbrain_client import ingest as gbrain_ingest

def process(item_id: int):
    item = db.get_item(item_id)
    if not item:
        logger.error(f"Item {item_id} not found")
        sys.exit(1)

    url = item["url"]
    try:
        db.update_item(item_id, status="processing",
                       status_message="Fetching transcript...")
        logger.info(f"Processing [{item_id}]: {url}")

        transcript, title, channel = get_transcript(url)
        if not transcript:
            db.update_item(item_id, status="error", status_message=None,
                           error_message="Could not extract transcript or captions")
            sys.exit(1)

        db.update_item(item_id, title=title, channel=channel,
                       status_message=f"Transcript fetched ({len(transcript):,} chars) — extracting chapters...")

        chapters = get_chapters(url)
        if chapters:
            chapter_segments = split_transcript_by_chapters(transcript, chapters)
            logger.info(f"Split transcript into {len(chapter_segments)} chapter segments")
        else:
            chapter_segments = []

        db.update_item(item_id, title=title, channel=channel,
                       status_message=f"Transcript fetched ({len(transcript):,} chars) — sending to LLM ({len(chapter_segments)} chapters)...")

        parsed = classify_and_parse(
            url=url,
            title=title or "Unknown",
            channel=channel or "Unknown",
            transcript=transcript,
            subject_area_override=item.get("subject_area"),
            chapters=chapter_segments
        )

        db.update_item(item_id,
                       status_message=f"Distilling transcript...")
        cfg = load_config()
        distilled = distill_transcript(transcript, cfg, title=title or "")

        db.update_item(item_id,
                       status_message=f"Writing markdown → {parsed.get('subject_area', 'misc')}...")

        filepath = write_markdown(url, title, channel, parsed, transcript=transcript, distilled=distilled)

        db.update_item(
            item_id,
            status="done",
            status_message=None,
            title=title,
            channel=channel,
            subject_area=parsed.get("subject_area"),
            file_path=filepath,
            processed_at=datetime.now().isoformat(),
            summary=(parsed.get("summary") or "")[:600],
            tags=json.dumps(parsed.get("tags", []))
        )
        logger.info(f"Done [{item_id}]: '{title}' -> {filepath}")

        gbrain_ingest(filepath)

    except Exception as e:
        logger.error(f"Failed [{item_id}] {url}: {e}", exc_info=True)
        db.update_item(item_id, status="error", status_message=None,
                       error_message=str(e)[:500])
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python worker_process.py <item_id>")
        sys.exit(1)
    process(int(sys.argv[1]))
