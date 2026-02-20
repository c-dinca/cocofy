#!/bin/bash
export MUSIC_DIR=/tmp/cocofy-music
export CACHE_DIR=/tmp/cocofy-cache
mkdir -p $MUSIC_DIR $CACHE_DIR
python3 -m uvicorn app:app --reload --port 8888