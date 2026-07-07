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

`bridge/controller.py` subscribed die Statuscache-Topics weiterhin unabhaengig von der JSON, aber nun standardmaessig nur auf konkrete Text-/JSON-Topics:

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

Die Compose-Datei mountet `./controller_data` nach `/data`. Deshalb muss die aktive JSON-Datei auf dem Host ersetzt werden, wenn im Container noch eine alte JSON liegt:

```bash
cd /opt/stacks/whatsapp
cp controller_data/bot_commands.json controller_data/bot_commands.json.bak.$(date +%Y%m%d-%H%M%S)
# Danach die neue controller_data/bot_commands.json aus diesem Paket an diese Stelle kopieren.
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


## v1.3 Hinweis: Statusformat und Hilfe

`Mobert: Status` formatiert die Zeit lokal ueber `STATUS_TIMEZONE`, zeigt WhatsApp-fette Feldnamen und gibt Fläche sowie Bearbeitung als eigene Zeilen aus. Der Fortschritt kommt bevorzugt aus `map/mowing_progress/status/json -> areas[current_area_id].percent`; `Emergency` und `Fehler` werden immer ausgegeben.

`Mobert: ?` wird aus der aktiv geladenen JSON-Konfiguration erzeugt. Die JSON-Datei ist damit die Quelle der Wahrheit fuer die angezeigten Befehle.

## Erweiterung 2026-06-26: GPS-Status und Positionsplatzhalter

Die Status-Cache-Liste enthält jetzt zusätzlich GPS-Topics:

```text
gps_state/json
gps/position/json
gps_position/json
openmower/gps_state/json
openmower/gps/position/json
openmower/gps_position/json
```

`gps_state/json` liefert die GPS-Fahrbereitschaft und Diagnosewerte für `Mobert: Status` und das GPS-Untermenü.  Die Positionstopics sind Platzhalter für eine spätere MQTT-Schnittstellenversion mit echten WGS84-Koordinaten.  Lokale OpenMower-Kartenkoordinaten `pose.x` und `pose.y` werden nicht als Google-Maps-Koordinaten interpretiert.

Hinweis: Der Controller ergänzt die internen Standard-Statuscache-Topics auch dann, wenn eine ältere `.env` noch eine eigene `OPENMOWER_STATUS_CACHE_TOPICS`-Liste ohne GPS-Topics enthält. Trotzdem sollte die `.env` beim Update mit der neuen `.env.example` abgeglichen werden.

## v1.5 Erweiterung: Mowing-Progress-Status

Für die aktuelle Mähfläche und den Mähfortschritt wird zusätzlich die Web-App-nahe Progress-Quelle abonniert:

```text
map/mowing_progress/status/json
mowing_progress/status/json
openmower/map/mowing_progress/status/json
openmower/mowing_progress/status/json
```

Der Controller trennt diesen Live-Fortschritt vom Area-Queue/Plan-Cache. Dadurch bleiben Flächenname und `mowing_order` aus `area_queue/json` erhalten, während `areas[current_area_id].percent`, `state`, `current_path` und `current_path_index` aus dem Progress-Payload gelesen werden.

Priorität für die aktive Fläche:

1. `robot_state/json -> current_area_id`
2. `map/mowing_progress/status/json -> current_area_id`
3. `robot_state/json -> checkpoint_area_id` als Fallback

Priorität für den Fortschritt:

1. `map/mowing_progress/status/json -> areas[current_area_id].percent`
2. `map/mowing_progress/status/json -> areas[current_area_id].paths[].completed_percent`
3. älterer Area-Plan-Fallback `current_path_index / areas[area_id].paths[].points`
4. plausibler `current_action_progress`, wenn größer als 0

Verifikation:

```bash
docker logs waha_mqtt_controller --tail=100 | grep -Ei 'mowing_progress|mowing/area_queue|area_queue'
```

Erwartete neue Einträge:

```text
Subscribed OpenMower status cache topic: map/mowing_progress/status/json
Subscribed OpenMower status cache topic: openmower/map/mowing_progress/status/json
```
