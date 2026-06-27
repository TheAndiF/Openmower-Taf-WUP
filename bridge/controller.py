#!/usr/bin/env python3
"""Messenger MQTT Controller for OpenMower.

The public MQTT API is provider-neutral below ``messenger/``.  The currently
implemented provider is WAHA and therefore WAHA-specific data lives below
``messenger/waha/``.  Mobert is provider-neutral and lives below
``messenger/bot/``.
"""

from __future__ import annotations

import copy
import json
import os
import re
import signal
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import paho.mqtt.client as mqtt
import requests


# ---------------------------------------------------------------------------
# Environment parsing helpers
# ---------------------------------------------------------------------------

def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "ja", "aktiv"}


def env_int(name: str, default: int, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except Exception:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


# ---------------------------------------------------------------------------
# Environment configuration
# ---------------------------------------------------------------------------

MQTT_HOST = os.getenv("MQTT_HOST", "Mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_BASE_TOPIC = os.getenv("MQTT_BASE_TOPIC", "messenger").strip("/")

WAHA_URL = os.getenv("WAHA_URL", "http://waha:3000").rstrip("/")
WAHA_API_KEY = os.getenv("WAHA_API_KEY", "")
# Preferred WAHA session for sending/receiving.  Empty means: use the session
# configured in bot_commands.xml; "default" is intentionally treated as
# "not configured" for compatibility with older XML templates.
WAHA_SESSION = (
    os.getenv("WAHA_SESSION")
    or os.getenv("WHATSAPP_SESSION")
    or os.getenv("WAHA_DEFAULT_SESSION")
    or ""
).strip()
# Automatic self-healing for stuck WAHA sessions.  This only uses the WAHA API
# inside the Docker network; it does not require Docker socket access.
WAHA_AUTO_REPAIR_SESSION = env_bool("WAHA_AUTO_REPAIR_SESSION", True)
WAHA_START_STOPPED_SESSION = env_bool("WAHA_START_STOPPED_SESSION", True)
WAHA_STARTING_TIMEOUT_SECONDS = env_int("WAHA_STARTING_TIMEOUT_SECONDS", 90, minimum=10)
WAHA_REPAIR_COOLDOWN_SECONDS = env_int("WAHA_REPAIR_COOLDOWN_SECONDS", 300, minimum=10)
WAHA_MAX_RESTARTS_PER_HOUR = env_int("WAHA_MAX_RESTARTS_PER_HOUR", 3, minimum=0)
WAHA_SEND_READY_WAIT_SECONDS = env_int("WAHA_SEND_READY_WAIT_SECONDS", 30, minimum=0)
WAHA_WATCHDOG_SECONDS = env_int("WAHA_WATCHDOG_SECONDS", 60, minimum=30)
PROVIDER_NAME = os.getenv("MESSENGER_PROVIDER", "waha").strip().lower() or "waha"
PROTOCOL_NAME = os.getenv("MESSENGER_PROTOCOL", "whatsapp").strip().lower() or "whatsapp"

# Human-readable deployment hints for MQTT status/description.
# Do not publish secrets.  The dashboard password and API key remain in the
# host-side .env file and are only referenced by variable name.
WAHA_EXTERNAL_PORT = os.getenv("WAHA_EXTERNAL_PORT", "9629")
WAHA_DASHBOARD_URL = os.getenv("WAHA_DASHBOARD_URL", f"http://<openmower-ip>:{WAHA_EXTERNAL_PORT}/dashboard")
CREDENTIALS_FILE_HINT = os.getenv("CREDENTIALS_FILE_HINT", "/opt/stacks/whatsapp/.env")

REFRESH_SECONDS = int(os.getenv("CONTROLLER_REFRESH_SECONDS", "60"))
BOT_HTTP_PORT = int(os.getenv("BOT_HTTP_PORT", "8080"))

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
CONFIG_FILE = DATA_DIR / "config.json"
BOT_COMMANDS_FILE = Path(os.getenv("BOT_COMMANDS_FILE", str(DATA_DIR / "bot_commands.xml")))
DEFAULT_BOT_COMMANDS_FILE = Path(os.getenv("DEFAULT_BOT_COMMANDS_FILE", "/app/bot_commands.example.xml"))

RUNNING = True
MQTT_CLIENT: Optional[mqtt.Client] = None

# These values are refreshed from WAHA and reused by MQTT commands and webhooks.
SESSION: Dict[str, Any] = {}
GROUPS_BY_KEY: Dict[str, Dict[str, str]] = {}
GROUP_KEYS_BY_CHAT_ID: Dict[str, str] = {}
LAST_STATUS_ERROR = ""

# Message history lives in memory and is mirrored as a retained MQTT snapshot.
MESSAGE_HISTORY: Deque[Dict[str, Any]] = deque(maxlen=10)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    print(f"[messenger-mqtt-controller] {message}", flush=True)


def topic(*parts: str) -> str:
    return "/".join([MQTT_BASE_TOPIC] + [p.strip("/") for p in parts if p != ""])


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "ja", "aktiv"}


def read_text_payload(payload: bytes) -> str:
    return payload.decode("utf-8", errors="replace").strip()


def parse_json_payload(payload: str, fallback: Any = None) -> Any:
    if not payload.strip():
        return {} if fallback is None else fallback
    try:
        return json.loads(payload)
    except Exception:
        if fallback is not None:
            return fallback
        raise


def publish(client: mqtt.Client, mqtt_topic: str, payload: Any, retain: bool = True) -> None:
    if isinstance(payload, (dict, list)):
        value = json.dumps(payload, ensure_ascii=False)
    elif payload is None:
        value = ""
    else:
        value = str(payload)
    client.publish(mqtt_topic, value, qos=0, retain=retain)


def clear_retained(client: mqtt.Client, mqtt_topic: str) -> None:
    client.publish(mqtt_topic, payload=None, qos=0, retain=True)


def publish_validation(
    client: mqtt.Client,
    mqtt_topic: str,
    *,
    valid: bool,
    mode: str,
    accepted: Optional[Dict[str, Any]] = None,
    rejected: Optional[Dict[str, Any]] = None,
    remarks: Optional[List[str]] = None,
    request_id: str = "",
    result: Optional[Dict[str, Any]] = None,
) -> None:
    payload: Dict[str, Any] = {
        "valid": valid,
        "ok": valid,
        "mode": mode,
        "time": now_iso(),
        "accepted": accepted or {},
        "rejected": rejected or {},
        "remarks": remarks or [],
    }
    if request_id:
        payload["request_id"] = request_id
    if result is not None:
        payload["result"] = result
    publish(client, mqtt_topic, payload)


def publish_error(client: mqtt.Client, source_topic: str, exc: Exception) -> None:
    global LAST_STATUS_ERROR
    LAST_STATUS_ERROR = str(exc)
    publish(client, topic("status", "json"), make_status_json(error=str(exc)))
    publish(client, topic("waha", "session", "last_error"), str(exc))
    publish(client, topic("waha", "messages", "out", "validation", "json"), {
        "valid": False,
        "ok": False,
        "time": now_iso(),
        "source_topic": source_topic,
        "rejected": {"error": str(exc)},
        "remarks": ["Fehler beim Verarbeiten der MQTT-Nachricht"],
    })
    log(f"Error handling {source_topic}: {exc}")


# ---------------------------------------------------------------------------
# Persistent configuration
# ---------------------------------------------------------------------------

def default_config() -> Dict[str, Any]:
    return {
        "waha": {
            "enabled": True,
            "session": WAHA_SESSION,
            "auto_repair_session": WAHA_AUTO_REPAIR_SESSION,
            "start_stopped_session": WAHA_START_STOPPED_SESSION,
            "starting_timeout_seconds": WAHA_STARTING_TIMEOUT_SECONDS,
            "repair_cooldown_seconds": WAHA_REPAIR_COOLDOWN_SECONDS,
            "max_restarts_per_hour": WAHA_MAX_RESTARTS_PER_HOUR,
            "send_ready_wait_seconds": WAHA_SEND_READY_WAIT_SECONDS,
            "watchdog_seconds": WAHA_WATCHDOG_SECONDS,
        },
        "default_group": "",
        "forward_topics": [],
        "templates": {},
        "messages": {
            "history": {
                "enabled": True,
                "limit": 10,
            }
        },
        "bot": {
            "enabled": True,
            # Empty means: use the wakeWord defined by the XML whatsapp_watchdog module.
            "wake_word": "",
            # listen_group is configured by MQTT, e.g. g001.
            # The controller resolves g001 to the internal WhatsApp group chatId.
            "listen_group": "",
            # When enabled through WhatsApp, normal command confirmations append
            # the current compact OpenMower status below the confirmation text.
            "append_status_to_confirmations": False,
        },
        "status_push": {
            "enabled": False,
            "interval_minutes": 30,
            "min_interval_minutes": 5,
            # Empty means: send to the current default group.  When the mode is
            # enabled from WhatsApp, the controller stores the listening group.
            "target_group": "",
        },
        "gps": {
            # Placeholder settings for the future MQTT interface that will provide
            # real latitude/longitude values.  Until those values exist, Mobert
            # shows a clear placeholder instead of pretending that local map x/y
            # coordinates are Google Maps coordinates.
            "position_placeholder": {
                "enabled": True,
                "latitude": "{latitude}",
                "longitude": "{longitude}",
                "position_text": "Platzhalter: GPS-Koordinaten aus zukuenftiger MQTT-Schnittstelle",
                "map_url": "https://www.google.com/maps?q={latitude},{longitude}",
            },
        },
    }


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def normalize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = deep_merge(default_config(), config or {})
    normalized["forward_topics"] = list(normalized.get("forward_topics") or [])
    normalized["templates"] = dict(normalized.get("templates") or {})
    waha = normalized.setdefault("waha", {})
    waha["enabled"] = as_bool(waha.get("enabled", True))
    waha["session"] = str(waha.get("session", WAHA_SESSION) or WAHA_SESSION or "").strip()
    waha["auto_repair_session"] = as_bool(waha.get("auto_repair_session", WAHA_AUTO_REPAIR_SESSION))
    waha["start_stopped_session"] = as_bool(waha.get("start_stopped_session", WAHA_START_STOPPED_SESSION))
    for key, default_value, minimum in (
        ("starting_timeout_seconds", WAHA_STARTING_TIMEOUT_SECONDS, 10),
        ("repair_cooldown_seconds", WAHA_REPAIR_COOLDOWN_SECONDS, 10),
        ("max_restarts_per_hour", WAHA_MAX_RESTARTS_PER_HOUR, 0),
        ("send_ready_wait_seconds", WAHA_SEND_READY_WAIT_SECONDS, 0),
        ("watchdog_seconds", WAHA_WATCHDOG_SECONDS, 30),
    ):
        try:
            waha[key] = max(minimum, int(waha.get(key, default_value)))
        except Exception:
            waha[key] = default_value
    history = normalized.setdefault("messages", {}).setdefault("history", {})
    history["enabled"] = as_bool(history.get("enabled", True))
    try:
        history["limit"] = max(0, min(100, int(history.get("limit", 10))))
    except Exception:
        history["limit"] = 10
    bot = normalized.setdefault("bot", {})
    bot["enabled"] = as_bool(bot.get("enabled", True))
    bot["append_status_to_confirmations"] = as_bool(bot.get("append_status_to_confirmations", False))
    status_push = normalized.setdefault("status_push", {})
    status_push["enabled"] = as_bool(status_push.get("enabled", False))
    try:
        status_push["min_interval_minutes"] = max(1, int(status_push.get("min_interval_minutes", 5)))
    except Exception:
        status_push["min_interval_minutes"] = 5
    try:
        requested_interval = int(status_push.get("interval_minutes", 30))
    except Exception:
        requested_interval = 30
    status_push["interval_minutes"] = max(status_push["min_interval_minutes"], requested_interval)
    status_push["target_group"] = str(status_push.get("target_group", "") or "").strip()
    gps = normalized.setdefault("gps", {})
    placeholder = gps.setdefault("position_placeholder", {})
    placeholder["enabled"] = as_bool(placeholder.get("enabled", True))
    placeholder["latitude"] = str(placeholder.get("latitude", "{latitude}") or "{latitude}")
    placeholder["longitude"] = str(placeholder.get("longitude", "{longitude}") or "{longitude}")
    placeholder["position_text"] = str(placeholder.get("position_text", "Platzhalter: GPS-Koordinaten aus zukuenftiger MQTT-Schnittstelle") or "")
    placeholder["map_url"] = str(placeholder.get("map_url", "https://www.google.com/maps?q={latitude},{longitude}") or "")
    return normalized


def load_config() -> Dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            return normalize_config(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception as exc:
            log(f"Could not read config, using defaults: {exc}")
    config = default_config()
    save_config(config)
    return normalize_config(config)


def save_config(config: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(normalize_config(config), indent=2, ensure_ascii=False), encoding="utf-8")


CONFIG = load_config()


def waha_enabled() -> bool:
    return as_bool(CONFIG.get("waha", {}).get("enabled", True))


def resize_message_history() -> None:
    global MESSAGE_HISTORY
    limit = int(CONFIG.get("messages", {}).get("history", {}).get("limit", 10) or 0)
    old_items = list(MESSAGE_HISTORY)[-limit:] if limit > 0 else []
    MESSAGE_HISTORY = deque(old_items, maxlen=max(1, limit or 1))


resize_message_history()


# ---------------------------------------------------------------------------
# WAHA HTTP helpers
# ---------------------------------------------------------------------------

def waha_headers() -> Dict[str, str]:
    return {"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"}


def waha_get(path: str) -> Any:
    if not waha_enabled():
        raise RuntimeError("WAHA provider is disabled")
    response = requests.get(f"{WAHA_URL}{path}", headers={"X-Api-Key": WAHA_API_KEY}, timeout=20)
    response.raise_for_status()
    return response.json()


def waha_post(path: str, payload: Dict[str, Any]) -> Any:
    if not waha_enabled():
        raise RuntimeError("WAHA provider is disabled")
    response = requests.post(f"{WAHA_URL}{path}", headers=waha_headers(), json=payload, timeout=25)
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text}
    if response.status_code >= 400:
        raise RuntimeError(f"WAHA HTTP {response.status_code}: {body}")
    return body


# ---------------------------------------------------------------------------
# WAHA session self-healing
# ---------------------------------------------------------------------------

@dataclass
class WahaRepairResult:
    ready: bool
    status: str
    action: str
    reason: str
    session: str = ""
    error: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {
            "ready": self.ready,
            "status": self.status,
            "action": self.action,
            "reason": self.reason,
            "session": self.session,
            "error": self.error,
            "time": now_iso(),
        }


WAHA_REPAIR_LOCK = threading.Lock()
WAHA_REPAIR_ATTEMPTS: Deque[float] = deque()
WAHA_REPAIR_STATE: Dict[str, Any] = {
    "enabled": WAHA_AUTO_REPAIR_SESSION,
    "last_session": "",
    "last_status": "",
    "last_status_since_monotonic": time.monotonic(),
    "last_action": "none",
    "last_action_time": "",
    "last_reason": "",
    "last_error": "",
    "restart_attempts_last_hour": 0,
}


def waha_repair_config() -> Dict[str, Any]:
    cfg = CONFIG.get("waha", {}) or {}
    return {
        "enabled": as_bool(cfg.get("auto_repair_session", WAHA_AUTO_REPAIR_SESSION)),
        "start_stopped_session": as_bool(cfg.get("start_stopped_session", WAHA_START_STOPPED_SESSION)),
        "starting_timeout_seconds": max(10, int(cfg.get("starting_timeout_seconds", WAHA_STARTING_TIMEOUT_SECONDS) or WAHA_STARTING_TIMEOUT_SECONDS)),
        "repair_cooldown_seconds": max(10, int(cfg.get("repair_cooldown_seconds", WAHA_REPAIR_COOLDOWN_SECONDS) or WAHA_REPAIR_COOLDOWN_SECONDS)),
        "max_restarts_per_hour": max(0, int(cfg.get("max_restarts_per_hour", WAHA_MAX_RESTARTS_PER_HOUR) or WAHA_MAX_RESTARTS_PER_HOUR)),
        "send_ready_wait_seconds": max(0, int(cfg.get("send_ready_wait_seconds", WAHA_SEND_READY_WAIT_SECONDS) or WAHA_SEND_READY_WAIT_SECONDS)),
        "watchdog_seconds": max(30, int(cfg.get("watchdog_seconds", WAHA_WATCHDOG_SECONDS) or WAHA_WATCHDOG_SECONDS)),
    }


def remember_waha_repair_result(result: WahaRepairResult) -> None:
    with WAHA_REPAIR_LOCK:
        WAHA_REPAIR_STATE["last_action"] = result.action
        WAHA_REPAIR_STATE["last_action_time"] = now_iso()
        WAHA_REPAIR_STATE["last_reason"] = result.reason
        WAHA_REPAIR_STATE["last_error"] = result.error
        WAHA_REPAIR_STATE["last_session"] = result.session
        WAHA_REPAIR_STATE["last_status"] = result.status
        WAHA_REPAIR_STATE["enabled"] = waha_repair_config()["enabled"]
        cleanup_waha_repair_attempts_locked()
        WAHA_REPAIR_STATE["restart_attempts_last_hour"] = len(WAHA_REPAIR_ATTEMPTS)


def note_waha_status_seen(session_name: str, status: str) -> float:
    now = time.monotonic()
    with WAHA_REPAIR_LOCK:
        if WAHA_REPAIR_STATE.get("last_session") != session_name or WAHA_REPAIR_STATE.get("last_status") != status:
            WAHA_REPAIR_STATE["last_session"] = session_name
            WAHA_REPAIR_STATE["last_status"] = status
            WAHA_REPAIR_STATE["last_status_since_monotonic"] = now
        return max(0.0, now - float(WAHA_REPAIR_STATE.get("last_status_since_monotonic", now)))


def cleanup_waha_repair_attempts_locked() -> None:
    cutoff = time.monotonic() - 3600
    while WAHA_REPAIR_ATTEMPTS and WAHA_REPAIR_ATTEMPTS[0] < cutoff:
        WAHA_REPAIR_ATTEMPTS.popleft()


def waha_repair_allowed() -> Tuple[bool, str]:
    cfg = waha_repair_config()
    now = time.monotonic()
    with WAHA_REPAIR_LOCK:
        cleanup_waha_repair_attempts_locked()
        max_per_hour = int(cfg["max_restarts_per_hour"])
        if max_per_hour == 0:
            return False, "Automatische WAHA-Restarts sind per max_restarts_per_hour=0 deaktiviert"
        if len(WAHA_REPAIR_ATTEMPTS) >= max_per_hour:
            return False, f"Maximale WAHA-Restarts pro Stunde erreicht ({max_per_hour})"
        last_attempt = WAHA_REPAIR_ATTEMPTS[-1] if WAHA_REPAIR_ATTEMPTS else 0.0
        remaining = int(cfg["repair_cooldown_seconds"] - (now - last_attempt))
        if remaining > 0:
            return False, f"WAHA-Restart-Cooldown aktiv: noch {remaining} Sekunden"
        WAHA_REPAIR_ATTEMPTS.append(now)
        WAHA_REPAIR_STATE["restart_attempts_last_hour"] = len(WAHA_REPAIR_ATTEMPTS)
        return True, ""


def waha_session_action(session_name: str, action: str) -> Any:
    # WAHA accepts an empty JSON body here.  Keeping this inside the controller
    # avoids shell/Docker access and works over the already configured WAHA API.
    return waha_post(f"/api/sessions/{session_name}/{action}", {})


def maybe_repair_waha_session(session: Dict[str, Any]) -> WahaRepairResult:
    cfg = waha_repair_config()
    session_name = str(session.get("name") or effective_whatsapp_session() or "").strip()
    status = str(session.get("status") or "UNKNOWN").upper()
    ready = bool(session.get("ready", False)) or status == "WORKING"
    status_age = note_waha_status_seen(session_name, status)

    if ready:
        result = WahaRepairResult(True, status, "none", "WAHA-Session ist bereit", session_name)
        remember_waha_repair_result(result)
        return result

    if not cfg["enabled"]:
        result = WahaRepairResult(False, status, "none", "Automatische WAHA-Session-Reparatur ist deaktiviert", session_name)
        remember_waha_repair_result(result)
        return result

    if not session_name:
        result = WahaRepairResult(False, status, "config_error", "Keine WAHA-Session konfiguriert oder gefunden", session_name)
        remember_waha_repair_result(result)
        return result

    if status == "NOT_FOUND":
        result = WahaRepairResult(False, status, "config_error", f"WAHA-Session nicht gefunden: {session_name}", session_name)
        remember_waha_repair_result(result)
        return result

    if status in {"SCAN_QR_CODE", "QR"}:
        result = WahaRepairResult(False, status, "manual_required", "WhatsApp muss in WAHA per QR-Code neu gekoppelt werden", session_name)
        remember_waha_repair_result(result)
        return result

    if status == "STOPPED" and cfg["start_stopped_session"]:
        allowed, reason = waha_repair_allowed()
        if not allowed:
            result = WahaRepairResult(False, status, "start_blocked", reason, session_name)
            remember_waha_repair_result(result)
            return result
        try:
            waha_session_action(session_name, "start")
            result = WahaRepairResult(False, status, "start", "WAHA-Session war STOPPED und wurde gestartet", session_name)
        except Exception as exc:
            result = WahaRepairResult(False, status, "start_failed", str(exc), session_name, str(exc))
        remember_waha_repair_result(result)
        return result

    if status in {"FAILED", "CRASHED"}:
        allowed, reason = waha_repair_allowed()
        if not allowed:
            result = WahaRepairResult(False, status, "restart_blocked", reason, session_name)
            remember_waha_repair_result(result)
            return result
        try:
            waha_session_action(session_name, "restart")
            result = WahaRepairResult(False, status, "restart", f"WAHA-Session war {status} und wurde neu gestartet", session_name)
        except Exception as exc:
            result = WahaRepairResult(False, status, "restart_failed", str(exc), session_name, str(exc))
        remember_waha_repair_result(result)
        return result

    if status in {"STARTING", "OPENING"}:
        timeout = int(cfg["starting_timeout_seconds"])
        if status_age < timeout:
            result = WahaRepairResult(False, status, "wait", f"WAHA-Session startet seit {int(status_age)} Sekunden", session_name)
            remember_waha_repair_result(result)
            return result
        allowed, reason = waha_repair_allowed()
        if not allowed:
            result = WahaRepairResult(False, status, "restart_blocked", reason, session_name)
            remember_waha_repair_result(result)
            return result
        try:
            waha_session_action(session_name, "restart")
            # Reset age after a restart request so the next watchdog cycle waits
            # before attempting another restart.
            note_waha_status_seen(session_name, f"{status}:restart")
            note_waha_status_seen(session_name, status)
            result = WahaRepairResult(False, status, "restart", f"WAHA-Session hing laenger als {timeout} Sekunden auf {status} und wurde neu gestartet", session_name)
        except Exception as exc:
            result = WahaRepairResult(False, status, "restart_failed", str(exc), session_name, str(exc))
        remember_waha_repair_result(result)
        return result

    result = WahaRepairResult(False, status, "none", f"WAHA-Session ist nicht bereit: {status}", session_name)
    remember_waha_repair_result(result)
    return result


def wait_for_waha_ready(max_seconds: int) -> Tuple[Dict[str, Any], WahaRepairResult]:
    deadline = time.monotonic() + max(0, max_seconds)
    current = fetch_session()
    result = maybe_repair_waha_session(current)
    while max_seconds > 0 and not result.ready and time.monotonic() < deadline:
        if result.status in {"SCAN_QR_CODE", "QR", "NOT_FOUND"} or result.action in {"config_error", "manual_required"}:
            break
        time.sleep(2)
        current = fetch_session()
        result = maybe_repair_waha_session(current)
    return current, result


def ensure_waha_ready_for_send(client: Optional[mqtt.Client] = None) -> None:
    global SESSION
    SESSION = fetch_session()
    result = maybe_repair_waha_session(SESSION)
    cfg = waha_repair_config()
    if not result.ready and int(cfg["send_ready_wait_seconds"]) > 0:
        SESSION, result = wait_for_waha_ready(int(cfg["send_ready_wait_seconds"]))
    if client is not None:
        publish_waha_repair_status(client)
    if not result.ready:
        raise RuntimeError(f"WAHA session not ready: status={result.status}, action={result.action}, reason={result.reason}")


def make_waha_repair_payload() -> Dict[str, Any]:
    cfg = waha_repair_config()
    with WAHA_REPAIR_LOCK:
        payload = dict(WAHA_REPAIR_STATE)
    payload.pop("last_status_since_monotonic", None)
    payload.update({
        "enabled": cfg["enabled"],
        "start_stopped_session": cfg["start_stopped_session"],
        "starting_timeout_seconds": cfg["starting_timeout_seconds"],
        "repair_cooldown_seconds": cfg["repair_cooldown_seconds"],
        "max_restarts_per_hour": cfg["max_restarts_per_hour"],
        "send_ready_wait_seconds": cfg["send_ready_wait_seconds"],
        "watchdog_seconds": cfg["watchdog_seconds"],
    })
    return payload


def publish_waha_repair_status(client: mqtt.Client) -> None:
    repair = make_waha_repair_payload()
    publish(client, topic("waha", "session", "repair", "json"), {"d": repair})
    publish(client, topic("waha", "session", "repair", "enabled"), str(repair["enabled"]).lower())
    publish(client, topic("waha", "session", "repair", "action"), repair.get("last_action", ""))
    publish(client, topic("waha", "session", "repair", "reason"), repair.get("last_reason", ""))
    publish(client, topic("waha", "session", "repair", "error"), repair.get("last_error", ""))


# ---------------------------------------------------------------------------
# WAHA response parsing
# ---------------------------------------------------------------------------

def id_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("_serialized", "serialized", "chatId", "jid", "groupId"):
            if value.get(key):
                return str(value[key])
        user = value.get("user") or value.get("id")
        server = value.get("server")
        if user and server:
            return f"{user}@{server}"
        if user:
            return str(user)
    return str(value)


def mask_chat_id(chat_id: str) -> str:
    if "@" not in chat_id:
        return "***"
    left, right = chat_id.split("@", 1)
    if len(left) <= 4:
        masked = left[:1] + "***"
    else:
        masked = left[:2] + "***" + left[-2:]
    return f"{masked}@{right}"


def safe_key(index: int) -> str:
    return f"g{index:03d}"


def unwrap_list(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("data", "groups", "sessions", "result", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                return unwrap_list(value)
        mapped = []
        for map_key, value in data.items():
            if isinstance(value, dict):
                item = dict(value)
                item["__map_key"] = str(map_key)
                mapped.append(item)
        if mapped:
            return mapped
        return [data]
    return []


def session_text(status: str, reachable: bool) -> str:
    normalized = (status or "").upper()
    if normalized == "DISABLED":
        return "WAHA deaktiviert"
    if not reachable:
        return "WAHA nicht erreichbar"
    if normalized == "WORKING":
        return "WhatsApp verbunden"
    if normalized in {"STARTING", "OPENING"}:
        return "WhatsApp startet oder verbindet"
    if normalized in {"SCAN_QR_CODE", "QR"}:
        return "QR-Code erforderlich"
    if normalized:
        return f"WhatsApp-Session: {status}"
    return "WhatsApp-Session unbekannt"


def fetch_session() -> Dict[str, Any]:
    if not waha_enabled():
        return {
            "name": "",
            "status": "DISABLED",
            "account": "",
            "reachable": False,
            "ready": False,
            "can_send": False,
            "can_read_groups": False,
            "last_error": "",
        }
    sessions = unwrap_list(waha_get("/api/sessions"))
    selected = None
    preferred_name = effective_whatsapp_session()
    if preferred_name:
        for session in sessions:
            name = str(session.get("name") or session.get("session") or session.get("id") or "")
            if name == preferred_name:
                selected = session
                break
    if selected is None and preferred_name:
        return {
            "name": preferred_name,
            "status": "NOT_FOUND",
            "account": "",
            "reachable": True,
            "ready": False,
            "can_send": False,
            "can_read_groups": False,
            "last_error": f"Configured WAHA session not found: {preferred_name}",
            "assigned_worker": "",
            "timestamps": {},
        }
    if selected is None:
        for session in sessions:
            if str(session.get("status", "")).upper() == "WORKING":
                selected = session
                break
    if selected is None and sessions:
        selected = sessions[0]
    if selected is None:
        return {
            "name": "",
            "status": "NOT_FOUND",
            "account": "",
            "reachable": True,
            "ready": False,
            "can_send": False,
            "can_read_groups": False,
            "last_error": "",
            "assigned_worker": "",
            "timestamps": {},
        }

    me = selected.get("me") or selected.get("account") or ""
    if isinstance(me, dict):
        me = me.get("id") or me.get("pushName") or json.dumps(me, ensure_ascii=False)
    status = str(selected.get("status") or selected.get("state") or "")
    ready = status.upper() == "WORKING"
    return {
        "name": str(selected.get("name") or selected.get("session") or selected.get("id") or ""),
        "status": status,
        "account": str(me),
        "reachable": True,
        "ready": ready,
        "can_send": ready,
        "can_read_groups": ready,
        "last_error": "",
        "assigned_worker": str(selected.get("assignedWorker") or selected.get("assigned_worker") or ""),
        "timestamps": selected.get("timestamps") if isinstance(selected.get("timestamps"), dict) else {},
    }


def fetch_groups(session_name: str) -> Dict[str, Dict[str, str]]:
    if not session_name:
        return {}
    raw = waha_get(f"/api/{session_name}/groups?limit=500&offset=0")
    items = unwrap_list(raw)
    groups = []
    for item in items:
        group_metadata = item.get("groupMetadata") or {}
        chat_id = (
            id_to_text(group_metadata.get("id"))
            or id_to_text(group_metadata.get("jid"))
            or id_to_text(group_metadata.get("chatId"))
            or id_to_text(group_metadata.get("groupId"))
            or id_to_text(item.get("id"))
            or id_to_text(item.get("jid"))
            or id_to_text(item.get("chatId"))
            or id_to_text(item.get("groupId"))
            or str(item.get("__map_key") or "")
        )
        subject = (
            group_metadata.get("subject")
            or group_metadata.get("name")
            or group_metadata.get("title")
            or item.get("subject")
            or item.get("name")
            or item.get("title")
            or "(kein Subject gefunden)"
        )
        participants = group_metadata.get("participants") or item.get("participants") or []
        participants_count = len(participants) if isinstance(participants, list) else 0
        if chat_id and "@" not in chat_id and chat_id.isdigit():
            chat_id = f"{chat_id}@g.us"
        if chat_id:
            groups.append({"chatId": str(chat_id), "subject": str(subject), "participants_count": participants_count})

    groups.sort(key=lambda x: x["subject"].lower())
    result: Dict[str, Dict[str, str]] = {}
    for index, group in enumerate(groups, start=1):
        key = safe_key(index)
        result[key] = {
            "key": key,
            "chatId": group["chatId"],
            "chatId_masked": mask_chat_id(group["chatId"]),
            "subject": group["subject"],
            "participants_count": str(group.get("participants_count", 0)),
        }
    return result


# ---------------------------------------------------------------------------
# Group resolution
# ---------------------------------------------------------------------------

def rebuild_group_index() -> None:
    global GROUP_KEYS_BY_CHAT_ID
    GROUP_KEYS_BY_CHAT_ID = {group["chatId"]: key for key, group in GROUPS_BY_KEY.items()}


def resolve_group(value: str) -> Optional[Dict[str, str]]:
    value = (value or "").strip()
    if not value:
        return None
    if value in GROUPS_BY_KEY:
        return GROUPS_BY_KEY[value]
    for group in GROUPS_BY_KEY.values():
        if value == group["chatId"]:
            return group
        if value.lower() == group["subject"].lower():
            return group
    return None


def group_subject(value: str) -> str:
    group = resolve_group(value)
    return group["subject"] if group else ""


def group_chat_id(value: str) -> str:
    group = resolve_group(value)
    return group["chatId"] if group else value.strip()


def group_alias_from_chat_id(chat_id: str) -> str:
    return GROUP_KEYS_BY_CHAT_ID.get(chat_id, "")


# ---------------------------------------------------------------------------
# Bot flow XML module
# ---------------------------------------------------------------------------

# The controller supports two XML dialects for compatibility:
# 1. Legacy <mobertCommands> command files.
# 2. New <mobertBotConfig> flow files with modules, head, input,
#    processing and output blocks.
#
# Internally both dialects are exposed as BotCommand entries so the public MQTT
# command status topics remain stable.  New flow files additionally populate
# BOT_MODULES and BOT_FLOWS and are executed by the small flow engine below.

@dataclass
class BotCommand:
    command_id: str
    enabled: bool
    category: str
    trigger: str
    example: str
    description: str
    action_type: str
    name: str = ""
    response: str = ""
    response_template: str = ""
    mqtt_topic: str = ""
    mqtt_payload: str = ""
    mqtt_qos: int = 0
    mqtt_retain: bool = False
    immediate_confirmation: str = ""
    parameters: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    wait_confirmation: Dict[str, Any] = field(default_factory=dict)
    flow_id: str = ""
    step_id: str = "start"
    _regex: Optional[re.Pattern[str]] = field(default=None, repr=False)

    def to_json(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": self.command_id,
            "enabled": self.enabled,
            "category": self.category,
            "name": self.name,
            "trigger": self.trigger,
            "example": self.example,
            "description": self.description,
            "action_type": self.action_type,
        }
        if self.flow_id:
            data["flow"] = {"id": self.flow_id, "step": self.step_id}
        if self.parameters:
            data["parameters"] = self.parameters
        if self.mqtt_topic:
            data["mqtt"] = {"topic": self.mqtt_topic, "payload": self.mqtt_payload, "qos": self.mqtt_qos, "retain": self.mqtt_retain}
        if self.wait_confirmation:
            data["wait_confirmation"] = self.wait_confirmation
        return data


BOT_MODULES: Dict[str, Dict[str, Any]] = {}
BOT_FLOWS: Dict[str, Dict[str, Any]] = {}
BOT_CONFIG_ENABLED = True
PENDING_CONFIRMATIONS: List[Dict[str, Any]] = []
PENDING_CONFIRMATIONS_LOCK = threading.Lock()

# Latest external MQTT values collected by the mqtt_watchdog.  These values are
# intentionally small and retained in memory only; they are used for WhatsApp
# status texts and for transition matching in flow XML conditions.
OPENMOWER_STATE: Dict[str, Any] = {
    "robot_state": {},
    "robot_state_previous": {},
    "robot_state_time": "",
    "gps_state": {},
    "gps_state_previous": {},
    "gps_state_time": "",
    "gps_position": {},
    "gps_position_time": "",
    "wifi_percent": "unbekannt",
    "wifi_time": "",
    "last_mqtt_topic": "",
    "last_mqtt_payload": "",
    "last_mqtt_time": "",
}
GPS_LOSS_ALERT_ACTIVE = False
MQTT_TOPIC_CACHE: Dict[str, Dict[str, Any]] = {}
OPENMOWER_STATE_LOCK = threading.Lock()
OPENMOWER_STATE_UPDATED = threading.Condition(OPENMOWER_STATE_LOCK)

# Status requests wait only briefly for fresh ROS-MQTT data.  This avoids
# "unbekannt" values when the command arrives shortly before the next
# robot_state/json or WLAN sample, but it still replies quickly if ROS-MQTT
# is quiet.
STATUS_FRESH_WAIT_SECONDS = float(os.getenv("STATUS_FRESH_WAIT_SECONDS", "3"))
STATUS_TIMEZONE = os.getenv("STATUS_TIMEZONE", "Europe/Berlin").strip() or "Europe/Berlin"

# Status cache topics are subscribed independently from the XML flows.  This is
# important for installations that still use the legacy bot_commands.xml format:
# legacy XML can answer "Mobert: Status", but it does not define mqtt_watchdog
# flow subscriptions.  The defaults intentionally subscribe only to text/JSON
# status topics.  Do not subscribe the WiFi cache to the parent # wildcard by
# default because OpenMower also publishes a binary bson sibling there.
DEFAULT_STATUS_CACHE_TOPICS = [
    "robot_state/json",
    "gps_state/json",
    # Placeholder/future interface: real WGS84 latitude/longitude values can be
    # published here when the OpenMower MQTT interface provides them.
    "gps/position/json",
    "gps_position/json",
    "sensors/om_system_wifi_signal_percent/data",
    "openmower/robot_state/json",
    "openmower/gps_state/json",
    "openmower/gps/position/json",
    "openmower/gps_position/json",
    "openmower/sensors/om_system_wifi_signal_percent/data",
]
STATUS_CACHE_TOPICS_RAW = os.getenv("OPENMOWER_STATUS_CACHE_TOPICS", "").strip()


def configured_status_cache_topics() -> List[str]:
    if STATUS_CACHE_TOPICS_RAW:
        raw = STATUS_CACHE_TOPICS_RAW.replace(";", ",").replace("\n", ",")
        items = [item.strip().strip("/") for item in raw.split(",") if item.strip()]
    else:
        items = list(DEFAULT_STATUS_CACHE_TOPICS)
    # Keep required status/GPS cache sources even if an older .env still defines
    # OPENMOWER_STATUS_CACHE_TOPICS without the newer GPS placeholders.
    for default_topic in DEFAULT_STATUS_CACHE_TOPICS:
        if default_topic not in items:
            items.append(default_topic)
    seen = set()
    result: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def subscribe_status_cache_topics(client: mqtt.Client) -> None:
    for pattern in configured_status_cache_topics():
        client.subscribe(pattern)
        log(f"Subscribed OpenMower status cache topic: {pattern}")


def ensure_default_bot_commands_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if BOT_COMMANDS_FILE.exists():
        return
    if DEFAULT_BOT_COMMANDS_FILE.exists():
        BOT_COMMANDS_FILE.write_text(DEFAULT_BOT_COMMANDS_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        BOT_COMMANDS_FILE.write_text(default_bot_commands_xml(), encoding="utf-8")


def default_bot_commands_xml() -> str:
    if DEFAULT_BOT_COMMANDS_FILE.exists():
        return DEFAULT_BOT_COMMANDS_FILE.read_text(encoding="utf-8")
    return """<?xml version="1.0" encoding="UTF-8"?>
<mobertBotConfig version="0.4" language="de">

  <head>
    <name>Mobert OpenMower Flow Configuration</name>
    <description>Fallback-Konfiguration mit zentralem WhatsApp-Modul.</description>
    <enabled>true</enabled>
  </head>

  <modules>
    <whatsappModule id="whatsapp">
      <enabled>true</enabled>
      <session>
        <default>default</default>
      </session>
      <groups>
        <defaultGroup>g014</defaultGroup>
        <listenerGroup>g014</listenerGroup>
      </groups>
      <wakeWord>
        <text>Mobert</text>
        <required>true</required>
        <syntax>colon</syntax>
        <caseSensitive>false</caseSensitive>
      </wakeWord>
    </whatsappModule>
    <inputModule id="whatsapp_watchdog">
      <enabled>true</enabled>
      <moduleRef>whatsapp</moduleRef>
    </inputModule>
    <outputModule id="whatsapp_output">
      <enabled>true</enabled>
      <moduleRef>whatsapp</moduleRef>
    </outputModule>
    <inputModule id="mqtt_watchdog">
      <enabled>true</enabled>
      <subscribeMode>enabled_flows</subscribeMode>
    </inputModule>
    <outputModule id="mqtt_output">
      <enabled>true</enabled>
    </outputModule>
  </modules>

  <flows />

</mobertBotConfig>
"""

def xml_child_text(element: Optional[ET.Element], path: str, default: str = "") -> str:
    if element is None:
        return default
    child = element.find(path)
    if child is None or child.text is None:
        return default
    return child.text.strip()


def xml_enabled(element: Optional[ET.Element], default: bool = True) -> bool:
    if element is None:
        return default
    if "enabled" in element.attrib:
        return as_bool(element.attrib.get("enabled"))
    enabled_text = xml_child_text(element, "enabled", "")
    if enabled_text != "":
        return as_bool(enabled_text)
    return default


def compile_trigger_regex(trigger: str, parameters: Dict[str, Dict[str, Any]]) -> re.Pattern[str]:
    pattern_parts: List[str] = []
    pos = 0
    for match in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", trigger):
        pattern_parts.append(re.escape(trigger[pos:match.start()]))
        name = match.group(1)
        param_type = str(parameters.get(name, {}).get("type", "string")).lower()
        if param_type in {"int", "integer"}:
            pattern_parts.append(fr"(?P<{name}>\d+)")
        else:
            pattern_parts.append(fr"(?P<{name}>.+?)")
        pos = match.end()
    pattern_parts.append(re.escape(trigger[pos:]))
    regex = "^" + "".join(pattern_parts).replace(r"\ ", r"\s+") + "$"
    return re.compile(regex, re.IGNORECASE)


def parse_parameters(parent: Optional[ET.Element]) -> Dict[str, Dict[str, Any]]:
    params: Dict[str, Dict[str, Any]] = {}
    if parent is None:
        return params
    for param in parent.findall("parameters/parameter"):
        name = param.attrib.get("name", "").strip()
        if name:
            params[name] = {
                "type": param.attrib.get("type", "string"),
                "required": as_bool(param.attrib.get("required", "true")),
                "min": param.attrib.get("min"),
                "max": param.attrib.get("max"),
            }
    return params


def parse_output_node(node: ET.Element) -> Dict[str, Any]:
    return {
        "module": node.attrib.get("module", "").strip(),
        "type": node.attrib.get("type", "send").strip(),
        "result": node.attrib.get("result", "").strip(),
        "target": xml_child_text(node, "target"),
        "message": xml_child_text(node, "message"),
        "topic": xml_child_text(node, "topic"),
        "payload": xml_child_text(node, "payload"),
        "qos": int(xml_child_text(node, "qos", "0") or 0),
        "retain": as_bool(xml_child_text(node, "retain", "false")),
    }


def parse_json_conditions(expect: Optional[ET.Element]) -> List[Dict[str, Any]]:
    conditions: List[Dict[str, Any]] = []
    if expect is None:
        return conditions
    for field_node in expect.findall("json/field"):
        name = field_node.attrib.get("name", "").strip()
        if not name:
            continue
        conditions.append({
            "name": name,
            "equals": field_node.attrib.get("equals"),
            "not_equals": field_node.attrib.get("notEquals"),
            "previous_equals": field_node.attrib.get("previousEquals"),
            "previous_not_equals": field_node.attrib.get("previousNotEquals"),
            "previous_exists": as_bool(field_node.attrib.get("previousExists", "false")),
            "exists": as_bool(field_node.attrib.get("exists", "false")),
        })
    return conditions


def parse_step(node: ET.Element) -> Dict[str, Any]:
    input_node = node.find("input")
    expect = input_node.find("expect") if input_node is not None else None
    processing_node = node.find("processing")
    processing: Dict[str, Any] = {
        "mode": processing_node.attrib.get("mode", "passthrough") if processing_node is not None else "passthrough",
        "template": xml_child_text(processing_node, "template"),
        "response_template": xml_child_text(processing_node, "responseTemplate"),
        "module_ref": xml_child_text(processing_node, "moduleRef"),
        "property": xml_child_text(processing_node, "property"),
        "value": xml_child_text(processing_node, "value"),
        "persist": as_bool(xml_child_text(processing_node, "persist", "false")),
        "success_default": as_bool(xml_child_text(processing_node, "successWhen/default", "true")),
        "error_payload_contains": xml_child_text(processing_node, "errorWhen/payloadContains"),
    }
    step = {
        "id": node.attrib.get("id", "start"),
        "input": {
            "module": input_node.attrib.get("module", "") if input_node is not None else "",
            "type": input_node.attrib.get("type", "") if input_node is not None else "",
            "timeout_seconds": int(xml_child_text(input_node, "timeoutSeconds", "0") or 0),
            "command": xml_child_text(expect, "command"),
            "topic": xml_child_text(expect, "topic"),
            "payload_equals": xml_child_text(expect, "payloadEquals"),
            "payload_not_empty": as_bool(xml_child_text(expect, "payloadNotEmpty", "false")),
            "json_conditions": parse_json_conditions(expect),
            "parameters": parse_parameters(input_node),
        },
        "processing": processing,
        "outputs": [parse_output_node(out) for out in node.findall("output")],
        "next_step": xml_child_text(node, "nextStep"),
    }
    return step


def parse_wake_word(node: Optional[ET.Element]) -> Dict[str, Any]:
    if node is None:
        return {}
    return {
        "text": xml_child_text(node, "text", "Mobert"),
        "required": as_bool(xml_child_text(node, "required", "true")),
        "syntax": xml_child_text(node, "syntax", "colon"),
        "caseSensitive": as_bool(xml_child_text(node, "caseSensitive", "false")),
    }


def parse_module_settings(node: ET.Element) -> Dict[str, Any]:
    settings: Dict[str, Any] = {
        "kind": node.tag,
        "enabled": xml_enabled(node, True),
        "moduleRef": xml_child_text(node, "moduleRef"),
        "session": xml_child_text(node, "session"),
        "listenerGroup": xml_child_text(node, "listenerGroup"),
        "defaultGroup": xml_child_text(node, "defaultGroup"),
        "subscribeMode": xml_child_text(node, "subscribeMode"),
    }

    # WhatsApp-specific grouped module.  This is the central place for the
    # shared session, group routing and wake word used by whatsapp_watchdog and
    # whatsapp_output via <moduleRef>whatsapp</moduleRef>.
    session_default = xml_child_text(node, "session/default")
    if session_default:
        settings["session"] = session_default
    group_listener = xml_child_text(node, "groups/listenerGroup")
    if group_listener:
        settings["listenerGroup"] = group_listener
    group_default = xml_child_text(node, "groups/defaultGroup")
    if group_default:
        settings["defaultGroup"] = group_default

    wake = parse_wake_word(node.find("wakeWord"))
    if wake:
        settings["wakeWord"] = wake
    return settings


def parse_modules(root: ET.Element) -> Dict[str, Dict[str, Any]]:
    raw_modules: Dict[str, Dict[str, Any]] = {}
    for node in root.findall("modules/whatsappModule") + root.findall("modules/inputModule") + root.findall("modules/outputModule"):
        module_id = node.attrib.get("id", "").strip()
        if not module_id:
            continue
        raw_modules[module_id] = parse_module_settings(node)

    modules: Dict[str, Dict[str, Any]] = {}
    for module_id, settings in raw_modules.items():
        module_ref = str(settings.get("moduleRef") or "").strip()
        if module_ref and module_ref in raw_modules:
            parent = dict(raw_modules[module_ref])
            parent_enabled = as_bool(parent.get("enabled", True))
            child_enabled = as_bool(settings.get("enabled", True))
            # Child settings override parent values only when they are explicitly
            # present.  Empty values inherit from the referenced module.
            merged = parent
            for key, value in settings.items():
                if key in {"session", "listenerGroup", "defaultGroup", "subscribeMode"} and value == "":
                    continue
                merged[key] = value
            merged["enabled"] = parent_enabled and child_enabled
            merged["moduleRef"] = module_ref
            merged["referencedEnabled"] = parent_enabled
            modules[module_id] = merged
        else:
            modules[module_id] = settings
    return modules


def make_flow_command(flow: Dict[str, Any], step: Dict[str, Any]) -> Optional[BotCommand]:
    input_cfg = step.get("input", {})
    if input_cfg.get("module") != "whatsapp_watchdog" or input_cfg.get("type") != "command":
        return None
    trigger = str(input_cfg.get("command") or "").strip()
    if not trigger:
        return None
    params = dict(input_cfg.get("parameters") or {})
    command = BotCommand(
        command_id=flow["id"],
        enabled=as_bool(flow.get("enabled", True)),
        category=flow.get("category", "general"),
        name=flow.get("name", ""),
        trigger=trigger,
        example=f"{effective_wake_word(raw=True)}: {trigger}",
        description=flow.get("description", ""),
        action_type="flow",
        parameters=params,
        flow_id=flow["id"],
        step_id=step.get("id", "start"),
    )
    command._regex = compile_trigger_regex(command.trigger, command.parameters)
    return command


def load_flow_bot_config(raw_xml: str, root: ET.Element) -> Tuple[Dict[str, Any], List[BotCommand], Dict[str, Any], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    version = root.attrib.get("version", "")
    language = root.attrib.get("language", "de")
    head = root.find("head")
    modules = parse_modules(root)
    flows: Dict[str, Dict[str, Any]] = {}
    commands: List[BotCommand] = []
    root_enabled = xml_enabled(head, True)
    for flow_node in root.findall("flows/flow"):
        flow_id = flow_node.attrib.get("id", "").strip()
        if not flow_id:
            continue
        flow_head = flow_node.find("head")
        steps: Dict[str, Dict[str, Any]] = {}
        flow = {
            "id": flow_id,
            "name": xml_child_text(flow_head, "name", flow_id),
            "description": xml_child_text(flow_head, "description"),
            "category": xml_child_text(flow_head, "category", "general"),
            "enabled": root_enabled and xml_enabled(flow_head, True),
            "steps": steps,
        }
        for step_node in flow_node.findall("step"):
            step = parse_step(step_node)
            steps[step["id"]] = step
        flows[flow_id] = flow
        for step in steps.values():
            command = make_flow_command(flow, step)
            if command is not None:
                commands.append(command)
    meta = {
        "format": "flow",
        "version": version,
        "language": language,
        "name": xml_child_text(head, "name"),
        "description": xml_child_text(head, "description"),
        "enabled": root_enabled,
        "source": str(BOT_COMMANDS_FILE),
        "modules": list(modules.keys()),
        "flows": len(flows),
    }
    return meta, commands, {"valid": True, "error": "", "format": "flow"}, modules, flows


def load_legacy_bot_commands(raw_xml: str, root: ET.Element) -> Tuple[Dict[str, Any], List[BotCommand], Dict[str, Any], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    version = root.attrib.get("version", "")
    language = root.attrib.get("language", "de")
    meta = {
        "format": "legacy",
        "version": version,
        "language": language,
        "name": xml_child_text(root, "meta/name"),
        "description": xml_child_text(root, "meta/description"),
        "source": str(BOT_COMMANDS_FILE),
    }
    commands: List[BotCommand] = []
    for node in root.findall("command"):
        params = parse_parameters(node)
        action = node.find("action")
        action_type = action.attrib.get("type", "local_reply") if action is not None else "local_reply"
        mqtt_publish = action.find("mqttPublish") if action is not None else None
        wait_node = action.find("waitForMqttConfirmation") if action is not None else None
        wait_confirmation: Dict[str, Any] = {}
        if wait_node is not None:
            wait_confirmation = {
                "enabled": as_bool(wait_node.attrib.get("enabled", "false")),
                "timeout_seconds": int(wait_node.attrib.get("timeoutSeconds", "0") or 0),
                "topic": xml_child_text(wait_node, "topic"),
                "success_response": xml_child_text(wait_node, "successResponse"),
                "timeout_response": xml_child_text(wait_node, "timeoutResponse"),
                "error_response": xml_child_text(wait_node, "errorResponse"),
            }
        command = BotCommand(
            command_id=node.attrib.get("id", "").strip(),
            enabled=as_bool(node.attrib.get("enabled", "true")),
            category=node.attrib.get("category", "general"),
            name=xml_child_text(node, "name"),
            trigger=xml_child_text(node, "trigger"),
            example=xml_child_text(node, "example"),
            description=xml_child_text(node, "description"),
            action_type=action_type,
            response=xml_child_text(action, "response") if action is not None else "",
            response_template=xml_child_text(action, "responseTemplate") if action is not None else "",
            mqtt_topic=mqtt_publish.attrib.get("topic", "") if mqtt_publish is not None else "",
            mqtt_payload=xml_child_text(mqtt_publish, "payload") if mqtt_publish is not None else "",
            mqtt_qos=int(mqtt_publish.attrib.get("qos", "0") or 0) if mqtt_publish is not None else 0,
            mqtt_retain=as_bool(mqtt_publish.attrib.get("retain", "false")) if mqtt_publish is not None else False,
            immediate_confirmation=xml_child_text(action, "immediateConfirmation") if action is not None else "",
            parameters=params,
            wait_confirmation=wait_confirmation,
        )
        command._regex = compile_trigger_regex(command.trigger, command.parameters)
        if command.command_id and command.trigger:
            commands.append(command)
    return meta, commands, {"valid": True, "error": "", "format": "legacy"}, {}, {}


def load_bot_commands() -> Tuple[str, Dict[str, Any], List[BotCommand], Dict[str, Any], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    ensure_default_bot_commands_file()
    raw_xml = BOT_COMMANDS_FILE.read_text(encoding="utf-8")
    root = ET.fromstring(raw_xml)
    if root.tag == "mobertBotConfig":
        meta, commands, validation, modules, flows = load_flow_bot_config(raw_xml, root)
    elif root.tag == "mobertCommands":
        meta, commands, validation, modules, flows = load_legacy_bot_commands(raw_xml, root)
    else:
        raise RuntimeError(f"Unsupported bot XML root element: {root.tag}")
    return raw_xml, meta, commands, validation, modules, flows


BOT_COMMANDS_XML = ""
BOT_COMMANDS_META: Dict[str, Any] = {}
BOT_COMMANDS: List[BotCommand] = []
BOT_COMMANDS_VALIDATION: Dict[str, Any] = {"valid": False, "error": "not loaded"}
BOT_HELP_TEXT = ""
BOT_HELP_JSON: Dict[str, Any] = {}


def reload_bot_commands() -> None:
    global BOT_COMMANDS_XML, BOT_COMMANDS_META, BOT_COMMANDS, BOT_COMMANDS_VALIDATION, BOT_MODULES, BOT_FLOWS, BOT_CONFIG_ENABLED, BOT_HELP_TEXT, BOT_HELP_JSON
    try:
        BOT_COMMANDS_XML, BOT_COMMANDS_META, BOT_COMMANDS, BOT_COMMANDS_VALIDATION, BOT_MODULES, BOT_FLOWS = load_bot_commands()
        BOT_CONFIG_ENABLED = as_bool(BOT_COMMANDS_META.get("enabled", True))
        BOT_HELP_TEXT, BOT_HELP_JSON = build_bot_help_artifacts()
        log(f"Loaded {len(BOT_COMMANDS)} bot commands from {BOT_COMMANDS_FILE} ({BOT_COMMANDS_META.get('format', 'unknown')})")
        log(f"Generated help from bot XML with {len(BOT_HELP_JSON.get('entries', []))} visible entries")
    except Exception as exc:
        BOT_COMMANDS_VALIDATION = {"valid": False, "error": str(exc)}
        BOT_COMMANDS = []
        BOT_MODULES = {}
        BOT_FLOWS = {}
        BOT_COMMANDS_XML = ""
        BOT_COMMANDS_META = {"version": "", "source": str(BOT_COMMANDS_FILE)}
        BOT_CONFIG_ENABLED = False
        BOT_HELP_TEXT = "*Mobert Hilfe*\n────────────\n\nKeine Befehle geladen."
        BOT_HELP_JSON = {"generated_at": now_iso(), "source": str(BOT_COMMANDS_FILE), "valid": False, "error": str(exc), "entries": []}
        log(f"Could not load bot commands: {exc}")


def module_config(module_id: str) -> Dict[str, Any]:
    return BOT_MODULES.get(module_id, {})


def module_is_enabled(module_id: str) -> bool:
    if not BOT_MODULES:
        return True
    return as_bool(module_config(module_id).get("enabled", True))


def effective_whatsapp_session() -> str:
    configured = str(CONFIG.get("waha", {}).get("session", "") or "").strip()
    if configured:
        return "" if configured == "default" else configured
    if WAHA_SESSION:
        return "" if WAHA_SESSION == "default" else WAHA_SESSION
    session_name = str(module_config("whatsapp_output").get("session", "") or module_config("whatsapp_watchdog").get("session", "") or module_config("whatsapp").get("session", "") or "").strip()
    return "" if session_name == "default" else session_name


def effective_wake_word(raw: bool = False) -> str:
    if not raw:
        configured = str(CONFIG.get("bot", {}).get("wake_word", "") or "").strip()
        if configured:
            return configured
    wake = module_config("whatsapp_watchdog").get("wakeWord") if BOT_MODULES else None
    if isinstance(wake, dict) and str(wake.get("text") or "").strip():
        return str(wake.get("text") or "Mobert").strip()
    return "Mobert"


def effective_listener_group() -> str:
    configured = str(CONFIG.get("bot", {}).get("listen_group", "") or "").strip()
    if configured:
        return configured
    return str(module_config("whatsapp_watchdog").get("listenerGroup", "") or "").strip()


def effective_default_group() -> str:
    configured = str(CONFIG.get("default_group", "") or "").strip()
    if configured:
        return configured
    return str(module_config("whatsapp_output").get("defaultGroup", "") or "").strip()


def effective_bot_enabled() -> bool:
    return BOT_CONFIG_ENABLED and as_bool(CONFIG.get("bot", {}).get("enabled", True)) and module_is_enabled("whatsapp_watchdog")


def command_help_example(cmd: BotCommand, wake_word: str) -> str:
    """Return the visible WhatsApp command example for one XML command."""
    trigger = str(cmd.trigger or "").strip()
    example = str(cmd.example or "").strip() or f"{wake_word}: {trigger}"
    # Keep examples aligned with the currently active wake word.  The XML stores
    # the start command in <expect><command>; the wake word comes from the
    # whatsapp module and may be changed centrally.
    if ":" in example:
        example = f"{wake_word}: {example.split(':', 1)[1].strip()}"
    elif not example.lower().startswith(wake_word.lower()):
        example = f"{wake_word}: {example}"
    return example


def build_bot_help_artifacts() -> Tuple[str, Dict[str, Any]]:
    """Generate WhatsApp help text and JSON metadata from bot_commands.xml.

    The active XML command model is the source of truth.  For flow XML, visible
    help entries are derived from enabled flows that start with a
    whatsapp_watchdog command input.  The display text comes from
    <head><description>, and the start command comes from
    <step><input module="whatsapp_watchdog" type="command"><expect><command>.
    After /data/bot_commands.xml is replaced or reloaded via MQTT, this function
    is called again and the rebuilt help is published on messenger/bot/help/#.
    """
    wake_word = effective_wake_word()
    entries: List[Dict[str, Any]] = []
    for cmd in BOT_COMMANDS:
        if not cmd.enabled:
            continue
        example = command_help_example(cmd, wake_word)
        description = (cmd.description or cmd.name or cmd.command_id or cmd.trigger).strip()
        if description and not description.endswith(('.', '!', '?')):
            description += "."
        entries.append({
            "id": cmd.command_id,
            "flow_id": cmd.flow_id,
            "step_id": cmd.step_id,
            "name": cmd.name or cmd.command_id,
            "category": cmd.category,
            "command": cmd.trigger,
            "example": example,
            "description": description,
        })

    title = f"*{wake_word} Hilfe*"
    lines = [title, "────────────", ""]
    if not entries:
        lines.append("Keine Befehle geladen.")
    else:
        for entry in entries:
            lines.append(f"*{entry['example']}*")
            if entry["description"]:
                lines.append(entry["description"])
            lines.append("")
    text = "\n".join(lines).rstrip()
    payload = {
        "generated_at": now_iso(),
        "source": BOT_COMMANDS_META.get("source", str(BOT_COMMANDS_FILE)),
        "format": BOT_COMMANDS_META.get("format", "xml"),
        "version": BOT_COMMANDS_META.get("version", ""),
        "wake_word": wake_word,
        "valid": bool(BOT_COMMANDS_VALIDATION.get("valid", True)),
        "entries": entries,
        "text": text,
    }
    return text, payload


def command_help_text() -> str:
    """Return the current XML-generated WhatsApp help text."""
    return BOT_HELP_TEXT or build_bot_help_artifacts()[0]

def interpolate_template(template: str, values: Dict[str, Any]) -> str:
    text = template or ""
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def find_command(command_text: str) -> Tuple[Optional[BotCommand], Dict[str, str]]:
    normalized = command_text.strip()
    for cmd in BOT_COMMANDS:
        if not cmd.enabled or cmd._regex is None:
            continue
        match = cmd._regex.match(normalized)
        if match:
            return cmd, {key: value.strip() for key, value in match.groupdict().items()}
    return None, {}


reload_bot_commands()

# ---------------------------------------------------------------------------
# State JSON builders and publishing
# ---------------------------------------------------------------------------

def session_payload() -> Dict[str, Any]:
    status = SESSION.get("status", "")
    reachable = bool(SESSION.get("reachable", False))
    return {
        "d": {
            "name": SESSION.get("name", ""),
            "status": status,
            "text": session_text(status, reachable),
            "account": SESSION.get("account", ""),
            "ready": bool(SESSION.get("ready", False)),
            "can_send": bool(SESSION.get("can_send", False)),
            "can_read_groups": bool(SESSION.get("can_read_groups", False)),
            "last_error": SESSION.get("last_error", "") or LAST_STATUS_ERROR,
            "assigned_worker": SESSION.get("assigned_worker", ""),
            "repair": make_waha_repair_payload(),
            "last_update": now_iso(),
        }
    }


def waha_payload() -> Dict[str, Any]:
    session = session_payload()["d"] if SESSION else {}
    enabled = waha_enabled()
    if enabled:
        if session.get("ready"):
            text = f"{PROVIDER_NAME} ist aktiv und verbunden."
        elif session.get("status"):
            text = f"{PROVIDER_NAME} ist aktiv, Session: {session.get('status')}."
        else:
            text = f"{PROVIDER_NAME} ist aktiv, Session noch nicht geladen."
    else:
        text = f"{PROVIDER_NAME} ist deaktiviert."
    return {
        "d": {
            "enabled": enabled,
            "text": text,
            "provider": PROVIDER_NAME,
            "protocol": PROTOCOL_NAME,
            "url": WAHA_URL,
            "session": session,
            "last_update": now_iso(),
        }
    }


def groups_payload() -> Dict[str, Any]:
    default_group = effective_default_group()
    default_data = GROUPS_BY_KEY.get(default_group, {})
    return {
        "d": {
            "loaded": True,
            "count": len(GROUPS_BY_KEY),
            "last_refresh": now_iso(),
            "default_group": {
                "set": bool(default_group),
                "alias": default_group,
                "name": default_data.get("subject", ""),
                "known": bool(default_data),
            },
            "groups": [
                {
                    "alias": key,
                    "name": group["subject"],
                    "chat_id_masked": group["chatId_masked"],
                    "participants_count": int(group.get("participants_count", "0") or 0),
                    "is_default_group": key == default_group,
                }
                for key, group in GROUPS_BY_KEY.items()
            ],
            "last_error": LAST_STATUS_ERROR,
        }
    }


def contacts_payload() -> Dict[str, Any]:
    return {
        "d": {
            "loaded": False,
            "count": 0,
            "contacts": [],
            "remarks": ["Kontakte sind fuer diese Version vorbereitet, aber noch nicht implementiert."],
        }
    }


def message_history_config() -> Dict[str, Any]:
    return CONFIG.get("messages", {}).get("history", {}) or {"enabled": True, "limit": 10}


def messages_payload() -> Dict[str, Any]:
    history_config = message_history_config()
    messages = list(MESSAGE_HISTORY) if as_bool(history_config.get("enabled", True)) else []
    return {
        "d": {
            "count": len(messages),
            "limit": int(history_config.get("limit", 10) or 0),
            "enabled": as_bool(history_config.get("enabled", True)),
            "messages": messages,
        }
    }


def bot_listener_payload() -> Dict[str, Any]:
    listen_group = effective_listener_group()
    listen_group_data = GROUPS_BY_KEY.get(listen_group, {})
    bot_enabled = effective_bot_enabled()
    provider_enabled = waha_enabled()
    session_ready = bool(SESSION.get("ready", False))
    listening = bot_enabled and provider_enabled and session_ready and bool(listen_group) and bool(listen_group_data)
    wake_word = effective_wake_word()
    if listening:
        text = f"{wake_word} lauscht in {listen_group} ({listen_group_data.get('subject', '')})."
    elif not bot_enabled:
        text = f"{wake_word} ist deaktiviert."
    elif not provider_enabled:
        text = f"{wake_word} lauscht nicht, weil WAHA deaktiviert ist."
    elif not session_ready:
        text = f"{wake_word} lauscht nicht, weil die Messenger-Session nicht bereit ist."
    elif not listen_group:
        text = f"{wake_word} ist aktiv, aber es ist keine Lauschgruppe gesetzt."
    else:
        text = f"{wake_word} ist aktiv, aber die Lauschgruppe {listen_group} ist nicht bekannt."
    return {
        "d": {
            "listening": listening,
            "wake_word": wake_word,
            "text": text,
            "provider": PROVIDER_NAME,
            "group": {"alias": listen_group, "name": listen_group_data.get("subject", "")},
            "input_module": "whatsapp_watchdog",
        }
    }


def commands_payload() -> Dict[str, Any]:
    return {
        "d": {
            "version": BOT_COMMANDS_META.get("version", ""),
            "source": BOT_COMMANDS_META.get("source", str(BOT_COMMANDS_FILE)),
            "format": BOT_COMMANDS_META.get("format", "legacy"),
            "modules": list(BOT_MODULES.keys()),
            "flows": len(BOT_FLOWS),
            "valid": bool(BOT_COMMANDS_VALIDATION.get("valid", False)),
            "count": len(BOT_COMMANDS),
            "commands": [cmd.to_json() for cmd in BOT_COMMANDS],
            "last_error": BOT_COMMANDS_VALIDATION.get("error", ""),
        }
    }


def bot_payload() -> Dict[str, Any]:
    listener = bot_listener_payload()["d"]
    return {
        "d": {
            "enabled": effective_bot_enabled(),
            "text": listener["text"],
            "listener": listener,
            "commands": {
                "count": len(BOT_COMMANDS),
                "version": BOT_COMMANDS_META.get("version", ""),
                "valid": bool(BOT_COMMANDS_VALIDATION.get("valid", False)),
            },
        }
    }


def actions_payload() -> Dict[str, Any]:
    return {
        "d": [
            {"action_id": "messenger:waha/enable", "enabled": 0 if waha_enabled() else 1, "label": "WAHA aktivieren"},
            {"action_id": "messenger:waha/disable", "enabled": 1 if waha_enabled() else 0, "label": "WAHA deaktivieren"},
            {"action_id": "messenger:waha/groups/refresh", "enabled": 1 if waha_enabled() else 0, "label": "WAHA-Gruppen aktualisieren"},
            {"action_id": "messenger:waha/session/start", "enabled": 1 if waha_enabled() else 0, "label": "WAHA-Session starten"},
            {"action_id": "messenger:waha/session/restart", "enabled": 1 if waha_enabled() else 0, "label": "WAHA-Session neu starten"},
            {"action_id": "messenger:bot/commands/reload", "enabled": 1, "label": "Bot-Befehlsdatei neu laden"},
        ]
    }


def status_description_payload() -> Dict[str, Any]:
    """Return a retained, non-secret status description for MQTT users.

    The description is intended for MQTT Explorer and external integrations.
    It deliberately publishes only locations and variable names, never the
    dashboard password or WAHA API key.
    """
    dashboard_user = os.getenv("WAHA_DASHBOARD_USERNAME", "waha")
    description_text = (
        f"Messenger bridge using provider {PROVIDER_NAME} ({PROTOCOL_NAME}). "
        f"WAHA API URL: {WAHA_URL}. "
        f"WAHA dashboard: {WAHA_DASHBOARD_URL}. "
        f"Dashboard credentials are configured on the OpenMower host in "
        f"{CREDENTIALS_FILE_HINT}: user variable WAHA_DASHBOARD_USERNAME "
        f"(current default/display value: {dashboard_user}), password variable "
        f"WAHA_DASHBOARD_PASSWORD. The password and WAHA_API_KEY are not "
        f"published to MQTT."
    )
    return {
        "text": description_text,
        "provider": PROVIDER_NAME,
        "protocol": PROTOCOL_NAME,
        "waha_api_url": WAHA_URL,
        "waha_dashboard_url": WAHA_DASHBOARD_URL,
        "credentials_file": CREDENTIALS_FILE_HINT,
        "dashboard_username_variable": "WAHA_DASHBOARD_USERNAME",
        "dashboard_username_hint": dashboard_user,
        "dashboard_password_variable": "WAHA_DASHBOARD_PASSWORD",
        "api_key_variable": "WAHA_API_KEY",
        "security_note": "Secrets are not published to MQTT. Read them on the host from the .env file.",
    }


def make_status_json(error: str = "") -> Dict[str, Any]:
    provider_text = PROVIDER_NAME
    session_status = SESSION.get("status", "") if SESSION else "UNKNOWN"
    bot_text = bot_listener_payload()["d"]["text"] if SESSION else "Bot-Status noch nicht geladen"
    status_text = f"{provider_text} {session_status}: {bot_text}"
    description = status_description_payload()
    return {
        "d": {
            "online": True,
            "text": status_text,
            "description": description,
            "provider": PROVIDER_NAME,
            "protocol": PROTOCOL_NAME,
            "waha_enabled": waha_enabled(),
            "session": session_payload()["d"] if SESSION else {},
            "last_error": error or LAST_STATUS_ERROR,
            "last_update": now_iso(),
        }
    }


def publish_message_history(client: mqtt.Client) -> None:
    messages = list(MESSAGE_HISTORY)
    out_messages = [entry for entry in messages if entry.get("direction") == "out"]
    in_messages = [entry for entry in messages if entry.get("direction") == "in"]

    publish(client, topic("waha", "messages", "json"), messages_payload())
    publish(client, topic("waha", "messages", "count"), len(messages))
    publish(client, topic("waha", "messages", "out", "history", "json"), {"d": out_messages})
    publish(client, topic("waha", "messages", "out", "count"), len(out_messages))
    publish(client, topic("waha", "messages", "in", "history", "json"), {"d": in_messages})
    publish(client, topic("waha", "messages", "in", "count"), len(in_messages))

    history_config = message_history_config()
    publish(client, topic("waha", "messages", "history", "enabled"), str(as_bool(history_config.get("enabled", True))).lower())
    publish(client, topic("waha", "messages", "history", "limit"), int(history_config.get("limit", 10) or 0))


def publish_outgoing_message_event(client: mqtt.Client, entry: Dict[str, Any]) -> None:
    """Publish outgoing WhatsApp messages as explicit MQTT events and retained last-state topics."""
    publish(client, topic("waha", "messages", "out", "json"), {"d": entry}, retain=False)
    publish(client, topic("waha", "messages", "out", "last", "json"), {"d": entry}, retain=True)
    publish(client, topic("waha", "messages", "out", "last", "text"), entry.get("text", ""), retain=True)
    publish(client, topic("waha", "messages", "out", "last", "status"), entry.get("status", ""), retain=True)
    publish(client, topic("waha", "messages", "out", "last", "time"), entry.get("timestamp", ""), retain=True)


def add_message_history(client: mqtt.Client, entry: Dict[str, Any]) -> None:
    if not as_bool(message_history_config().get("enabled", True)):
        return
    message_id = str(entry.get("message_id") or "").strip()
    if message_id:
        for existing in MESSAGE_HISTORY:
            if str(existing.get("message_id") or "").strip() == message_id:
                return
    MESSAGE_HISTORY.append(entry)
    publish_message_history(client)


def publish_bot_commands(client: mqtt.Client) -> None:
    publish(client, topic("bot", "commands", "json"), commands_payload())
    publish(client, topic("bot", "commands", "xml"), BOT_COMMANDS_XML)
    publish(client, topic("bot", "commands", "count"), len(BOT_COMMANDS))
    publish(client, topic("bot", "commands", "version"), BOT_COMMANDS_META.get("version", ""))
    publish(client, topic("bot", "commands", "source"), BOT_COMMANDS_META.get("source", str(BOT_COMMANDS_FILE)))
    publish(client, topic("bot", "help", "text"), BOT_HELP_TEXT)
    publish(client, topic("bot", "help", "json"), BOT_HELP_JSON)
    publish_validation(
        client,
        topic("bot", "commands", "validation", "json"),
        valid=bool(BOT_COMMANDS_VALIDATION.get("valid", False)),
        mode="load",
        accepted={"commands": len(BOT_COMMANDS)} if BOT_COMMANDS_VALIDATION.get("valid") else {},
        rejected={} if BOT_COMMANDS_VALIDATION.get("valid") else {"xml": BOT_COMMANDS_VALIDATION.get("error", "unknown error")},
        remarks=["Bot-Befehle geladen"] if BOT_COMMANDS_VALIDATION.get("valid") else ["Bot-Befehle konnten nicht geladen werden"],
    )


def publish_state(client: mqtt.Client, refresh_groups: bool = True) -> None:
    global GROUPS_BY_KEY, SESSION, LAST_STATUS_ERROR
    try:
        SESSION = fetch_session()
        if waha_enabled():
            repair_result = maybe_repair_waha_session(SESSION)
            # If the watchdog just started/restarted a session, fetch once more so
            # the retained MQTT status reflects the latest WAHA state.
            if repair_result.action in {"start", "restart"}:
                try:
                    SESSION = fetch_session()
                except Exception:
                    pass
        if not waha_enabled():
            GROUPS_BY_KEY = {}
            rebuild_group_index()
        elif refresh_groups and SESSION.get("can_read_groups"):
            GROUPS_BY_KEY = fetch_groups(str(SESSION.get("name", "")))
            rebuild_group_index()
        LAST_STATUS_ERROR = ""
    except Exception as exc:
        LAST_STATUS_ERROR = str(exc)
        if not SESSION:
            SESSION = {
                "name": "",
                "status": "ERROR",
                "account": "",
                "reachable": False,
                "ready": False,
                "can_send": False,
                "can_read_groups": False,
                "last_error": str(exc),
            }
        else:
            SESSION["last_error"] = str(exc)
        log(f"State refresh error: {exc}")

    status = make_status_json()
    publish(client, topic("status", "json"), status)
    publish(client, topic("status", "online"), "true")
    publish(client, topic("status", "text"), status["d"]["text"])
    publish(client, topic("status", "description"), status["d"]["description"]["text"])
    publish(client, topic("status", "provider"), PROVIDER_NAME)
    publish(client, topic("status", "protocol"), PROTOCOL_NAME)

    waha = waha_payload()["d"]
    publish(client, topic("waha", "json"), {"d": waha})
    publish(client, topic("waha", "enabled"), str(waha["enabled"]).lower())
    publish(client, topic("waha", "text"), waha["text"])

    session = session_payload()["d"]
    publish(client, topic("waha", "session", "json"), {"d": session})
    publish(client, topic("waha", "session", "status"), session["status"])
    publish(client, topic("waha", "session", "text"), session["text"])
    publish(client, topic("waha", "session", "ready"), str(session["ready"]).lower())
    publish(client, topic("waha", "session", "can_send"), str(session["can_send"]).lower())
    publish(client, topic("waha", "session", "can_read_groups"), str(session["can_read_groups"]).lower())
    publish(client, topic("waha", "session", "last_error"), session["last_error"])
    publish_waha_repair_status(client)

    groups = groups_payload()["d"]
    publish(client, topic("waha", "groups", "json"), {"d": groups})
    publish(client, topic("waha", "groups", "count"), groups["count"])
    publish(client, topic("waha", "groups", "default", "alias"), groups["default_group"]["alias"])
    publish(client, topic("waha", "groups", "default", "name"), groups["default_group"]["name"])

    contacts = contacts_payload()["d"]
    publish(client, topic("waha", "contacts", "json"), {"d": contacts})
    publish(client, topic("waha", "contacts", "count"), contacts["count"])
    publish(client, topic("waha", "contacts", "status", "json"), {"d": {"loaded": False, "count": 0, "last_error": "not implemented"}})

    publish_message_history(client)

    listener = bot_listener_payload()["d"]
    bot = bot_payload()["d"]
    publish(client, topic("bot", "json"), {"d": bot})
    publish(client, topic("bot", "enabled"), str(bot["enabled"]).lower())
    publish(client, topic("bot", "text"), bot["text"])
    publish(client, topic("bot", "listener", "json"), {"d": listener})
    publish(client, topic("bot", "listener", "listening"), str(listener["listening"]).lower())
    publish(client, topic("bot", "listener", "wake_word"), listener["wake_word"])
    publish(client, topic("bot", "listener", "text"), listener["text"])
    publish(client, topic("bot", "listener", "provider"), listener["provider"])
    publish(client, topic("bot", "listener", "group", "alias"), listener["group"]["alias"])
    publish(client, topic("bot", "listener", "group", "name"), listener["group"]["name"])

    push = status_push_payload()
    publish(client, topic("bot", "status_push", "json"), {"d": push})
    publish(client, topic("bot", "status_push", "enabled"), str(push["enabled"]).lower())
    publish(client, topic("bot", "status_push", "interval_minutes"), push["interval_minutes"])
    publish(client, topic("bot", "status_push", "target", "alias"), push["target_group"])
    publish(client, topic("bot", "status_push", "text"), push["text"])
    publish(client, topic("bot", "append_status_to_confirmations"), append_status_setting_text())

    publish_bot_commands(client)
    publish(client, topic("waha", "actions", "json"), actions_payload())

    log(f"Published state: session={SESSION.get('name')} groups={len(GROUPS_BY_KEY)}")


# ---------------------------------------------------------------------------
# Sending, messages and forwarding
# ---------------------------------------------------------------------------

def make_chat_descriptor(alias: str, chat_id: str = "") -> Dict[str, Any]:
    group = resolve_group(alias) if alias else None
    if not group and chat_id:
        alias = group_alias_from_chat_id(chat_id)
        group = resolve_group(alias) if alias else None
    return {
        "type": "group" if (group or chat_id.endswith("@g.us")) else "chat",
        "alias": group["key"] if group else alias,
        "name": group["subject"] if group else "",
        "chat_id_masked": group["chatId_masked"] if group else mask_chat_id(chat_id) if chat_id else "",
    }


def resolve_message_target(target: Any) -> Tuple[str, Optional[Dict[str, str]]]:
    if isinstance(target, dict):
        value = str(target.get("alias") or target.get("group") or target.get("chatId") or target.get("id") or "")
    else:
        value = str(target or "")
    if not value:
        value = effective_default_group()
    group = resolve_group(value)
    chat_id = group["chatId"] if group else value.strip()
    if not chat_id:
        raise RuntimeError("No target group/chatId configured")
    return chat_id, group


def send_text(client: mqtt.Client, target: Any, text: str, request_id: str = "") -> Dict[str, Any]:
    if not waha_enabled():
        raise RuntimeError("WAHA provider is disabled")
    ensure_waha_ready_for_send(client)
    if not SESSION or not SESSION.get("name"):
        raise RuntimeError("No active WAHA session available")
    chat_id, group = resolve_message_target(target)
    if not text.strip():
        raise RuntimeError("Message text is empty")
    result = waha_post("/api/sendText", {"session": SESSION["name"], "chatId": chat_id, "text": text})
    chat = make_chat_descriptor(group["key"] if group else "", chat_id)
    entry = {
        "timestamp": now_iso(),
        "direction": "out",
        "message_id": str(result.get("id") or result.get("messageId") or ""),
        "request_id": request_id,
        "chat": chat,
        "text": text,
        "status": "sent",
        "error": None,
    }
    publish_outgoing_message_event(client, entry)
    add_message_history(client, entry)
    return {"sent": True, "chat": chat, "waha": result, "message": entry}


def format_forwarded_message(source_topic: str, payload: str) -> str:
    templates = CONFIG.get("templates", {}) or {}
    template = templates.get(source_topic)
    if not template:
        for pattern, value in templates.items():
            if mqtt.topic_matches_sub(pattern, source_topic):
                template = value
                break
    if not template:
        return f"{source_topic}: {payload}"
    data: Dict[str, Any] = {"payload": payload, "topic": source_topic}
    try:
        parsed = json.loads(payload)
        if isinstance(parsed, dict):
            data.update(parsed)
    except Exception:
        pass
    try:
        return template.format(**data)
    except Exception:
        return f"{source_topic}: {payload}"


def flow_mqtt_watch_topics() -> List[str]:
    patterns: List[str] = []
    if not module_is_enabled("mqtt_watchdog"):
        return patterns
    for flow in BOT_FLOWS.values():
        if not as_bool(flow.get("enabled", True)):
            continue
        for step in flow.get("steps", {}).values():
            input_cfg = step.get("input", {})
            if input_cfg.get("module") != "mqtt_watchdog":
                continue
            pattern = str(input_cfg.get("topic") or "").strip()
            if pattern and pattern not in patterns:
                patterns.append(pattern)
    return patterns


def rebuild_forward_subscriptions(client: mqtt.Client) -> None:
    for pattern in CONFIG.get("forward_topics", []) or []:
        if pattern and not pattern.startswith(MQTT_BASE_TOPIC + "/"):
            client.subscribe(pattern)
            log(f"Subscribed forward topic: {pattern}")
    for pattern in flow_mqtt_watch_topics():
        if pattern and not pattern.startswith(MQTT_BASE_TOPIC + "/"):
            client.subscribe(pattern)
            log(f"Subscribed flow MQTT watchdog topic: {pattern}")


# ---------------------------------------------------------------------------
# MQTT command handlers
# ---------------------------------------------------------------------------

def handle_groups_renew(client: mqtt.Client) -> None:
    if not waha_enabled():
        publish_state(client, refresh_groups=False)
        publish_validation(
            client,
            topic("waha", "groups", "validation", "json"),
            valid=False,
            mode="renew",
            rejected={"waha": "disabled"},
            remarks=["WAHA ist deaktiviert; Gruppenliste wurde nicht aktualisiert"],
        )
        return
    publish_state(client, refresh_groups=True)
    publish_validation(
        client,
        topic("waha", "groups", "validation", "json"),
        valid=True,
        mode="renew",
        accepted={"renew": True, "count": len(GROUPS_BY_KEY)},
        remarks=["Gruppenliste aktualisiert"],
    )


def handle_groups_set(client: mqtt.Client, payload: str) -> None:
    data = parse_json_payload(payload)
    default_group_value = str(data.get("default_group_alias") or data.get("default_group") or data.get("alias") or "")
    if not default_group_value:
        raise RuntimeError("groups/set/json requires default_group_alias")
    group = resolve_group(default_group_value)
    if not group:
        raise RuntimeError(f"Unknown default group: {default_group_value}")
    CONFIG["default_group"] = group["key"]
    save_config(CONFIG)
    publish_state(client, refresh_groups=False)
    publish_validation(
        client,
        topic("waha", "groups", "validation", "json"),
        valid=True,
        mode="set",
        accepted={"default_group_alias": group["key"]},
        remarks=["Standard-Zielgruppe übernommen"],
    )


def apply_waha_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    waha = CONFIG.setdefault("waha", {})
    accepted: Dict[str, Any] = {}
    if "enabled" in data:
        waha["enabled"] = as_bool(data.get("enabled"))
        accepted["enabled"] = waha["enabled"]
    if "session" in data or "session_name" in data:
        session_name = str(data.get("session") or data.get("session_name") or "").strip()
        if not session_name:
            raise RuntimeError("session must not be empty")
        waha["session"] = session_name
        accepted["session"] = session_name
    for key in ("auto_repair_session", "start_stopped_session"):
        if key in data:
            waha[key] = as_bool(data.get(key))
            accepted[key] = waha[key]
    for key, minimum in (
        ("starting_timeout_seconds", 10),
        ("repair_cooldown_seconds", 10),
        ("max_restarts_per_hour", 0),
        ("send_ready_wait_seconds", 0),
        ("watchdog_seconds", 30),
    ):
        if key in data:
            try:
                waha[key] = max(minimum, int(data.get(key)))
            except Exception:
                raise RuntimeError(f"{key} must be an integer")
            accepted[key] = waha[key]
    if not accepted:
        raise RuntimeError("waha set payload requires enabled, session or repair settings")
    return accepted


def handle_waha_set(client: mqtt.Client, payload: str, mode: str) -> None:
    data = parse_json_payload(payload)
    if not isinstance(data, dict):
        raise RuntimeError("waha set payload must be a JSON object")
    accepted = apply_waha_settings(data)
    if mode == "persistent":
        save_config(CONFIG)
    publish_state(client, refresh_groups=bool(accepted.get("enabled", False)))
    publish_validation(
        client,
        topic("waha", "validation", "json"),
        valid=True,
        mode=mode,
        accepted=accepted,
        remarks=["WAHA-Konfiguration übernommen" if mode == "session" else "WAHA-Konfiguration gespeichert"],
    )


def apply_bot_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    bot = CONFIG.setdefault("bot", {})
    accepted: Dict[str, Any] = {}
    if "enabled" in data:
        bot["enabled"] = as_bool(data.get("enabled"))
        accepted["enabled"] = bot["enabled"]
    if "wake_word" in data:
        wake_word = str(data.get("wake_word") or "").strip()
        if not wake_word:
            raise RuntimeError("wake_word must not be empty")
        bot["wake_word"] = wake_word
        accepted["wake_word"] = wake_word
    if "append_status_to_confirmations" in data:
        bot["append_status_to_confirmations"] = as_bool(data.get("append_status_to_confirmations"))
        accepted["append_status_to_confirmations"] = bot["append_status_to_confirmations"]
    if isinstance(data.get("status_push"), dict):
        push_data = data.get("status_push") or {}
        push = CONFIG.setdefault("status_push", {})
        if "enabled" in push_data:
            push["enabled"] = as_bool(push_data.get("enabled"))
        if "interval_minutes" in push_data:
            minimum = int(push.get("min_interval_minutes", 5) or 5)
            push["interval_minutes"] = max(minimum, int(push_data.get("interval_minutes")))
        if "target_group" in push_data:
            push["target_group"] = str(push_data.get("target_group") or "").strip()
        accepted["status_push"] = status_push_payload()
    listener = data.get("listener") if isinstance(data.get("listener"), dict) else {}
    group_value = str(data.get("listen_group_alias") or data.get("group_alias") or listener.get("group_alias") or listener.get("group") or "")
    if group_value:
        group = resolve_group(group_value)
        if not group:
            raise RuntimeError(f"Unknown bot listen group: {group_value}")
        bot["listen_group"] = group["key"]
        accepted["listen_group_alias"] = group["key"]
    return accepted


def handle_bot_set(client: mqtt.Client, payload: str, mode: str) -> None:
    data = parse_json_payload(payload)
    if not isinstance(data, dict):
        raise RuntimeError("bot set payload must be a JSON object")
    accepted = apply_bot_settings(data)
    if mode == "persistent":
        save_config(CONFIG)
    publish_state(client, refresh_groups=False)
    publish_validation(
        client,
        topic("bot", "validation", "json"),
        valid=True,
        mode=mode,
        accepted=accepted,
        remarks=["Bot-Konfiguration übernommen" if mode == "session" else "Bot-Konfiguration gespeichert"],
    )


def handle_history_set(client: mqtt.Client, payload: str, mode: str) -> None:
    data = parse_json_payload(payload)
    if not isinstance(data, dict):
        raise RuntimeError("history set payload must be a JSON object")
    history = CONFIG.setdefault("messages", {}).setdefault("history", {})
    accepted: Dict[str, Any] = {}
    if "enabled" in data:
        history["enabled"] = as_bool(data.get("enabled"))
        accepted["enabled"] = history["enabled"]
    if "limit" in data:
        limit = max(0, min(100, int(data.get("limit"))))
        history["limit"] = limit
        accepted["limit"] = limit
        resize_message_history()
    if mode == "persistent":
        save_config(CONFIG)
    publish_message_history(client)
    publish_validation(
        client,
        topic("waha", "messages", "history", "validation", "json"),
        valid=True,
        mode=mode,
        accepted=accepted,
        remarks=["Nachrichtenspeicher aktualisiert"],
    )


def handle_commands_renew(client: mqtt.Client) -> None:
    reload_bot_commands()
    rebuild_forward_subscriptions(client)
    publish_bot_commands(client)
    publish_state(client, refresh_groups=False)


def handle_commands_set_xml(client: mqtt.Client, payload: str) -> None:
    # Allows replacing /data/bot_commands.xml via MQTT while keeping the XML
    # parser safe: the payload must be well-formed and use a supported root.
    xml_text = payload.strip()
    if not xml_text:
        raise RuntimeError("commands/set/xml requires an XML payload")
    root = ET.fromstring(xml_text)
    if root.tag not in {"mobertBotConfig", "mobertCommands"}:
        raise RuntimeError(f"Unsupported bot XML root element: {root.tag}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BOT_COMMANDS_FILE.write_text(xml_text + "\n", encoding="utf-8")
    reload_bot_commands()
    rebuild_forward_subscriptions(client)
    publish_bot_commands(client)
    publish_state(client, refresh_groups=False)
    publish_validation(
        client,
        topic("bot", "commands", "validation", "json"),
        valid=True,
        mode="set_xml",
        accepted={"source": str(BOT_COMMANDS_FILE), "format": BOT_COMMANDS_META.get("format", ""), "help_entries": len(BOT_HELP_JSON.get("entries", []))},
        remarks=["Bot-XML gespeichert, Befehle neu geladen und Hilfe neu aufgebaut"],
    )


def handle_outgoing_message(client: mqtt.Client, payload: str) -> None:
    data = parse_json_payload(payload)
    if isinstance(data, dict):
        text = str(data.get("text") or data.get("message") or "")
        target = data.get("target") or data.get("group") or data.get("chatId") or ""
        request_id = str(data.get("request_id") or "")
    else:
        text = str(payload)
        target = ""
        request_id = ""
    try:
        result = send_text(client, target, text.strip(), request_id=request_id)
        publish_validation(
            client,
            topic("waha", "messages", "out", "validation", "json"),
            valid=True,
            mode="send",
            request_id=request_id,
            accepted={"message": True, "target": True},
            result={"sent": True, "chat": result["chat"]},
            remarks=["WhatsApp-Nachricht gesendet"],
        )
    except Exception as exc:
        entry = {
            "timestamp": now_iso(),
            "direction": "out",
            "message_id": "",
            "request_id": request_id,
            "chat": {"type": "unknown", "alias": "", "name": ""},
            "text": text,
            "status": "failed",
            "error": str(exc),
        }
        publish_outgoing_message_event(client, entry)
        add_message_history(client, entry)
        publish_validation(
            client,
            topic("waha", "messages", "out", "validation", "json"),
            valid=False,
            mode="send",
            request_id=request_id,
            rejected={"error": str(exc)},
            result={"sent": False},
            remarks=["WhatsApp-Nachricht nicht gesendet"],
        )
        raise


def handle_action(client: mqtt.Client, payload: str) -> None:
    action_id = payload.strip()
    if action_id == "messenger:waha/enable":
        handle_waha_set(client, '{"enabled": true}', "session")
    elif action_id == "messenger:waha/disable":
        handle_waha_set(client, '{"enabled": false}', "session")
    elif action_id == "messenger:waha/groups/refresh":
        handle_groups_renew(client)
    elif action_id == "messenger:waha/session/start":
        session_name = str(SESSION.get("name") or effective_whatsapp_session() or "").strip()
        if not session_name:
            raise RuntimeError("No WAHA session configured for start action")
        waha_session_action(session_name, "start")
        publish_state(client, refresh_groups=False)
    elif action_id == "messenger:waha/session/restart":
        session_name = str(SESSION.get("name") or effective_whatsapp_session() or "").strip()
        if not session_name:
            raise RuntimeError("No WAHA session configured for restart action")
        waha_session_action(session_name, "restart")
        publish_state(client, refresh_groups=False)
    elif action_id == "messenger:bot/commands/reload":
        handle_commands_renew(client)
    else:
        raise RuntimeError(f"Unknown messenger action: {action_id}")


def handle_forward_topics(client: mqtt.Client, payload: str) -> None:
    data = json.loads(payload)
    if not isinstance(data, list):
        raise RuntimeError("forward_topics payload must be a JSON list")
    CONFIG["forward_topics"] = [str(x) for x in data]
    save_config(CONFIG)
    rebuild_forward_subscriptions(client)
    publish_state(client, refresh_groups=False)


def handle_templates(client: mqtt.Client, payload: str) -> None:
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise RuntimeError("templates payload must be a JSON object")
    CONFIG["templates"] = {str(k): str(v) for k, v in data.items()}
    save_config(CONFIG)
    publish_state(client, refresh_groups=False)


def handle_forwarded_mqtt(client: mqtt.Client, source_topic: str, payload: str) -> None:
    for pattern in CONFIG.get("forward_topics", []) or []:
        if mqtt.topic_matches_sub(pattern, source_topic):
            text = format_forwarded_message(source_topic, payload)
            send_text(client, "", text)
            return


# ---------------------------------------------------------------------------
# Mobert WhatsApp bot
# ---------------------------------------------------------------------------

def get_nested(data: Dict[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def extract_webhook_message(data: Dict[str, Any]) -> Dict[str, Any]:
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
    chat_id = (
        payload.get("from")
        or payload.get("chatId")
        or payload.get("remoteJid")
        or get_nested(payload, "id", "remote")
        or get_nested(payload, "key", "remoteJid")
        or ""
    )
    text = (
        payload.get("body")
        or payload.get("text")
        or payload.get("message")
        or get_nested(payload, "message", "conversation")
        or get_nested(payload, "message", "extendedTextMessage", "text")
        or ""
    )
    sender = payload.get("participant") or payload.get("author") or get_nested(payload, "key", "participant") or payload.get("from") or ""
    from_me = bool(payload.get("fromMe") or get_nested(payload, "key", "fromMe"))
    message_id = id_to_text(payload.get("id") or get_nested(payload, "key", "id") or payload.get("messageId") or "")
    return {
        "chatId": str(chat_id),
        "text": str(text),
        "sender": str(sender),
        "fromMe": from_me,
        "message_id": message_id,
        "session": str(data.get("session") or payload.get("session") or SESSION.get("name", "")),
    }


def mqtt_connection_text() -> str:
    client = MQTT_CLIENT
    if client is None:
        return "nicht gestartet"
    try:
        return "verbunden" if client.is_connected() else "nicht verbunden"
    except Exception:
        return "unbekannt"


def now_local_text() -> str:
    """Return a human-readable local timestamp for WhatsApp status replies."""
    try:
        tz = ZoneInfo(STATUS_TIMEZONE)
    except Exception:
        tz = timezone.utc
    return datetime.now(timezone.utc).astimezone(tz).strftime("%d.%m.%Y %H:%M:%S")


def parse_bool_like(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "on", "ja", "laden", "charging"}:
        return True
    if text in {"false", "0", "no", "off", "nein", "nicht laden", "not_charging"}:
        return False
    return None


def format_wifi_percent(value: Any) -> str:
    if value is None or value == "":
        return "unbekannt"
    try:
        number = float(str(value).replace(",", "."))
        return f"{number:.0f} %"
    except Exception:
        return "unbekannt"


def format_percent_fraction(value: Any) -> str:
    if value is None or value == "":
        return "unbekannt"
    try:
        number = float(str(value).replace(",", "."))
        if 0 <= number <= 1:
            number *= 100
        return f"{number:.0f} %"
    except Exception:
        return str(value)


def format_progress_percent(value: Any) -> str:
    """Format OpenMower current_action_progress as 00% through 100%."""
    number = parse_float_like(value)
    if number is None:
        return ""
    if 0 <= number <= 1:
        number *= 100
    number = max(0.0, min(100.0, number))
    rounded = int(round(number))
    if rounded >= 100:
        return "100%"
    return f"{rounded:02d}%"


def format_local_datetime_value(value: Any) -> str:
    """Format ISO or Unix-style timestamps for the configured local time zone."""
    if value is None or value == "":
        return ""
    try:
        tz = ZoneInfo(STATUS_TIMEZONE)
    except Exception:
        tz = timezone.utc
    try:
        if isinstance(value, (int, float)) or str(value).strip().replace(".", "", 1).isdigit():
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            text = str(value).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(value)


def automow_text(robot_state: Dict[str, Any]) -> str:
    """Return the compact AutoMow/Zeitplan status including suspension end."""
    automow = parse_bool_like(robot_state.get("AutoMow"))
    suspension = robot_state.get("AutoMowSuspension")
    if automow is False:
        return "deaktiviert"
    if suspension in (None, ""):
        return "unbekannt" if automow is None else "aktiv"
    suspension_text = str(suspension).strip()
    suspension_bool = parse_bool_like(suspension)
    if suspension_bool is False or suspension_text in {"0", "0.0"}:
        return "aktiv"
    if suspension_text.startswith("9999-") or suspension_text.lower() in {"true", "1", "on", "yes", "ja", "forever", "unlimited", "dauerhaft", "unbestimmt"}:
        return "deaktiviert ohne Ablaufzeit"
    until_text = format_local_datetime_value(suspension)
    if until_text:
        return f"deaktiviert bis {until_text}"
    return "deaktiviert ohne Ablaufzeit"


def gps_state_dict() -> Dict[str, Any]:
    state = OPENMOWER_STATE.get("gps_state") or {}
    return dict(state) if isinstance(state, dict) else {}


def gps_position_dict() -> Dict[str, Any]:
    state = OPENMOWER_STATE.get("gps_position") or {}
    return dict(state) if isinstance(state, dict) else {}


def gps_drive_ready_value(gps_state: Dict[str, Any]) -> Optional[bool]:
    if "gps_drive_ready" in gps_state:
        return parse_bool_like(gps_state.get("gps_drive_ready"))
    if "available" in gps_state:
        available = parse_bool_like(gps_state.get("available"))
        if available is False:
            return False
    if parse_bool_like(gps_state.get("gps_timeout")) is True:
        return False
    if parse_bool_like(gps_state.get("recent_absolute_pose")) is False:
        return False
    rtk_state = str(gps_state.get("rtk_state") or "").strip().lower()
    if rtk_state:
        return rtk_state == "fixed"
    return None


def gps_drive_ready_text(gps_state: Dict[str, Any]) -> str:
    ready = gps_drive_ready_value(gps_state)
    if ready is True:
        return "fahrbereit"
    if ready is False:
        return "nicht fahrbereit"
    return "unbekannt"


def first_present_value(data: Dict[str, Any], *paths: str) -> Any:
    for path in paths:
        value = json_path_value(data, path)
        if value not in (None, ""):
            return value
    return None


def parse_coordinate_pair(*sources: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Find WGS84 latitude/longitude values without using local map pose.x/y.

    OpenMower robot_state/json can contain both a local map pose and a
    world_pose.  Only world_pose.latitude/longitude are suitable for Google
    Maps, and only when world_pose.valid=true and coordinate_system=WGS84.
    """
    lat_keys = ("latitude", "lat", "gps.latitude", "gps.lat", "position.latitude", "position.lat", "wgs84.latitude", "wgs84.lat")
    lon_keys = ("longitude", "lon", "lng", "gps.longitude", "gps.lon", "gps.lng", "position.longitude", "position.lon", "position.lng", "wgs84.longitude", "wgs84.lon")
    for source in sources:
        if not isinstance(source, dict):
            continue
        world_pose = source.get("world_pose") if isinstance(source.get("world_pose"), dict) else None
        if world_pose:
            valid = parse_bool_like(world_pose.get("valid"))
            coordinate_system = str(world_pose.get("coordinate_system") or "").strip().upper()
            lat = parse_float_like(world_pose.get("latitude"))
            lon = parse_float_like(world_pose.get("longitude"))
            if valid is True and coordinate_system == "WGS84" and lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
        lat = parse_float_like(first_present_value(source, *lat_keys))
        lon = parse_float_like(first_present_value(source, *lon_keys))
        if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    return None, None


def gps_placeholder_config() -> Dict[str, Any]:
    placeholder = CONFIG.get("gps", {}).get("position_placeholder", {})
    return placeholder if isinstance(placeholder, dict) else {}


def gps_position_and_map_lines(gps_ready: Optional[bool]) -> List[str]:
    if gps_ready is False:
        return ["*Position:* nicht verfügbar"]
    robot_state = dict(OPENMOWER_STATE.get("robot_state") or {})
    gps_state = gps_state_dict()
    gps_position = gps_position_dict()
    lat, lon = parse_coordinate_pair(gps_position, gps_state, robot_state)
    if lat is not None and lon is not None:
        coord = f"{lat:.6f}, {lon:.6f}"
        return [f"*Position:* {coord}", f"*Karte:* https://www.google.com/maps?q={lat:.6f},{lon:.6f}"]
    placeholder = gps_placeholder_config()
    if as_bool(placeholder.get("enabled", True)):
        lat_text = str(placeholder.get("latitude") or "{latitude}")
        lon_text = str(placeholder.get("longitude") or "{longitude}")
        position_text = str(placeholder.get("position_text") or "Platzhalter: GPS-Koordinaten aus zukuenftiger MQTT-Schnittstelle")
        map_url = str(placeholder.get("map_url") or "https://www.google.com/maps?q={latitude},{longitude}")
        map_url = map_url.replace("{latitude}", lat_text).replace("{longitude}", lon_text)
        return [f"*Position:* {position_text}", f"*Karte:* {map_url}"]
    return ["*Position:* nicht verfügbar"]


def bot_gps_compact_lines() -> List[str]:
    gps_state = gps_state_dict()
    ready = gps_drive_ready_value(gps_state)
    lines = [f"*GPS:* {gps_drive_ready_text(gps_state)}"]
    lines.extend(gps_position_and_map_lines(ready))
    return lines


def bool_text(value: Any) -> str:
    parsed = parse_bool_like(value)
    if parsed is True:
        return "ja"
    if parsed is False:
        return "nein"
    return "unbekannt"


def format_number(value: Any, suffix: str = "") -> str:
    number = parse_float_like(value)
    if number is None:
        return "unbekannt"
    text = f"{number:.3f}".rstrip("0").rstrip(".")
    return f"{text}{suffix}"


def bot_gps_text() -> str:
    """Return detailed GPS diagnostics for the WhatsApp GPS submenu."""
    wait_for_fresh_openmower_status()
    gps_state = gps_state_dict()
    ready = gps_drive_ready_value(gps_state)
    reason = str(gps_state.get("gps_drive_block_reason") or gps_state.get("gps_drive_reason") or gps_state.get("gps_drive_label") or "unbekannt")
    lines = [
        "*Mobert GPS*",
        "──────────",
        "",
        f"*GPS verfügbar:* {bool_text(gps_state.get('available'))}",
        f"*Fahrbereit:* {gps_drive_ready_text(gps_state)}",
        f"*Qualität:* {gps_state.get('quality', 'unbekannt')}",
        f"*RTK:* {gps_state.get('rtk_state', 'unbekannt')}",
        f"*Satelliten sichtbar:* {gps_state.get('visible', 'unbekannt')}",
        f"*Satelliten benutzt:* {gps_state.get('used', 'unbekannt')}",
        f"*Genauigkeit:* {format_number(gps_state.get('position_accuracy_m'), ' m')}",
        f"*Max. Genauigkeit:* {format_number(gps_state.get('max_position_accuracy_m'), ' m')}",
        f"*Orientierung gültig:* {bool_text(gps_state.get('orientation_valid'))}",
        f"*Pose aktuell:* {bool_text(gps_state.get('recent_absolute_pose'))}",
        f"*GPS-Timeout:* {bool_text(gps_state.get('gps_timeout'))}",
        f"*Alter:* {format_number(gps_state.get('age_ms'), ' ms')}",
        f"*Grund:* {reason}",
    ]
    lines.extend(bot_gps_compact_lines()[1:])
    return "\n".join(lines)


def status_push_config() -> Dict[str, Any]:
    return dict(CONFIG.get("status_push") or {})


def status_push_payload() -> Dict[str, Any]:
    cfg = status_push_config()
    target = str(cfg.get("target_group") or effective_default_group() or "")
    return {
        "enabled": as_bool(cfg.get("enabled", False)),
        "interval_minutes": int(cfg.get("interval_minutes", 30) or 30),
        "min_interval_minutes": int(cfg.get("min_interval_minutes", 5) or 5),
        "target_group": target,
        "target_name": group_subject(target),
        "text": status_push_text(),
    }


def status_push_text() -> str:
    cfg = status_push_config()
    enabled = as_bool(cfg.get("enabled", False))
    interval = int(cfg.get("interval_minutes", 30) or 30)
    target = str(cfg.get("target_group") or effective_default_group() or "")
    if enabled:
        suffix = f" an {target} {group_subject(target)}" if target else ""
        return f"Automatischer Status aktiv: alle {interval} Minuten{suffix}."
    return "Automatischer Status ist aus."


def append_status_after_confirmation_enabled() -> bool:
    return as_bool(CONFIG.get("bot", {}).get("append_status_to_confirmations", False))


def append_status_setting_text() -> str:
    return "ein" if append_status_after_confirmation_enabled() else "aus"


def should_append_status_to_confirmation(context: Dict[str, Any], message: str) -> bool:
    if not append_status_after_confirmation_enabled():
        return False
    if not str(context.get("command_id") or ""):
        return False
    if str(context.get("request_id") or "").startswith("mqtt:"):
        return False
    command_id = str(context.get("command_id") or "")
    processing_mode = str(context.get("processing.mode") or "")
    excluded_commands = {
        "help", "status", "groups", "target", "gps_details", "gps_status_details", "gps_details_alias",
        "status_push_info", "append_status_info",
    }
    if command_id in excluded_commands or processing_mode in {"local_status", "local_gps", "local_reply", "local_groups", "local_default_group"}:
        return False
    if "*Mobert Status*" in message:
        return False
    return True


def maybe_append_status_to_confirmation(message: str, context: Dict[str, Any]) -> str:
    if should_append_status_to_confirmation(context, message):
        wait_for_fresh_openmower_status(timeout_seconds=1.0)
        return message.rstrip() + "\n\n" + bot_status_text()
    return message


def battery_text(robot_state: Dict[str, Any]) -> str:
    battery = format_percent_fraction(robot_state.get("battery_percentage"))
    charging = parse_bool_like(robot_state.get("is_charging"))
    if charging is True:
        return f"{battery} (lädt)"
    if charging is False:
        return battery
    return battery


def robot_has_active_area(robot_state: Dict[str, Any]) -> bool:
    area_number = str(robot_state.get("current_area") or "").strip()
    area_id = str(robot_state.get("current_area_id") or "").strip()
    return bool((area_number and area_number not in {"-1", "0"}) or area_id)


def current_area_text(robot_state: Dict[str, Any], include_progress: bool = True) -> str:
    area_number = str(robot_state.get("current_area") or "").strip()
    area_id = str(robot_state.get("current_area_id") or "").strip()
    if area_number and area_number not in {"-1", "0"}:
        area = f"Fläche {area_number}"
    elif area_id:
        area = f"Fläche {area_id}"
    else:
        return "keine aktive Fläche"

    if include_progress and str(robot_state.get("current_state") or "").upper() == "MOWING":
        progress = format_progress_percent(robot_state.get("current_action_progress"))
        if progress:
            return f"{area} ({progress})"
    return area


def emergency_text(robot_state: Dict[str, Any]) -> str:
    emergency = parse_bool_like(robot_state.get("emergency"))
    if emergency is True:
        return "ja"
    if emergency is False:
        return "nein"
    return "unbekannt"


def error_text(robot_state: Dict[str, Any]) -> str:
    emergency = parse_bool_like(robot_state.get("emergency"))
    sub_state = str(robot_state.get("current_sub_state") or "").strip()
    if emergency is True:
        return sub_state or "Emergency/Notfall aktiv"
    if sub_state:
        return sub_state
    if emergency is False:
        return "keiner"
    return "unbekannt"


def bot_status_text() -> str:
    robot_state = dict(OPENMOWER_STATE.get("robot_state") or {})
    wifi_value = OPENMOWER_STATE.get("wifi_percent")
    state_name = str(robot_state.get("current_state") or "unbekannt")
    lines = [
        "*Mobert Status*",
        "──────────────",
        "",
        f"*Zeit:* {now_local_text()}",
        f"*Status:* {state_name}",
        f"*Fläche:* {current_area_text(robot_state)}",
        f"*Akku:* {battery_text(robot_state)}",
        f"*Automatik:* {automow_text(robot_state)}",
        f"*WLAN:* {format_wifi_percent(wifi_value)}",
    ]
    lines.extend(bot_gps_compact_lines())
    lines.extend([
        f"*Emergency:* {emergency_text(robot_state)}",
        f"*Fehler:* {error_text(robot_state)}",
        f"*MQTT:* {mqtt_connection_text()}",
    ])
    return "\n".join(lines)


def bot_groups_text() -> str:
    lines = ["Mobert Gruppen:"]
    default_group = effective_default_group()
    listen_group = effective_listener_group()
    for key, group in GROUPS_BY_KEY.items():
        flags = []
        if key == default_group:
            flags.append("Ziel")
        if key == listen_group:
            flags.append("Lauschen")
        suffix = f" [{' / '.join(flags)}]" if flags else ""
        lines.append(f"{key}: {group['subject']}{suffix}")
    return "\n".join(lines)


def render_value(template_value: str, context: Dict[str, Any]) -> str:
    rendered = template_value or ""
    for key, value in context.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    # Support simple dotted aliases used in the flow XML.
    rendered = rendered.replace("{processing.result}", str(context.get("processing.result", "")))
    return rendered


def try_parse_json_value(payload: str) -> Any:
    try:
        return json.loads(payload)
    except Exception:
        return payload


def unwrap_data_root(value: Any) -> Any:
    if isinstance(value, dict) and isinstance(value.get("d"), dict):
        return value["d"]
    return value


def json_path_value(data: Any, path: str) -> Any:
    current = unwrap_data_root(data)
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def comparable_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value).strip()


def condition_matches_value(actual: Any, expected: Optional[str]) -> bool:
    if expected is None:
        return True
    actual_bool = parse_bool_like(actual)
    expected_bool = parse_bool_like(expected)
    if expected_bool is not None and actual_bool is not None:
        return actual_bool == expected_bool
    actual_text = comparable_value(actual).lower()
    expected_text = comparable_value(expected).lower()
    return actual_text == expected_text


def mqtt_topic_matches_filter_or_suffix(source_topic: str, expected_filter: str) -> bool:
    """Match MQTT filters even when ROS adds a topic prefix.

    OpenMower ROS installations often publish with OM_MQTT_TOPIC_PREFIX, for
    example openmower/robot_state/json instead of robot_state/json.  The flow
    XML subscribes to the concrete prefixed topics, but the status cache should
    still recognize the semantic status source independent of the prefix.
    """
    source = str(source_topic or "").strip("/")
    expected = str(expected_filter or "").strip("/")
    if not source or not expected:
        return False
    if mqtt.topic_matches_sub(expected, source):
        return True
    if source == expected or source.endswith("/" + expected):
        return True
    if expected.endswith("/#"):
        base = expected[:-2].strip("/")
        if not base:
            return False
        padded_source = "/" + source
        return padded_source.endswith("/" + base) or ("/" + base + "/") in padded_source
    return False


def mqtt_topic_matches_suffix(source_topic: str, expected_topic: str) -> bool:
    """Match a concrete MQTT topic with or without an arbitrary prefix."""
    source = str(source_topic or "").strip("/")
    expected = str(expected_topic or "").strip("/")
    return bool(source and expected and (source == expected or source.endswith("/" + expected)))


def parse_float_like(value: Any) -> Optional[float]:
    """Return a float for numeric MQTT payloads and ignore binary/text garbage."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    if text.endswith("%"):
        text = text[:-1].strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def extract_wifi_percent_value(payload: str, parsed: Any) -> Optional[float]:
    """Extract the numeric WiFi percentage from the text /data topic only."""
    root = unwrap_data_root(parsed)
    candidate: Any = root
    if isinstance(root, dict):
        candidate = None
        for key in ("data", "value", "percent", "wifi_percent"):
            if key in root:
                candidate = root[key]
                break
    value = parse_float_like(candidate)
    if value is not None:
        return value
    return parse_float_like(payload)


def update_mqtt_state_cache(source_topic: str, payload: str) -> Tuple[Any, Any]:
    parsed = try_parse_json_value(payload)
    previous_entry = MQTT_TOPIC_CACHE.get(source_topic, {})
    previous = previous_entry.get("json")
    MQTT_TOPIC_CACHE[source_topic] = {"payload": payload, "json": parsed, "time": now_iso()}
    update_openmower_state(source_topic, payload, parsed, previous)
    return parsed, previous


def update_openmower_state(source_topic: str, payload: str, parsed: Any, previous: Any = None) -> None:
    changed = False
    with OPENMOWER_STATE_UPDATED:
        OPENMOWER_STATE["last_mqtt_topic"] = source_topic
        OPENMOWER_STATE["last_mqtt_payload"] = payload
        OPENMOWER_STATE["last_mqtt_time"] = now_iso()
        changed = True
        if mqtt_topic_matches_filter_or_suffix(source_topic, "robot_state/#"):
            root = unwrap_data_root(parsed)
            if isinstance(root, dict):
                OPENMOWER_STATE["robot_state_previous"] = dict(OPENMOWER_STATE.get("robot_state") or {})
                OPENMOWER_STATE["robot_state"] = root
                OPENMOWER_STATE["robot_state_time"] = now_iso()
                changed = True
        elif mqtt_topic_matches_filter_or_suffix(source_topic, "gps_state/#"):
            root = unwrap_data_root(parsed)
            if isinstance(root, dict):
                OPENMOWER_STATE["gps_state_previous"] = dict(OPENMOWER_STATE.get("gps_state") or {})
                OPENMOWER_STATE["gps_state"] = root
                OPENMOWER_STATE["gps_state_time"] = now_iso()
                changed = True
        elif mqtt_topic_matches_filter_or_suffix(source_topic, "gps/position/#") or mqtt_topic_matches_filter_or_suffix(source_topic, "gps_position/#"):
            root = unwrap_data_root(parsed)
            if isinstance(root, dict):
                OPENMOWER_STATE["gps_position"] = root
                OPENMOWER_STATE["gps_position_time"] = now_iso()
                changed = True
        elif mqtt_topic_matches_suffix(source_topic, "sensors/om_system_wifi_signal_percent/data"):
            # Only the /data sibling is a human-readable number.  The parent
            # sensor topic can also contain /bson, which is binary and must not
            # overwrite the last valid WLAN percentage in the status cache.
            value = extract_wifi_percent_value(payload, parsed)
            if value is not None:
                OPENMOWER_STATE["wifi_percent"] = value
                OPENMOWER_STATE["wifi_time"] = now_iso()
                changed = True
        if changed:
            OPENMOWER_STATE_UPDATED.notify_all()


def robot_current_state_upper() -> str:
    robot_state = OPENMOWER_STATE.get("robot_state") or {}
    if isinstance(robot_state, dict):
        return str(robot_state.get("current_state") or "").strip().upper()
    return ""


def gps_loss_warning_text(gps_state: Dict[str, Any]) -> str:
    reason = str(gps_state.get("gps_drive_block_reason") or gps_state.get("gps_drive_reason") or gps_state.get("gps_drive_label") or "GPS ist nicht fahrbereit")
    return "\n".join([
        "⚠️ GPS-Verlust während des Mähens erkannt.",
        f"Zeit: {now_local_text()}",
        f"Status: {robot_current_state_upper() or 'unbekannt'}",
        f"GPS: {gps_drive_ready_text(gps_state)}",
        f"RTK: {gps_state.get('rtk_state', 'unbekannt')}",
        f"Genauigkeit: {format_number(gps_state.get('position_accuracy_m'), ' m')}",
        f"Grund: {reason}",
    ])


def handle_internal_openmower_events(client: mqtt.Client, source_topic: str, parsed: Any, previous: Any) -> None:
    """Handle cross-topic events that cannot be expressed in XML alone.

    GPS loss while mowing needs the current robot_state and the current gps_state
    at the same time.  The XML flow matcher can compare only the payload of the
    triggering MQTT topic, so this one event is handled centrally in Python.
    """
    global GPS_LOSS_ALERT_ACTIVE
    if mqtt_topic_matches_filter_or_suffix(source_topic, "robot_state/#"):
        if robot_current_state_upper() != "MOWING":
            GPS_LOSS_ALERT_ACTIVE = False
        return
    if not mqtt_topic_matches_filter_or_suffix(source_topic, "gps_state/#"):
        return
    root = unwrap_data_root(parsed)
    previous_root = unwrap_data_root(previous)
    if not isinstance(root, dict):
        return
    mowing = robot_current_state_upper() == "MOWING"
    current_ready = gps_drive_ready_value(root)
    previous_ready = gps_drive_ready_value(previous_root) if isinstance(previous_root, dict) else None
    if mowing and current_ready is False and previous_ready is not False and not GPS_LOSS_ALERT_ACTIVE:
        GPS_LOSS_ALERT_ACTIVE = True
        send_text(client, effective_default_group(), gps_loss_warning_text(root), request_id="openmower:gps_loss")
    elif current_ready is True or not mowing:
        GPS_LOSS_ALERT_ACTIVE = False


def wait_for_fresh_openmower_status(timeout_seconds: float = STATUS_FRESH_WAIT_SECONDS) -> bool:
    """Wait briefly for newer ROS-MQTT status samples.

    The status command should prefer fresh robot_state/json and WLAN values.
    If one of the topics is quiet, this function returns after the timeout and
    the status text uses the latest cached values.
    """
    if timeout_seconds <= 0:
        return False
    end_time = time.monotonic() + timeout_seconds
    with OPENMOWER_STATE_UPDATED:
        start_robot_time = str(OPENMOWER_STATE.get("robot_state_time") or "")
        start_wifi_time = str(OPENMOWER_STATE.get("wifi_time") or "")
        while True:
            robot_is_fresh = bool(OPENMOWER_STATE.get("robot_state_time")) and str(OPENMOWER_STATE.get("robot_state_time")) != start_robot_time
            wifi_is_fresh = bool(OPENMOWER_STATE.get("wifi_time")) and str(OPENMOWER_STATE.get("wifi_time")) != start_wifi_time
            # If one value did not exist before and arrives now, that is fresh too.
            if robot_is_fresh and wifi_is_fresh:
                return True
            remaining = end_time - time.monotonic()
            if remaining <= 0:
                return robot_is_fresh or wifi_is_fresh
            OPENMOWER_STATE_UPDATED.wait(timeout=remaining)


def enrich_mqtt_context(context: Dict[str, Any], source_topic: str, payload: str, parsed: Any, previous: Any) -> None:
    context.update({
        "topic": source_topic,
        "payload": payload,
        "mqttTopic": source_topic,
        "mqttPayload": payload,
        "timestamp": now_iso(),
        "timestampLocal": now_local_text(),
        "mqttConnection": mqtt_connection_text(),
        "wifi_percent": format_wifi_percent(OPENMOWER_STATE.get("wifi_percent")),
    })
    root = unwrap_data_root(parsed)
    previous_root = unwrap_data_root(previous)
    if isinstance(root, dict):
        for key, value in root.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                context[key] = value
                context[f"json.{key}"] = value
        context["areaText"] = current_area_text(root)
        context["batteryText"] = battery_text(root)
        context["chargingText"] = "lädt" if parse_bool_like(root.get("is_charging")) is True else "lädt nicht" if parse_bool_like(root.get("is_charging")) is False else "unbekannt"
        context["emergencyText"] = emergency_text(root)
        context["errorText"] = error_text(root)
    if isinstance(previous_root, dict):
        for key, value in previous_root.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                context[f"previous.{key}"] = value


def json_conditions_match(conditions: List[Dict[str, Any]], parsed: Any, previous: Any) -> bool:
    if not conditions:
        return True
    root = unwrap_data_root(parsed)
    previous_root = unwrap_data_root(previous)
    for condition in conditions:
        name = str(condition.get("name") or "")
        actual = json_path_value(root, name)
        previous_actual = json_path_value(previous_root, name) if previous_root is not None else None
        if as_bool(condition.get("exists", False)) and actual is None:
            return False
        if as_bool(condition.get("previous_exists", False)) and previous_root is None:
            return False
        if condition.get("equals") is not None and not condition_matches_value(actual, condition.get("equals")):
            return False
        if condition.get("not_equals") is not None and condition_matches_value(actual, condition.get("not_equals")):
            return False
        if condition.get("previous_equals") is not None and not condition_matches_value(previous_actual, condition.get("previous_equals")):
            return False
        if condition.get("previous_not_equals") is not None and condition_matches_value(previous_actual, condition.get("previous_not_equals")):
            return False
    return True


def flow_step_matches_mqtt(step: Dict[str, Any], source_topic: str, payload: str, parsed: Any = None, previous: Any = None) -> bool:
    input_cfg = step.get("input", {})
    expected_topic = str(input_cfg.get("topic") or "").strip()
    if not expected_topic:
        return False
    if not mqtt.topic_matches_sub(expected_topic, source_topic):
        return False
    payload_equals = str(input_cfg.get("payload_equals") or "")
    if payload_equals and payload != payload_equals:
        return False
    if as_bool(input_cfg.get("payload_not_empty", False)) and payload == "":
        return False
    if parsed is None:
        parsed = try_parse_json_value(payload)
    if not json_conditions_match(list(input_cfg.get("json_conditions") or []), parsed, previous):
        return False
    return True


def execute_output(client: mqtt.Client, output_cfg: Dict[str, Any], context: Dict[str, Any]) -> str:
    module_id = output_cfg.get("module", "")
    if module_id and not module_is_enabled(module_id):
        return ""
    output_type = output_cfg.get("type", "")
    if module_id == "mqtt_output" and output_type == "publish":
        mqtt_topic = render_value(str(output_cfg.get("topic") or ""), context)
        payload_value = render_value(str(output_cfg.get("payload") or ""), context)
        qos = int(output_cfg.get("qos", 0) or 0)
        retain = as_bool(output_cfg.get("retain", False))
        if not mqtt_topic:
            raise RuntimeError("mqtt_output requires topic")
        client.publish(mqtt_topic, payload_value, qos=qos, retain=retain)
        return ""
    if module_id == "whatsapp_output" and output_type == "send":
        target = render_value(str(output_cfg.get("target") or ""), context)
        if target == "default_group":
            target = effective_default_group()
        elif target in {"{replyTarget}", "replyTarget"}:
            target = str(context.get("replyTarget", ""))
        message = render_value(str(output_cfg.get("message") or ""), context)
        message = maybe_append_status_to_confirmation(message, context)
        if message.strip():
            send_text(client, target, message, request_id=str(context.get("request_id", "bot")))
        return message
    # Unknown output modules are intentionally not executed. This keeps XML safe:
    # only registered modules can perform actions.
    log(f"Ignored unsupported output module={module_id} type={output_type}")
    return ""


def execute_processing(client: mqtt.Client, flow: Dict[str, Any], step: Dict[str, Any], context: Dict[str, Any]) -> Tuple[bool, str]:
    processing = step.get("processing", {})
    mode = processing.get("mode", "passthrough")
    context["processing.mode"] = mode
    try:
        if mode in {"passthrough", "confirmation_result"}:
            if mode == "confirmation_result":
                contains = str(processing.get("error_payload_contains") or "")
                payload = str(context.get("payload", ""))
                if contains and contains.lower() in payload.lower():
                    context["result"] = "error"
            return True, ""
        if mode == "local_reply":
            template = str(processing.get("template") or "")
            context["processing.result"] = command_help_text() if template == "{help}" else render_value(template, context)
            return True, str(context.get("processing.result", ""))
        if mode == "local_status":
            wait_for_fresh_openmower_status()
            context["processing.result"] = bot_status_text()
            return True, str(context.get("processing.result", ""))
        if mode == "local_gps":
            context["processing.result"] = bot_gps_text()
            return True, str(context.get("processing.result", ""))
        if mode == "set_status_push":
            minutes = int(context.get("minutes") or context.get("interval") or 0)
            cfg = CONFIG.setdefault("status_push", {})
            minimum = int(cfg.get("min_interval_minutes", 5) or 5)
            interval = max(minimum, minutes)
            target = str(context.get("chatAlias") or effective_default_group() or "").strip()
            cfg["enabled"] = True
            cfg["interval_minutes"] = interval
            if target:
                cfg["target_group"] = target
            save_config(CONFIG)
            publish_state(client, refresh_groups=False)
            note = f" Mindestintervall {minimum} Minuten wurde verwendet." if interval != minutes else ""
            context["processing.result"] = f"Automatischer Status aktiv: alle {interval} Minuten.{note}"
            return True, str(context.get("processing.result", ""))
        if mode == "status_push_off":
            CONFIG.setdefault("status_push", {})["enabled"] = False
            save_config(CONFIG)
            publish_state(client, refresh_groups=False)
            context["processing.result"] = "Automatischer Status wurde ausgeschaltet."
            return True, str(context.get("processing.result", ""))
        if mode == "status_push_info":
            context["processing.result"] = status_push_text()
            return True, str(context.get("processing.result", ""))
        if mode == "set_append_status":
            value = render_value(str(processing.get("value") or "false"), context)
            CONFIG.setdefault("bot", {})["append_status_to_confirmations"] = as_bool(value)
            save_config(CONFIG)
            publish_state(client, refresh_groups=False)
            context["processing.result"] = f"Status nach Bestaetigungen ist jetzt {append_status_setting_text()}."
            return True, str(context.get("processing.result", ""))
        if mode == "append_status_info":
            context["processing.result"] = f"Status nach Bestaetigungen ist {append_status_setting_text()}."
            return True, str(context.get("processing.result", ""))
        if mode == "local_groups":
            context["processing.result"] = bot_groups_text()
            return True, str(context.get("processing.result", ""))
        if mode == "local_default_group":
            value = effective_default_group()
            context["processing.result"] = f"Standard-Zielgruppe: {value} {group_subject(value)}"
            return True, str(context.get("processing.result", ""))
        if mode == "set_module_property":
            module_ref = str(processing.get("module_ref") or "")
            property_name = str(processing.get("property") or "")
            value = render_value(str(processing.get("value") or ""), context)
            if module_ref in {"whatsapp", "whatsapp_watchdog"} and property_name == "listenerGroup":
                group = resolve_group(value)
                if not group:
                    context["processing.result"] = f"Unbekannte Gruppe: {value}"
                    context["result"] = "error"
                    return False, str(context["processing.result"])
                CONFIG.setdefault("bot", {})["listen_group"] = group["key"]
                if as_bool(processing.get("persist", False)):
                    save_config(CONFIG)
                publish_state(client, refresh_groups=False)
                context["group"] = group["key"]
                context["processing.result"] = f"Bot-Lauschgruppe gesetzt: {group['key']} {group['subject']}"
                return True, str(context["processing.result"])
            context["processing.result"] = f"Eigenschaft nicht unterstuetzt: {module_ref}.{property_name}"
            context["result"] = "error"
            return False, str(context["processing.result"])
        context["processing.result"] = f"Processing-Modul noch nicht implementiert: {mode}"
        context["result"] = "error"
        return False, str(context["processing.result"])
    except Exception as exc:
        context["processing.result"] = str(exc)
        context["result"] = "error"
        return False, str(exc)


def execute_flow_step(client: mqtt.Client, flow: Dict[str, Any], step: Dict[str, Any], context: Dict[str, Any], result_filter: str = "") -> Tuple[bool, List[str]]:
    accepted, processing_text = execute_processing(client, flow, step, context)
    selected_result = result_filter or str(context.get("result", "success" if accepted else "error"))
    messages: List[str] = []
    for output_cfg in step.get("outputs", []) or []:
        output_result = str(output_cfg.get("result") or "")
        if output_result and output_result != selected_result:
            continue
        try:
            message = execute_output(client, output_cfg, context)
            if message:
                messages.append(message)
        except Exception as exc:
            messages.append(f"Ausgabe fehlgeschlagen: {exc}")
            accepted = False
    if accepted and step.get("next_step"):
        register_pending_confirmation(client, flow, step, context)
    if not messages and processing_text:
        messages.append(processing_text)
    return accepted, messages


def register_pending_confirmation(client: mqtt.Client, flow: Dict[str, Any], step: Dict[str, Any], context: Dict[str, Any]) -> None:
    next_id = str(step.get("next_step") or "")
    next_step = flow.get("steps", {}).get(next_id)
    if not next_step:
        return
    input_cfg = next_step.get("input", {})
    if input_cfg.get("module") != "mqtt_watchdog":
        return
    timeout_seconds = int(input_cfg.get("timeout_seconds", 0) or 0)
    pending_id = f"{flow['id']}:{next_id}:{time.time()}"
    pending_context = dict(context)
    pending = {"id": pending_id, "flow": flow, "step": next_step, "context": pending_context, "client": client}
    def timeout_callback() -> None:
        finish_pending_confirmation(pending_id, "timeout", "", "")
    if timeout_seconds > 0:
        pending["timer"] = threading.Timer(timeout_seconds, timeout_callback)
        pending["timer"].daemon = True
    with PENDING_CONFIRMATIONS_LOCK:
        PENDING_CONFIRMATIONS.append(pending)
    if pending.get("timer") is not None:
        pending["timer"].start()
    publish(client, topic("bot", "confirmations", "pending", "json"), {"d": pending_confirmations_payload()}, retain=False)


def pending_confirmations_payload() -> List[Dict[str, Any]]:
    with PENDING_CONFIRMATIONS_LOCK:
        return [
            {
                "id": item.get("id", ""),
                "flow": item.get("flow", {}).get("id", ""),
                "step": item.get("step", {}).get("id", ""),
                "topic": item.get("step", {}).get("input", {}).get("topic", ""),
            }
            for item in PENDING_CONFIRMATIONS
        ]


def finish_pending_confirmation(pending_id: str, result: str, source_topic: str, payload: str) -> bool:
    pending: Optional[Dict[str, Any]] = None
    with PENDING_CONFIRMATIONS_LOCK:
        for index, item in enumerate(PENDING_CONFIRMATIONS):
            if item.get("id") == pending_id:
                pending = PENDING_CONFIRMATIONS.pop(index)
                break
    if pending is None:
        return False
    timer = pending.get("timer")
    if timer is not None:
        timer.cancel()
    client = pending["client"]
    flow = pending["flow"]
    step = pending["step"]
    context = dict(pending["context"])
    context.update({"result": result})
    if result != "timeout":
        parsed = try_parse_json_value(payload)
        previous = MQTT_TOPIC_CACHE.get(source_topic, {}).get("json")
        enrich_mqtt_context(context, source_topic, payload, parsed, previous)
        execute_processing(client, flow, step, context)
        result = str(context.get("result", result))
    for output_cfg in step.get("outputs", []) or []:
        output_result = str(output_cfg.get("result") or "")
        if output_result and output_result != result:
            continue
        execute_output(client, output_cfg, context)
    publish(client, topic("bot", "confirmations", "pending", "json"), {"d": pending_confirmations_payload()}, retain=False)
    return True


def handle_flow_mqtt_event(client: mqtt.Client, source_topic: str, payload: str) -> bool:
    handled = False
    parsed, previous = update_mqtt_state_cache(source_topic, payload)
    handle_internal_openmower_events(client, source_topic, parsed, previous)
    # Confirmation steps have priority because they belong to a command that is already in progress.
    matching_pending: List[str] = []
    with PENDING_CONFIRMATIONS_LOCK:
        for item in PENDING_CONFIRMATIONS:
            if flow_step_matches_mqtt(item.get("step", {}), source_topic, payload, parsed, previous):
                matching_pending.append(str(item.get("id")))
    for pending_id in matching_pending:
        handled = finish_pending_confirmation(pending_id, "success", source_topic, payload) or handled

    # MQTT watchdog start inputs can also create flows directly, e.g. automatic error or state notifications.
    for flow in BOT_FLOWS.values():
        if not as_bool(flow.get("enabled", True)):
            continue
        for step in flow.get("steps", {}).values():
            input_cfg = step.get("input", {})
            if input_cfg.get("module") != "mqtt_watchdog" or input_cfg.get("type") == "confirmation":
                continue
            if flow_step_matches_mqtt(step, source_topic, payload, parsed, previous):
                context = {
                    "replyTarget": effective_default_group(),
                    "request_id": f"mqtt:{flow.get('id', '')}",
                }
                enrich_mqtt_context(context, source_topic, payload, parsed, previous)
                execute_flow_step(client, flow, step, context)
                handled = True
    return handled


def execute_legacy_command_response(client: mqtt.Client, cmd: BotCommand, values: Dict[str, str]) -> Tuple[str, bool]:
    if cmd.action_type == "local_reply":
        if cmd.response == "{help}":
            return command_help_text(), True
        return interpolate_template(cmd.response, values), True

    if cmd.action_type == "local_status":
        return bot_status_text(), True

    if cmd.action_type == "local_groups":
        return bot_groups_text(), True

    if cmd.action_type == "local_default_group":
        value = effective_default_group()
        return f"Standard-Zielgruppe: {value} {group_subject(value)}", True

    if cmd.action_type == "local_set_listener_group":
        group_value = values.get("group", "")
        group = resolve_group(group_value)
        if not group:
            return f"Unbekannte Gruppe: {group_value}", False
        CONFIG.setdefault("bot", {})["listen_group"] = group["key"]
        save_config(CONFIG)
        publish_state(client, refresh_groups=False)
        return f"Bot-Lauschgruppe gesetzt: {group['key']} {group['subject']}", True

    if cmd.action_type == "mqtt_publish":
        mqtt_payload = interpolate_template(cmd.mqtt_payload, values)
        client.publish(cmd.mqtt_topic, mqtt_payload, qos=cmd.mqtt_qos, retain=cmd.mqtt_retain)
        response = interpolate_template(cmd.immediate_confirmation, values) or "Befehl wurde an MQTT gesendet."
        if cmd.wait_confirmation.get("enabled"):
            response += "\nHinweis: Warten auf MQTT-Bestaetigung ist nur in der Flow-XML aktiv."
        return response, True

    return f"Aktionstyp noch nicht implementiert: {cmd.action_type}", False


def execute_bot_command(client: mqtt.Client, command_text: str, context: Dict[str, Any]) -> Tuple[str, str, bool]:
    cmd, values = find_command(command_text)
    if cmd is None:
        response = f"Unbekannter Mobert-Befehl. Schreibe: {effective_wake_word()}: ?"
        send_text(client, context.get("replyTarget", ""), response, request_id="bot")
        return "unknown", response, False
    context.update(values)
    context.setdefault("command", cmd.trigger)
    context.setdefault("command_id", cmd.command_id)
    if cmd.action_type == "flow" and cmd.flow_id in BOT_FLOWS:
        flow = BOT_FLOWS[cmd.flow_id]
        step = flow.get("steps", {}).get(cmd.step_id or "start")
        if not step:
            response = f"Flow-Schritt nicht gefunden: {cmd.flow_id}/{cmd.step_id}"
            send_text(client, context.get("replyTarget", ""), response, request_id="bot")
            return cmd.command_id, response, False
        accepted, messages = execute_flow_step(client, flow, step, context)
        response_text = "\n".join([m for m in messages if m])
        return cmd.command_id, response_text, accepted

    response_text, accepted = execute_legacy_command_response(client, cmd, values)
    send_text(client, context.get("replyTarget", ""), response_text, request_id="bot")
    return cmd.command_id, response_text, accepted


def parse_wake_command(text: str, wake_word: str) -> Optional[str]:
    stripped = text.strip()
    prefix = wake_word.strip()
    if not prefix:
        return stripped or "?"
    wake_cfg = module_config("whatsapp_watchdog").get("wakeWord") if BOT_MODULES else {}
    case_sensitive = as_bool(wake_cfg.get("caseSensitive", False)) if isinstance(wake_cfg, dict) else False
    if case_sensitive:
        if not stripped.startswith(prefix):
            return None
    else:
        if not stripped.lower().startswith(prefix.lower()):
            return None
    remainder = stripped[len(prefix):].lstrip()
    syntax = str(wake_cfg.get("syntax", "colon") if isinstance(wake_cfg, dict) else "colon")
    if syntax == "colon":
        # The colon is intentionally required: "Mobert: Status".
        if not remainder.startswith(":"):
            return None
        return remainder[1:].strip() or "?"
    return remainder.strip() or "?"


def handle_webhook(data: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    client = MQTT_CLIENT
    if client is None:
        return 503, {"ok": False, "error": "MQTT client not ready"}
    if not waha_enabled():
        return 200, {"ok": True, "ignored": "waha disabled"}

    message = extract_webhook_message(data)
    chat_alias = group_alias_from_chat_id(message["chatId"])
    chat = make_chat_descriptor(chat_alias, message["chatId"])
    incoming_entry = {
        "timestamp": now_iso(),
        "direction": "out" if message["fromMe"] else "in",
        "message_id": message.get("message_id", ""),
        "chat": chat,
        "sender": {"name": "", "number_masked": mask_chat_id(message.get("sender", ""))},
        "text": message["text"],
        "bot": {"matched": False, "command": "", "accepted": False},
    }

    if message["fromMe"]:
        # WAHA may echo messages that were sent by this bridge.  send_text()
        # already writes bridge-originated messages to the ring buffer.  This
        # fallback still records externally sent WhatsApp messages as outgoing
        # history entries when they appear only via webhook.
        publish_outgoing_message_event(client, incoming_entry)
        add_message_history(client, incoming_entry)
        return 200, {"ok": True, "ignored": "fromMe"}

    publish(client, topic("waha", "messages", "in", "json"), {"d": incoming_entry}, retain=False)

    if not effective_bot_enabled():
        add_message_history(client, incoming_entry)
        return 200, {"ok": True, "ignored": "bot disabled"}

    listen_key = effective_listener_group()
    listen_chat_id = group_chat_id(listen_key)
    if not listen_chat_id or message["chatId"] != listen_chat_id:
        add_message_history(client, incoming_entry)
        return 200, {"ok": True, "ignored": "chat not configured listen group"}

    wake_word = effective_wake_word()
    command_text = parse_wake_command(message["text"], wake_word)
    if command_text is None:
        add_message_history(client, incoming_entry)
        return 200, {"ok": True, "ignored": "wake word or colon not found"}

    context = {
        "replyTarget": message["chatId"],
        "chatId": message["chatId"],
        "chatAlias": chat_alias,
        "sender": message.get("sender", ""),
        "senderMasked": mask_chat_id(message.get("sender", "")),
        "rawText": message["text"],
        "request_id": "bot",
    }
    command_id, response_text, accepted = execute_bot_command(client, command_text, context)
    incoming_entry["bot"] = {"matched": True, "command": command_id, "accepted": accepted}
    add_message_history(client, incoming_entry)

    event = {
        "time": now_iso(),
        "chat": listen_key,
        "sender": mask_chat_id(message.get("sender", "")),
        "text": message["text"],
        "command_text": command_text,
        "command": command_id,
        "accepted": accepted,
        "response": response_text,
    }
    publish(client, topic("bot", "events", "json"), {"d": event}, retain=False)
    return 200, {"ok": True, "response": response_text, "command": command_id}


# WAHA calls this HTTP server for incoming WhatsApp events.
#
# Docker/WAHA note:
# WAHA validates webhook URLs strictly and rejects hostnames with underscores.
# Do not use the container name "waha_mqtt_controller" in WHATSAPP_HOOK_URL.
# Use the Docker network alias "waha-mqtt-controller" instead:
#
#   WHATSAPP_HOOK_URL=http://waha-mqtt-controller:8080/webhook
#
# The OpenMower deployment provides this alias through compose.override.yaml.
class WebhookHandler(BaseHTTPRequestHandler):
    def _json_response(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/health":
            self._json_response(200, {"ok": True, "time": now_iso()})
        else:
            self._json_response(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/webhook":
            self._json_response(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
            status, payload = handle_webhook(data)
            self._json_response(status, payload)
        except Exception as exc:
            if MQTT_CLIENT is not None:
                publish_error(MQTT_CLIENT, "webhook", exc)
            self._json_response(500, {"ok": False, "error": str(exc)})

    def log_message(self, fmt: str, *args: Any) -> None:
        log(f"webhook: {fmt % args}")


def start_webhook_server() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", BOT_HTTP_PORT), WebhookHandler)
    log(f"Webhook server listening on port {BOT_HTTP_PORT}")
    server.serve_forever()


# ---------------------------------------------------------------------------
# MQTT callbacks and runtime
# ---------------------------------------------------------------------------

def on_connect(client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
    log(f"Connected to MQTT {MQTT_HOST}:{MQTT_PORT}, reason={reason_code}")

    client.subscribe(topic("waha", "set", "session", "json"))
    client.subscribe(topic("waha", "set", "persistent", "json"))
    client.subscribe(topic("waha", "groups", "set", "renew", "json"))
    client.subscribe(topic("waha", "groups", "set", "json"))
    client.subscribe(topic("waha", "messages", "out", "set", "json"))
    client.subscribe(topic("waha", "messages", "history", "set", "session", "json"))
    client.subscribe(topic("waha", "messages", "history", "set", "persistent", "json"))
    client.subscribe(topic("waha", "action"))

    client.subscribe(topic("bot", "set", "session", "json"))
    client.subscribe(topic("bot", "set", "persistent", "json"))
    client.subscribe(topic("bot", "commands", "set", "renew", "json"))
    client.subscribe(topic("bot", "commands", "set", "xml"))

    # Subscribe status cache topics independently from XML.  This keeps
    # "Mobert: Status" working even when /data/bot_commands.xml is still
    # the legacy command-only file and contains no mqtt_watchdog flows.
    subscribe_status_cache_topics(client)

    # Optional internal forwarding configuration. This has no public legacy topic,
    # but can still be controlled via config.json mounted into /data.
    rebuild_forward_subscriptions(client)
    client.publish(topic("status", "online"), "true", qos=0, retain=True)

    publish_state(client)


def on_disconnect(client: mqtt.Client, userdata: Any, reason_code: Any, properties: Any = None) -> None:
    log(f"Disconnected from MQTT {MQTT_HOST}:{MQTT_PORT}, reason={reason_code}")


def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    mqtt_topic = msg.topic
    payload = read_text_payload(msg.payload)
    try:
        if mqtt_topic == topic("waha", "set", "session", "json"):
            handle_waha_set(client, payload, "session")
        elif mqtt_topic == topic("waha", "set", "persistent", "json"):
            handle_waha_set(client, payload, "persistent")
        elif mqtt_topic == topic("waha", "groups", "set", "renew", "json"):
            handle_groups_renew(client)
        elif mqtt_topic == topic("waha", "groups", "set", "json"):
            handle_groups_set(client, payload)
        elif mqtt_topic == topic("waha", "messages", "out", "set", "json"):
            handle_outgoing_message(client, payload)
        elif mqtt_topic == topic("waha", "messages", "history", "set", "session", "json"):
            handle_history_set(client, payload, "session")
        elif mqtt_topic == topic("waha", "messages", "history", "set", "persistent", "json"):
            handle_history_set(client, payload, "persistent")
        elif mqtt_topic == topic("waha", "action"):
            handle_action(client, payload)
        elif mqtt_topic == topic("bot", "set", "session", "json"):
            handle_bot_set(client, payload, "session")
        elif mqtt_topic == topic("bot", "set", "persistent", "json"):
            handle_bot_set(client, payload, "persistent")
        elif mqtt_topic == topic("bot", "commands", "set", "renew", "json"):
            handle_commands_renew(client)
        elif mqtt_topic == topic("bot", "commands", "set", "xml"):
            handle_commands_set_xml(client, payload)
        elif not mqtt_topic.startswith(MQTT_BASE_TOPIC + "/"):
            if not handle_flow_mqtt_event(client, mqtt_topic, payload):
                handle_forwarded_mqtt(client, mqtt_topic, payload)
    except Exception as exc:
        publish_error(client, mqtt_topic, exc)


def waha_watchdog_loop(client: mqtt.Client) -> None:
    while RUNNING:
        cfg = waha_repair_config()
        time.sleep(int(cfg["watchdog_seconds"]))
        if not waha_enabled():
            continue
        try:
            global SESSION
            SESSION = fetch_session()
            maybe_repair_waha_session(SESSION)
            publish_waha_repair_status(client)
        except Exception as exc:
            with WAHA_REPAIR_LOCK:
                WAHA_REPAIR_STATE["last_action"] = "watchdog_error"
                WAHA_REPAIR_STATE["last_action_time"] = now_iso()
                WAHA_REPAIR_STATE["last_error"] = str(exc)
                WAHA_REPAIR_STATE["last_reason"] = "WAHA-Watchdog konnte die Session nicht pruefen"
            publish_waha_repair_status(client)
            log(f"WAHA watchdog error: {exc}")


def refresh_loop(client: mqtt.Client) -> None:
    while RUNNING:
        time.sleep(REFRESH_SECONDS)
        try:
            publish_state(client)
        except Exception as exc:
            publish_error(client, "refresh_loop", exc)


def status_push_loop(client: mqtt.Client) -> None:
    last_sent_monotonic = 0.0
    last_signature = ""
    while RUNNING:
        time.sleep(5)
        cfg = status_push_config()
        if not as_bool(cfg.get("enabled", False)):
            last_sent_monotonic = 0.0
            last_signature = ""
            continue
        interval = max(int(cfg.get("min_interval_minutes", 5) or 5), int(cfg.get("interval_minutes", 30) or 30))
        target = str(cfg.get("target_group") or effective_default_group() or "").strip()
        signature = f"{target}:{interval}"
        if signature != last_signature:
            last_signature = signature
            last_sent_monotonic = 0.0
        if not target:
            log("Status push is enabled but no target group is configured")
            last_sent_monotonic = time.monotonic()
            continue
        if last_sent_monotonic and time.monotonic() - last_sent_monotonic < interval * 60:
            continue
        try:
            wait_for_fresh_openmower_status(timeout_seconds=1.0)
            send_text(client, target, bot_status_text(), request_id="bot:status_push")
            last_sent_monotonic = time.monotonic()
        except Exception as exc:
            last_sent_monotonic = time.monotonic()
            publish_error(client, "status_push_loop", exc)


def handle_signal(signum: int, frame: Any) -> None:
    global RUNNING
    RUNNING = False


def main() -> None:
    global MQTT_CLIENT
    if waha_enabled() and not WAHA_API_KEY:
        raise RuntimeError("WAHA_API_KEY is missing")
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        client = mqtt.Client()
    MQTT_CLIENT = client
    client.will_set(topic("status", "online"), "false", qos=0, retain=True)
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    log(f"Starting controller with MQTT_BASE_TOPIC={MQTT_BASE_TOPIC}")
    log(f"WAHA_URL={WAHA_URL}")
    log(f"Provider={PROVIDER_NAME}, protocol={PROTOCOL_NAME}")

    webhook_thread = threading.Thread(target=start_webhook_server, daemon=True)
    webhook_thread.start()
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    thread = threading.Thread(target=refresh_loop, args=(client,), daemon=True)
    thread.start()
    waha_watchdog_thread = threading.Thread(target=waha_watchdog_loop, args=(client,), daemon=True)
    waha_watchdog_thread.start()
    status_thread = threading.Thread(target=status_push_loop, args=(client,), daemon=True)
    status_thread.start()
    client.loop_forever()


if __name__ == "__main__":
    main()
