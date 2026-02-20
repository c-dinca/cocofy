echo '#!/bin/bash
export MUSIC_DIR=/tmp/cocofy-music
export CACHE_DIR=/tmp/cocofy-cache
mkdir -p $MUSIC_DIR $CACHE_DIR
/opt/homebrew/bin/python3 -m pip install -r requirements.txt -q
/opt/homebrew/bin/python3 -m pip install yt-dlp -q
/opt/homebrew/bin/python3 -m uvicorn app:app --reload --port 8888' > start.sh