import re
import json
import logging
import urllib.request
import urllib.parse
from typing import Optional, Tuple, List
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("destillo.transcriber")

YOUTUBE_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')


def extract_video_id(url: str) -> Optional[str]:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().removeprefix("www.")
    path_parts = [part for part in parsed.path.split("/") if part]

    candidate = None
    if host == "youtu.be" and path_parts:
        candidate = path_parts[0]
    elif host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [None])[0]
        elif len(path_parts) >= 2 and path_parts[0] in {"shorts", "embed", "live"}:
            candidate = path_parts[1]

    if candidate and YOUTUBE_ID_RE.fullmatch(candidate):
        return candidate
    return None


def normalize_url(url: str) -> str:
    """
    Normalize any YouTube URL variant to https://www.youtube.com/watch?v=VIDEO_ID
    Strips si, feature, pp, list, index, t and other tracking/playlist params.
    """
    video_id = extract_video_id(url)
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return url


def get_transcript(url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Returns (transcript_text, title, channel).
    Normalizes URL first, then tries youtube-transcript-api, then yt-dlp.
    """
    url = normalize_url(url)
    video_id = extract_video_id(url)
    if not video_id:
        logger.error(f"Could not extract video ID from: {url}")
        return None, None, None

    # Strategy 1: youtube-transcript-api
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        segs = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US", "en-GB"])
        text = " ".join(s["text"] for s in segs)
        text = re.sub(r'\s+', ' ', text).strip()
        title, channel = _get_metadata(url)
        logger.info(f"[{video_id}] Transcript via youtube-transcript-api ({len(text)} chars)")
        return text, title, channel
    except Exception as e:
        logger.warning(f"[{video_id}] youtube-transcript-api failed: {e}")

    # Strategy 2: yt-dlp with auto-generated captions
    try:
        import yt_dlp
        ydl_opts = {
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get("title")
            channel = info.get("uploader") or info.get("channel")

            all_subs = {}
            all_subs.update(info.get("subtitles") or {})
            all_subs.update(info.get("automatic_captions") or {})

            for lang in ["en", "en-US", "en-orig"]:
                if lang in all_subs:
                    for fmt in all_subs[lang]:
                        if fmt.get("ext") in ("vtt", "json3"):
                            try:
                                with urllib.request.urlopen(fmt["url"], timeout=15) as resp:
                                    raw = resp.read().decode("utf-8")
                                text = _parse_vtt(raw) if fmt["ext"] == "vtt" else _parse_json3(raw)
                                if text:
                                    logger.info(f"[{video_id}] Transcript via yt-dlp ({lang}, {fmt['ext']})")
                                    return text, title, channel
                            except Exception as sub_e:
                                logger.warning(f"[{video_id}] Sub download failed: {sub_e}")

            # Last resort: description
            desc = info.get("description", "")
            if desc:
                logger.warning(f"[{video_id}] No transcript found, using description")
                return desc[:8000], title, channel

            return None, title, channel

    except Exception as e:
        logger.error(f"[{video_id}] yt-dlp failed: {e}")
        return None, None, None


def _get_metadata(url: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("title"), info.get("uploader") or info.get("channel")
    except Exception:
        return None, None


def _parse_vtt(vtt: str) -> str:
    lines = []
    for line in vtt.split("\n"):
        line = line.strip()
        if not line or "-->" in line or line.startswith("WEBVTT") or line.isdigit():
            continue
        clean = re.sub(r'<[^>]+>', '', line)
        if clean:
            lines.append(clean)
    return " ".join(lines)


def _parse_json3(raw: str) -> str:
    import json
    try:
        data = json.loads(raw)
        words = []
        for event in data.get("events", []):
            for seg in event.get("segs", []):
                words.append(seg.get("utf8", ""))
        return re.sub(r'\s+', ' ', "".join(words)).strip()
    except Exception:
        return ""


def get_chapters(url: str) -> List[dict]:
    """Extract chapter markers from YouTube video."""
    url = normalize_url(url)
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            chapters_raw = info.get("chapters") or []
            if chapters_raw:
                chapters = []
                for i, ch in enumerate(chapters_raw):
                    title = ch.get("title", "").strip()
                    if not title:
                        continue
                    start = ch.get("start_time", 0)
                    end = ch.get("end_time", chapters_raw[i + 1]["start_time"]) if i + 1 < len(chapters_raw) else None
                    chapters.append({
                        "title": title,
                        "start": start,
                        "end": end,
                    })
                if chapters:
                    logger.info(f"[{extract_video_id(url)}] {len(chapters)} chapters extracted")
                    return chapters
    except Exception as e:
        logger.debug(f"Chapter extraction failed: {e}")

    # Fallback: parse timestamps from description
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            desc = info.get("description", "") or ""
            pattern = re.compile(r"(?:^|\n)(\d{1,2}:\d{2}(?::\d{2})?)\s*[–\-:]\s*(.+)", re.MULTILINE)
            matches = pattern.findall(desc)
            if len(matches) >= 2:
                chapters = []
                for i, (ts, title) in enumerate(matches):
                    parts = [int(x) for x in ts.split(":")]
                    secs = parts[0] * 3600 + parts[1] * 60 + parts[-1] if len(parts) == 3 else parts[0] * 60 + parts[1]
                    end = None if i + 1 >= len(matches) else sum(
                        int(x) * [3600, 60, 1][-len(matches[i + 1][0].split(":")):]
                        for x in matches[i + 1][0].split(":")
                    )
                    chapters.append({"title": title.strip(), "start": secs, "end": end})
                if chapters:
                    logger.info(f"[{extract_video_id(url)}] {len(chapters)} chapters from description")
                    return chapters
    except Exception:
        pass

    return []


def split_transcript_by_chapters(transcript: str, chapters: List[dict]) -> List[dict]:
    """Split transcript text into chapter-aligned segments."""
    if not chapters or not transcript:
        return []

    words = transcript.split()
    total_words = len(words)

    for ch in chapters:
        if ch["end"] is None:
            ch["end"] = chapters[-1]["end"] or 600

    max_sec = max(ch["end"] or 600 for ch in chapters)
    words_per_sec = total_words / max_sec if max_sec > 0 else 1

    result = []
    for ch in chapters:
        start_w = int(ch["start"] * words_per_sec)
        end_w = int((ch["end"] or ch["start"] + 60) * words_per_sec)
        seg_words = words[start_w:end_w]
        result.append({
            "title": ch["title"],
            "text": " ".join(seg_words),
        })
    return result


def truncate_transcript(text: str, max_chars: int = 14000) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n\n[...transcript truncated...]\n\n" + text[-(max_chars - half):]
