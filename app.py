import os
import json
import asyncio
import glob
import re
import sqlite3
import time
import urllib.request
import urllib.parse
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC
import io

app = FastAPI(title="Cocofy")
app.add_middleware(GZipMiddleware, minimum_size=500)

MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music")
CACHE_DIR = os.environ.get("CACHE_DIR", "/cache")
DB_PATH = os.path.join(CACHE_DIR, "cocofy.db")
os.makedirs(MUSIC_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

templates = Jinja2Templates(directory="templates")

download_progress: dict[str, float] = {}
search_cache: dict[str, dict] = {}
SEARCH_CACHE_TTL = 300


# ── DATABASE ──────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS playlist_songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER NOT NULL,
            video_id TEXT NOT NULL,
            title TEXT,
            artist TEXT,
            position INTEGER DEFAULT 0,
            FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS favorites (
            video_id TEXT PRIMARY KEY,
            title TEXT,
            artist TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


init_db()


# ── HELPERS ───────────────────────────────────────────────

def find_downloaded(video_id: str):
    marker = os.path.join(CACHE_DIR, f"{video_id}.done")
    if os.path.exists(marker):
        with open(marker, "r") as f:
            path = f.read().strip()
            if os.path.exists(path):
                return path
    return None


def extract_video_id(url: str) -> str:
    if "v=" in url:
        return url.split("v=")[1].split("&")[0]
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0]
    return ""


def get_safe_filename(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*]', '', text)
    text = text.strip('. ')
    return text[:200] if text else "unknown"


def get_song_meta(video_id: str, filepath: str) -> dict:
    title = os.path.splitext(os.path.basename(filepath))[0]
    artist = os.path.basename(os.path.dirname(filepath))
    duration = 0
    try:
        audio = MP3(filepath, ID3=ID3)
        duration = int(audio.info.length)
        if audio.tags:
            if "TIT2" in audio.tags:
                title = str(audio.tags["TIT2"])
            if "TPE1" in audio.tags:
                tag_artist = str(audio.tags["TPE1"])
                if tag_artist and tag_artist not in ("NA", "Unknown", ""):
                    artist = tag_artist
    except Exception:
        pass
    if not artist or artist in ("NA", "Unknown", ""):
        artist = "Unknown"
    return {"id": video_id, "title": title, "artist": artist, "duration": duration}


# ── FRONTEND ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/sw.js")
async def service_worker():
    sw = """
const CACHE='cocofy-v2';
const PRECACHE=['/','/health'];

self.addEventListener('install',e=>{
  e.waitUntil(caches.open(CACHE).then(c=>c.addAll(PRECACHE)).then(()=>self.skipWaiting()));
});

self.addEventListener('activate',e=>{
  e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));
});

self.addEventListener('fetch',e=>{
  const url=new URL(e.request.url);
  if(e.request.method!=='GET')return;
  if(url.pathname.startsWith('/api/stream')||url.pathname.startsWith('/api/download'))return;

  if(url.pathname.startsWith('/api/cover')){
    e.respondWith(caches.open(CACHE).then(c=>c.match(e.request).then(r=>{
      if(r)return r;
      return fetch(e.request).then(resp=>{if(resp.ok)c.put(e.request,resp.clone());return resp}).catch(()=>new Response('',{status:404}));
    })));
    return;
  }

  if(url.origin.includes('fonts.googleapis')||url.origin.includes('fonts.gstatic')){
    e.respondWith(caches.open(CACHE).then(c=>c.match(e.request).then(r=>{
      if(r)return r;
      return fetch(e.request).then(resp=>{if(resp.ok)c.put(e.request,resp.clone());return resp});
    })));
    return;
  }

  if(url.pathname==='/'||url.pathname.startsWith('/api/')){
    e.respondWith(fetch(e.request).then(resp=>{
      if(resp.ok){const cl=resp.clone();caches.open(CACHE).then(c=>c.put(e.request,cl));}
      return resp;
    }).catch(()=>caches.match(e.request)));
    return;
  }
});
"""
    return Response(content=sw, media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})


# ── SEARCH ────────────────────────────────────────────────

@app.get("/api/search")
async def search(q: str):
    if not q or len(q.strip()) < 2:
        return {"results": []}

    try:
        conn = get_db()
        conn.execute("INSERT INTO search_history (query) VALUES (?)", (q.strip(),))
        conn.execute(
            "DELETE FROM search_history WHERE id NOT IN "
            "(SELECT id FROM search_history ORDER BY searched_at DESC LIMIT 50)"
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    cache_key = q.strip().lower()
    cached = search_cache.get(cache_key)
    if cached and time.time() - cached["ts"] < SEARCH_CACHE_TTL:
        return {"results": cached["results"]}

    try:
        cmd = [
            "yt-dlp",
            "--remote-components", "ejs:github",
            f"ytsearch10:{q}",
            "--dump-json",
            "--flat-playlist",
            "--no-download",
            "--default-search", "ytsearch",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()

        results = []
        for line in stdout.decode().strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
                duration = data.get("duration") or 0
                if duration and duration > 900:
                    continue
                results.append({
                    "id": data.get("id", ""),
                    "title": data.get("title", "Unknown"),
                    "artist": data.get("channel", data.get("uploader", "Unknown")),
                    "duration": duration,
                    "thumbnail": data.get("thumbnail") or data.get("thumbnails", [{}])[-1].get("url", ""),
                    "url": f"https://www.youtube.com/watch?v={data.get('id', '')}",
                })
            except json.JSONDecodeError:
                continue

        search_cache[cache_key] = {"results": results, "ts": time.time()}
        if len(search_cache) > 100:
            oldest = min(search_cache, key=lambda k: search_cache[k]["ts"])
            search_cache.pop(oldest, None)

        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/search-history")
async def get_search_history():
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT query FROM search_history ORDER BY searched_at DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return {"history": [r["query"] for r in rows]}


@app.delete("/api/search-history")
async def clear_search_history():
    conn = get_db()
    conn.execute("DELETE FROM search_history")
    conn.commit()
    conn.close()
    return {"status": "cleared"}


# ── DOWNLOAD ──────────────────────────────────────────────

async def _read_stderr_progress(stderr, video_id: str):
    while True:
        line = await stderr.readline()
        if not line:
            break
        text = line.decode().strip()
        m = re.search(r'(\d+\.?\d*)%', text)
        if m:
            download_progress[video_id] = float(m.group(1))


@app.post("/api/download")
async def download(url: str, title: str = "", artist: str = ""):
    video_id = extract_video_id(url)

    existing = find_downloaded(video_id)
    if existing:
        return {"status": "exists", "path": existing, "id": video_id}

    download_progress[video_id] = 0

    try:
        output_template = os.path.join(
            MUSIC_DIR, "%(channel,uploader,artist)s", "%(title)s.%(ext)s"
        )
        cmd = [
            "yt-dlp",
            "--remote-components", "ejs:github",
            "-f", "bestaudio/best",
            "-x",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "--postprocessor-args", "ffmpeg:-b:a 320k",
            "--embed-metadata",
            "--embed-thumbnail",
            "--parse-metadata", "%(artist)s:%(meta_artist)s",
            "--newline",
            "--print", "after_move:filepath",
            "-o", output_template,
            url,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        progress_task = asyncio.create_task(
            _read_stderr_progress(proc.stderr, video_id)
        )
        stdout = await proc.stdout.read()
        await progress_task
        await proc.wait()

        if proc.returncode != 0:
            download_progress.pop(video_id, None)
            raise HTTPException(status_code=500, detail="Download failed")

        filepath = stdout.decode().strip().split("\n")[-1]

        if video_id:
            marker = os.path.join(CACHE_DIR, f"{video_id}.done")
            with open(marker, "w") as f:
                f.write(filepath)

        download_progress.pop(video_id, None)
        return {"status": "downloaded", "path": filepath, "id": video_id}
    except HTTPException:
        raise
    except Exception as e:
        download_progress.pop(video_id, None)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/download-progress/{video_id}")
async def get_download_progress(video_id: str):
    return {"progress": download_progress.get(video_id, -1)}


# ── STREAM & COVER ────────────────────────────────────────

@app.get("/api/stream/{video_id}")
async def stream(video_id: str, request: Request):
    filepath = find_downloaded(video_id)
    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Song not found")

    file_size = os.path.getsize(filepath)
    range_header = request.headers.get("range")

    if range_header:
        m = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1

            def iter_range():
                with open(filepath, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk

            return StreamingResponse(
                iter_range(),
                status_code=206,
                media_type="audio/mpeg",
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Content-Length": str(length),
                    "Accept-Ranges": "bytes",
                    "Cache-Control": "public, max-age=86400",
                },
            )

    return FileResponse(
        filepath,
        media_type="audio/mpeg",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/cover/{video_id}")
async def cover(video_id: str):
    filepath = find_downloaded(video_id)
    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Song not found")

    try:
        audio = MP3(filepath, ID3=ID3)
        for tag in audio.tags.values():
            if isinstance(tag, APIC):
                return StreamingResponse(
                    io.BytesIO(tag.data),
                    media_type=tag.mime or "image/jpeg",
                    headers={"Cache-Control": "public, max-age=604800"},
                )
    except Exception:
        pass

    raise HTTPException(status_code=404, detail="No cover art")


# ── LIBRARY ───────────────────────────────────────────────

@app.get("/api/library")
async def library():
    fav_ids: set[str] = set()
    try:
        conn = get_db()
        fav_ids = {r["video_id"] for r in conn.execute("SELECT video_id FROM favorites").fetchall()}
        conn.close()
    except Exception:
        pass

    songs = []
    for marker_file in glob.glob(os.path.join(CACHE_DIR, "*.done")):
        video_id = os.path.basename(marker_file).replace(".done", "")
        with open(marker_file, "r") as f:
            filepath = f.read().strip()
        if not os.path.exists(filepath):
            continue

        meta = get_song_meta(video_id, filepath)
        meta["favorite"] = video_id in fav_ids
        meta["path"] = filepath
        songs.append(meta)

    songs.sort(key=lambda x: x.get("title", "").lower())
    return {"songs": songs}


@app.delete("/api/library/{video_id}")
async def delete_song(video_id: str):
    filepath = find_downloaded(video_id)
    if filepath and os.path.exists(filepath):
        os.remove(filepath)
        parent = os.path.dirname(filepath)
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)

    marker = os.path.join(CACHE_DIR, f"{video_id}.done")
    if os.path.exists(marker):
        os.remove(marker)

    return {"status": "deleted"}


# ── FAVORITES ─────────────────────────────────────────────

@app.get("/api/favorites")
async def list_favorites():
    conn = get_db()
    favs = conn.execute("SELECT * FROM favorites ORDER BY added_at DESC").fetchall()
    conn.close()
    return {"favorites": [dict(f) for f in favs]}


@app.post("/api/favorites/{video_id}")
async def toggle_favorite(video_id: str, request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    title = body.get("title", "")
    artist = body.get("artist", "")

    conn = get_db()
    existing = conn.execute(
        "SELECT video_id FROM favorites WHERE video_id = ?", (video_id,)
    ).fetchone()

    if existing:
        conn.execute("DELETE FROM favorites WHERE video_id = ?", (video_id,))
        status = "removed"
    else:
        conn.execute(
            "INSERT INTO favorites (video_id, title, artist) VALUES (?, ?, ?)",
            (video_id, title, artist),
        )
        status = "added"

    conn.commit()
    conn.close()
    return {"status": status, "favorite": status == "added"}


# ── PLAYLISTS ─────────────────────────────────────────────

@app.get("/api/playlists")
async def list_playlists():
    conn = get_db()
    playlists = conn.execute("""
        SELECT p.*, COUNT(ps.id) as song_count
        FROM playlists p
        LEFT JOIN playlist_songs ps ON p.id = ps.playlist_id
        GROUP BY p.id
        ORDER BY p.created_at DESC
    """).fetchall()
    conn.close()
    return {"playlists": [dict(p) for p in playlists]}


@app.post("/api/playlists")
async def create_playlist(request: Request):
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    conn = get_db()
    c = conn.execute("INSERT INTO playlists (name) VALUES (?)", (name,))
    pid = c.lastrowid
    conn.commit()
    conn.close()
    return {"id": pid, "name": name}


@app.get("/api/playlists/{playlist_id}")
async def get_playlist(playlist_id: int):
    conn = get_db()
    pl = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
    if not pl:
        conn.close()
        raise HTTPException(status_code=404, detail="Playlist not found")
    songs = conn.execute(
        "SELECT * FROM playlist_songs WHERE playlist_id = ? ORDER BY position",
        (playlist_id,),
    ).fetchall()
    conn.close()
    return {"playlist": dict(pl), "songs": [dict(s) for s in songs]}


@app.put("/api/playlists/{playlist_id}")
async def update_playlist(playlist_id: int, request: Request):
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    conn = get_db()
    conn.execute("UPDATE playlists SET name = ? WHERE id = ?", (name, playlist_id))
    conn.commit()
    conn.close()
    return {"status": "updated"}


@app.delete("/api/playlists/{playlist_id}")
async def delete_playlist(playlist_id: int):
    conn = get_db()
    conn.execute("DELETE FROM playlist_songs WHERE playlist_id = ?", (playlist_id,))
    conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}


@app.post("/api/playlists/{playlist_id}/songs")
async def add_song_to_playlist(playlist_id: int, request: Request):
    body = await request.json()
    video_id = body.get("video_id", "")
    if not video_id:
        raise HTTPException(status_code=400, detail="video_id required")

    conn = get_db()
    row = conn.execute(
        "SELECT MAX(position) as mp FROM playlist_songs WHERE playlist_id = ?",
        (playlist_id,),
    ).fetchone()
    pos = (row["mp"] or 0) + 1
    conn.execute(
        "INSERT INTO playlist_songs (playlist_id, video_id, title, artist, position) VALUES (?, ?, ?, ?, ?)",
        (playlist_id, video_id, body.get("title", ""), body.get("artist", ""), pos),
    )
    conn.commit()
    conn.close()
    return {"status": "added"}


@app.delete("/api/playlists/{playlist_id}/songs/{video_id}")
async def remove_song_from_playlist(playlist_id: int, video_id: str):
    conn = get_db()
    conn.execute(
        "DELETE FROM playlist_songs WHERE playlist_id = ? AND video_id = ?",
        (playlist_id, video_id),
    )
    conn.commit()
    conn.close()
    return {"status": "removed"}


# ── LYRICS ────────────────────────────────────────────────

@app.get("/api/lyrics")
async def get_lyrics(artist: str = "", title: str = ""):
    if not artist or not title:
        raise HTTPException(status_code=400, detail="artist and title required")

    clean_title = re.sub(r'\(.*?\)|\[.*?\]', '', title).strip()
    clean_artist = re.sub(r'\(.*?\)|\[.*?\]', '', artist).strip()

    async def try_lrclib():
        url = (
            f"https://lrclib.net/api/get?"
            f"artist_name={urllib.parse.quote(clean_artist)}&"
            f"track_name={urllib.parse.quote(clean_title)}"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Cocofy/1.0"})
            resp = await asyncio.to_thread(urllib.request.urlopen, req, timeout=5)
            data = json.loads(resp.read().decode())
            return data.get("syncedLyrics") or data.get("plainLyrics") or None
        except Exception:
            return None

    async def try_lrclib_search():
        url = f"https://lrclib.net/api/search?q={urllib.parse.quote(clean_artist + ' ' + clean_title)}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Cocofy/1.0"})
            resp = await asyncio.to_thread(urllib.request.urlopen, req, timeout=5)
            data = json.loads(resp.read().decode())
            if data and len(data) > 0:
                return data[0].get("syncedLyrics") or data[0].get("plainLyrics") or None
        except Exception:
            return None

    lyrics = await try_lrclib()
    if not lyrics:
        lyrics = await try_lrclib_search()

    if lyrics:
        return {"lyrics": lyrics}
    raise HTTPException(status_code=404, detail="Lyrics not found")


# ── IMPORT YOUTUBE PLAYLIST ───────────────────────────────

@app.post("/api/import-playlist")
async def import_playlist_videos(request: Request):
    body = await request.json()
    url = body.get("url", "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL required")

    cmd = [
        "yt-dlp",
        "--remote-components", "ejs:github",
        "--flat-playlist",
        "--dump-json",
        "--no-download",
        url,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()

    videos = []
    for line in stdout.decode().strip().split("\n"):
        if not line:
            continue
        try:
            data = json.loads(line)
            duration = data.get("duration") or 0
            if duration and duration > 900:
                continue
            videos.append({
                "id": data.get("id", ""),
                "title": data.get("title", "Unknown"),
                "artist": data.get("channel", data.get("uploader", "Unknown")),
                "duration": duration,
                "url": f"https://www.youtube.com/watch?v={data.get('id', '')}",
                "thumbnail": data.get("thumbnail") or "",
            })
        except json.JSONDecodeError:
            continue

    return {"videos": videos, "count": len(videos)}
