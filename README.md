# Openmower-Taf-WUP

WAHA MQTT Controller for OpenMower / TAF / WhatsApp push notifications.

## Purpose

This repository contains a Docker-based MQTT controller for connecting an OpenMower Mosquitto broker with the WAHA WhatsApp HTTP API.

The controller exposes WAHA sessions and WhatsApp groups as retained MQTT topics and allows WhatsApp messages to be sent and configured via MQTT.

## Main features

- Publish WAHA online/status information to MQTT
- Publish all WAHA sessions and the active session
- Publish WhatsApp groups with readable `groupMetadata.subject`
- Mask real WhatsApp chat IDs in MQTT manager topics
- Select a default WhatsApp group via MQTT
- Send manual WhatsApp messages via MQTT
- Configure which MQTT topics are forwarded to WhatsApp
- Configure message templates for forwarded topics
- Store runtime configuration persistently in `/data/config.json`
- Build multi-platform Docker images through GitHub Actions for `linux/amd64` and `linux/arm64`

## MQTT base topic

Default base topic:

```text
waha
```

## Important retained topics

```text
waha/status/online
waha/status/last_update
waha/status/error
waha/sessions/list
waha/session/name
waha/session/status
waha/session/account_masked
waha/groups/list
waha/groups/g001/subject
waha/groups/g001/chatId_masked
waha/groups/g001/selected
waha/config/default_group/value
waha/config/default_group/subject
waha/config/forward_topics/value
waha/config/templates/value
waha/result/last
waha/error/last
```

## Important command topics

Refresh WAHA status and group list:

```text
Topic:   waha/cmd/refresh
Payload: 1
```

Select the default group:

```text
Topic:   waha/config/default_group/set
Payload: g001
```

Send to the default group:

```text
Topic:   waha/send
Payload: Test message
```

Send to a selected group:

```text
Topic:   waha/send
Payload: {"group":"g001","text":"Test message"}
```

Configure forwarded MQTT topics:

```text
Topic:   waha/config/forward_topics/set
Payload: ["openmower/alerts/#", "openmower/status/error"]
```

Configure message templates:

```text
Topic:   waha/config/templates/set
Payload: {"openmower/alerts/#":"OpenMower Alarm: {payload}"}
```

## OpenMower / Dockge deployment

The controller is designed to run in the separate `whatsapp` stack next to the existing `waha` container and to join the external Docker network `openmower_default`.

Use `compose.example.yaml` as reference. Replace `DEIN_GITHUB_NAME` with your GitHub account or organization name.

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
