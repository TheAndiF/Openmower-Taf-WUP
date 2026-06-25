# Openmower-Taf-WUP - WLAN data topic fix - v1.2

## Problem

Der OpenMower WLAN-Sensor veroeffentlicht unter `sensors/om_system_wifi_signal_percent` mehrere Untertopics. Der lesbare Prozentwert steht auf:

```text
sensors/om_system_wifi_signal_percent/data
```

Zusaetzlich gibt es ein binaeres Topic:

```text
sensors/om_system_wifi_signal_percent/bson
```

Wenn der Controller den Wildcard-Filter `sensors/om_system_wifi_signal_percent/#` abonniert, verarbeitet er auch den binaeren `bson`-Payload. Dadurch konnte `Mobert: Status` beim WLAN-Wert Zeichenmuell statt Prozent anzeigen.

## Umsetzung

Die Standard-Statuscache-Subscriptions wurden auf konkrete Topics geaendert:

```text
robot_state/json
sensors/om_system_wifi_signal_percent/data
openmower/robot_state/json
openmower/sensors/om_system_wifi_signal_percent/data
```

Die Funktion `update_openmower_state()` aktualisiert WLAN nur noch fuer Topics, die semantisch auf `sensors/om_system_wifi_signal_percent/data` enden. Der Payload muss numerisch sein. Nicht-numerische Werte werden ignoriert.

## Pruefung nach dem Update

```bash
cd /opt/stacks/whatsapp
docker compose build --no-cache waha_mqtt_controller
docker compose up -d --force-recreate waha_mqtt_controller

docker logs waha_mqtt_controller --tail=100 | grep -Ei 'Subscribed OpenMower status cache topic|Loaded'
```

Erwartet ist `.../data`, nicht `.../#` fuer WLAN.

Danach WhatsApp-Befehl senden:

```text
Mobert: Status
```

Der WLAN-Wert sollte als Prozentwert erscheinen, z. B.:

```text
WLAN: 62 %
```
