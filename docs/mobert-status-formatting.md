# Openmower-Taf-WUP - Mobert status formatting and XML help - v1.3

## Ziel

`Mobert: Status` soll auf WhatsApp besser lesbar sein und die wichtigsten OpenMower-Werte eindeutig anzeigen. Außerdem soll `Mobert: ?` nicht mehr aus einer separaten, fest codierten Befehlsliste entstehen, sondern aus der aktuell geladenen XML-Konfiguration.

## Statusformat

Die Statusantwort nutzt WhatsApp-kompatible Formatierung:

```text
*Mobert Status*
──────────────

*Zeit:* 25.06.2026 23:24:37
*Status:* MOWING
*Fläche:* Plantage
*Bearbeitung:* 72.0 %
*Akku:* 53 % (lädt)
*WLAN:* 64 %
*Emergency:* nein
*Fehler:* keiner
*MQTT:* verbunden
```

Hinweise:

- WhatsApp unterstuetzt Fettschrift ueber Sternchen.
- Echtes Unterstreichen ist in WhatsApp nicht verlaesslich verfuegbar; deshalb wird unter dem Titel eine Trennlinie verwendet.
- Die Zeit wird lokal formatiert. Standard ist `Europe/Berlin`.
- Die Zeitzone kann mit `STATUS_TIMEZONE` gesetzt werden.

## MowArea und Flaechenfortschritt

`Mobert: Status` zeigt die aktive Mähfläche bewusst kurz an:

```text
*Fläche:* Plantage
*Bearbeitung:* 72.0 %
```

Der ausführlichere Befehl `Mobert: MowArea` liefert nur die aktuellen Mähflächenwerte ohne Erklärung:

```text
Fläche: Plantage
Flächenreihenfolge: 50
Bearbeitung: 72.0 %
Pfad: 1
Pfadindex: 8261
```

Die aktive Fläche wird über `checkpoint_area_id` aus `robot_state/json` bestimmt. Der lesbare Name und die Flächenreihenfolge kommen aus dem gecachten `area_queue`-Payload. Der Fortschritt wird bevorzugt aus `current_path_index` im Verhältnis zur Gesamtzahl der geplanten Punkte in `areas[area_id].paths[].points` berechnet. `current_action_progress` wird nur als Fallback genutzt, wenn es einen plausiblen Wert groesser als 0 liefert; dadurch wird kein irreführendes `00%` mehr angezeigt.

Bei keiner aktiven Flaeche bleibt die Ausgabe:

```text
*Fläche:* keine aktive Fläche
*Bearbeitung:* nicht aktiv
```

## Emergency und Fehler

Die Statusantwort enthaelt immer beide Zeilen:

- `Emergency`: `ja`, `nein` oder `unbekannt`
- `Fehler`: `current_sub_state`, `Emergency/Notfall aktiv`, `keiner` oder `unbekannt`

Dadurch ist auch bei normalem Betrieb sichtbar, dass kein Emergency-Status anliegt.

## XML als Quelle der Wahrheit fuer Hilfe

`Mobert: ?` wird aus den geladenen `BotCommand`-Eintraegen erzeugt. Diese Eintraege entstehen beim Laden der aktiven XML-Datei:

```text
/data/bot_commands.xml
```

Damit gilt:

- Aktivierte XML-Command-Flows erscheinen in der Hilfe.
- Deaktivierte XML-Command-Flows erscheinen nicht.
- Neue XML-Commands erscheinen nach Reload automatisch.
- Der Python-Code enthaelt keine separate Befehlsliste fuer die Hilfe.

Reload der XML:

```bash
docker exec -it Mosquitto mosquitto_pub -h localhost \
  -t 'messenger/bot/commands/set/renew/json' \
  -m '{}'
```

## Deployment

Nach dem Einspielen:

```bash
cd /opt/stacks/whatsapp
docker compose build --no-cache waha_mqtt_controller
docker compose up -d --force-recreate waha_mqtt_controller
```

Pruefen:

```bash
docker logs waha_mqtt_controller --tail=100 | grep -Ei 'Loaded|Subscribed OpenMower status cache topic'
```

Danach `Mobert: Status` und `Mobert: ?` in der WhatsApp-Lauschgruppe testen.


## v1.4 Hinweis: Hilfe als neu aufgebautes XML-Artefakt

Die Hilfe wird nun beim Laden der XML explizit neu aufgebaut. `Mobert: ?` verwendet dieses erzeugte Hilfeartefakt. Nach einer Aenderung ueber `messenger/bot/commands/set/xml` oder einem Reload ueber `messenger/bot/commands/set/renew/json` wird die Hilfe ebenfalls neu erzeugt.

Die retained MQTT-Snapshots stehen hier bereit:

```text
messenger/bot/help/text
messenger/bot/help/json
```
