# Openmower-Taf-WUP - Status Topic Prefix Fix

Stand: 2026-06-25  
Status: v0.3

## Zweck

`Mobert: Status` zeigte Status, Fläche, Akku und WLAN als `unbekannt` bzw. `keine aktive Fläche`, wenn OpenMower seine ROS-MQTT-Daten mit dem Prefix `openmower/` veröffentlicht hat, die Flow-XML aber noch auf unpräfixte Topics wie `robot_state/json` lauschte.

## Geändertes Verhalten

Die aktive XML und die Beispiel-XML verwenden jetzt die OpenMower-Topics mit Prefix:

| Zweck | Topic |
|---|---|
| Start/Pause/Stop-Aktion | `openmower/action` |
| Zeitplan-Steuerung | `openmower/timetable/set/suspension/json` |
| OpenMower-Status | `openmower/robot_state/json` |
| WLAN-Signal | `openmower/sensors/om_system_wifi_signal_percent/data` |

Die vorhandenen Start-Fläche-Topics `openmower/cmd/start_area` und `openmower/cmd/start_area/result` waren bereits korrekt präfixiert und wurden nicht doppelt geändert.

## Controller-Fix

`bridge/controller.py` erkennt Status- und WLAN-Topics zusätzlich über ihr semantisches Suffix. Dadurch kann der interne Cache auch Daten von Topics wie `robot_state/json`, `openmower/robot_state/json` oder `<anderer-prefix>/robot_state/json` übernehmen.

## Prüfung nach dem Deployment

```bash
docker exec -it Mosquitto mosquitto_sub -h localhost -v \
  -t 'openmower/robot_state/json' \
  -t 'openmower/sensors/om_system_wifi_signal_percent/data'
```

Danach sollten in den Controller-Logs die abonnierten Flow-Topics sichtbar sein:

```bash
docker logs waha_mqtt_controller --tail=100 | grep 'Subscribed flow MQTT watchdog topic'
```

Erwartete Topics:

```text
openmower/robot_state/json
openmower/sensors/om_system_wifi_signal_percent/data
```

## Deployment-Hinweis

Nach Änderung von `bridge/controller.py` muss der Controller neu gebaut werden, wenn er aus dem Dockerfile gestartet wird:

```bash
cd /opt/stacks/whatsapp
docker compose build waha_mqtt_controller
docker compose up -d
```

Wenn bereits eine alte `/data/bot_commands.xml` im Volume liegt, muss die aktive XML ersetzt oder neu eingelesen werden:

```bash
docker exec -it Mosquitto mosquitto_pub -h localhost \
  -t 'messenger/bot/commands/set/renew/json' \
  -m '{}'
```
