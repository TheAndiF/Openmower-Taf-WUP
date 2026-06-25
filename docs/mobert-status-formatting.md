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
*Fläche:* Fläche 1 (42%)
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

## Fortschritt hinter der Flaeche

Wenn OpenMower eine aktive Flaeche maeht, haengt der Controller den Fortschritt direkt an die Flaeche an:

```text
*Fläche:* Fläche 1 (07%)
*Fläche:* Fläche 1 (42%)
*Fläche:* Fläche 1 (100%)
```

Quelle ist `current_action_progress` aus `robot_state/json`. Werte zwischen `0` und `1` werden als Bruchteil interpretiert und in Prozent umgerechnet. Werte zwischen `0` und `100` werden als Prozentwert gelesen. Die Ausgabe wird auf `00%` bis `100%` begrenzt.

Bei keiner aktiven Flaeche bleibt die Ausgabe:

```text
*Fläche:* keine aktive Fläche
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
