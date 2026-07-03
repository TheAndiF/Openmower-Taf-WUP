# Openmower-Taf-WUP - ROS-MQTT-WhatsApp-Meldungen

Stand: 2026-06-25  
Status: Entwurf  
Version: v0.2

## Zweck

Die Bridge meldet wichtige OpenMower-Ereignisse automatisch per WhatsApp und beantwortet `Mobert: Status` mit einer kompakten Statusmeldung. Die Werte stammen aus ROS-MQTT.

## Verwendete ROS-MQTT-Quellen

| Quelle | Verwendung |
|---|---|
| `robot_state/json` | Zustand, Fläche, Akku, Laden, Emergency |
| `sensors/om_system_wifi_signal_percent/data` | WLAN-Stärke in Prozent |

## Statusausgabe

Beispiel:

```text
Mobert Status

Zeit: 2026-06-25T18:55:00.503419+00:00
Status: IDLE
Fläche: keine aktive Fläche
Akku: 95 % (lädt)
WLAN: 82 %
MQTT: verbunden
```

Die Akku-Zeile kombiniert `battery_percentage` und `is_charging`. Werte zwischen 0 und 1 werden als Prozentwert interpretiert. `is_charging=1` wird als `(lädt)` dargestellt, `is_charging=0` als `(lädt nicht)`.

Der Begriff Dock wird nicht mehr automatisch verwendet. Wenn keine aktive Fläche vorhanden ist, wird `keine aktive Fläche` angezeigt. Ob geladen wird, steht ausschließlich in der Akku-Zeile.

Die Zeile `Fehler:` wird nur ergänzt, wenn `emergency` aktiv ist.

## Automatische WhatsApp-Meldungen

Standardmäßig aktivierte Flows:

| Flow | Auslöser | Nachricht |
|---|---|---|
| `openmower_drives_off_notification` | `current_state` wechselt von `IDLE` auf einen anderen Zustand | OpenMower fährt los |
| `openmower_charging_finished_notification` | `is_charging` wechselt von aktiv auf inaktiv | Laden beendet |
| `openmower_error_notification` | `emergency` wird aktiv | Fehler/Emergency erkannt |

## Hinweise

Die Flow-XML nutzt zentrale Module für WhatsApp-Input, WhatsApp-Output, MQTT-Watchdog und MQTT-Output. Die ROS-MQTT-Topics werden über aktivierte MQTT-Watchdog-Flows abonniert. Bestehende MQTT-Konfigurationsbefehle bleiben weiterhin gültig.


Der interne Statuscache erkennt Status- und WLAN-Topics zusätzlich mit `openmower/` Prefix. Fuer WLAN wird nur `sensors/om_system_wifi_signal_percent/data` bzw. `openmower/sensors/om_system_wifi_signal_percent/data` verarbeitet, damit binaere `bson`-Payloads den Prozentwert nicht ueberschreiben.


## v1.3 Hinweis: Statusformat und Hilfe

`Mobert: Status` formatiert die Zeit lokal ueber `STATUS_TIMEZONE`, zeigt WhatsApp-fette Feldnamen und gibt Fläche sowie Bearbeitung als eigene Zeilen aus. Der Fortschritt kommt bevorzugt aus `map/mowing_progress/status/json -> areas[current_area_id].percent`; `Emergency` und `Fehler` werden immer ausgegeben.

`Mobert: ?` wird aus der aktiv geladenen XML-Konfiguration erzeugt. Die XML-Datei ist damit die Quelle der Wahrheit fuer die angezeigten Befehle.
