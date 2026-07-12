import os
import re
import json
import logging
from datetime import datetime
from typing import Dict, Optional

from config_manager import load_config, get_subject_area_path

logger = logging.getLogger("destillo.storage")


def slugify(text: str) -> str:
    text = (text or "untitled").lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text).strip('-')
    return text[:60]


SERIES_RE = re.compile(
    r"(?P<series>.+?)\s*[–\-:]\s*(?:Ep|Episode|Part|Chapter)\s*[#]?(?P<ep>\d+)|"
    r"(?P<series2>.+?)\s*[\[\(](?:Ep|Episode|Part|Chapter)[\s\.]*(?P<ep2>\d+)[\]\)]|"
    r"(?P<series3>.+?)\s+S(?P<season>\d+)E(?P<ep3>\d+)",
    re.IGNORECASE
)


def _detect_series(title: Optional[str]) -> Optional[dict]:
    if not title:
        return None
    m = SERIES_RE.search(title)
    if not m:
        return None
    name = m.group("series") or m.group("series2") or m.group("series3") or ""
    ep = m.group("ep") or m.group("ep2") or m.group("ep3") or ""
    season = m.group("season") or ""
    return {
        "name": name.strip().rstrip(",-: "),
        "episode": int(ep) if ep else None,
        "season": int(season) if season else None,
    }


def write_markdown(url: str, title: str, channel: str, parsed: Dict,
                   transcript: str = None) -> str:
    config = load_config()
    subject_area = parsed.get("subject_area", "misc")
    storage_path = get_subject_area_path(config, subject_area)
    os.makedirs(storage_path, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d")
    slug = slugify(title)
    filepath = os.path.join(storage_path, f"{date_str}_{slug}.md")

    counter = 1
    while os.path.exists(filepath):
        filepath = os.path.join(storage_path, f"{date_str}_{slug}_{counter}.md")
        counter += 1

    series = _detect_series(title)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    tags = parsed.get("tags", [])
    title_safe = (title or "Unknown").replace('"', "'")
    tags_yaml = "[" + ", ".join(tags) + "]" if tags else "[]"

    lines = [
        "---",
        f'title: "{title_safe}"',
        f"url: {url}",
        f"channel: {channel or 'Unknown'}",
        f"processed: {now}",
        f"subject_area: {subject_area}",
        f"series: {json.dumps(series) if series else 'false'}",
        f"tags: {tags_yaml}",
        "chapters: []",
        "source: destillo",
        "---",
        "",
        f"# {title or 'Unknown Title'}",
        "",
        f"**Channel**: {channel or 'Unknown'} | **Processed**: {now}",
        f"**Source**: [{url}]({url})",
        "",
        "## Summary",
        "",
        parsed.get("summary", ""),
        "",
        "## Key Points",
        "",
    ]

    for point in parsed.get("key_points", []):
        lines.append(f"- {point}")

    quotes = parsed.get("quotes", [])
    if quotes:
        lines += ["", "## Notable Quotes", ""]
        for q in quotes:
            lines.append(f"> {q}")
            lines.append("")

    related = parsed.get("related_concepts", [])
    if related:
        lines += ["", "## Related Concepts", ""]
        for r in related:
            lines.append(f"- {r}")

    chapter_data = parsed.get("chapters", [])
    if chapter_data:
        lines += ["", "## Chapters", ""]
        for ch in chapter_data:
            ts = ch.get("start", 0)
            mm, ss = divmod(int(ts), 60)
            hh, mm = divmod(mm, 60)
            timestamp = f"{hh}:{mm:02d}:{ss:02d}" if hh else f"{mm}:{ss:02d}"
            summary = ch.get("summary", "").strip()
            if summary:
                lines.append(f"**{timestamp} - {ch['title']}** — {summary}")
            else:
                lines.append(f"**{timestamp} - {ch['title']}**")
            lines.append("")

    if tags:
        lines += ["", "## Tags", ""]
        lines.append(" ".join(f"`{t}`" for t in tags))

    lines += ["", "## My Notes", ""]
    lines.append("")

    if transcript:
        lines += ["", "## Full Transcript", ""]
        lines.append(transcript)

    lines.append("")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info(f"Wrote: {filepath}")
    return filepath
