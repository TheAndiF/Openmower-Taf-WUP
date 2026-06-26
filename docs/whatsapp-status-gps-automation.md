# Openmower-Taf-WUP - WhatsApp Status, GPS und Automatisierung

Stand: 2026-06-26  
Status: umgesetzt mit GPS-Positionsplatzhaltern

## Ziel

Diese Änderung erweitert den Mobert WhatsApp-Bot um einen aussagekräftigeren Status, ein GPS-Untermenü, automatische Statusmeldungen, gezielte Einmal-Benachrichtigungen und eine optionale Status-Ergänzung hinter Befehlsbestätigungen.

Die echten GPS-Koordinaten für Google Maps sind bewusst als Platzhalter umgesetzt. Die aktuelle OpenMower MQTT-Schnittstelle liefert `gps_state`-Diagnosedaten und lokale Kartenkoordinaten, aber noch keine verlässlichen WGS84-Koordinaten `latitude`/`longitude`. Sobald eine spätere MQTT-Version diese Werte bereitstellt, kann der Controller sie automatisch aus vorbereiteten GPS-Positionstopics übernehmen.

## Status-Menü

`Mobert: Status` enthält jetzt zusätzlich:

- Automatik-/Zeitplanstatus inklusive Ablaufzeit oder unbestimmter Deaktivierung
- GPS-Fahrbereitschaft
- GPS-Positionsplatzhalter oder später echte GPS-Position
- Google-Maps-Link als Platzhalter oder später echter Link

Beispiel mit Platzhalter:

```text
*GPS:* fahrbereit
*Position:* Platzhalter: GPS-Koordinaten aus zukuenftiger MQTT-Schnittstelle
*Karte:* https://www.google.com/maps?q={latitude},{longitude}
```

Wenn GPS nicht fahrbereit ist, wird der Status bewusst kurz gehalten:

```text
*GPS:* nicht fahrbereit
*Position:* nicht verfügbar
```

Es wird keine Zeile `Karte: nicht verfügbar` ausgegeben.

## Automatik-/Zeitplanstatus

Der Status verwendet `AutoMow` und `AutoMowSuspension` aus `robot_state/json`.

Beispiele:

```text
*Automatik:* aktiv
*Automatik:* deaktiviert bis 26.06.2026 18:30
*Automatik:* deaktiviert ohne Ablaufzeit
```

`AutoMowSuspension=0` bedeutet aktiv.  Ein ISO-Zeitwert wird als Ablaufzeit formatiert.  Ein sehr weit in der Zukunft liegender Wert wie `9999-12-31T23:59:59Z` wird als unbestimmte Deaktivierung behandelt.

## GPS-Untermenü

Neue WhatsApp-Befehle:

```text
Mobert: GPS
Mobert: GPS Status
```

Das GPS-Untermenü zeigt Detailwerte aus `gps_state/json`, unter anderem:

- `available`
- `quality`
- `visible`
- `used`
- `rtk_state`
- `gps_drive_ready`
- `position_accuracy_m`
- `max_position_accuracy_m`
- `orientation_valid`
- `recent_absolute_pose`
- `gps_timeout`
- `age_ms`
- `gps_drive_reason` oder `gps_drive_block_reason`

## GPS-Positionsplatzhalter

Die Platzhalter stehen in `/data/config.json` und in `bridge/config.example.json`:

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

Vorbereitete MQTT-Topics für spätere echte GPS-Koordinaten:

```text
gps/position/json
gps_position/json
openmower/gps/position/json
openmower/gps_position/json
```

Unterstützte Feldnamen sind zum Beispiel:

```json
{"latitude": 48.123456, "longitude": 11.123456}
```

oder:

```json
{"lat": 48.123456, "lon": 11.123456}
```

Die lokalen OpenMower-Koordinaten `pose.x` und `pose.y` werden nicht als Google-Maps-Koordinaten verwendet.

## Automatischer Status alle X Minuten

Neue WhatsApp-Befehle:

```text
Mobert: Status alle 15
Mobert: Status automatisch
Mobert: Status automatisch aus
```

Konfiguration:

```json
"status_push": {
  "enabled": false,
  "interval_minutes": 30,
  "min_interval_minutes": 5,
  "target_group": ""
}
```

`target_group` kann leer bleiben. Dann wird die aktuelle Standardgruppe verwendet. Beim Einschalten per WhatsApp wird die aktuelle Bot-Gruppe als Ziel gespeichert, soweit sie aufgelöst werden kann.

Der Controller veröffentlicht den Zustand zusätzlich unter:

```text
messenger/bot/status_push/json
messenger/bot/status_push/enabled
messenger/bot/status_push/interval_minutes
messenger/bot/status_push/target/alias
messenger/bot/status_push/text
```

## Status nach Befehlsbestätigungen

Neue WhatsApp-Befehle:

```text
Mobert: Status nach Befehl ein
Mobert: Status nach Befehl
Mobert: Status nach Befehl aus
```

Konfiguration:

```json
"bot": {
  "append_status_to_confirmations": false
}
```

Wenn diese Option aktiv ist, hängt Mobert den aktuellen Status unter normale Bestätigungen an, zum Beispiel bei Start, Pause, Stop, Zeitplan ein/aus oder MQTT-Bestätigungen. Status-, Hilfe-, Gruppen-, Ziel- und GPS-Untermenü-Antworten werden nicht erneut mit Status ergänzt.

Der aktuelle Zustand wird zusätzlich unter diesem MQTT-Topic veröffentlicht:

```text
messenger/bot/append_status_to_confirmations
```

## Einmalige Benachrichtigungen

Die aktiven Meldungen sind:

| Ereignis | Umsetzung |
|---|---|
| Undocking | XML-Flow `openmower_undocking_notification`, wenn `current_state` nach `UNDOCKING` wechselt |
| Docking abgeschlossen / Laden beginnt | XML-Flow `openmower_docking_idle_notification`, wenn `current_state` von `DOCKING` nach `IDLE` wechselt |
| Emergency | XML-Flow `openmower_error_notification`, wenn `emergency` nach `1` wechselt |
| GPS-Verlust während des Mähens | Python-Logik im Controller, wenn `gps_state` nicht mehr fahrbereit ist und `robot_state.current_state=MOWING` gilt |

Es gibt bewusst keine separate Meldung nur beim Eintritt in `DOCKING`.

## Geänderte Dateien

- `bridge/controller.py`
- `bridge/bot_commands.example.xml`
- `bridge/config.example.json`
- `controller_data/bot_commands.xml`
- `controller_data/config.json`
- `.env.example`
- `README.md`
- `docs/mqtt-topics.md`
- `docs/status-cache-subscriptions.md`
- `docs/whatsapp-status-gps-automation.md`
- `CHANGELOG.md`
- `COMMIT_MESSAGE.txt`

## Prüfung

- `bridge/controller.py` wurde mit `python -m py_compile` syntaktisch geprüft.
- `controller_data/bot_commands.xml` wurde mit `xml.etree.ElementTree` geparst.
- `bridge/bot_commands.example.xml` wurde mit `xml.etree.ElementTree` geparst.

Hinweis: Der Controller ergänzt die internen Standard-Statuscache-Topics auch dann, wenn eine ältere `.env` noch eine eigene `OPENMOWER_STATUS_CACHE_TOPICS`-Liste ohne GPS-Topics enthält. Trotzdem sollte die `.env` beim Update mit der neuen `.env.example` abgeglichen werden.
