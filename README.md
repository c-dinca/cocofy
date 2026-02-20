<p align="center">
  <img src="https://img.shields.io/badge/Cocofy-Self--Hosted%20Music-1db954?style=for-the-badge&logoColor=white" alt="Cocofy">
</p>

<h1 align="center">🎵 Cocofy</h1>

<p align="center">
  <strong>Your personal, self-hosted music streaming app.</strong><br>
  Search any song. Click play. It downloads and streams instantly.<br>
  Like Spotify, but it's yours.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12-blue?style=flat-square" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square" alt="FastAPI">
  <img src="https://img.shields.io/badge/Docker-ready-2496ED?style=flat-square" alt="Docker">
  <img src="https://img.shields.io/github/actions/workflow/status/c-dinca/cocofy/build.yml?style=flat-square&label=build" alt="Build">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License">
</p>

---

## How It Works

1. **Search** for any song or artist
2. **Click** a result — it downloads in the background at max quality
3. **Listen** — playback starts automatically as soon as it's ready
4. **Keep it** — every song is saved to your server permanently

No accounts. No ads. No subscriptions. Just your music, on your hardware.

---

## Features

- 🔍 **Instant Search** — Search YouTube's entire music catalog directly from the app
- ⬇️ **One-Click Download + Play** — Songs download as high-quality MP3 with full metadata and cover art
- 📚 **Persistent Library** — Everything you play is saved and organized on your server
- 🎨 **Cover Art** — Automatically embedded from YouTube thumbnails
- 📱 **Mobile Ready** — Fully responsive, works on iPhone/Android via Tailscale
- ⌨️ **Keyboard Shortcuts** — Space to play/pause, and more
- 🔗 **Navidrome Compatible** — Songs land in your music folder, auto-detected by Navidrome for offline listening via Subsonic apps
- 🐳 **Docker Deployed** — One command to run, auto-updates via Watchtower

---

## Quick Start

### Docker (Recommended)

```bash
docker run -d \
  --name cocofy \
  -p 8888:8888 \
  -v /path/to/your/music:/music \
  -v cocofy-cache:/cache \
  --restart unless-stopped \
  ghcr.io/c-dinca/cocofy:latest
```

Open `http://localhost:8888` and start listening.

### Docker Compose

```yaml
version: "3"
services:
  cocofy:
    image: ghcr.io/c-dinca/cocofy:latest
    container_name: cocofy
    ports:
      - "8888:8888"
    environment:
      - MUSIC_DIR=/music
      - CACHE_DIR=/cache
    volumes:
      - /path/to/your/music:/music
      - cocofy-cache:/cache
    restart: unless-stopped

volumes:
  cocofy-cache:
```

```bash
docker compose up -d
```

### Auto-Updates with Watchtower

Add Watchtower to your `docker-compose.yml` and Cocofy will automatically update whenever a new version is pushed:

```yaml
  watchtower:
    image: containrrr/watchtower
    container_name: watchtower
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      - WATCHTOWER_POLL_INTERVAL=30
      - WATCHTOWER_CLEANUP=true
    restart: unless-stopped
```

---

## Local Development

```bash
git clone https://github.com/c-dinca/cocofy.git
cd cocofy
```

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install yt-dlp
```

Run the dev server:

```bash
./start.sh
```

Open `http://127.0.0.1:8888` — the server auto-reloads on file changes.

---

## Architecture

```
cocofy/
├── app.py                  # FastAPI backend — search, download, stream, library
├── templates/
│   └── index.html          # Single-file frontend (HTML + CSS + JS)
├── Dockerfile              # Production container
├── docker-compose.yml      # Deployment config
├── requirements.txt        # Python dependencies
├── start.sh                # Local dev launcher
└── .github/workflows/
    └── build.yml           # CI/CD — builds & pushes Docker image on every push
```

### API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Serves the web UI |
| `GET` | `/api/search?q=` | Searches YouTube, returns up to 30 results |
| `POST` | `/api/download?url=` | Downloads a song to the server |
| `GET` | `/api/stream/{id}` | Streams a downloaded song |
| `GET` | `/api/cover/{id}` | Returns embedded cover art |
| `GET` | `/api/library` | Lists all downloaded songs |
| `DELETE` | `/api/library/{id}` | Deletes a song |

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python, FastAPI, Uvicorn |
| Frontend | Vanilla HTML, CSS, JavaScript |
| Audio Source | YouTube via yt-dlp |
| Audio Format | MP3 (max quality, embedded metadata + cover art) |
| Metadata | Mutagen (ID3 tags) |
| Container | Docker, GitHub Container Registry |
| CI/CD | GitHub Actions |
| Auto-Update | Watchtower |

---

## Navidrome Integration

Cocofy saves music to the same directory Navidrome watches. Set Navidrome's scan interval to 15 seconds and every song you play in Cocofy will automatically appear in Navidrome:

```
ND_SCANSCHEDULE=@every 15s
```

This means you can use Cocofy to discover and download music, then listen offline on your phone through any Subsonic-compatible app (Substreamer, play:Sub, etc.).

---

## Deployment Pipeline

```
MacBook (code) → git push → GitHub Actions (build image) → GHCR → Watchtower (auto-pull) → Server (restart)
```

Every push to `main` triggers a new Docker image build. Watchtower detects it within 30 seconds and restarts the container. Zero-touch deployments.

---

## Roadmap

- [ ] Queue management & shuffle/repeat
- [ ] Playlists (create, edit, delete)
- [ ] Favorites / liked songs
- [ ] Lyrics display
- [ ] YouTube playlist import (bulk download)
- [ ] Equalizer (Web Audio API)
- [ ] Sleep timer
- [ ] Dynamic background based on cover art
- [ ] Full-screen "Now Playing" view
- [ ] Search history
- [ ] Offline PWA support

---

## Requirements

- Docker (or Python 3.12+ for local dev)
- yt-dlp
- ffmpeg
- ~50MB disk for the app, plus space for your music

---

## License

MIT — do whatever you want with it.

---

<p align="center">
  Built with <3 by <a href="https://github.com/c-dinca">c-dinca</a>
</p>
