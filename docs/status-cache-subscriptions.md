# Openmower-Taf-WUP - Status cache subscriptions - v1.2

## Anlass

Live-Tests auf dem Zielsystem zeigen, dass OpenMower Statusdaten auf unprefixed MQTT topics veroeffentlicht:

- `robot_state/json`
- `sensors/om_system_wifi_signal_percent/data`

Der MQTT-Baum des WLAN-Sensors enthaelt aber mindestens zwei Untertopics:

- `sensors/om_system_wifi_signal_percent/data` - lesbarer Zahlenwert, z. B. `62.000000`
- `sensors/om_system_wifi_signal_percent/bson` - binaerer Payload

Wenn der Controller auf `sensors/om_system_wifi_signal_percent/#` subscribed, kann der binaere `bson`-Payload den letzten lesbaren WLAN-Wert ueberschreiben. In WhatsApp erschien dann ein Zeichenmuell wie `\x10\x01dP@` statt `62 %`.

## Korrektur

`bridge/controller.py` subscribed die Statuscache-Topics weiterhin unabhaengig von der XML, aber nun standardmaessig nur auf konkrete Text-/JSON-Topics:

- `robot_state/json`
- `sensors/om_system_wifi_signal_percent/data`
- `openmower/robot_state/json`
- `openmower/sensors/om_system_wifi_signal_percent/data`

Zusätzlich aktualisiert der Controller den WLAN-Cache nur noch, wenn das empfangene Topic semantisch auf `sensors/om_system_wifi_signal_percent/data` endet und der Payload als Zahl gelesen werden kann. Binaere oder nicht-numerische Payloads werden fuer WLAN ignoriert und koennen den letzten gueltigen Wert nicht mehr ueberschreiben.

## Optionale Anpassung

Bei anderen Prefixen kann die Liste ueber eine Umgebungsvariable gesetzt werden. Auch dort sollte WLAN immer auf `/data` zeigen, nicht auf `/#`:

```bash
OPENMOWER_STATUS_CACHE_TOPICS=robot_state/json,sensors/om_system_wifi_signal_percent/data,meinprefix/robot_state/json,meinprefix/sensors/om_system_wifi_signal_percent/data
```

Nicht empfohlen fuer WLAN:

```bash
OPENMOWER_STATUS_CACHE_TOPICS=sensors/om_system_wifi_signal_percent/#
```

Der Wildcard-Filter empfaengt auch `bson` und andere Geschwistertopics.

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
Subscribed OpenMower status cache topic: robot_state/json
Subscribed OpenMower status cache topic: sensors/om_system_wifi_signal_percent/data
Subscribed OpenMower status cache topic: openmower/robot_state/json
Subscribed OpenMower status cache topic: openmower/sensors/om_system_wifi_signal_percent/data
```

Es sollte nicht mehr erscheinen:

```text
Subscribed OpenMower status cache topic: sensors/om_system_wifi_signal_percent/#
```

Statusdaten auf Mosquitto pruefen:

```bash
docker exec -it Mosquitto mosquitto_sub -h localhost -v \
  -t 'robot_state/json' \
  -t 'sensors/om_system_wifi_signal_percent/data'
```

Danach sollte `Mobert: Status` Status, Flaeche, Akku und WLAN als Zahl ausgeben, z. B. `WLAN: 62 %`.
