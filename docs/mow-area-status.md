# Openmower-Taf-WUP - MowArea Status - v1.0

## Ziel

Der normale WhatsApp-Status soll die Mähfläche kurz anzeigen und keine irreführenden `00%` mehr ausgeben. Der separate Befehl `Mobert: MowArea` liefert nur die aktuellen Werte der aktiven Mähfläche.

## Status-Ausgabe

Im normalen Status werden nur Flächenname und Bearbeitung angezeigt:

```text
*Fläche:* Plantage
*Bearbeitung:* 72.0 %
```

Der Status-Anhang wird bei Status-ähnlichen Abfragen nicht angehängt. Dadurch wird `Mobert: Status` nicht doppelt ausgegeben, wenn `append_status_to_confirmations` aktiv ist.

## MowArea-Ausgabe

`Mobert: MowArea` liefert nur diese aktuellen Werte ohne Erklärung:

```text
Fläche: Plantage
Flächenreihenfolge: 50
Bearbeitung: 72.0 %
Pfad: 1
Pfadindex: 8261
```

## Datenquellen

- `robot_state/json`: `checkpoint_area_id`, `current_path`, `current_path_index`
- Area-Queue-Payload: `area_queue[].name`, `area_queue[].mowing_order`, `areas[area_id].paths[].points`

Die aktive Fläche wird bevorzugt über `checkpoint_area_id` bestimmt. Der Fortschritt wird aus dem aktuellen Pfadindex im Verhältnis zur Gesamtzahl der geplanten Punkte berechnet. `current_action_progress` bleibt nur ein Fallback, wenn ein plausibler Wert groesser als 0 vorliegt.

## MQTT-Cache

Der Controller erkennt Area-Queue-Payloads, wenn ein JSON-Payload `area_queue` oder `areas` enthält. Standardmaessig werden mehrere mögliche Topics abonniert, zum Beispiel `area_queue/json`, `mow_area/json`, `mow_area/status/json` sowie die jeweiligen `openmower/` Varianten. Falls die Installation ein anderes Topic nutzt, wird es in `OPENMOWER_STATUS_CACHE_TOPICS` ergänzt.
