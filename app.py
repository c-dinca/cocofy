import os
import json
import subprocess
import asyncio
import hashlib
import glob
import re
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC
import io

app = FastAPI(title="Cocofy")

MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music")
CACHE_DIR = os.environ.get("CACHE_DIR", "/cache")
os.makedirs(MUSIC_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/search")
async def search(q: str):
    """Search YouTube for music"""
    if not q or len(q.strip()) < 2:
        return {"results": []}

    try:
        cmd = [
            "yt-dlp",
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
                # Filter out non-music / very long videos
                duration = data.get("duration") or 0
                if duration and duration > 900:  # skip >15 min
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

        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def get_safe_filename(text):
    """Clean filename"""
    text = re.sub(r'[<>:"/\\|?*]', '', text)
    text = text.strip('. ')
    return text[:200] if text else "unknown"


def find_downloaded(video_id):
    """Check if a song is already downloaded"""
    marker = os.path.join(CACHE_DIR, f"{video_id}.done")
    if os.path.exists(marker):
        with open(marker, "r") as f:
            path = f.read().strip()
            if os.path.exists(path):
                return path
    return None


@app.post("/api/download")
async def download(url: str, title: str = "", artist: str = ""):
    """Download a song from YouTube"""
    # Extract video ID
    video_id = ""
    if "v=" in url:
        video_id = url.split("v=")[1].split("&")[0]
    elif "youtu.be/" in url:
        video_id = url.split("youtu.be/")[1].split("?")[0]

    # Check if already downloaded
    existing = find_downloaded(video_id)
    if existing:
        return {"status": "exists", "path": existing, "id": video_id}

    try:
        safe_artist = get_safe_filename(artist) if artist and artist != "Unknown" else "%(artist,channel)s"
        
        output_template = os.path.join(MUSIC_DIR, "%(artist,channel)s", "%(title)s.%(ext)s")

        cmd = [
            "yt-dlp",
            "-x",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "--embed-metadata",
            "--embed-thumbnail",
            "--parse-metadata", "%(artist)s:%(meta_artist)s",
            "--print", "after_move:filepath",
            "-o", output_template,
            url,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Download failed: {stderr.decode()}")

        # Get the output filepath
        filepath = stdout.decode().strip().split("\n")[-1]

        # Save marker
        if video_id:
            marker = os.path.join(CACHE_DIR, f"{video_id}.done")
            with open(marker, "w") as f:
                f.write(filepath)

        return {"status": "downloaded", "path": filepath, "id": video_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stream/{video_id}")
async def stream(video_id: str):
    """Stream a downloaded song"""
    filepath = find_downloaded(video_id)
    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Song not found")

    return FileResponse(
        filepath,
        media_type="audio/mpeg",
        headers={"Accept-Ranges": "bytes"}
    )


@app.get("/api/cover/{video_id}")
async def cover(video_id: str):
    """Get cover art for a song"""
    filepath = find_downloaded(video_id)
    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Song not found")

    try:
        audio = MP3(filepath, ID3=ID3)
        for tag in audio.tags.values():
            if isinstance(tag, APIC):
                return StreamingResponse(
                    io.BytesIO(tag.data),
                    media_type=tag.mime or "image/jpeg"
                )
    except Exception:
        pass

    raise HTTPException(status_code=404, detail="No cover art")


@app.get("/api/library")
async def library():
    """List all downloaded songs"""
    songs = []
    for marker_file in glob.glob(os.path.join(CACHE_DIR, "*.done")):
        video_id = os.path.basename(marker_file).replace(".done", "")
        with open(marker_file, "r") as f:
            filepath = f.read().strip()

        if not os.path.exists(filepath):
            continue

        # Extract metadata
        title = os.path.splitext(os.path.basename(filepath))[0]
        artist = os.path.basename(os.path.dirname(filepath))

        try:
            audio = MP3(filepath, ID3=ID3)
            duration = int(audio.info.length)
            # Try to get better metadata from tags
            if audio.tags:
                if "TIT2" in audio.tags:
                    title = str(audio.tags["TIT2"])
                if "TPE1" in audio.tags:
                    artist = str(audio.tags["TPE1"])
        except Exception:
            duration = 0

        songs.append({
            "id": video_id,
            "title": title,
            "artist": artist,
            "duration": duration,
            "path": filepath,
        })

    songs.sort(key=lambda x: x.get("title", "").lower())
    return {"songs": songs}


@app.delete("/api/library/{video_id}")
async def delete_song(video_id: str):
    """Delete a song from library"""
    filepath = find_downloaded(video_id)
    if filepath and os.path.exists(filepath):
        os.remove(filepath)
        # Remove parent dir if empty
        parent = os.path.dirname(filepath)
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)

    marker = os.path.join(CACHE_DIR, f"{video_id}.done")
    if os.path.exists(marker):
        os.remove(marker)

    return {"status": "deleted"}
