import os
import re
import json
import sys
import logging
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database as db
from config_manager import load_config, save_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-25s %(levelname)s %(message)s"
)
logger = logging.getLogger("destillo")

WORKER_SCRIPT = os.path.join(os.path.dirname(__file__), "worker_process.py")
MAX_CONCURRENT = 3
ACTIVE_PIDS = set()
ACTIVE_LOCK = threading.Lock()


def _clean_finished():
    """Remove finished PIDs from the active set."""
    with ACTIVE_LOCK:
        finished = [p for p in ACTIVE_PIDS if os.waitpid(p, os.WNOHANG) == (p, 0)]
        for p in finished:
            ACTIVE_PIDS.discard(p)


def _spawn_worker(item_id: int):
    """Launch a subprocess to process an item."""
    with ACTIVE_LOCK:
        if len(ACTIVE_PIDS) >= MAX_CONCURRENT:
            return False
    proc = subprocess.Popen(
        [sys.executable, WORKER_SCRIPT, str(item_id)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with ACTIVE_LOCK:
        ACTIVE_PIDS.add(proc.pid)
    logger.info(f"Worker spawned for item {item_id} (pid {proc.pid})")
    return True


def _queue_worker():
    """Background thread: poll for queued items, dispatch to subprocesses."""
    while True:
        try:
            _clean_finished()
            items = db.get_queued_items()
            for item in items:
                if not _spawn_worker(item["id"]):
                    break
        except Exception:
            pass
        time.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    t = threading.Thread(target=_queue_worker, daemon=True)
    t.start()
    logger.info("Destillo started")
    yield


app = FastAPI(title="Destillo", lifespan=lifespan)

STATIC_DIR = "/opt/destillo/app/static"
DATA_DIR = "/opt/destillo/data"


class SubmitRequest(BaseModel):
    url: str
    subject_area: Optional[str] = None
    defer: bool = False


@app.post("/api/submit")
def submit_url(req: SubmitRequest):
    from transcriber import normalize_url, extract_video_id
    url = normalize_url(req.url.strip())
    if not extract_video_id(url):
        raise HTTPException(400, "Could not find a YouTube video ID in that URL")
    initial_status = "deferred" if req.defer else "queued"
    item_id = db.add_item(url, source="manual",
                          subject_area_override=req.subject_area or None,
                          status=initial_status)
    if item_id == -1:
        with db.get_conn() as conn:
            row = conn.execute("SELECT id, status FROM items WHERE url = ?", (url,)).fetchone()
        if row and row["status"] == "error":
            db.update_item(row["id"], status="queued", error_message=None)
            return {"id": row["id"], "message": "Re-queued for processing"}
        raise HTTPException(409, "URL is already in the library")
    msg = "Saved for later" if req.defer else "Queued for processing"
    return {"id": item_id, "message": msg}


@app.post("/api/process-deferred")
def trigger_process_deferred():
    count = db.process_deferred()
    return {"ok": True, "processed": count}


@app.get("/api/tags")
def get_tags():
    return {"tags": db.get_tags()}


@app.get("/api/channels")
def get_channels():
    return {"channels": db.get_channels()}


@app.get("/api/library")
def get_library(limit: int = 20, offset: int = 0,
                subject_area: str = None, search: str = None,
                include_active: bool = False,
                favorite: int = None, tag: str = None,
                channel: str = None):
    items = db.get_items(limit=limit, offset=offset,
                         subject_area=subject_area, search=search,
                         include_active=include_active,
                         favorite=favorite, tag=tag, channel=channel)
    return {"items": items}


@app.get("/api/library/{item_id}")
def get_item(item_id: int):
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(404, "Not found")
    return item


@app.post("/api/library/{item_id}/retry")
def retry_item(item_id: int):
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(404, "Not found")
    if item["status"] != "error":
        raise HTTPException(400, "Item is not in error state")
    db.update_item(item_id, status="queued", error_message=None, status_message=None)
    return {"ok": True}


@app.post("/api/library/{item_id}/favorite")
def toggle_favorite(item_id: int):
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(404, "Not found")
    new_val = 0 if item.get("favorite") else 1
    db.update_item(item_id, favorite=new_val)
    return {"ok": True, "favorite": bool(new_val)}


@app.get("/api/favorites")
def get_favorites():
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM items WHERE favorite=1 ORDER BY updated_at DESC").fetchall()
        items = [dict(r) for r in rows]
        for item in items:
            if item.get("tags") and isinstance(item["tags"], str):
                try: item["tags"] = json.loads(item["tags"])
                except: item["tags"] = []
    return {"items": items}


@app.delete("/api/library/{item_id}")
def delete_item(item_id: int):
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(404, "Not found")
    filepath = item.get("file_path")
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
            logger.info(f"Deleted file: {filepath}")
        except Exception as e:
            logger.warning(f"Could not delete file {filepath}: {e}")
    db.delete_item(item_id)
    return {"ok": True, "file_deleted": bool(filepath and os.path.exists(filepath))}


@app.get("/api/stats")
def get_stats():
    return db.get_stats()


@app.get("/api/status")
def get_status():
    with ACTIVE_LOCK:
        active = len(ACTIVE_PIDS)
    return {
        "worker_running": True,
        "version": "destillo-0.1",
        "active_workers": active,
        "max_workers": MAX_CONCURRENT
    }


@app.post("/api/process-now")
def trigger_process():
    items = db.get_queued_items()
    count = 0
    for item in items:
        if _spawn_worker(item["id"]):
            count += 1
    return {"ok": True, "processed": count}


@app.get("/api/config")
def get_config():
    config = load_config()
    safe = json.loads(json.dumps(config))
    for key in list(safe.keys()):
        if "key" in key.lower() or "password" in key.lower() or "secret" in key.lower():
            if safe[key]:
                safe[key] = "••••••••"
    return safe


@app.post("/api/config")
def update_config(new_config: dict):
    current = load_config()
    for key in list(new_config.keys()):
        if isinstance(new_config[key], str) and new_config[key].startswith("•"):
            new_config[key] = current.get(key, "")
    save_config(new_config)
    return {"ok": True}


class ReclassifyRequest(BaseModel):
    subject_area: Optional[str] = None
    tags: Optional[list] = None


def _rewrite_markdown_metadata(filepath: str, subject_area: str = None, tags: list = None):
    if not os.path.exists(filepath):
        return
    with open(filepath, encoding="utf-8") as f:
        content = f.read()
    if subject_area is not None:
        content = re.sub(r"^subject_area:.*$", f"subject_area: {subject_area}", content, flags=re.MULTILINE)
    if tags is not None:
        tag_str = f"[{', '.join(tags)}]"
        content = re.sub(r"^tags:.*$", f"tags: {tag_str}", content, flags=re.MULTILINE)
        new_tag_line = " ".join(f"`{t}`" for t in tags) if tags else ""
        if re.search(r"^## Tags\s*$", content, flags=re.MULTILINE):
            content = re.sub(
                r"(^## Tags\s*\n\n?).*?(\n(?=##|\Z))",
                lambda m: m.group(1) + new_tag_line + m.group(2),
                content, flags=re.MULTILINE | re.DOTALL
            )
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)


@app.patch("/api/library/{item_id}")
def reclassify_item(item_id: int, req: ReclassifyRequest):
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(404, "Not found")
    updates = {}
    if req.subject_area is not None:
        updates["subject_area"] = req.subject_area
    if req.tags is not None:
        updates["tags"] = json.dumps(req.tags)
    if updates:
        db.update_item(item_id, **updates)
    if item.get("file_path") and os.path.exists(item["file_path"]):
        _rewrite_markdown_metadata(
            item["file_path"],
            subject_area=req.subject_area,
            tags=req.tags
        )
    return {"ok": True}


@app.get("/api/library/{item_id}/file")
def get_item_file(item_id: int):
    item = db.get_item(item_id)
    if not item or not item.get("file_path"):
        raise HTTPException(404, "File not found")
    fp = item["file_path"]
    if not os.path.exists(fp):
        raise HTTPException(404, f"File does not exist on disk: {fp}")
    return FileResponse(fp, media_type="text/markdown",
                        filename=os.path.basename(fp))


@app.get("/share", response_class=HTMLResponse)
def share_page(url: str = Query("")):
    html = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Destillo</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f8fafc;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:1rem}
.card{background:white;border-radius:16px;padding:2rem;max-width:400px;width:100%;box-shadow:0 4px 24px rgba(0,0,0,.08);text-align:center}
.logo{font-size:2rem;margin-bottom:.5rem}
h1{font-size:1.25rem;color:#1a1a1a;margin-bottom:.5rem}
p{color:#666;font-size:.9rem;margin-bottom:1.5rem;line-height:1.5}
.url{font-size:.8rem;color:#10b981;word-break:break-all;margin-bottom:1.5rem;background:#f0fdf4;padding:.75rem;border-radius:8px}
.status{display:inline-block;padding:.5rem 1.25rem;border-radius:8px;font-size:.875rem;font-weight:600}
.status-ok{background:#10b981;color:white}
.status-err{background:#ef4444;color:white}
.btn{display:inline-block;margin-top:1rem;padding:.5rem 1rem;border-radius:8px;background:#1a1a1a;color:white;text-decoration:none;font-size:.875rem}
</style></head>
<body>
<div class="card">
<div class="logo">&zwj;&#x2697;&#xFE0F;</div>
<h1>Destillo</h1>
"""
    if not url:
        html += """<p>No URL provided. Share a YouTube video link to capture it.</p>
<p class="status status-err">No URL</p>"""
    else:
        from transcriber import extract_video_id, normalize_url
        normalized = normalize_url(url)
        if not extract_video_id(normalized):
            html += f"""<p>Not a valid YouTube URL.</p>
<div class="url">{url[:200]}</div>
<p class="status status-err">Invalid URL</p>"""
        else:
            try:
                import urllib.request, json as j
                req = urllib.request.Request(
                    "http://localhost:8097/api/submit",
                    data=j.dumps({"url": normalized}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = j.loads(resp.read())
                if data.get("id"):
                    html += f"""<p>Captured!</p>
<div class="url">{normalized}</div>
<p class="status status-ok">Queued for processing</p>"""
                else:
                    html += f"""<p>Already in your library.</p>
<div class="url">{normalized}</div>
<p class="status status-ok">Already saved</p>"""
            except Exception as e:
                html += f"""<p>Could not capture video.</p>
<div class="url">{normalized}</div>
<p class="status status-err">Error: {str(e)[:100]}</p>"""

    html += """<a class="btn" href="/">Go to Library</a></div></body></html>"""
    return HTMLResponse(html)


@app.get("/bookmarklet", response_class=HTMLResponse)
def bookmarklet_page():
    bookmarklet_js = (
        "javascript:void(function(){"
        "var u=location.href;"
        "var s=document.createElement('script');"
        "s.src='//cdn.jsdelivr.net/npm/urijs@1.19.11/src/URI.min.js';"
        "s.onload=function(){"
        "var v=new URI(u).search(true).v||u.match(/[?&]v=([^&]+)/)?.[1];"
        "var url=v?'https://youtube.com/watch?v='+v:u;"
        "var x=new XMLHttpRequest();"
        "x.open('POST','http://optimus.tail19365.ts.net:8097/api/submit',true);"
        "x.setRequestHeader('Content-Type','application/json');"
        "x.onload=function(){alert(JSON.parse(x.responseText).message||'Done!')};"
        "x.send(JSON.stringify({url:url}));"
        "};document.head.appendChild(s)})()"
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Destillo — Bookmarklet</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f8fafc;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:1rem}}
.card{{background:white;border-radius:16px;padding:2rem;max-width:500px;width:100%;box-shadow:0 4px 24px rgba(0,0,0,.08)}}
h1{{font-size:1.25rem;color:#1a1a1a;margin-bottom:1rem}}
h2{{font-size:1rem;color:#333;margin:1.5rem 0 .75rem}}
p{{color:#555;font-size:.9rem;line-height:1.6;margin-bottom:.75rem}}
code{{background:#f1f5f9;padding:.15rem .4rem;border-radius:4px;font-size:.85rem;word-break:break-all}}
.bm{{display:inline-block;padding:.75rem 1.5rem;background:#1a1a1a;color:white;border-radius:8px;text-decoration:none;font-size:.9rem;cursor:move;margin:.5rem 0}}
.bm:hover{{background:#333}}
.step{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:1rem;margin:.5rem 0}}
.step b{{color:#10b981}}
</style></head>
<body>
<div class="card">
<h1>Destillo Bookmarklet</h1>
<p>Drag this button to your bookmarks bar, or right-click and bookmark the link:</p>
<a class="bm" href="{bookmarklet_js}">Destillo</a>
<h2>How to install on Android (Chrome)</h2>
<div class="step"><b>1.</b> Bookmark this page (⋮ → Star icon)</div>
<div class="step"><b>2.</b> Open your bookmarks, find "Destillo", edit it</div>
<div class="step"><b>3.</b> Replace the URL with the bookmarklet code below</div>
<div class="step"><b>4.</b> Save. On any YouTube page, tap the bookmark to capture</div>
<h2>Or copy this code manually:</h2>
<p>Create a bookmark with this URL:</p>
<code style="display:block;background:#f1f5f9;padding:.75rem;border-radius:6px;font-size:.75rem;margin-top:.5rem">{bookmarklet_js[:200]}...</code>
</div></body></html>"""
    return HTMLResponse(html)



@app.get("/api/library/{item_id}/keypoints")
def get_item_keypoints(item_id: int):
    """Parse ## Key Points from the markdown file."""
    item = db.get_item(item_id)
    if not item or not item.get("file_path"):
        return {"key_points": []}
    fp = item["file_path"]
    if not os.path.exists(fp):
        return {"key_points": []}
    with open(fp, encoding="utf-8") as f:
        content = f.read()
    m = re.search(r"^## Key Points\s*$", content, re.MULTILINE)
    if not m:
        return {"key_points": []}
    start = m.end()
    next_m = re.search(r"^## ", content[start:], re.MULTILINE)
    section = content[start:start + (next_m.start() if next_m else len(content))].strip()
    points = [line.strip().lstrip("-* ") for line in section.split("\n") if line.strip().startswith("-")]
    return {"key_points": points}


@app.get("/api/library/{item_id}/notes")
def get_item_notes(item_id: int):
    """Return the raw content of ## My Notes section from the markdown file."""
    item = db.get_item(item_id)
    if not item or not item.get("file_path"):
        raise HTTPException(404, "File not found")
    fp = item["file_path"]
    if not os.path.exists(fp):
        raise HTTPException(404, "File does not exist on disk")
    with open(fp, encoding="utf-8") as f:
        content = f.read()
    # Extract text between ## My Notes and the next ## heading (or EOF)
    m = re.search(r"^## My Notes\s*$", content, re.MULTILINE)
    if not m:
        return {"notes": ""}
    start = m.end()
    # Find next heading
    next_m = re.search(r"^## ", content[start:], re.MULTILINE)
    notes = content[start:start + (next_m.start() if next_m else len(content))].strip()
    return {"notes": notes}


@app.post("/api/library/{item_id}/notes")
def save_item_notes(item_id: int, req: dict):
    """Save notes back into the ## My Notes section of the markdown file."""
    notes = (req.get("notes") or "").strip()
    item = db.get_item(item_id)
    if not item or not item.get("file_path"):
        raise HTTPException(404, "File not found")
    fp = item["file_path"]
    if not os.path.exists(fp):
        raise HTTPException(404, "File does not exist on disk")
    with open(fp, encoding="utf-8") as f:
        content = f.read()
    m = re.search(r"^## My Notes\s*$", content, re.MULTILINE)
    if not m:
        # Add the section before ## Full Transcript or at end
        insert_before = re.search(r"^## Full Transcript", content, re.MULTILINE)
        if insert_before:
            pos = insert_before.start()
            notes_block = "## My Notes\n\n" + notes + "\n\n"
            content = content[:pos] + notes_block + content[pos:]
        else:
            content += "\n## My Notes\n\n" + notes + "\n"
    else:
        start = m.end()
        next_m = re.search(r"^## ", content[start:], re.MULTILINE)
        end = start + (next_m.start() if next_m else len(content))
        content = content[:start] + "\n" + notes + "\n" + content[end:]
    with open(fp, "w", encoding="utf-8") as f:
        f.write(content)
    return {"ok": True}

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

import urllib.request, json as json_mod

@app.post("/api/fast-tutor/{item_id}")
def upload_to_fast_tutor(item_id: int):
    """Upload a Destillo item to Fast-Tutor."""
    # Get the file content from database
    import os
    md = None
    item = db.get_item(item_id)
    if item and item.get("file_path"):
        fp = item["file_path"]
        if os.path.exists(fp):
            md = open(fp).read()
    
    if not md:
        raise HTTPException(404, "No content found for this item")
    
    # Forward to Fast-Tutor
    url = "http://127.0.0.1:8411/api/ingest"
    data = json_mod.dumps({"content": md, "exam_code": "MS-Intune"}).encode()
    req = urllib.request.Request(url, data=data, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req)
        result = json_mod.loads(resp.read())
        return result
    except Exception as e:
        raise HTTPException(502, f"Fast-Tutor error: {str(e)}")
