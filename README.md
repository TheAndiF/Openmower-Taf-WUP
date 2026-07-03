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
- Send standard WhatsApp notifications from ROS MQTT for undocking, docking-to-idle charging start, emergency/error and GPS loss while mowing.
- Format `Mobert: Status` for WhatsApp with readable local time, bold labels, current mowing area, live mowing progress, Auto Mow suspension status, compact GPS readiness and GPS position placeholders.
- Provide a WhatsApp GPS submenu with detailed `gps_state` diagnostics.
- Send automatic status pushes every configurable X minutes.
- Optionally append the current status below normal WhatsApp command confirmations.
- Automatically start or restart the selected WAHA session when it is stopped, failed or stuck on `STARTING`.
- Store runtime configuration persistently under `/data/config.json`.
- Build multi-platform Docker images through GitHub Actions.


## XML-driven flow architecture

`/data/bot_commands.xml` now supports the flow format:

```text
mobertBotConfig
├── modules
│   ├── whatsappModule whatsapp
│   ├── inputModule    whatsapp_watchdog -> moduleRef whatsapp
│   ├── inputModule    mqtt_watchdog
│   ├── outputModule   whatsapp_output -> moduleRef whatsapp
│   └── outputModule   mqtt_output
└── flows
    └── flow
        ├── head
        └── step
            ├── input
            ├── processing
            └── output
```

There is only one central watchdog/output instance per module type. The XML does not start separate listeners for every command. Instead, the active `flow` entries decide which WhatsApp commands and MQTT topics are relevant.

Legacy `mobertCommands` XML files are still accepted, but the supplied example file uses the new flow structure. Existing MQTT configuration topics remain available. For example, `messenger/bot/set/session/json` can still set `enabled`, `wake_word` and `listen_group_alias`; `messenger/waha/set/session/json` can also set `session`. These values override the XML defaults until the controller is restarted or the persistent config is changed.

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
| `openmower_undocking_notification` | `robot_state/json`, `current_state` changes to `UNDOCKING` and `emergency=0` | Message that the mower is undocking. |
| `openmower_docking_idle_notification` | `robot_state/json`, `current_state` changes from `DOCKING` to `IDLE` | Message that docking has completed and charging begins. |
| `openmower_error_notification` | `robot_state/json`, `emergency` changes to `1` | Warning message for OpenMower error/emergency. |
| Internal GPS loss notification | `gps_state/json` becomes not drive-ready while cached `robot_state.current_state=MOWING` | Warning that GPS was lost during mowing. |
| `openmower_wifi_cache` | `sensors/om_system_wifi_signal_percent/data` | Updates the internal WLAN percentage cache for status and notifications. |

The supplied XML follows the unprefixed OpenMower topics observed on the target system. Command outputs use `action` and `timetable/set/suspension/json`, while status inputs use `robot_state/json` and `sensors/om_system_wifi_signal_percent/data`. The controller status cache still accepts matching status topics with or without a prefix, so `Mobert: Status` remains robust after future prefix changes.

`Mobert: Status` uses the latest cached ROS MQTT values and sends a WhatsApp-friendly reply. The timestamp is shown in the configured local time zone (`STATUS_TIMEZONE`, default `Europe/Berlin`). Field labels are bold, the title is visually separated by a line, and Emergency/Fehler are always visible. If OpenMower is actively mowing an area, the status shows only the human-readable area name and the calculated mowing progress, for example `Fläche: Plantage` and `Bearbeitung: 72.0 %`.

Example:

```text
*Mobert Status*
──────────────

*Zeit:* 25.06.2026 23:24:37
*Status:* MOWING
*Fläche:* Plantage
*Bearbeitung:* 72.0 %
*Akku:* 53 % (lädt)
*WLAN:* 64 %
*Emergency:* nein
*Fehler:* keiner
*MQTT:* verbunden
```

`Mobert: ?` is generated from the loaded XML command model. The active `/data/bot_commands.xml` is therefore the source of truth for the help reply: disabling, adding or changing command flows in the XML changes the help output after reload. Starting with v1.4, the generated help is also rebuilt on every MQTT XML replacement/reload and published as retained MQTT snapshots on `messenger/bot/help/text` and `messenger/bot/help/json`.

For `Mobert: Status`, the controller waits briefly for fresh `robot_state` and WLAN MQTT samples before replying. If no fresh sample arrives within the timeout, it replies with the latest cached values.

### MowArea command

`Mobert: MowArea` returns only the current mowing-area values, without explanatory text:

```text
Fläche: Plantage
Flächenreihenfolge: 50
Bearbeitung: 72.0 %
Pfad: 1
Pfadindex: 8261
```

The normal `Mobert: Status` reply intentionally stays shorter and only shows area name plus progress. The command also disables the append-status suffix for status-like replies so the status output is not duplicated.


## WhatsApp status, GPS placeholders and automation

`Mobert: Status` now includes the compact GPS and automation fields requested for the WUP project:

```text
*Mobert Status*
──────────────

*Zeit:* 26.06.2026 14:30:00
*Status:* MOWING
*Fläche:* Plantage
*Bearbeitung:* 72.0 %
*Akku:* 68 %
*Auto Mow:* aktiviert
*WLAN:* 64 %
*GPS:* fahrbereit
*Position:* Platzhalter: GPS-Koordinaten aus zukuenftiger MQTT-Schnittstelle
*Karte:* https://www.google.com/maps?q={latitude},{longitude}
*Emergency:* nein
*Fehler:* keiner
*MQTT:* verbunden
```

When GPS is not ready to drive, the compact status intentionally stays short:

```text
*GPS:* nicht fahrbereit
*Position:* nicht verfügbar
```

No `Karte: nicht verfügbar` line is emitted.  Detailed GPS quality, RTK status, satellites, accuracy, timeout and reasons are available in the GPS submenu instead.

### GPS submenu

The following WhatsApp commands show detailed values from `gps_state/json`:

```text
Mobert: GPS
Mobert: GPS Status
```

The submenu shows `available`, `quality`, `visible`, `used`, `rtk_state`, `gps_drive_ready`, `position_accuracy_m`, `max_position_accuracy_m`, `orientation_valid`, `recent_absolute_pose`, `gps_timeout`, `age_ms` and the available GPS drive reason/block reason.

### GPS position placeholders

The package does not convert OpenMower local map `pose.x/y` into Google Maps coordinates.  If `robot_state/json` contains `world_pose.valid=true`, `world_pose.coordinate_system=WGS84`, `world_pose.latitude` and `world_pose.longitude`, those real world coordinates are used for the status and Google Maps link.  If no real WGS84 coordinates are available, the status uses the configured placeholder from `/data/config.json`:

```json
"gps": {
  "position_placeholder": {
    "enabled": true,
    "latitude": "{latitude}",
    "longitude": "{longitude}",
    "position_text": "Platzhalter: GPS-Koordinaten aus zukuenftiger MQTT-Schnittstelle",
    "map_url": "https://www.google.com/maps?q={latitude},{longitude}"
  }
}
```

When real coordinates become available, prefer `world_pose.latitude` and `world_pose.longitude` with `coordinate_system=WGS84`.  The prepared cache topics `gps/position/json` and `gps_position/json` are still accepted for future interfaces that publish direct `latitude`/`longitude` fields.

### Automatic status push

Use WhatsApp commands:

```text
Mobert: Status alle 15
Mobert: Status automatisch
Mobert: Status automatisch aus
```

The default minimum interval is 5 minutes.  The target group is the current listening/default group unless `status_push.target_group` is configured explicitly.

### Status after command confirmations

Use WhatsApp commands:

```text
Mobert: Status nach Befehl ein
Mobert: Status nach Befehl
Mobert: Status nach Befehl aus
```

When enabled, normal command confirmations such as Start, Pause, Stop, Zeitplan ein/aus and MQTT confirmations append the current compact status.  The append is not applied to status, help, group, target or GPS submenu replies.

### One-time notifications

The active XML sends one-time WhatsApp notifications for:

| Event | Trigger |
|---|---|
| Undocking | `robot_state/json` changes to `current_state=UNDOCKING` |
| Docking complete / charging starts | `robot_state/json` changes from `current_state=DOCKING` to `current_state=IDLE` |
| Emergency | `emergency` changes to `1` |
| GPS loss while mowing | `gps_state/json` becomes not drive-ready while the cached `robot_state.current_state` is `MOWING` |

There is intentionally no separate notification for entering `DOCKING`.

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
│   │   ├── last_error
│   │   └── repair/
│   │       ├── json
│   │       ├── enabled
│   │       ├── action
│   │       ├── reason
│   │       └── error
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

## WAHA session self-healing

The controller checks the configured WAHA session before sending and in a background watchdog.  It can call WAHA `/start` when the session is `STOPPED`, and `/restart` when the session is `FAILED`, `CRASHED` or stuck on `STARTING` longer than the configured timeout.

Important limits prevent restart loops:

```env
WAHA_AUTO_REPAIR_SESSION=true
WAHA_STARTING_TIMEOUT_SECONDS=90
WAHA_REPAIR_COOLDOWN_SECONDS=300
WAHA_MAX_RESTARTS_PER_HOUR=3
WAHA_SEND_READY_WAIT_SECONDS=30
WAHA_WATCHDOG_SECONDS=60
```

The repair status is retained under `messenger/waha/session/repair/#`.  If the state is `SCAN_QR_CODE`, the controller reports that manual WAHA pairing is required; it does not loop restarts.

See also `docs/waha-session-auto-repair.md`.

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

Enable or disable WAHA live, or select the WAHA session:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/set/session/json -m '{"enabled":true}'
mosquitto_pub -h Mosquitto -t messenger/waha/set/session/json -m '{"enabled":false}'
mosquitto_pub -h Mosquitto -t messenger/waha/set/session/json -m '{"session":"Wasserleberweg"}'
```

Enable or disable WAHA persistently, or persist the selected WAHA session:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/set/persistent/json -m '{"enabled":true}'
mosquitto_pub -h Mosquitto -t messenger/waha/set/persistent/json -m '{"enabled":false}'
mosquitto_pub -h Mosquitto -t messenger/waha/set/persistent/json -m '{"session":"Wasserleberweg"}'
```

Refresh the WAHA group list:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/groups/set/renew/json -m '{}'
```

Manually start or restart the configured WAHA session through the controller:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/action -m 'messenger:waha/session/start'
mosquitto_pub -h Mosquitto -t messenger/waha/action -m 'messenger:waha/session/restart'
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
Mobert: Stop
```

The command XML is stored at `/data/bot_commands.xml`. The package now also includes `controller_data/bot_commands.xml` with the current Flow XML so an existing Docker volume can be initialized directly from the delivered package. If the file does not exist, the controller creates it from the packaged `bot_commands.example.xml`.

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

Read the generated help from MQTT:

```bash
mosquitto_sub -h Mosquitto -C 1 -v -t messenger/bot/help/text
mosquitto_sub -h Mosquitto -C 1 -v -t messenger/bot/help/json
```

The help is rebuilt from the active XML whenever `messenger/bot/commands/set/xml` or `messenger/bot/commands/set/renew/json` is processed.

## Kompakter ROS-MQTT-Status in WhatsApp

`Mobert: Status` nutzt die zuletzt empfangenen ROS-MQTT-Werte aus `robot_state/json`, `map/mowing_progress/status/json` und `sensors/om_system_wifi_signal_percent/data`. Der interne Cache erkennt zusätzlich semantisch passende Topics mit anderem oder ohne Prefix, z. B. `robot_state/json`, damit die Statusausgabe nicht wegen eines reinen Topic-Prefixes auf `unbekannt` fällt. Die Ausgabe ist bewusst kurz gehalten:

```text
Mobert Status

Zeit: 2026-06-25T18:55:00.503419+00:00
Status: IDLE
Fläche: Plantage
Bearbeitung: 72.0 %
Akku: 95 % (lädt)
Auto Mow: aktiviert
WLAN: 82 %
MQTT: verbunden
```

Die Zeile `Fehler:` erscheint nur, wenn `robot_state.emergency` aktiv ist. Der Dock-Zustand wird nicht mehr separat ausgegeben; Laden wird über `robot_state.is_charging` als Teil der Akku-Zeile dargestellt.

Die Standard-XML enthält aktivierte MQTT-Watchdog-Flows für:

- OpenMower fährt los: Wechsel von `current_state=IDLE` zu einem anderen Zustand.
- Laden beendet: Wechsel von `is_charging=true` zu `false`.
- Fehler erkannt: Wechsel von `emergency` nicht aktiv zu aktiv.

`Mobert: Stop` ist als Gegenstück zu `Mobert: Start` aktiviert und sendet auf `action` den Payload `mower_logic:mowing/abort_mowing`. Die früher diskutierten Synonyme `Home`, `Dock` und `Docking` sind nicht enthalten, solange kein gesicherter Docking-MQTT-Befehl vorliegt.

Ausgehende WhatsApp-Nachrichten werden durch `send_text()` als `direction: out` im Ringspeicher `messenger/waha/messages/history/json` dokumentiert. WAHA-Webhook-Echos von selbst gesendeten Nachrichten werden ebenfalls als ausgehend erkannt und per Message-ID dedupliziert, soweit WAHA eine ID liefert.


## Paketbereinigung

Das Auslieferungspaket enthaelt keine lokalen Git-Daten und keine Python-Cachedateien. `controller_data/bot_commands.xml` bleibt bewusst enthalten, weil diese Datei die aktive Flow-XML fuer die Bridge bereitstellt. Weitere Details stehen in `docs/package-hygiene.md`.


## v1.2 status cache correction for WiFi data topics

The target installation publishes OpenMower status on the unprefixed MQTT topics `robot_state/json` and `sensors/om_system_wifi_signal_percent/data`. The delivered XML keeps these unprefixed topics for status and WLAN watchdog flows.

`Mobert: Status` no longer depends only on XML flow subscriptions. The controller subscribes independently to these concrete status cache topics on startup, including the Web-App style progress payload:

- `robot_state/json`
- `map/mowing_progress/status/json`
- `mowing_progress/status/json`
- `sensors/om_system_wifi_signal_percent/data`
- `openmower/robot_state/json`
- `openmower/map/mowing_progress/status/json`
- `openmower/mowing_progress/status/json`
- `openmower/sensors/om_system_wifi_signal_percent/data`

The WLAN subscription intentionally targets only `/data`. OpenMower also publishes a binary sibling such as `sensors/om_system_wifi_signal_percent/bson`; wildcard subscriptions like `sensors/om_system_wifi_signal_percent/#` can cache binary data and produce unreadable WLAN output. The controller additionally validates WLAN payloads as numbers before updating the cache.

Custom filters can be provided with `OPENMOWER_STATUS_CACHE_TOPICS` as a comma-separated list. For WiFi, always use the concrete `/data` topic. After deployment, verify the active mounted XML inside the container. If it still starts with `<mobertCommands version="0.1">`, replace `/opt/stacks/whatsapp/controller_data/bot_commands.xml` with the file from this package and recreate the controller container.
