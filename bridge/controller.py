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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import paho.mqtt.client as mqtt
import requests


# ---------------------------------------------------------------------------
# Environment configuration
# ---------------------------------------------------------------------------

MQTT_HOST = os.getenv("MQTT_HOST", "Mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_BASE_TOPIC = os.getenv("MQTT_BASE_TOPIC", "messenger").strip("/")

WAHA_URL = os.getenv("WAHA_URL", "http://waha:3000").rstrip("/")
WAHA_API_KEY = os.getenv("WAHA_API_KEY", "")
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
    history = normalized.setdefault("messages", {}).setdefault("history", {})
    history["enabled"] = as_bool(history.get("enabled", True))
    try:
        history["limit"] = max(0, min(100, int(history.get("limit", 10))))
    except Exception:
        history["limit"] = 10
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


def ensure_default_bot_commands_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if BOT_COMMANDS_FILE.exists():
        return
    if DEFAULT_BOT_COMMANDS_FILE.exists():
        BOT_COMMANDS_FILE.write_text(DEFAULT_BOT_COMMANDS_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        BOT_COMMANDS_FILE.write_text(default_bot_commands_xml(), encoding="utf-8")


def default_bot_commands_xml() -> str:
    return '<?xml version="1.0" encoding="UTF-8"?>\n<mobertBotConfig version="0.3" language="de">\n\n  <head>\n    <name>Mobert OpenMower Flow Configuration</name>\n    <description>XML-gesteuerte Flow-Konfiguration mit zentralen Watchdog- und Output-Modulen.</description>\n    <enabled>true</enabled>\n  </head>\n\n  <modules>\n\n    <inputModule id="whatsapp_watchdog">\n      <enabled>true</enabled>\n      <session>default</session>\n      <listenerGroup>g014</listenerGroup>\n\n      <wakeWord>\n        <text>Mobert</text>\n        <required>true</required>\n        <syntax>colon</syntax>\n        <caseSensitive>false</caseSensitive>\n      </wakeWord>\n    </inputModule>\n\n    <outputModule id="whatsapp_output">\n      <enabled>true</enabled>\n      <session>default</session>\n      <defaultGroup>g014</defaultGroup>\n    </outputModule>\n\n    <inputModule id="mqtt_watchdog">\n      <enabled>true</enabled>\n      <subscribeMode>enabled_flows</subscribeMode>\n    </inputModule>\n\n    <outputModule id="mqtt_output">\n      <enabled>true</enabled>\n    </outputModule>\n\n  </modules>\n\n  <flows>\n\n    <flow id="help">\n      <head>\n        <name>Help</name>\n        <description>Hilfe anzeigen.</description>\n        <enabled>true</enabled>\n      </head>\n\n      <step id="start">\n        <input module="whatsapp_watchdog" type="command">\n          <expect>\n            <command>?</command>\n          </expect>\n        </input>\n\n        <processing mode="local_reply">\n          <template>{help}</template>\n        </processing>\n\n        <output module="whatsapp_output" type="send">\n          <target>{replyTarget}</target>\n          <message>{processing.result}</message>\n        </output>\n      </step>\n    </flow>\n\n    <flow id="status">\n      <head>\n        <name>Status</name>\n        <description>Status von Mobert und OpenMower anzeigen.</description>\n        <enabled>true</enabled>\n      </head>\n\n      <step id="start">\n        <input module="whatsapp_watchdog" type="command">\n          <expect>\n            <command>Status</command>\n          </expect>\n        </input>\n\n        <processing mode="local_status">\n          <responseTemplate>status_short</responseTemplate>\n        </processing>\n\n        <output module="whatsapp_output" type="send">\n          <target>{replyTarget}</target>\n          <message>{processing.result}</message>\n        </output>\n      </step>\n    </flow>\n\n    <flow id="groups">\n      <head>\n        <name>Groups</name>\n        <description>Bekannte WhatsApp-Gruppen anzeigen.</description>\n        <enabled>true</enabled>\n      </head>\n\n      <step id="start">\n        <input module="whatsapp_watchdog" type="command">\n          <expect>\n            <command>Gruppen</command>\n          </expect>\n        </input>\n\n        <processing mode="local_groups" />\n\n        <output module="whatsapp_output" type="send">\n          <target>{replyTarget}</target>\n          <message>{processing.result}</message>\n        </output>\n      </step>\n    </flow>\n\n    <flow id="target">\n      <head>\n        <name>Target</name>\n        <description>Standard-Zielgruppe anzeigen.</description>\n        <enabled>true</enabled>\n      </head>\n\n      <step id="start">\n        <input module="whatsapp_watchdog" type="command">\n          <expect>\n            <command>Ziel</command>\n          </expect>\n        </input>\n\n        <processing mode="local_default_group" />\n\n        <output module="whatsapp_output" type="send">\n          <target>{replyTarget}</target>\n          <message>{processing.result}</message>\n        </output>\n      </step>\n    </flow>\n\n    <flow id="set_listener_group">\n      <head>\n        <name>Set listener group</name>\n        <description>Bot-Lauschgruppe setzen.</description>\n        <enabled>true</enabled>\n      </head>\n\n      <step id="start">\n        <input module="whatsapp_watchdog" type="command">\n          <expect>\n            <command>Lauschen {group}</command>\n          </expect>\n          <parameters>\n            <parameter name="group" type="string" required="true" />\n          </parameters>\n        </input>\n\n        <processing mode="set_module_property">\n          <moduleRef>whatsapp_watchdog</moduleRef>\n          <property>listenerGroup</property>\n          <value>{group}</value>\n          <persist>true</persist>\n        </processing>\n\n        <output module="whatsapp_output" type="send">\n          <target>{replyTarget}</target>\n          <message>{processing.result}</message>\n        </output>\n      </step>\n    </flow>\n\n    <flow id="start_mowing">\n      <head>\n        <name>Start mowing</name>\n        <description>Maehbetrieb starten.</description>\n        <enabled>true</enabled>\n      </head>\n\n      <step id="start">\n        <input module="whatsapp_watchdog" type="command">\n          <expect>\n            <command>Start</command>\n          </expect>\n        </input>\n\n        <processing mode="passthrough" />\n\n        <output module="mqtt_output" type="publish">\n          <topic>action</topic>\n          <qos>1</qos>\n          <retain>false</retain>\n          <payload>mower_logic:idle/start_mowing</payload>\n        </output>\n\n        <output module="whatsapp_output" type="send">\n          <target>{replyTarget}</target>\n          <message>Start-Befehl wurde an OpenMower gesendet.</message>\n        </output>\n      </step>\n    </flow>\n\n    <flow id="pause_mowing">\n      <head>\n        <name>Pause mowing</name>\n        <description>Laufenden Maehbetrieb pausieren.</description>\n        <enabled>true</enabled>\n      </head>\n\n      <step id="start">\n        <input module="whatsapp_watchdog" type="command">\n          <expect>\n            <command>Pause</command>\n          </expect>\n        </input>\n\n        <processing mode="passthrough" />\n\n        <output module="mqtt_output" type="publish">\n          <topic>action</topic>\n          <qos>1</qos>\n          <retain>false</retain>\n          <payload>mower_logic:mowing/pause</payload>\n        </output>\n\n        <output module="whatsapp_output" type="send">\n          <target>{replyTarget}</target>\n          <message>Pause-Befehl wurde an OpenMower gesendet.</message>\n        </output>\n      </step>\n    </flow>\n\n    <flow id="dock">\n      <head>\n        <name>Dock</name>\n        <description>Zur Dockingstation fahren.</description>\n        <enabled>false</enabled>\n      </head>\n\n      <step id="start">\n        <input module="whatsapp_watchdog" type="command">\n          <expect>\n            <command>Dock</command>\n          </expect>\n        </input>\n\n        <processing mode="passthrough" />\n\n        <output module="mqtt_output" type="publish">\n          <topic>action</topic>\n          <qos>1</qos>\n          <retain>false</retain>\n          <payload>TODO_OPENMOWER_DOCK_ACTION</payload>\n        </output>\n\n        <output module="whatsapp_output" type="send">\n          <target>{replyTarget}</target>\n          <message>Dock-Befehl wurde an OpenMower gesendet.</message>\n        </output>\n      </step>\n    </flow>\n\n    <flow id="start_area">\n      <head>\n        <name>Start area</name>\n        <description>Bestimmte Flaeche starten und MQTT-Bestaetigung abwarten.</description>\n        <enabled>false</enabled>\n      </head>\n\n      <step id="start">\n        <input module="whatsapp_watchdog" type="command">\n          <expect>\n            <command>Start {area}</command>\n          </expect>\n          <parameters>\n            <parameter name="area" type="integer" required="true" min="1" />\n          </parameters>\n        </input>\n\n        <processing mode="passthrough" />\n\n        <output module="mqtt_output" type="publish">\n          <topic>openmower/cmd/start_area</topic>\n          <qos>1</qos>\n          <retain>false</retain>\n          <payload>{"area":"{area}"}</payload>\n        </output>\n\n        <output module="whatsapp_output" type="send">\n          <target>{replyTarget}</target>\n          <message>Start fuer Flaeche {area} wurde an MQTT gesendet.</message>\n        </output>\n\n        <nextStep>wait_for_mqtt_confirmation</nextStep>\n      </step>\n\n      <step id="wait_for_mqtt_confirmation">\n        <input module="mqtt_watchdog" type="confirmation">\n          <timeoutSeconds>10</timeoutSeconds>\n          <expect>\n            <topic>openmower/cmd/start_area/result</topic>\n            <payloadNotEmpty>true</payloadNotEmpty>\n          </expect>\n        </input>\n\n        <processing mode="confirmation_result">\n          <successWhen>\n            <default>true</default>\n          </successWhen>\n          <errorWhen>\n            <payloadContains>error</payloadContains>\n          </errorWhen>\n        </processing>\n\n        <output module="whatsapp_output" type="send" result="success">\n          <target>{replyTarget}</target>\n          <message>Start fuer Flaeche {area} bestaetigt: {payload}</message>\n        </output>\n\n        <output module="whatsapp_output" type="send" result="timeout">\n          <target>{replyTarget}</target>\n          <message>Gesendet, aber keine MQTT-Bestaetigung erhalten.</message>\n        </output>\n\n        <output module="whatsapp_output" type="send" result="error">\n          <target>{replyTarget}</target>\n          <message>Start konnte nicht bestaetigt werden: {payload}</message>\n        </output>\n      </step>\n    </flow>\n\n    <flow id="schedule_on">\n      <head>\n        <name>Schedule on</name>\n        <description>Zeitplan aktivieren.</description>\n        <enabled>false</enabled>\n      </head>\n\n      <step id="start">\n        <input module="whatsapp_watchdog" type="command">\n          <expect>\n            <command>Zeitplan ein</command>\n          </expect>\n        </input>\n\n        <processing mode="passthrough" />\n\n        <output module="mqtt_output" type="publish">\n          <topic>timetable/set/suspension/json</topic>\n          <qos>1</qos>\n          <retain>false</retain>\n          <payload>{"AutoMowSuspension":0}</payload>\n        </output>\n\n        <output module="whatsapp_output" type="send">\n          <target>{replyTarget}</target>\n          <message>Zeitplan wurde aktiviert.</message>\n        </output>\n      </step>\n    </flow>\n\n    <flow id="schedule_off">\n      <head>\n        <name>Schedule off</name>\n        <description>Zeitplan deaktivieren.</description>\n        <enabled>false</enabled>\n      </head>\n\n      <step id="start">\n        <input module="whatsapp_watchdog" type="command">\n          <expect>\n            <command>Zeitplan aus</command>\n          </expect>\n        </input>\n\n        <processing mode="passthrough" />\n\n        <output module="mqtt_output" type="publish">\n          <topic>timetable/set/suspension/json</topic>\n          <qos>1</qos>\n          <retain>false</retain>\n          <payload>{"AutoMowSuspension":"9999-12-31T23:59:59Z"}</payload>\n        </output>\n\n        <output module="whatsapp_output" type="send">\n          <target>{replyTarget}</target>\n          <message>Zeitplan wurde deaktiviert.</message>\n        </output>\n      </step>\n    </flow>\n\n  </flows>\n\n</mobertBotConfig>\n'


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
            "parameters": parse_parameters(input_node),
        },
        "processing": processing,
        "outputs": [parse_output_node(out) for out in node.findall("output")],
        "next_step": xml_child_text(node, "nextStep"),
    }
    return step


def parse_modules(root: ET.Element) -> Dict[str, Dict[str, Any]]:
    modules: Dict[str, Dict[str, Any]] = {}
    for node in root.findall("modules/inputModule") + root.findall("modules/outputModule"):
        module_id = node.attrib.get("id", "").strip()
        if not module_id:
            continue
        settings: Dict[str, Any] = {
            "kind": node.tag,
            "enabled": xml_enabled(node, True),
            "session": xml_child_text(node, "session"),
            "listenerGroup": xml_child_text(node, "listenerGroup"),
            "defaultGroup": xml_child_text(node, "defaultGroup"),
            "subscribeMode": xml_child_text(node, "subscribeMode"),
        }
        wake = node.find("wakeWord")
        if wake is not None:
            settings["wakeWord"] = {
                "text": xml_child_text(wake, "text", "Mobert"),
                "required": as_bool(xml_child_text(wake, "required", "true")),
                "syntax": xml_child_text(wake, "syntax", "colon"),
                "caseSensitive": as_bool(xml_child_text(wake, "caseSensitive", "false")),
            }
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


def reload_bot_commands() -> None:
    global BOT_COMMANDS_XML, BOT_COMMANDS_META, BOT_COMMANDS, BOT_COMMANDS_VALIDATION, BOT_MODULES, BOT_FLOWS, BOT_CONFIG_ENABLED
    try:
        BOT_COMMANDS_XML, BOT_COMMANDS_META, BOT_COMMANDS, BOT_COMMANDS_VALIDATION, BOT_MODULES, BOT_FLOWS = load_bot_commands()
        BOT_CONFIG_ENABLED = as_bool(BOT_COMMANDS_META.get("enabled", True))
        log(f"Loaded {len(BOT_COMMANDS)} bot commands from {BOT_COMMANDS_FILE} ({BOT_COMMANDS_META.get('format', 'unknown')})")
    except Exception as exc:
        BOT_COMMANDS_VALIDATION = {"valid": False, "error": str(exc)}
        BOT_COMMANDS = []
        BOT_MODULES = {}
        BOT_FLOWS = {}
        BOT_COMMANDS_XML = ""
        BOT_COMMANDS_META = {"version": "", "source": str(BOT_COMMANDS_FILE)}
        BOT_CONFIG_ENABLED = False
        log(f"Could not load bot commands: {exc}")


def module_config(module_id: str) -> Dict[str, Any]:
    return BOT_MODULES.get(module_id, {})


def module_is_enabled(module_id: str) -> bool:
    if not BOT_MODULES:
        return True
    return as_bool(module_config(module_id).get("enabled", True))


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


def command_help_text() -> str:
    wake_word = effective_wake_word()
    enabled_commands = [cmd for cmd in BOT_COMMANDS if cmd.enabled]
    if not enabled_commands:
        return f"{wake_word} Befehle:\nKeine Befehle geladen."
    lines = [f"{wake_word} Befehle:"]
    for cmd in enabled_commands:
        example = cmd.example or f"{wake_word}: {cmd.trigger}"
        # Keep examples aligned with the currently active wake word even when
        # the XML provided a different default.
        if ":" in example:
            example = f"{wake_word}: {example.split(':', 1)[1].strip()}"
        lines.append(f"- {example} - {cmd.description}")
    return "\n".join(lines)


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
    publish(client, topic("waha", "messages", "json"), messages_payload())
    publish(client, topic("waha", "messages", "count"), len(list(MESSAGE_HISTORY)))
    history_config = message_history_config()
    publish(client, topic("waha", "messages", "history", "enabled"), str(as_bool(history_config.get("enabled", True))).lower())
    publish(client, topic("waha", "messages", "history", "limit"), int(history_config.get("limit", 10) or 0))


def add_message_history(client: mqtt.Client, entry: Dict[str, Any]) -> None:
    if not as_bool(message_history_config().get("enabled", True)):
        return
    MESSAGE_HISTORY.append(entry)
    publish_message_history(client)


def publish_bot_commands(client: mqtt.Client) -> None:
    publish(client, topic("bot", "commands", "json"), commands_payload())
    publish(client, topic("bot", "commands", "xml"), BOT_COMMANDS_XML)
    publish(client, topic("bot", "commands", "count"), len(BOT_COMMANDS))
    publish(client, topic("bot", "commands", "version"), BOT_COMMANDS_META.get("version", ""))
    publish(client, topic("bot", "commands", "source"), BOT_COMMANDS_META.get("source", str(BOT_COMMANDS_FILE)))
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
    if not accepted:
        raise RuntimeError("waha set payload requires enabled")
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
        accepted={"source": str(BOT_COMMANDS_FILE), "format": BOT_COMMANDS_META.get("format", "")},
        remarks=["Bot-XML gespeichert und neu geladen"],
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


def bot_status_text() -> str:
    default_group = effective_default_group()
    listen_group = effective_listener_group()
    return (
        "Mobert Status:\n"
        f"Session: {SESSION.get('name', '')} ({SESSION.get('status', '')})\n"
        f"Konto: {SESSION.get('account', '')}\n"
        f"Bot aktiv: {effective_bot_enabled()}\n"
        f"Startwort: {effective_wake_word()}\n"
        f"Lauschen: {listen_group} {group_subject(listen_group)}\n"
        f"Standardziel: {default_group} {group_subject(default_group)}\n"
        f"XML-Format: {BOT_COMMANDS_META.get('format', 'legacy')}\n"
        f"Gruppen: {len(GROUPS_BY_KEY)}\n"
        f"Befehle: {len(BOT_COMMANDS)}"
    )


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


def flow_step_matches_mqtt(step: Dict[str, Any], source_topic: str, payload: str) -> bool:
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
            context["processing.result"] = bot_status_text()
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
            if module_ref == "whatsapp_watchdog" and property_name == "listenerGroup":
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
    context.update({"topic": source_topic, "payload": payload, "mqttTopic": source_topic, "mqttPayload": payload, "result": result})
    if result != "timeout":
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
    # Confirmation steps have priority because they belong to a command that is already in progress.
    matching_pending: List[str] = []
    with PENDING_CONFIRMATIONS_LOCK:
        for item in PENDING_CONFIRMATIONS:
            if flow_step_matches_mqtt(item.get("step", {}), source_topic, payload):
                matching_pending.append(str(item.get("id")))
    for pending_id in matching_pending:
        handled = finish_pending_confirmation(pending_id, "success", source_topic, payload) or handled

    # MQTT watchdog start inputs can also create flows directly, e.g. automatic error or pause notifications.
    for flow in BOT_FLOWS.values():
        if not as_bool(flow.get("enabled", True)):
            continue
        for step in flow.get("steps", {}).values():
            input_cfg = step.get("input", {})
            if input_cfg.get("module") != "mqtt_watchdog" or input_cfg.get("type") == "confirmation":
                continue
            if flow_step_matches_mqtt(step, source_topic, payload):
                context = {
                    "topic": source_topic,
                    "payload": payload,
                    "mqttTopic": source_topic,
                    "mqttPayload": payload,
                    "replyTarget": effective_default_group(),
                    "request_id": f"mqtt:{flow.get('id', '')}",
                }
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
        "direction": "in",
        "message_id": message.get("message_id", ""),
        "chat": chat,
        "sender": {"name": "", "number_masked": mask_chat_id(message.get("sender", ""))},
        "text": message["text"],
        "bot": {"matched": False, "command": "", "accepted": False},
    }

    publish(client, topic("waha", "messages", "in", "json"), {"d": incoming_entry}, retain=False)

    if message["fromMe"]:
        add_message_history(client, incoming_entry)
        return 200, {"ok": True, "ignored": "fromMe"}

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

    # Optional internal forwarding configuration. This has no public legacy topic,
    # but can still be controlled via config.json mounted into /data.
    rebuild_forward_subscriptions(client)
    client.publish(topic("status", "online"), "true", qos=0, retain=True)

    publish_state(client)


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


def refresh_loop(client: mqtt.Client) -> None:
    while RUNNING:
        time.sleep(REFRESH_SECONDS)
        try:
            publish_state(client)
        except Exception as exc:
            publish_error(client, "refresh_loop", exc)


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
    log(f"Starting controller with MQTT_BASE_TOPIC={MQTT_BASE_TOPIC}")
    log(f"WAHA_URL={WAHA_URL}")
    log(f"Provider={PROVIDER_NAME}, protocol={PROTOCOL_NAME}")

    webhook_thread = threading.Thread(target=start_webhook_server, daemon=True)
    webhook_thread.start()
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    thread = threading.Thread(target=refresh_loop, args=(client,), daemon=True)
    thread.start()
    client.loop_forever()


if __name__ == "__main__":
    main()
