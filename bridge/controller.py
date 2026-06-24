#!/usr/bin/env python3
"""WAHA MQTT Controller for OpenMower.

This service connects three worlds:

1. Mosquitto MQTT
2. WAHA HTTP API
3. Optional WAHA webhooks for incoming WhatsApp messages

The code intentionally keeps all runtime settings in MQTT and /data/config.json.
That makes the Docker container stateless and keeps secrets out of the repository.
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import paho.mqtt.client as mqtt
import requests


# ---------------------------------------------------------------------------
# Environment configuration
# ---------------------------------------------------------------------------

MQTT_HOST = os.getenv("MQTT_HOST", "Mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_BASE_TOPIC = os.getenv("MQTT_BASE_TOPIC", "waha").strip("/")

WAHA_URL = os.getenv("WAHA_URL", "http://waha:3000").rstrip("/")
WAHA_API_KEY = os.getenv("WAHA_API_KEY", "")

REFRESH_SECONDS = int(os.getenv("CONTROLLER_REFRESH_SECONDS", "60"))
BOT_HTTP_PORT = int(os.getenv("BOT_HTTP_PORT", "8080"))

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
CONFIG_FILE = DATA_DIR / "config.json"

RUNNING = True
MQTT_CLIENT: Optional[mqtt.Client] = None

# These values are refreshed from WAHA and reused by MQTT commands and webhooks.
SESSION: Dict[str, str] = {}
GROUPS_BY_KEY: Dict[str, Dict[str, str]] = {}
GROUP_KEYS_BY_CHAT_ID: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    print(f"[waha-mqtt-controller] {message}", flush=True)


def topic(*parts: str) -> str:
    return "/".join([MQTT_BASE_TOPIC] + [p.strip("/") for p in parts if p != ""])


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "ja"}


def read_text_payload(payload: bytes) -> str:
    return payload.decode("utf-8", errors="replace").strip()


# ---------------------------------------------------------------------------
# Persistent configuration
# ---------------------------------------------------------------------------

def default_config() -> Dict[str, Any]:
    return {
        "default_group": "",
        "forward_topics": [],
        "templates": {},
        "bot": {
            "enabled": True,
            "wake_word": "Mobert",
            # listen_group is intentionally configured by MQTT, e.g. g001.
            # The controller resolves g001 to the internal WhatsApp group chatId.
            "listen_group": "",
        },
    }


def normalize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = default_config()
    normalized.update(config or {})

    bot = default_config()["bot"]
    bot.update(normalized.get("bot") or {})
    normalized["bot"] = bot

    normalized.setdefault("forward_topics", [])
    normalized.setdefault("templates", {})
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
    return config


def save_config(config: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(normalize_config(config), indent=2, ensure_ascii=False), encoding="utf-8")


CONFIG = load_config()


# ---------------------------------------------------------------------------
# MQTT publishing helpers
# ---------------------------------------------------------------------------

def publish(client: mqtt.Client, mqtt_topic: str, payload: Any, retain: bool = True) -> None:
    if isinstance(payload, (dict, list)):
        value = json.dumps(payload, ensure_ascii=False)
    elif payload is None:
        value = ""
    else:
        value = str(payload)

    client.publish(mqtt_topic, value, qos=0, retain=retain)


def publish_error(client: mqtt.Client, source_topic: str, exc: Exception) -> None:
    publish(client, topic("error", "last"), {
        "ok": False,
        "time": now_iso(),
        "topic": source_topic,
        "error": str(exc),
    })
    log(f"Error handling {source_topic}: {exc}")


# ---------------------------------------------------------------------------
# WAHA HTTP helpers
# ---------------------------------------------------------------------------

def waha_headers() -> Dict[str, str]:
    return {
        "X-Api-Key": WAHA_API_KEY,
        "Content-Type": "application/json",
    }


def waha_get(path: str) -> Any:
    response = requests.get(
        f"{WAHA_URL}{path}",
        headers={"X-Api-Key": WAHA_API_KEY},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def waha_post(path: str, payload: Dict[str, Any]) -> Any:
    response = requests.post(
        f"{WAHA_URL}{path}",
        headers=waha_headers(),
        json=payload,
        timeout=25,
    )

    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text}

    if response.status_code >= 400:
        raise RuntimeError(f"WAHA HTTP {response.status_code}: {body}")

    return body


# ---------------------------------------------------------------------------
# WAHA response parsing
# ---------------------------------------------------------------------------

def id_to_text(value: Any) -> str:
    """Convert WAHA id objects into a chatId string.

    WEBJS responses can represent a group id either as a string or as an
    object with user/server/_serialized fields. This helper keeps the parser
    robust across WAHA engines.
    """
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

        # Some WAHA/engine responses are maps: {"1000": {"groupMetadata": ...}}
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


def fetch_session() -> Dict[str, str]:
    sessions = unwrap_list(waha_get("/api/sessions"))
    selected = None

    for session in sessions:
        if str(session.get("status", "")).upper() == "WORKING":
            selected = session
            break

    if selected is None and sessions:
        selected = sessions[0]

    if selected is None:
        return {"name": "", "status": "NOT_FOUND", "account": ""}

    me = selected.get("me") or selected.get("account") or ""
    if isinstance(me, dict):
        me = me.get("id") or me.get("pushName") or json.dumps(me, ensure_ascii=False)

    return {
        "name": str(selected.get("name") or selected.get("session") or selected.get("id") or ""),
        "status": str(selected.get("status") or selected.get("state") or ""),
        "account": str(me),
    }


def fetch_groups(session_name: str) -> Dict[str, Dict[str, str]]:
    """Fetch groups and expose them as stable aliases g001, g002, ...

    The actual WhatsApp chatId remains internal. MQTT users can select groups
    by alias or subject, while the controller sends to the real chatId.
    """
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

        if chat_id and "@" not in chat_id and chat_id.isdigit():
            chat_id = f"{chat_id}@g.us"

        if chat_id:
            groups.append({"chatId": str(chat_id), "subject": str(subject)})

    groups.sort(key=lambda x: x["subject"].lower())

    result: Dict[str, Dict[str, str]] = {}
    for index, group in enumerate(groups, start=1):
        key = safe_key(index)
        result[key] = {
            "key": key,
            "chatId": group["chatId"],
            "chatId_masked": mask_chat_id(group["chatId"]),
            "subject": group["subject"],
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

    if value in GROUPS_BY_KEY:
        return GROUPS_BY_KEY[value]

    for group in GROUPS_BY_KEY.values():
        if value == group["chatId"]:
            return group
        if value.lower() == group["subject"].lower():
            return group

    return None


def resolve_group_key(value: str) -> str:
    group = resolve_group(value)
    return group["key"] if group else ""


def group_subject(value: str) -> str:
    group = resolve_group(value)
    return group["subject"] if group else ""


def group_chat_id(value: str) -> str:
    group = resolve_group(value)
    return group["chatId"] if group else value.strip()


# ---------------------------------------------------------------------------
# State publishing
# ---------------------------------------------------------------------------

def publish_state(client: mqtt.Client) -> None:
    global GROUPS_BY_KEY, SESSION

    SESSION = fetch_session()
    GROUPS_BY_KEY = fetch_groups(SESSION["name"])
    rebuild_group_index()

    default_group = CONFIG.get("default_group", "")
    listen_group = CONFIG.get("bot", {}).get("listen_group", "")

    default_group_data = GROUPS_BY_KEY.get(default_group, {})
    listen_group_data = GROUPS_BY_KEY.get(listen_group, {})

    publish(client, topic("status", "online"), "true")
    publish(client, topic("status", "last_update"), now_iso())
    publish(client, topic("status", "error"), "")

    publish(client, topic("session", "name"), SESSION.get("name", ""))
    publish(client, topic("session", "status"), SESSION.get("status", ""))
    publish(client, topic("session", "account"), SESSION.get("account", ""))

    list_payload = []
    for key, group in GROUPS_BY_KEY.items():
        selected = key == default_group
        bot_listen = key == listen_group

        publish(client, topic("groups", key, "subject"), group["subject"])
        publish(client, topic("groups", key, "chatId_masked"), group["chatId_masked"])
        publish(client, topic("groups", key, "selected"), str(selected).lower())
        publish(client, topic("groups", key, "bot_listen"), str(bot_listen).lower())

        list_payload.append({
            "key": key,
            "subject": group["subject"],
            "chatIdMasked": group["chatId_masked"],
            "selected": selected,
            "botListen": bot_listen,
        })

    publish(client, topic("groups", "list"), list_payload)

    publish(client, topic("config", "default_group", "value"), default_group)
    publish(client, topic("config", "default_group", "subject"), default_group_data.get("subject", ""))

    bot = CONFIG.get("bot", {})
    publish(client, topic("config", "bot", "enabled"), str(as_bool(bot.get("enabled", True))).lower())
    publish(client, topic("config", "bot", "wake_word", "value"), bot.get("wake_word", "Mobert"))
    publish(client, topic("config", "bot", "listen_group", "value"), listen_group)
    publish(client, topic("config", "bot", "listen_group", "subject"), listen_group_data.get("subject", ""))

    publish(client, topic("config", "forward_topics", "value"), CONFIG.get("forward_topics", []))
    publish(client, topic("config", "templates", "value"), CONFIG.get("templates", {}))

    log(f"Published state: session={SESSION.get('name')} groups={len(GROUPS_BY_KEY)}")


# ---------------------------------------------------------------------------
# Sending and forwarding
# ---------------------------------------------------------------------------

def send_text(client: mqtt.Client, target_key_or_chat_id: str, text: str) -> None:
    if not SESSION or not SESSION.get("name"):
        raise RuntimeError("No active WAHA session available")

    target = (target_key_or_chat_id or "").strip()
    if not target:
        target = CONFIG.get("default_group", "")

    group = resolve_group(target)
    chat_id = group["chatId"] if group else target

    if not chat_id:
        raise RuntimeError("No target group/chatId configured")

    result = waha_post("/api/sendText", {
        "session": SESSION["name"],
        "chatId": chat_id,
        "text": text,
    })

    publish(client, topic("result", "last"), {
        "ok": True,
        "time": now_iso(),
        "target": group["key"] if group else mask_chat_id(chat_id),
        "subject": group["subject"] if group else "",
        "text": text,
        "waha": result,
    })


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


def rebuild_forward_subscriptions(client: mqtt.Client) -> None:
    for pattern in CONFIG.get("forward_topics", []) or []:
        if pattern and not pattern.startswith(MQTT_BASE_TOPIC + "/"):
            client.subscribe(pattern)
            log(f"Subscribed forward topic: {pattern}")


# ---------------------------------------------------------------------------
# MQTT command handlers
# ---------------------------------------------------------------------------

def handle_config_default_group(client: mqtt.Client, payload: str) -> None:
    group = resolve_group(payload)
    if not group:
        raise RuntimeError(f"Unknown default group: {payload}")

    CONFIG["default_group"] = group["key"]
    save_config(CONFIG)
    publish_state(client)


def handle_bot_enabled(client: mqtt.Client, payload: str) -> None:
    CONFIG.setdefault("bot", {})["enabled"] = as_bool(payload)
    save_config(CONFIG)
    publish_state(client)


def handle_bot_wake_word(client: mqtt.Client, payload: str) -> None:
    wake_word = payload.strip()
    if not wake_word:
        raise RuntimeError("wake_word must not be empty")

    CONFIG.setdefault("bot", {})["wake_word"] = wake_word
    save_config(CONFIG)
    publish_state(client)


def handle_bot_listen_group(client: mqtt.Client, payload: str) -> None:
    # This is the requested feature: the WhatsApp group the bot listens to is
    # selected via MQTT. Accepted values are aliases like g001, exact chatIds,
    # or group subjects if they are unique.
    group = resolve_group(payload)
    if not group:
        raise RuntimeError(f"Unknown bot listen group: {payload}")

    CONFIG.setdefault("bot", {})["listen_group"] = group["key"]
    save_config(CONFIG)
    publish_state(client)


def handle_forward_topics(client: mqtt.Client, payload: str) -> None:
    data = json.loads(payload)
    if not isinstance(data, list):
        raise RuntimeError("forward_topics payload must be a JSON list")

    CONFIG["forward_topics"] = [str(x) for x in data]
    save_config(CONFIG)
    publish_state(client)
    rebuild_forward_subscriptions(client)


def handle_templates(client: mqtt.Client, payload: str) -> None:
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise RuntimeError("templates payload must be a JSON object")

    CONFIG["templates"] = {str(k): str(v) for k, v in data.items()}
    save_config(CONFIG)
    publish_state(client)


def handle_manual_send(client: mqtt.Client, payload: str) -> None:
    try:
        data = json.loads(payload)
        if isinstance(data, dict):
            text = str(data.get("text") or data.get("message") or "")
            target = str(data.get("group") or data.get("target") or data.get("chatId") or "")
        else:
            text = payload
            target = ""
    except Exception:
        text = payload
        target = ""

    if not text.strip():
        raise RuntimeError("Message text is empty")

    send_text(client, target, text.strip())


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
    """Normalize WAHA webhook payloads.

    WAHA payloads vary slightly between engines and versions. This normalizer
    extracts only the fields the bot needs: chatId, text, sender and fromMe.
    """
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

    sender = (
        payload.get("participant")
        or payload.get("author")
        or get_nested(payload, "key", "participant")
        or payload.get("from")
        or ""
    )

    from_me = bool(payload.get("fromMe") or get_nested(payload, "key", "fromMe"))

    return {
        "chatId": str(chat_id),
        "text": str(text),
        "sender": str(sender),
        "fromMe": from_me,
        "session": str(data.get("session") or payload.get("session") or SESSION.get("name", "")),
    }


def bot_help_text() -> str:
    wake_word = CONFIG.get("bot", {}).get("wake_word", "Mobert")
    return (
        f"{wake_word} Befehle:\n"
        f"{wake_word} ? - Hilfe anzeigen\n"
        f"{wake_word} status - WAHA/MQTT/Bot-Status anzeigen\n"
        f"{wake_word} gruppen - bekannte Gruppen anzeigen\n"
        f"{wake_word} ziel - Standard-Zielgruppe anzeigen\n"
        f"{wake_word} ziel g001 - Standard-Zielgruppe setzen\n"
        f"{wake_word} lauschen - Bot-Lauschen-Gruppe anzeigen\n"
        f"{wake_word} lauschen g001 - Bot-Lauschen-Gruppe setzen\n"
        f"{wake_word} topics - weitergeleitete MQTT-Topics anzeigen\n"
        f"{wake_word} test - Testantwort senden"
    )


def bot_status_text() -> str:
    bot = CONFIG.get("bot", {})
    default_group = CONFIG.get("default_group", "")
    listen_group = bot.get("listen_group", "")
    return (
        "Mobert Status:\n"
        f"Session: {SESSION.get('name', '')} ({SESSION.get('status', '')})\n"
        f"Konto: {SESSION.get('account', '')}\n"
        f"Bot aktiv: {as_bool(bot.get('enabled', True))}\n"
        f"Startwort: {bot.get('wake_word', 'Mobert')}\n"
        f"Lauschen: {listen_group} {group_subject(listen_group)}\n"
        f"Standardziel: {default_group} {group_subject(default_group)}\n"
        f"Gruppen: {len(GROUPS_BY_KEY)}\n"
        f"Forward Topics: {len(CONFIG.get('forward_topics', []) or [])}"
    )


def bot_groups_text() -> str:
    lines = ["Mobert Gruppen:"]
    default_group = CONFIG.get("default_group", "")
    listen_group = CONFIG.get("bot", {}).get("listen_group", "")

    for key, group in GROUPS_BY_KEY.items():
        flags = []
        if key == default_group:
            flags.append("Ziel")
        if key == listen_group:
            flags.append("Lauschen")
        suffix = f" [{' / '.join(flags)}]" if flags else ""
        lines.append(f"{key}: {group['subject']}{suffix}")

    return "\n".join(lines)


def bot_topics_text() -> str:
    topics = CONFIG.get("forward_topics", []) or []
    if not topics:
        return "Mobert Topics:\nKeine Weiterleitungs-Topics konfiguriert."
    return "Mobert Topics:\n" + "\n".join(f"- {item}" for item in topics)


def handle_bot_command(command_text: str) -> str:
    parts = command_text.strip().split()
    command = parts[0].lower() if parts else "?"
    argument = " ".join(parts[1:]).strip()

    if command in {"?", "help", "hilfe"}:
        return bot_help_text()

    if command == "status":
        return bot_status_text()

    if command in {"gruppen", "groups"}:
        return bot_groups_text()

    if command == "topics":
        return bot_topics_text()

    if command == "test":
        return "Mobert Testantwort: Verbindung funktioniert."

    if command == "ziel":
        if argument:
            handle_config_default_group(MQTT_CLIENT, argument)  # type: ignore[arg-type]
            value = CONFIG.get("default_group", "")
            return f"Standard-Zielgruppe gesetzt: {value} {group_subject(value)}"
        value = CONFIG.get("default_group", "")
        return f"Standard-Zielgruppe: {value} {group_subject(value)}"

    if command == "lauschen":
        if argument:
            handle_bot_listen_group(MQTT_CLIENT, argument)  # type: ignore[arg-type]
            value = CONFIG.get("bot", {}).get("listen_group", "")
            return f"Bot-Lauschen-Gruppe gesetzt: {value} {group_subject(value)}"
        value = CONFIG.get("bot", {}).get("listen_group", "")
        return f"Bot-Lauschen-Gruppe: {value} {group_subject(value)}"

    return "Unbekannter Mobert-Befehl. Schreibe: Mobert ?"


def handle_webhook(data: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    client = MQTT_CLIENT
    if client is None:
        return 503, {"ok": False, "error": "MQTT client not ready"}

    message = extract_webhook_message(data)

    if message["fromMe"]:
        return 200, {"ok": True, "ignored": "fromMe"}

    bot = CONFIG.get("bot", {})
    if not as_bool(bot.get("enabled", True)):
        return 200, {"ok": True, "ignored": "bot disabled"}

    listen_key = str(bot.get("listen_group", "") or "")
    listen_chat_id = group_chat_id(listen_key)

    # Safety check: Mobert reacts only in the configured listening group.
    if not listen_chat_id or message["chatId"] != listen_chat_id:
        return 200, {"ok": True, "ignored": "chat not configured listen group"}

    wake_word = str(bot.get("wake_word") or "Mobert")
    text = message["text"].strip()

    if not text.lower().startswith(wake_word.lower()):
        return 200, {"ok": True, "ignored": "wake word not found"}

    command_text = text[len(wake_word):].strip() or "?"
    response_text = handle_bot_command(command_text)

    waha_post("/api/sendText", {
        "session": SESSION.get("name") or message.get("session"),
        "chatId": message["chatId"],
        "text": response_text,
    })

    publish(client, topic("bot", "last_command"), {
        "time": now_iso(),
        "chat": listen_key,
        "sender": mask_chat_id(message.get("sender", "")),
        "command": command_text,
    })
    publish(client, topic("bot", "last_response"), response_text)
    publish(client, topic("bot", "last_sender"), mask_chat_id(message.get("sender", "")))
    publish(client, topic("bot", "last_chat"), listen_key)

    return 200, {"ok": True, "response": response_text}


class WebhookHandler(BaseHTTPRequestHandler):
    def _json_response(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if urlparse(self.path).path == "/health":
            self._json_response(200, {"ok": True, "time": now_iso()})
        else:
            self._json_response(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
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

    client.subscribe(topic("cmd", "#"))
    client.subscribe(topic("config", "default_group", "set"))
    client.subscribe(topic("config", "forward_topics", "set"))
    client.subscribe(topic("config", "templates", "set"))
    client.subscribe(topic("config", "bot", "enabled", "set"))
    client.subscribe(topic("config", "bot", "wake_word", "set"))
    client.subscribe(topic("config", "bot", "listen_group", "set"))
    client.subscribe(topic("send"))

    rebuild_forward_subscriptions(client)
    client.publish(topic("status", "online"), "true", qos=0, retain=True)

    try:
        publish_state(client)
    except Exception as exc:
        publish(client, topic("status", "error"), str(exc))
        log(f"State publish failed: {exc}")


def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    mqtt_topic = msg.topic
    payload = read_text_payload(msg.payload)

    try:
        if mqtt_topic == topic("cmd", "refresh"):
            publish_state(client)

        elif mqtt_topic == topic("config", "default_group", "set"):
            handle_config_default_group(client, payload)

        elif mqtt_topic == topic("config", "bot", "enabled", "set"):
            handle_bot_enabled(client, payload)

        elif mqtt_topic == topic("config", "bot", "wake_word", "set"):
            handle_bot_wake_word(client, payload)

        elif mqtt_topic == topic("config", "bot", "listen_group", "set"):
            handle_bot_listen_group(client, payload)

        elif mqtt_topic == topic("config", "forward_topics", "set"):
            handle_forward_topics(client, payload)

        elif mqtt_topic == topic("config", "templates", "set"):
            handle_templates(client, payload)

        elif mqtt_topic == topic("send"):
            handle_manual_send(client, payload)

        elif not mqtt_topic.startswith(MQTT_BASE_TOPIC + "/"):
            handle_forwarded_mqtt(client, mqtt_topic, payload)

    except Exception as exc:
        publish_error(client, mqtt_topic, exc)


def refresh_loop(client: mqtt.Client) -> None:
    while RUNNING:
        time.sleep(REFRESH_SECONDS)
        try:
            publish_state(client)
        except Exception as exc:
            publish(client, topic("status", "error"), str(exc))
            log(f"Refresh failed: {exc}")


def handle_signal(signum: int, frame: Any) -> None:
    global RUNNING
    RUNNING = False


def main() -> None:
    global MQTT_CLIENT

    if not WAHA_API_KEY:
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

    log(f"Starting controller with MQTT_BASE_TOPIC={MQTT_BASE_TOPIC}")
    log(f"WAHA_URL={WAHA_URL}")

    webhook_thread = threading.Thread(target=start_webhook_server, daemon=True)
    webhook_thread.start()

    client.connect(MQTT_HOST, MQTT_PORT, 60)

    thread = threading.Thread(target=refresh_loop, args=(client,), daemon=True)
    thread.start()

    client.loop_forever()


if __name__ == "__main__":
    main()
