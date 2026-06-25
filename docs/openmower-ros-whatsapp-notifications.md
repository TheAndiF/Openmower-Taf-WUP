# Openmower-Taf-WUP - ROS MQTT WhatsApp notifications

## Zweck

Diese Erweiterung nutzt ROS/OpenMower-MQTT-Daten, um Mobert standardmaessig ueber WhatsApp melden zu lassen, wenn der Rasenmaeher losfaehrt, das Laden beendet wurde oder ein Fehler/Emergency erkannt wird.

## Aktivierte XML-Flows

| Flow | Eingang | Bedingung | Ausgabe |
|---|---|---|---|
| `openmower_drives_off_notification` | `mqtt_watchdog` | `robot_state/json`: `current_state` wechselt von `IDLE` auf einen anderen Zustand. | WhatsApp an `default_group`. |
| `openmower_charging_finished_notification` | `mqtt_watchdog` | `robot_state/json`: `is_charging` wechselt von `true` auf `false`. | WhatsApp an `default_group`. |
| `openmower_error_notification` | `mqtt_watchdog` | `robot_state/json`: `emergency` wechselt auf `true`. | WhatsApp an `default_group`. |
| `openmower_wifi_cache` | `mqtt_watchdog` | `sensors/om_system_wifi_signal_percent/data` mit Payload. | Interner Cache fuer Status und Meldungen. |

## Statusanzeige

`Mobert: Status` enthaelt nun:

- Zeitstempel
- MQTT-Verbindung
- WLAN-Staerke in Prozent
- OpenMower-Zustand
- aktuelle Flaeche oder Dock-Text
- Ladezustand: laden / nicht laden / unbekannt
- Fehler/Emergency: ja / nein / unbekannt

## Topic-Prefix

Die XML verwendet standardmaessig unpraefixte OpenMower-Themen:

```text
robot_state/json
sensors/om_system_wifi_signal_percent/data
```

Wenn OpenMower mit `OM_MQTT_TOPIC_PREFIX=openmower` laeuft, muessen die XML-Topics entsprechend auf diese Werte geaendert werden:

```text
openmower/robot_state/json
openmower/sensors/om_system_wifi_signal_percent/data
```

## Reload

Nach Aenderungen an `/data/bot_commands.xml`:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/renew/json -m '{}'
```
