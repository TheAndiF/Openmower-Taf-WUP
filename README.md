# Openmower-Taf-WUP

WAHA MQTT Controller for OpenMower / TAF / WhatsApp push notifications.

## Purpose

This repository contains a Docker-based MQTT controller for connecting an OpenMower Mosquitto broker with the WAHA WhatsApp HTTP API.

The controller exposes WAHA status, sessions and WhatsApp groups as retained MQTT topics. It can send WhatsApp messages through MQTT and includes the optional **Mobert** WhatsApp bot.

## Main features

- Publish WAHA server/session status to MQTT.
- Publish WhatsApp groups using masked aliases such as `g001`, `g002`, ...
- Read group names from `groupMetadata.subject`.
- Select a default WhatsApp target group through MQTT.
- Configure the Mobert listening group through MQTT.
- Reply to `Mobert ?` and other commands only in the configured listening group.
- Forward selected OpenMower MQTT topics to WhatsApp.
- Store runtime configuration persistently under `/data/config.json`.
- Build multi-platform Docker images through GitHub Actions.

## Important MQTT topics

```text
waha/status/online
waha/session/name
waha/session/status
waha/session/account
waha/groups/list
waha/groups/g001/subject
waha/groups/g001/chatId_masked
waha/groups/g001/selected
waha/groups/g001/bot_listen
waha/config/default_group/set
waha/config/default_group/value
waha/config/bot/enabled/set
waha/config/bot/wake_word/set
waha/config/bot/listen_group/set
waha/config/bot/listen_group/value
waha/send
waha/result/last
waha/error/last
waha/bot/last_command
waha/bot/last_response
```

## Mobert bot

The bot reacts only in the configured WhatsApp group.

Example setup:

```bash
mosquitto_pub -h Mosquitto -t waha/config/bot/listen_group/set -m g001
mosquitto_pub -h Mosquitto -t waha/config/bot/wake_word/set -m Mobert
mosquitto_pub -h Mosquitto -t waha/config/bot/enabled/set -m true
```

Then write this inside the selected WhatsApp group:

```text
Mobert ?
```

## Security

Do not commit real secrets.

Do not commit:

```text
.env
WAHA_API_KEY
WAHA_DASHBOARD_PASSWORD
waha_sessions/
controller_data/
config.json
```

Use `.env.example` and `bridge/config.example.json` for examples only.
