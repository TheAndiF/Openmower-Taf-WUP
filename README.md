# Openmower-Taf-WUP

MQTT Controller for OpenMower / TAF / WhatsApp push notifications.

## Purpose

This repository contains a Docker-based MQTT controller for connecting an OpenMower Mosquitto broker with a Messenger provider. The current provider is **WAHA** for WhatsApp, but the public MQTT namespace is now provider-aware and starts at `messenger/`.

WAHA-specific topics live below `messenger/waha/`. The optional **Mobert** bot lives below `messenger/bot/` so it can later be reused with another provider.

## Main features

- Publish general Messenger status to MQTT.
- Publish and control WAHA provider state below `messenger/waha/#`.
- Publish WhatsApp groups below `messenger/waha/groups/#` using aliases such as `g001`, `g002`, ...
- Select the default WhatsApp target group through MQTT.
- Send WhatsApp messages through MQTT.
- Enable or disable WAHA through MQTT, live or persistently.
- Store a configurable retained history of the last messages, default `10`.
- Load Mobert flow commands from `/data/bot_commands.xml` using the XML-driven module architecture.
- Publish the raw command/flow XML and parsed command JSON below `messenger/bot/commands/#`.
- Configure Mobert through OpenMower-like `set/session/json`, `set/persistent/json` and `validation/json` topics. These MQTT settings remain compatible and override the XML defaults at runtime.
- Use the WhatsApp watchdog module from the XML for the command syntax `Mobert: Befehl`.
- Send standard WhatsApp notifications from ROS MQTT for mower drive-off, charging finished and emergency/error events.
- Extend `Mobert: Status` with WLAN strength, mower area/dock/charging state, MQTT connection and timestamp.
- Store runtime configuration persistently under `/data/config.json`.
- Build multi-platform Docker images through GitHub Actions.


## XML-driven flow architecture

`/data/bot_commands.xml` now supports the flow format:

```text
mobertBotConfig
├── modules
│   ├── inputModule  whatsapp_watchdog
│   ├── inputModule  mqtt_watchdog
│   ├── outputModule whatsapp_output
│   └── outputModule mqtt_output
└── flows
    └── flow
        ├── head
        └── step
            ├── input
            ├── processing
            └── output
```

There is only one central watchdog/output instance per module type. The XML does not start separate listeners for every command. Instead, the active `flow` entries decide which WhatsApp commands and MQTT topics are relevant.

Legacy `mobertCommands` XML files are still accepted, but the supplied example file uses the new flow structure. Existing MQTT configuration topics remain available. For example, `messenger/bot/set/session/json` can still set `enabled`, `wake_word` and `listen_group_alias`; these values override the XML defaults until the controller is restarted or the persistent config is changed.

The bot XML itself can be replaced through MQTT:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/xml -f bot_commands.xml
```

Reload the current file from disk:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/renew/json -m '{}'
```


## ROS MQTT status and standard WhatsApp notifications

The supplied `bridge/bot_commands.example.xml` enables these ROS MQTT driven flows by default:

| Flow | ROS MQTT input | WhatsApp output |
|---|---|---|
| `openmower_drives_off_notification` | `robot_state/json`, `current_state` changes away from `IDLE` | Message that the mower is driving off, including timestamp, state, area/dock text, WLAN strength and MQTT connection. |
| `openmower_charging_finished_notification` | `robot_state/json`, `is_charging` changes from `true` to `false` | Message that charging has finished, including dock/charging text. |
| `openmower_error_notification` | `robot_state/json`, `emergency` changes to `true` | Warning message for OpenMower error/emergency. |
| `openmower_wifi_cache` | `sensors/om_system_wifi_signal_percent/data` | Updates the internal WLAN percentage cache for status and notifications. |

The XML assumes the ROS MQTT topics are published without an extra prefix, which matches the existing `action`, `robot_state/json` and `sensors/...` topic style used by this stack. If the ROS system uses `OM_MQTT_TOPIC_PREFIX=openmower`, update the XML topics to `openmower/robot_state/json` and `openmower/sensors/om_system_wifi_signal_percent/data`.

`Mobert: Status` uses the latest cached ROS MQTT values and reports:

- WLAN strength in percent
- current area, or dock status with charging/not charging when the mower is idle/docked
- MQTT connection state
- timestamp
- OpenMower state and emergency flag

## MQTT base topic

Default base topic:

```text
messenger
```

## Current MQTT topic tree

```text
messenger/
├── status/
│   ├── json
│   ├── online
│   ├── text
│   ├── description
│   ├── provider
│   └── protocol
│
├── waha/
│   ├── json
│   ├── enabled
│   ├── text
│   ├── set/
│   │   ├── session/
│   │   │   └── json
│   │   └── persistent/
│   │       └── json
│   ├── validation/
│   │   └── json
│   │
│   ├── session/
│   │   ├── json
│   │   ├── status
│   │   ├── text
│   │   ├── ready
│   │   ├── can_send
│   │   ├── can_read_groups
│   │   └── last_error
│   │
│   ├── groups/
│   │   ├── json
│   │   ├── count
│   │   ├── default/
│   │   │   ├── alias
│   │   │   └── name
│   │   ├── set/
│   │   │   ├── renew/
│   │   │   │   └── json
│   │   │   └── json
│   │   └── validation/
│   │       └── json
│   │
│   ├── contacts/
│   │   ├── json
│   │   ├── count
│   │   ├── status/
│   │   │   └── json
│   │   ├── set/
│   │   │   ├── renew/
│   │   │   │   └── json
│   │   │   └── json
│   │   └── validation/
│   │       └── json
│   │
│   ├── messages/
│   │   ├── json
│   │   ├── count
│   │   ├── history/
│   │   │   ├── enabled
│   │   │   ├── limit
│   │   │   ├── set/
│   │   │   │   ├── session/
│   │   │   │   │   └── json
│   │   │   │   └── persistent/
│   │   │   │       └── json
│   │   │   └── validation/
│   │   │       └── json
│   │   ├── in/
│   │   │   └── json
│   │   └── out/
│   │       ├── set/
│   │       │   └── json
│   │       └── validation/
│   │           └── json
│   │
│   ├── actions/
│   │   └── json
│   │
│   └── action
│
└── bot/
    ├── json
    ├── enabled
    ├── text
    ├── listener/
    │   ├── json
    │   ├── listening
    │   ├── wake_word
    │   ├── text
    │   ├── provider
    │   └── group/
    │       ├── alias
    │       └── name
    ├── commands/
    │   ├── json
    │   ├── xml
    │   ├── count
    │   ├── version
    │   ├── source
    │   ├── set/
    │   │   ├── xml
    │   │   └── renew/
    │   │       └── json
    │   └── validation/
    │       └── json
    ├── set/
    │   ├── session/
    │   │   └── json
    │   └── persistent/
    │       └── json
    ├── validation/
    │   └── json
    ├── confirmations/
    │   └── pending/
    │       └── json
    └── events/
        └── json
```

## Deployment description in MQTT

The controller publishes a retained, non-secret description under:

```text
messenger/status/description
```

The same data is included in `messenger/status/json` under `d.description`. It includes the internal WAHA API URL, the configured WAHA dashboard URL and the host-side `.env` location where dashboard credentials are stored. It never publishes `WAHA_DASHBOARD_PASSWORD` or `WAHA_API_KEY`.

For the default OpenMower stack the credential hint is:

```text
/opt/stacks/whatsapp/.env
```

## Common commands

Enable or disable WAHA live:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/set/session/json -m '{"enabled":true}'
mosquitto_pub -h Mosquitto -t messenger/waha/set/session/json -m '{"enabled":false}'
```

Enable or disable WAHA persistently:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/set/persistent/json -m '{"enabled":true}'
mosquitto_pub -h Mosquitto -t messenger/waha/set/persistent/json -m '{"enabled":false}'
```

Refresh the WAHA group list:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/groups/set/renew/json -m '{}'
```

Set the default WhatsApp target group:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/groups/set/json -m '{"default_group_alias":"g014"}'
```

Send a WhatsApp message:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/messages/out/set/json -m '{"target":{"type":"group","alias":"g014"},"text":"Testnachricht"}'
```

Configure the message history live:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/messages/history/set/session/json -m '{"enabled":true,"limit":10}'
```

Configure Mobert live:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/set/session/json -m '{"enabled":true,"wake_word":"Mobert","listen_group_alias":"g014"}'
```

Configure Mobert persistently:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/set/persistent/json -m '{"enabled":true,"wake_word":"Mobert","listen_group_alias":"g014"}'
```

Reload the XML command file:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/renew/json -m '{}'
```

## Mobert bot

Mobert reacts only in the configured listening group. The colon is required:

```text
Mobert: ?
Mobert: Status
Mobert: Gruppen
Mobert: Lauschen g014
Mobert: Start
Mobert: Pause
```

The command XML is stored at `/data/bot_commands.xml`. If the file does not exist, the controller creates it from the packaged `bot_commands.example.xml`.

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
bot_commands.xml with private data
```

Use `.env.example`, `bridge/config.example.json` and `bridge/bot_commands.example.xml` for examples only.


Replace the Mobert XML via MQTT:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/xml -f bot_commands.xml
```

Reload the Mobert XML from disk:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/renew/json -m '{}'
```
