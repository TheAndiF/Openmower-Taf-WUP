import json
import os
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import paho.mqtt.client as mqtt
import requests


MQTT_HOST = os.getenv("MQTT_HOST", "Mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_BASE_TOPIC = os.getenv("MQTT_BASE_TOPIC", "waha").strip("/")
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")

WAHA_URL = os.getenv("WAHA_URL", "http://waha:3000").rstrip("/")
WAHA_API_KEY = os.getenv("WAHA_API_KEY", "")

REFRESH_SECONDS = int(os.getenv("CONTROLLER_REFRESH_SECONDS", "60"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
CONFIG_FILE = DATA_DIR / "config.json"

RUNNING = True
CONFIG: Dict[str, Any] = {}
SESSIONS: List[Dict[str, str]] = []
ACTIVE_SESSION: Dict[str, str] = {}
GROUPS_BY_KEY: Dict[str, Dict[str, str]] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    print(f"[waha-mqtt-controller] {message}", flush=True)


def base_topic(*parts: str) -> str:
    cleaned = [MQTT_BASE_TOPIC]
    cleaned.extend(part.strip("/") for part in parts if part)
    return "/".join(cleaned)


def load_config() -> Dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    default = {
        "default_group": "",
        "forward_topics": [],
        "templates": {},
        "group_aliases": {},
    }

    if not CONFIG_FILE.exists():
        save_config(default)
        return default

    try:
        loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"Could not read config, using defaults: {exc}")
        return default

    if not isinstance(loaded, dict):
        return default

    for key, value in default.items():
        loaded.setdefault(key, value)

    return loaded


def save_config(config: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def publish(client: mqtt.Client, topic: str, payload: Any, retain: bool = True) -> None:
    if isinstance(payload, (dict, list)):
        value = json.dumps(payload, ensure_ascii=False)
    elif payload is None:
        value = ""
    else:
        value = str(payload)

    client.publish(topic, value, qos=0, retain=retain)


def publish_error(client: mqtt.Client, source_topic: str, exc: Exception) -> None:
    payload = {
        "ok": False,
        "time": now_iso(),
        "topic": source_topic,
        "error": str(exc),
    }
    publish(client, base_topic("error", "last"), payload, retain=True)
    log(f"Error handling {source_topic}: {exc}")


def waha_headers(json_content: bool = False) -> Dict[str, str]:
    headers = {"X-Api-Key": WAHA_API_KEY}
    if json_content:
        headers["Content-Type"] = "application/json"
    return headers


def waha_get(path: str) -> Any:
    response = requests.get(f"{WAHA_URL}{path}", headers=waha_headers(), timeout=25)
    response.raise_for_status()
    return response.json()


def waha_post(path: str, payload: Dict[str, Any]) -> Any:
    response = requests.post(
        f"{WAHA_URL}{path}",
        headers=waha_headers(json_content=True),
        json=payload,
        timeout=30,
    )

    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text}

    if response.status_code >= 400:
        raise RuntimeError(f"WAHA HTTP {response.status_code}: {body}")

    return body


def unwrap_objects(data: Any, preferred_keys: List[str]) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if isinstance(data, dict):
        for key in preferred_keys:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                return unwrap_objects(value, preferred_keys)

        mapped: List[Dict[str, Any]] = []
        for map_key, value in data.items():
            if isinstance(value, dict):
                item = dict(value)
                item["__map_key"] = str(map_key)
                mapped.append(item)
        if mapped:
            return mapped

        return [data]

    return []


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


def next_group_key(existing_values: List[str]) -> str:
    used_numbers = []
    for value in existing_values:
        if value.startswith("g") and value[1:].isdigit():
            used_numbers.append(int(value[1:]))
    next_number = 1
    while next_number in used_numbers:
        next_number += 1
    return f"g{next_number:03d}"


def get_group_key(chat_id: str) -> str:
    aliases = CONFIG.setdefault("group_aliases", {})
    if chat_id in aliases:
        return str(aliases[chat_id])

    key = next_group_key(list(aliases.values()))
    aliases[chat_id] = key
    save_config(CONFIG)
    return key


def fetch_sessions() -> List[Dict[str, str]]:
    raw = waha_get("/api/sessions")
    items = unwrap_objects(raw, ["sessions", "data", "result", "items"])
    sessions = []

    for item in items:
        me = item.get("me") or item.get("account") or ""
        if isinstance(me, dict):
            me = me.get("id") or me.get("pushName") or json.dumps(me, ensure_ascii=False)

        name = str(item.get("name") or item.get("session") or item.get("id") or "")
        if not name:
            continue

        sessions.append({
            "name": name,
            "status": str(item.get("status") or item.get("state") or ""),
            "account": str(me),
            "account_masked": mask_chat_id(str(me)) if me else "",
        })

    return sessions


def select_active_session(sessions: List[Dict[str, str]]) -> Dict[str, str]:
    for session in sessions:
        if session.get("status", "").upper() == "WORKING":
            return session
    if sessions:
        return sessions[0]
    return {"name": "", "status": "NOT_FOUND", "account": "", "account_masked": ""}


def fetch_groups(session_name: str) -> Dict[str, Dict[str, str]]:
    if not session_name:
        return {}

    raw = waha_get(f"/api/{session_name}/groups?limit=500&offset=0")
    items = unwrap_objects(raw, ["data", "groups", "result", "items"])
    groups: Dict[str, Dict[str, str]] = {}

    for item in items:
        metadata = item.get("groupMetadata") or item.get("GroupMetadata") or item.get("metadata") or {}
        chat_id = (
            id_to_text(metadata.get("id"))
            or id_to_text(metadata.get("jid"))
            or id_to_text(metadata.get("chatId"))
            or id_to_text(metadata.get("groupId"))
            or id_to_text(item.get("id"))
            or id_to_text(item.get("jid"))
            or id_to_text(item.get("chatId"))
            or id_to_text(item.get("groupId"))
            or str(item.get("__map_key") or "")
        )

        subject = (
            metadata.get("subject")
            or metadata.get("name")
            or metadata.get("title")
            or item.get("subject")
            or item.get("name")
            or item.get("title")
            or "(kein Subject gefunden)"
        )

        if chat_id and "@" not in chat_id and chat_id.isdigit():
            chat_id = f"{chat_id}@g.us"

        if not chat_id:
            continue

        key = get_group_key(chat_id)
        groups[key] = {
            "key": key,
            "chatId": chat_id,
            "chatId_masked": mask_chat_id(chat_id),
            "subject": str(subject),
        }

    return dict(sorted(groups.items(), key=lambda pair: pair[1]["subject"].lower()))


def refresh_state(client: mqtt.Client) -> None:
    global SESSIONS, ACTIVE_SESSION, GROUPS_BY_KEY

    SESSIONS = fetch_sessions()
    ACTIVE_SESSION = select_active_session(SESSIONS)
    GROUPS_BY_KEY = fetch_groups(ACTIVE_SESSION.get("name", ""))

    default_group_key = str(CONFIG.get("default_group") or "")
    default_group = GROUPS_BY_KEY.get(default_group_key, {})

    publish(client, base_topic("status", "online"), "true")
    publish(client, base_topic("status", "last_update"), now_iso())
    publish(client, base_topic("status", "error"), "")

    publish(client, base_topic("sessions", "list"), [
        {
            "name": session["name"],
            "status": session["status"],
            "accountMasked": session["account_masked"],
        }
        for session in SESSIONS
    ])

    for index, session in enumerate(SESSIONS, start=1):
        key = f"s{index:03d}"
        publish(client, base_topic("sessions", key, "name"), session["name"])
        publish(client, base_topic("sessions", key, "status"), session["status"])
        publish(client, base_topic("sessions", key, "account_masked"), session["account_masked"])
        publish(client, base_topic("sessions", key, "active"), "true" if session["name"] == ACTIVE_SESSION.get("name") else "false")

    publish(client, base_topic("session", "name"), ACTIVE_SESSION.get("name", ""))
    publish(client, base_topic("session", "status"), ACTIVE_SESSION.get("status", ""))
    publish(client, base_topic("session", "account_masked"), ACTIVE_SESSION.get("account_masked", ""))

    groups_list = []
    for key, group in GROUPS_BY_KEY.items():
        selected = key == default_group_key
        publish(client, base_topic("groups", key, "subject"), group["subject"])
        publish(client, base_topic("groups", key, "chatId_masked"), group["chatId_masked"])
        publish(client, base_topic("groups", key, "selected"), "true" if selected else "false")
        groups_list.append({
            "key": key,
            "subject": group["subject"],
            "chatIdMasked": group["chatId_masked"],
            "selected": selected,
        })

    publish(client, base_topic("groups", "list"), groups_list)
    publish(client, base_topic("config", "default_group", "value"), default_group_key)
    publish(client, base_topic("config", "default_group", "subject"), default_group.get("subject", ""))
    publish(client, base_topic("config", "forward_topics", "value"), CONFIG.get("forward_topics", []))
    publish(client, base_topic("config", "templates", "value"), CONFIG.get("templates", {}))

    log(f"Published state: sessions={len(SESSIONS)} active={ACTIVE_SESSION.get('name')} groups={len(GROUPS_BY_KEY)}")


def resolve_group(value: str) -> Optional[Dict[str, str]]:
    wanted = value.strip()
    if not wanted:
        wanted = str(CONFIG.get("default_group") or "")

    if wanted in GROUPS_BY_KEY:
        return GROUPS_BY_KEY[wanted]

    for group in GROUPS_BY_KEY.values():
        if wanted == group["chatId"]:
            return group
        if wanted.lower() == group["subject"].lower():
            return group

    return None


def send_text(client: mqtt.Client, text: str, target: str = "") -> None:
    if not text.strip():
        raise RuntimeError("Message text is empty")

    if not ACTIVE_SESSION.get("name"):
        raise RuntimeError("No active WAHA session available")

    group = resolve_group(target)
    chat_id = group["chatId"] if group else target.strip()

    if not chat_id:
        raise RuntimeError("No target configured. Set waha/config/default_group/set first.")

    result = waha_post("/api/sendText", {
        "session": ACTIVE_SESSION["name"],
        "chatId": chat_id,
        "text": text.strip(),
    })

    publish(client, base_topic("result", "last"), {
        "ok": True,
        "time": now_iso(),
        "target": group["key"] if group else mask_chat_id(chat_id),
        "subject": group["subject"] if group else "",
        "text": text.strip(),
        "waha": result,
    })


def format_message(source_topic: str, raw_payload: str) -> str:
    templates = CONFIG.get("templates", {}) or {}
    selected_template = templates.get(source_topic)

    if not selected_template:
        for pattern, template in templates.items():
            if mqtt.topic_matches_sub(pattern, source_topic):
                selected_template = str(template)
                break

    if not selected_template:
        return f"{source_topic}: {raw_payload}"

    values: Dict[str, Any] = {"topic": source_topic, "payload": raw_payload}

    try:
        parsed = json.loads(raw_payload)
        if isinstance(parsed, dict):
            values.update(parsed)
    except Exception:
        pass

    try:
        return selected_template.format(**values)
    except Exception as exc:
        log(f"Template error for {source_topic}: {exc}")
        return f"{source_topic}: {raw_payload}"


def rebuild_forward_subscriptions(client: mqtt.Client) -> None:
    for pattern in CONFIG.get("forward_topics", []) or []:
        pattern = str(pattern).strip()
        if not pattern or pattern.startswith(MQTT_BASE_TOPIC + "/"):
            continue
        client.subscribe(pattern)
        log(f"Subscribed forwarding topic: {pattern}")


def set_default_group(client: mqtt.Client, payload: str) -> None:
    group = resolve_group(payload)
    if not group:
        raise RuntimeError(f"Unknown group: {payload}")
    CONFIG["default_group"] = group["key"]
    save_config(CONFIG)
    refresh_state(client)


def set_forward_topics(client: mqtt.Client, payload: str) -> None:
    values = json.loads(payload)
    if not isinstance(values, list):
        raise RuntimeError("forward_topics must be a JSON list")
    CONFIG["forward_topics"] = [str(value).strip() for value in values if str(value).strip()]
    save_config(CONFIG)
    rebuild_forward_subscriptions(client)
    refresh_state(client)


def set_templates(client: mqtt.Client, payload: str) -> None:
    values = json.loads(payload)
    if not isinstance(values, dict):
        raise RuntimeError("templates must be a JSON object")
    CONFIG["templates"] = {str(key): str(value) for key, value in values.items()}
    save_config(CONFIG)
    refresh_state(client)


def handle_send(client: mqtt.Client, payload: str) -> None:
    try:
        parsed = json.loads(payload)
        if isinstance(parsed, dict):
            text = str(parsed.get("text") or parsed.get("message") or "")
            target = str(parsed.get("group") or parsed.get("target") or parsed.get("chatId") or "")
        else:
            text = payload
            target = ""
    except Exception:
        text = payload
        target = ""

    send_text(client, text=text, target=target)


def handle_forwarded_topic(client: mqtt.Client, source_topic: str, payload: str) -> None:
    for pattern in CONFIG.get("forward_topics", []) or []:
        if mqtt.topic_matches_sub(str(pattern), source_topic):
            text = format_message(source_topic, payload)
            send_text(client, text=text, target="")
            return


def on_connect(client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
    log(f"Connected to MQTT {MQTT_HOST}:{MQTT_PORT}, reason={reason_code}")

    client.subscribe(base_topic("cmd", "#"))
    client.subscribe(base_topic("config", "default_group", "set"))
    client.subscribe(base_topic("config", "forward_topics", "set"))
    client.subscribe(base_topic("config", "templates", "set"))
    client.subscribe(base_topic("send"))
    rebuild_forward_subscriptions(client)

    publish(client, base_topic("status", "online"), "true")

    try:
        refresh_state(client)
    except Exception as exc:
        publish(client, base_topic("status", "error"), str(exc))
        log(f"Initial refresh failed: {exc}")


def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    source_topic = msg.topic
    payload = msg.payload.decode("utf-8", errors="replace").strip()

    try:
        if source_topic == base_topic("cmd", "refresh"):
            refresh_state(client)
        elif source_topic == base_topic("config", "default_group", "set"):
            set_default_group(client, payload)
        elif source_topic == base_topic("config", "forward_topics", "set"):
            set_forward_topics(client, payload)
        elif source_topic == base_topic("config", "templates", "set"):
            set_templates(client, payload)
        elif source_topic == base_topic("send"):
            handle_send(client, payload)
        elif not source_topic.startswith(MQTT_BASE_TOPIC + "/"):
            handle_forwarded_topic(client, source_topic, payload)
    except Exception as exc:
        publish_error(client, source_topic, exc)


def refresh_loop(client: mqtt.Client) -> None:
    while RUNNING:
        time.sleep(REFRESH_SECONDS)
        if not RUNNING:
            break
        try:
            refresh_state(client)
        except Exception as exc:
            publish(client, base_topic("status", "error"), str(exc))
            log(f"Refresh failed: {exc}")


def handle_signal(signum: int, frame: Any) -> None:
    global RUNNING
    RUNNING = False


def main() -> None:
    global CONFIG

    if not WAHA_API_KEY:
        raise RuntimeError("WAHA_API_KEY is missing")

    CONFIG = load_config()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        client = mqtt.Client()

    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD or None)

    client.will_set(base_topic("status", "online"), "false", qos=0, retain=True)
    client.on_connect = on_connect
    client.on_message = on_message

    log(f"Starting with MQTT_BASE_TOPIC={MQTT_BASE_TOPIC}")
    log(f"WAHA_URL={WAHA_URL}")

    client.connect(MQTT_HOST, MQTT_PORT, 60)

    thread = threading.Thread(target=refresh_loop, args=(client,), daemon=True)
    thread.start()

    client.loop_forever()


if __name__ == "__main__":
    main()
