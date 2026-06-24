#!/usr/bin/env python3
"""Debug helper for WAHA group metadata.

Run this script on a machine that can reach WAHA. It prints group aliases,
chatIds and groupMetadata.subject values so that the MQTT aliases can be
verified before selecting groups through MQTT.
"""

import json
import os
import sys
import urllib.request

WAHA_URL = os.getenv("WAHA_URL", "http://localhost:9629").rstrip("/")
WAHA_API_KEY = os.getenv("WAHA_API_KEY", "")

if not WAHA_API_KEY:
    print("Set WAHA_API_KEY first.", file=sys.stderr)
    raise SystemExit(1)


def get(path: str):
    req = urllib.request.Request(f"{WAHA_URL}{path}", headers={"X-Api-Key": WAHA_API_KEY})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def unwrap(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "groups", "result", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []

sessions = get("/api/sessions")
if isinstance(sessions, dict):
    sessions = sessions.get("data") or sessions.get("sessions") or [sessions]

session = next((s for s in sessions if str(s.get("status", "")).upper() == "WORKING"), sessions[0])
session_name = session.get("name") or session.get("session") or session.get("id")

groups = unwrap(get(f"/api/{session_name}/groups?limit=500&offset=0"))

for index, group in enumerate(groups, start=1):
    metadata = group.get("groupMetadata") or {}
    print(f"g{index:03d}\t{metadata.get('subject', '')}\t{metadata.get('id', group.get('id', ''))}")
