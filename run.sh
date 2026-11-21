#!/bin/bash
cd "$(dirname "$0")"
[ -d venv ] || python3 -m venv venv
source venv/bin/activate
[ -f venv/.installed ] || { pip install -r requirements.txt && touch venv/.installed; }
export AUTH_DISABLED=1 STORAGE_BACKEND=local   # old single-PC mode: no login, local files
python app.py
