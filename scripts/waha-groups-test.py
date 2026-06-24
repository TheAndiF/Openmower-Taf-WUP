#!/usr/bin/env python3
"""Small helper to inspect WAHA group metadata from a running WAHA instance."""
import json
import os
import urllib.request
from pathlib import Path

ENV_FILE = Path(os.getenv("ENV_FILE", "/opt/stacks/whatsapp/.env"))


def read_env(key: str, default: str = "") -> str:
    if not ENV_FILE.exists():
        return default
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


port = read_env("WAHA_EXTERNAL_PORT", "9629")
api_key = read_env("WAHA_API_KEY")
waha_url = os.getenv("WAHA_URL", f"http://localhost:{port}").rstrip("/")

headers = {"X-Api-Key": api_key}


def get(path: str):
    req = urllib.request.Request(waha_url + path, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


sessions = get("/api/sessions")
if isinstance(sessions, dict):
    sessions = sessions.get("sessions") or sessions.get("data") or [sessions]

session = next((s for s in sessions if str(s.get("status", "")).upper() == "WORKING"), sessions[0])
session_name = session.get("name") or session.get("session") or session.get("id")

groups = get(f"/api/{session_name}/groups?limit=500&offset=0")
if isinstance(groups, dict):
    groups = groups.get("data") or groups.get("groups") or groups.get("result") or groups.get("items") or []

for idx, group in enumerate(groups, start=1):
    metadata = group.get("groupMetadata", {}) if isinstance(group, dict) else {}
    subject = metadata.get("subject") or group.get("subject") or ""
    print(f"[{idx:02d}] {subject}")
