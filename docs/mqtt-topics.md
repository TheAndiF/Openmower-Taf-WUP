# MQTT Topics

Default base topic:

```text
messenger
```

The public namespace is provider-aware. The current provider is WAHA:

```text
messenger/status/#       general Messenger status
messenger/waha/#         WAHA-/WhatsApp-specific data and commands
messenger/bot/#          Mobert bot, provider-neutral
```

## Complete topic tree

```text
messenger/
├── status/
│   ├── json
│   ├── online
│   ├── text
│   ├── description
│   ├── provider
│   ├── protocol
│   ├── WAHA_QR_Code_Data
│   ├── WAHA_QR_Code_Required
│   ├── WAHA_QR_Code_Available
│   ├── WAHA_QR_Code_Text
│   ├── WAHA_QR_Code_Session
│   ├── WAHA_QR_Code_Status
│   └── WAHA_QR_Code_Error
│
├── waha/
│   ├── json
│   ├── enabled
│   ├── text
│   ├── QR_Code_Data
│   ├── QR_Code_Required
│   ├── QR_Code_Available
│   ├── QR_Code_Text
│   ├── QR_Code_Session
│   ├── QR_Code_Status
│   ├── QR_Code_Error
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
│   │   ├── qr/
│   │   │   ├── raw
│   │   │   ├── json
│   │   │   ├── required
│   │   │   ├── available
│   │   │   ├── session
│   │   │   ├── status
│   │   │   ├── text
│   │   │   ├── error
│   │   │   └── last_update
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

## Conventions

```text
*/json                         main JSON snapshot
*/set/renew/json               refresh current data from the backend
*/set/json                     set provider-specific state
*/set/session/json             set live/session state
*/set/persistent/json          persist a setting
*/validation/json              response for set/renew operations
```

JSON snapshots use a `d` wrapper.

## General status

```text
messenger/status/json
messenger/status/online
messenger/status/text
messenger/status/description
messenger/status/provider
messenger/status/protocol
```

`messenger/status/description` is a retained, human-readable, non-secret deployment hint. It includes the internal WAHA API URL, the configured dashboard URL and the host-side `.env` path where dashboard credentials are stored. It never publishes `WAHA_DASHBOARD_PASSWORD` or `WAHA_API_KEY`.

Example:

```json
{
  "d": {
    "online": true,
    "text": "waha WORKING: Mobert lauscht in g014 (OpenMower).",
    "provider": "waha",
    "protocol": "whatsapp",
    "description": {
      "waha_api_url": "http://waha:3000",
      "waha_dashboard_url": "http://<openmower-ip>:9629/dashboard",
      "credentials_file": "/opt/stacks/whatsapp/.env",
      "dashboard_password_variable": "WAHA_DASHBOARD_PASSWORD",
      "api_key_variable": "WAHA_API_KEY",
      "security_note": "Secrets are not published to MQTT. Read them on the host from the .env file."
    }
  }
}
```


## WAHA provider

```text
messenger/waha/json
messenger/waha/enabled
messenger/waha/text
messenger/waha/set/session/json
messenger/waha/set/persistent/json
messenger/waha/validation/json
```

`messenger/waha/json` is the retained provider snapshot. `enabled` controls whether the controller actively uses WAHA. When disabled, the controller does not query WAHA, does not refresh groups, does not send messages and the Bot listener cannot become active.

Enable WAHA live:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/set/session/json -m '{"enabled":true}'
mosquitto_pub -h Mosquitto -t messenger/waha/set/session/json -m '{"session":"Wasserleberweg"}'
```

Disable WAHA persistently:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/set/persistent/json -m '{"enabled":false}'
mosquitto_pub -h Mosquitto -t messenger/waha/set/persistent/json -m '{"session":"Wasserleberweg"}'
```

Validation is published to:

```text
messenger/waha/validation/json
```


## WAHA QR code data

The controller publishes the WAHA WhatsApp pairing QR raw value to MQTT while the selected session is waiting for a scan. The requested compact topics are:

```text
messenger/status/WAHA_QR_Code_Data
messenger/waha/QR_Code_Data
```

Additional metadata is published as:

```text
messenger/status/WAHA_QR_Code_Required
messenger/status/WAHA_QR_Code_Available
messenger/status/WAHA_QR_Code_Text
messenger/status/WAHA_QR_Code_Session
messenger/status/WAHA_QR_Code_Status
messenger/status/WAHA_QR_Code_Error

messenger/waha/QR_Code_Required
messenger/waha/QR_Code_Available
messenger/waha/QR_Code_Text
messenger/waha/QR_Code_Session
messenger/waha/QR_Code_Status
messenger/waha/QR_Code_Error

messenger/waha/session/qr/raw
messenger/waha/session/qr/json
messenger/waha/session/qr/required
messenger/waha/session/qr/available
messenger/waha/session/qr/session
messenger/waha/session/qr/status
messenger/waha/session/qr/text
messenger/waha/session/qr/error
messenger/waha/session/qr/last_update
```

Values:

| Situation | `WAHA_QR_Code_Data` / `QR_Code_Data` | Required | Available | Text |
|---|---|---:|---:|---|
| WAHA status is `SCAN_QR_CODE` or `QR` and a QR value is available | raw QR pairing value | `true` | `true` | `QR-Code zum Koppeln erforderlich` |
| WAHA requires a QR code but the value is not available yet | empty | `true` | `false` | `QR-Code erforderlich, aber noch nicht verfügbar` |
| WAHA is connected or no QR is needed | empty | `false` | `false` | `Kein QR-Code erforderlich` |
| QR publishing is disabled | empty | `false` | `false` | `QR-MQTT-Ausgabe deaktiviert` |

Security note: active raw QR values are not retained by default. Empty retained values are published when no QR is needed so old values are cleared from the broker.

Render the raw value as a QR code:

```bash
mosquitto_sub -h Mosquitto -t 'messenger/waha/QR_Code_Data' | while IFS= read -r QR; do
  clear
  if [ -n "$QR" ]; then
    qrencode -t ANSIUTF8 "$QR"
  else
    echo "Kein QR-Code erforderlich."
  fi
done
```

Configuration:

```env
WAHA_QR_MQTT_ENABLED=true
WAHA_QR_RAW_RETAIN=false
WAHA_QR_REFRESH_SECONDS=20
```

## WAHA session

```text
messenger/waha/session/json
```

Contains the WAHA/WhatsApp session status. Mirror topics exist for MQTT Explorer:

```text
messenger/waha/session/status
messenger/waha/session/text
messenger/waha/session/ready
messenger/waha/session/can_send
messenger/waha/session/can_read_groups
messenger/waha/session/last_error
messenger/waha/session/repair/json
messenger/waha/session/repair/enabled
messenger/waha/session/repair/action
messenger/waha/session/repair/reason
messenger/waha/session/repair/error
```

The `repair/#` topics show whether the controller had to start or restart the configured WAHA session.

## Groups

```text
messenger/waha/groups/json
```

Contains the full group snapshot, including the group list and default target group.

Refresh groups:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/groups/set/renew/json -m '{}'
```

Set default group:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/groups/set/json -m '{"default_group_alias":"g014"}'
```

Validation is published to:

```text
messenger/waha/groups/validation/json
```

## Messages and history

```text
messenger/waha/messages/json
```

Retained snapshot of the last messages in both directions. Default history limit: `10`.

```text
messenger/waha/messages/in/json
```

Live event for each incoming WhatsApp message. Not retained.

```text
messenger/waha/messages/out/set/json
```

Send a message.

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/messages/out/set/json -m '{"request_id":"req-1","target":{"alias":"g014"},"text":"Testnachricht"}'
```

Configure message history live:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/messages/history/set/session/json -m '{"enabled":true,"limit":10}'
```

Configure message history persistently:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/messages/history/set/persistent/json -m '{"enabled":true,"limit":20}'
```

## Bot listener

```text
messenger/bot/listener/json
```

Contains whether Mobert is really listening, the wake word, the provider and the selected listening group.

Mirror topics:

```text
messenger/bot/listener/listening
messenger/bot/listener/wake_word
messenger/bot/listener/text
messenger/bot/listener/provider
messenger/bot/listener/group/alias
messenger/bot/listener/group/name
```

Configure Mobert live:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/set/session/json -m '{"enabled":true,"wake_word":"Mobert","listen_group_alias":"g014"}'
```

Configure Mobert persistently:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/set/persistent/json -m '{"enabled":true,"wake_word":"Mobert","listen_group_alias":"g014"}'
```

## Bot commands XML

The command file is loaded from `/data/bot_commands.xml` and exposed as:

```text
messenger/bot/commands/xml
messenger/bot/commands/json
messenger/bot/commands/count
messenger/bot/commands/version
messenger/bot/commands/source
messenger/bot/commands/validation/json
```

Reload XML commands:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/renew/json -m '{}'
```

The WhatsApp command syntax is intentionally colon-based:

```text
Mobert: Status
Mobert: ?
```

## Mobert XML flow engine

`messenger/bot/commands/#` exposes the loaded XML and the parsed flow command list. The controller accepts both the legacy `<mobertCommands>` format and the new `<mobertBotConfig>` flow format. The new format defines central modules and flow steps:

```text
modules: whatsapp, whatsapp_watchdog, mqtt_watchdog, whatsapp_output, mqtt_output
whatsapp_watchdog and whatsapp_output reference the central whatsappModule.
flow step: input -> processing -> output
```

Replace the XML file at runtime:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/xml -f bot_commands.xml
```

Reload the XML from `/data/bot_commands.xml`:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/renew/json -m '{}'
```

Existing Bot settings remain available and override XML defaults where applicable:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/set/session/json -m '{"enabled":true,"wake_word":"Mobert","listen_group_alias":"g014"}'
mosquitto_pub -h Mosquitto -t messenger/bot/set/persistent/json -m '{"enabled":true,"wake_word":"Mobert","listen_group_alias":"g014"}'
```

MQTT confirmation steps are published while pending under:

```text
messenger/bot/confirmations/pending/json
```


## ROS MQTT inputs used by Mobert

The default flow XML subscribes to ROS/OpenMower MQTT topics through the central `mqtt_watchdog` input module.

| Topic | Used for |
|---|---|
| `robot_state/json` | OpenMower state, active/checkpoint area IDs, path, charging flag, emergency flag and Auto Mow values. |
| `map/mowing_progress/status/json` | Live mowing progress: `current_area_id`, `areas[current_area_id].percent`, state, path and path index. |
| `sensors/om_system_wifi_signal_percent/data` | WLAN strength in percent for status and WhatsApp notifications. |

Enabled default flows:

| Flow | Trigger condition | Output |
|---|---|---|
| `openmower_drives_off_notification` | `current_state` changes from `IDLE` to another state and `emergency` is not `true`. | WhatsApp notification that the mower drives off. |
| `openmower_charging_finished_notification` | `is_charging` changes from `true` to `false`. | WhatsApp notification that charging has finished. |
| `openmower_error_notification` | `emergency` changes to `true`. | WhatsApp warning for an OpenMower error/emergency. |
| `openmower_wifi_cache` | WLAN payload is received. | Internal cache update for status output. |

The `Mobert: Status` reply contains a timestamp, MQTT connection state, WLAN strength, OpenMower state, current mowing area, mowing progress, Auto Mow state, charging text and error/emergency status.

## ROS-MQTT-Statusquellen für Mobert

Mobert wertet folgende ROS-MQTT-Topics für Status und automatische WhatsApp-Meldungen aus:

| Topic/Filter | Zweck |
|---|---|
| `robot_state/json` | OpenMower Statusmeldung ohne Prefix |
| `openmower/robot_state/json` | OpenMower Statusmeldung mit `openmower/` Prefix, wird ebenfalls akzeptiert |
| `sensors/om_system_wifi_signal_percent/data` | WLAN-Signalstaerke ohne Prefix; nur dieses Text-/Zahlen-Topic fuer WLAN verwenden |
| `openmower/sensors/om_system_wifi_signal_percent/data` | WLAN-Signalstaerke mit `openmower/` Prefix, wird ebenfalls akzeptiert |
| `area_queue/json`, `mow_area/json`, `mow_area/status/json`, `mowing_area/json`, `mowing/area_queue/json` | MowArea-Cache fuer Flächenname, `mowing_order` und Pfadpunkte ohne Prefix |
| `map/mowing_progress/status/json`, `mowing_progress/status/json` | Live-Fortschritt aus der Web-App-Quelle ohne Prefix |
| `openmower/area_queue/json`, `openmower/mow_area/json`, `openmower/mow_area/status/json`, `openmower/mowing_area/json`, `openmower/mowing/area_queue/json` | MowArea-Cache mit `openmower/` Prefix |
| `openmower/map/mowing_progress/status/json`, `openmower/mowing_progress/status/json` | Live-Fortschritt aus der Web-App-Quelle mit `openmower/` Prefix |
| `sensors/om_system_wifi_signal_percent/bson` | Binaeres Geschwistertopic; wird fuer den WLAN-Cache bewusst ignoriert |

Wichtige Felder aus `robot_state`:

| Feld | Verwendung |
|---|---|
| `current_state` | Statuszeile und Losfahr-Erkennung |
| `current_area_id` | bevorzugte aktuell aktive Mähfläche fuer Status und MowArea |
| `checkpoint_area_id` | letzter gespeicherter Checkpoint-Flächenbezug, Fallback falls keine aktive Fläche gemeldet wird |
| `current_area` | numerischer Fallback fuer Flächenanzeige |
| `current_path` / `current_path_index` | Pfad und Pfadindex fuer MowArea, falls der Progress-Payload keinen Pfad liefert |
| `battery_percentage` | Akkustand, Werte 0..1 werden automatisch in Prozent umgerechnet |
| `is_charging` | Ladezustand in der Akku-Zeile, z. B. `95 % (lädt)` |
| `AutoMow` / `AutoMowSuspension` | Auto-Mow-Anzeige: aktiviert, deaktiviert, ausgesetzt bis Datum oder ausgesetzt unendlich bei Jahr 9999 |
| `emergency` | Fehler-/Notfall-Erkennung |



## Status-Frische und Nachrichtenhistorie

`Mobert: Status` wartet kurz auf neue ROS-MQTT-Daten, bevor die WhatsApp-Antwort ausgegeben wird. Standardmäßig werden bis zu 3 Sekunden auf frische Werte aus `robot_state/json`, `map/mowing_progress/status/json` und `sensors/om_system_wifi_signal_percent/data` gewartet. Der interne Statuscache erkennt dieselben Quellen auch mit `openmower/` Prefix; fuer WLAN wird ausschliesslich das konkrete `/data` Topic akzeptiert. Der Timeout kann über die Umgebungsvariable `STATUS_FRESH_WAIT_SECONDS` angepasst werden.

Der kompakte Status enthält keine Dock-Zeile. `is_charging=1` wird in der Akku-Zeile als `(lädt)` angezeigt. Bei `is_charging=0` wird nur der Akkustand ausgegeben.

Ausgehende WhatsApp-Nachrichten, die die Bridge per WAHA sendet, werden im Ringspeicher unter `messenger/waha/messages/history/json` mit `direction: out` dokumentiert. Selbst gesendete WAHA-Webhooks werden zusätzlich unter `messenger/waha/messages/out/json` gespiegelt und anhand der Message-ID dedupliziert, sofern WAHA eine Message-ID liefert.

## Stop-Befehl

Der aktivierte Standardbefehl `Mobert: Stop` sendet den MQTT-Payload `mower_logic:mowing/abort_mowing` auf das Topic `action`. Die Befehle `Home`, `Dock` und `Docking` sind nicht in der Standard-XML enthalten, weil dafür noch kein gesicherter OpenMower-Docking-Payload hinterlegt ist.


## v1.3 Hinweis: Statusformat und Hilfe

`Mobert: Status` formatiert die Zeit lokal ueber `STATUS_TIMEZONE`, zeigt WhatsApp-fette Feldnamen und gibt Fläche sowie Bearbeitung als eigene Zeilen aus. Der Fortschritt kommt bevorzugt aus `map/mowing_progress/status/json -> areas[current_area_id].percent`; `Emergency` und `Fehler` werden immer ausgegeben.

`Mobert: ?` wird aus der aktiv geladenen XML-Konfiguration erzeugt. Die XML-Datei ist damit die Quelle der Wahrheit fuer die angezeigten Befehle.


## v1.4 Hilfe-Snapshots

Die aus `bot_commands.xml` erzeugte Hilfe wird retained auf MQTT veroeffentlicht:

```text
messenger/bot/help/text
messenger/bot/help/json
```

`messenger/bot/help/text` enthaelt die WhatsApp-fertige Hilfe. `messenger/bot/help/json` enthaelt Metadaten wie Quelle, Format, Wake Word und die aus der XML gemappten Eintraege.

Nach diesen Aktionen wird die Hilfe neu aufgebaut und erneut veroeffentlicht:

```text
messenger/bot/commands/set/xml
messenger/bot/commands/set/renew/json
```

## Mobert Status, GPS und Status Push

Ab der WUP-Erweiterung vom 2026-06-26 nutzt `Mobert: Status` zusätzliche OpenMower Statusquellen:

```text
robot_state/json
gps_state/json
gps/position/json
gps_position/json
sensors/om_system_wifi_signal_percent/data
```

Die entsprechenden `openmower/`-präfixierten Varianten sind ebenfalls in der Status-Cache-Konfiguration vorbereitet.

Neue Mobert-Zustände werden unter `messenger/bot/#` veröffentlicht:

```text
messenger/bot/status_push/json
messenger/bot/status_push/enabled
messenger/bot/status_push/interval_minutes
messenger/bot/status_push/target/alias
messenger/bot/status_push/text
messenger/bot/append_status_to_confirmations
```

Die GPS-Positionstopics sind Platzhalter für eine spätere MQTT-Schnittstellenversion. Sobald echte WGS84-Koordinaten als `latitude`/`longitude`, `lat`/`lon` oder `lat`/`lng` eintreffen, erzeugt der Status daraus automatisch den Google-Maps-Link.


## MowArea-Status

Der Controller cached Area-Queue-Payloads, die `area_queue` und `areas` enthalten. Fuer `Mobert: Status` wird daraus nur die kurze Anzeige erzeugt:

```text
*Fläche:* Plantage
*Bearbeitung:* 72.0 %
```

`Mobert: MowArea` liefert die aktuellen Detailwerte ohne Erklaertext:

```text
Fläche: Plantage
Flächenreihenfolge: 50
Bearbeitung: 72.0 %
Pfad: 1
Pfadindex: 8261
```

Die Berechnung nutzt `checkpoint_area_id` als aktive Flaeche, den Namen und `mowing_order` aus `area_queue` und den Fortschritt aus `current_path_index / Gesamtzahl der Punkte in areas[area_id].paths[].points`. `current_action_progress` wird nur als Fallback verwendet, wenn es plausibel groesser als 0 ist.

Wenn die Installation einen anderen MQTT-Topic fuer diese Area-Queue verwendet, muss er in `OPENMOWER_STATUS_CACHE_TOPICS` ergaenzt werden.
