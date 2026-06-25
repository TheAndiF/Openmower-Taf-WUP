# Openmower-Taf-WUP - MQTT-Protokollierung ausgehender WhatsApp-Nachrichten

Stand: 2026-06-25  
Status: v0.2

## Zweck

Ausgehende WhatsApp-Nachrichten werden jetzt nicht nur in der allgemeinen Nachrichtenhistorie gespeichert, sondern zusätzlich als eigene MQTT-Ereignisse und als retained Last-State veröffentlicht. Dadurch sind gesendete Nachrichten auch in MQTT-Tools sichtbar, wenn das Tool den Live-Zeitpunkt verpasst hat.

## Neue MQTT-Topics

| Topic | Retain | Inhalt |
|---|---:|---|
| `messenger/waha/messages/out/json` | nein | Live-Ereignis jeder ausgehenden WhatsApp-Nachricht |
| `messenger/waha/messages/out/last/json` | ja | Letzte ausgehende WhatsApp-Nachricht |
| `messenger/waha/messages/out/last/text` | ja | Text der letzten ausgehenden WhatsApp-Nachricht |
| `messenger/waha/messages/out/last/status` | ja | Sendestatus der letzten ausgehenden Nachricht |
| `messenger/waha/messages/out/last/time` | ja | Zeitstempel der letzten ausgehenden Nachricht |
| `messenger/waha/messages/out/history/json` | ja | Historie der ausgehenden Nachrichten |
| `messenger/waha/messages/out/count` | ja | Anzahl ausgehender Nachrichten in der aktuellen Historie |
| `messenger/waha/messages/in/history/json` | ja | Historie der eingehenden Nachrichten |
| `messenger/waha/messages/in/count` | ja | Anzahl eingehender Nachrichten in der aktuellen Historie |

## Prüfung per Kommandozeile

```bash
docker exec -it Mosquitto mosquitto_sub \
  -h localhost \
  -t 'messenger/waha/messages/out/#' \
  -v
```

Eine Testnachricht kann über MQTT ausgelöst werden:

```bash
docker exec -it Mosquitto mosquitto_pub \
  -h localhost \
  -t 'messenger/waha/messages/out/set/json' \
  -m '{"text":"Testnachricht aus MQTT","target":"g014"}'
```

Danach sollte unter `messenger/waha/messages/out/last/json` die zuletzt gesendete Nachricht sichtbar sein.

## ROS-MQTT-Topics

Die Flow-XML nutzt jetzt die konkreten ROS-MQTT-Topics:

| Zweck | Topic |
|---|---|
| OpenMower-Zustand | `robot_state/json` |
| WLAN-Stärke | `sensors/om_system_wifi_signal_percent/data` |

Die JSON-Felder `emergency` und `is_charging` werden als numerische Werte `0` oder `1` ausgewertet.
