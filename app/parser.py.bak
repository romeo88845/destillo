import json
import logging
import os
import re
from typing import Dict, Optional

from config_manager import load_config
from transcriber import truncate_transcript

logger = logging.getLogger("destillo.parser")

_SYSTEM = "You are a research assistant that analyzes YouTube video transcripts and returns structured JSON."

_PROMPT = """\
Analyze this YouTube video and produce a structured knowledge-base entry.

**Title**: {title}
**Channel**: {channel}
**URL**: {url}
**Available subject areas**: {subject_areas}

**Transcript** (may be truncated):
{transcript}

Return ONLY valid JSON — no markdown fences, no preamble:
{{
  "subject_area": "<one of the listed subject areas exactly, or 'misc' if none fit>",
  "summary": "<3-4 paragraph comprehensive summary of main ideas and conclusions>",
  "key_points": ["<specific actionable or informative insight>", ...],
  "quotes": ["<verbatim or near-verbatim notable quote>", ...],
  "tags": ["<lowercase-hyphenated-tag>", ...],
  "related_concepts": ["<rabbit hole worth exploring>", ...]
}}

Rules:
- subject_area: must exactly match one of the listed names, or be "misc"
- key_points: 5-10 items, each a full sentence
- quotes: 0-3 only if genuinely interesting; empty array is fine
- tags: 4-8 lowercase tags
- related_concepts: 3-5 ideas this video opens up
"""

_CHAPTER_PROMPT = """\
You are analyzing a section of a YouTube video transcript.
Summarize this chapter in 1-2 concise sentences.

**Video**: {title}
**Chapter**: {chapter_title}

**Transcript excerpt**:
{excerpt}

Return ONLY valid JSON with a single "summary" field:
{{"summary": "<1-2 sentence summary of this chapter's content>"}}
"""


def _call_local(prompt: str, config: dict) -> str:
    from openai import OpenAI
    url = config.get("local_llm_url", "https://opencode.ai/zen")
    model = config.get("local_llm_model", "deepseek-v4-flash-free")
    api_key = os.environ.get("LLM_API_KEY", "none")
    client = OpenAI(base_url=f"{url.rstrip('/')}/v1", api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ]
    )
    return response.choices[0].message.content.strip()


def call_llm(prompt: str, config: dict) -> str:
    provider = config.get("llm_provider", "local")
    if provider == "anthropic":
        import anthropic
        api_key = config.get("anthropic_api_key", "")
        if not api_key:
            raise ValueError("Anthropic API key not configured")
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()
    return _call_local(prompt, config)


def _parse_json(raw: str) -> dict:
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
    raw = re.sub(r"\\(?![\"\\/bfnrtu])", r"\\", raw)
    return json.loads(raw)


def classify_and_parse(
    url: str,
    title: str,
    channel: str,
    transcript: str,
    subject_area_override: Optional[str] = None,
    chapters: Optional[list] = None
) -> Dict:
    config = load_config()

    subject_areas = [sa["name"] for sa in config.get("subject_areas", [])]
    if not subject_areas:
        subject_areas = ["misc"]

    prompt = _PROMPT.format(
        title=title or "Unknown",
        channel=channel or "Unknown",
        url=url,
        subject_areas=", ".join(subject_areas),
        transcript=truncate_transcript(transcript)
    )

    raw = call_llm(prompt, config)
    result = _parse_json(raw)

    if subject_area_override and subject_area_override in subject_areas:
        result["subject_area"] = subject_area_override
    if result.get("subject_area") not in set(subject_areas) | {"misc"}:
        result["subject_area"] = subject_areas[0] if subject_areas else "misc"

    # Per-chapter summaries
    chapter_summaries = []
    if chapters:
        for ch in chapters:
            excerpt = ch.get("text", "")[:4000]
            if not excerpt.strip():
                chapter_summaries.append({"title": ch["title"], "summary": ""})
                continue
            cp = _CHAPTER_PROMPT.format(
                title=title or "Unknown",
                chapter_title=ch["title"],
                excerpt=excerpt,
            )
            try:
                raw_ch = call_llm(cp, config)
                ch_data = _parse_json(raw_ch)
                chapter_summaries.append({
                    "title": ch["title"],
                    "start": ch.get("start", 0),
                    "summary": ch_data.get("summary", ""),
                })
            except Exception as e:
                logger.warning(f"Chapter summary failed for '{ch['title']}': {e}")
                chapter_summaries.append({"title": ch["title"], "start": ch.get("start", 0), "summary": ""})

    result["chapters"] = chapter_summaries

    return result
