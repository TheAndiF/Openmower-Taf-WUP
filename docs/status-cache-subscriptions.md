# Openmower-Taf-WUP - Status cache subscriptions - v1.1

## Anlass

Live-Tests auf dem Zielsystem zeigen, dass OpenMower Statusdaten auf unprefixed MQTT topics veroeffentlicht:

- `robot_state/json`
- `sensors/om_system_wifi_signal_percent/data`

Gleichzeitig zeigte der laufende Container noch eine legacy `/data/bot_commands.xml` mit `<mobertCommands version="0.1">`. Diese legacy XML kann lokale Befehle wie `Mobert: Status` definieren, enthaelt aber keine `mqtt_watchdog` Flow-Subscriptions. Dadurch blieb der interne Statuscache leer, obwohl Mosquitto die OpenMower-Daten hatte.

## Korrektur

`bridge/controller.py` subscribed die Statuscache-Topics nun unabhaengig von der XML:

- `robot_state/#`
- `sensors/om_system_wifi_signal_percent/#`
- `openmower/robot_state/#`
- `openmower/sensors/om_system_wifi_signal_percent/#`

Wenn eins dieser Topics empfangen wird, wird der interne Cache ueber `update_mqtt_state_cache()` und `update_openmower_state()` aktualisiert. Dadurch kann `Mobert: Status` auch mit einer legacy XML aktuelle Werte ausgeben.

## Optionale Anpassung

Bei anderen Prefixen kann die Liste ueber eine Umgebungsvariable gesetzt werden:

```bash
OPENMOWER_STATUS_CACHE_TOPICS=robot_state/#,sensors/om_system_wifi_signal_percent/#,meinprefix/robot_state/#,meinprefix/sensors/om_system_wifi_signal_percent/#
```

## Deployment-Hinweis

Die Compose-Datei mountet `./controller_data` nach `/data`. Deshalb muss die aktive XML-Datei auf dem Host ersetzt werden, wenn im Container noch die alte legacy XML liegt:

```bash
cd /opt/stacks/whatsapp
cp controller_data/bot_commands.xml controller_data/bot_commands.xml.bak.$(date +%Y%m%d-%H%M%S)
# Danach die neue controller_data/bot_commands.xml aus diesem Paket an diese Stelle kopieren.
docker compose build --no-cache waha_mqtt_controller
docker compose up -d --force-recreate waha_mqtt_controller
```

## Verifikation

```bash
docker logs waha_mqtt_controller --tail=100 | grep -Ei 'Subscribed OpenMower status cache topic|Loaded'
```

Erwartete Eintraege:

```text
Subscribed OpenMower status cache topic: robot_state/#
Subscribed OpenMower status cache topic: sensors/om_system_wifi_signal_percent/#
```

Statusdaten auf Mosquitto pruefen:

```bash
docker exec -it Mosquitto mosquitto_sub -h localhost -v \
  -t 'robot_state/json' \
  -t 'sensors/om_system_wifi_signal_percent/data'
```

Danach sollte `Mobert: Status` Status, Flaeche, Akku und WLAN ausgeben.
