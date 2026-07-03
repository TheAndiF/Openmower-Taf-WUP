# Openmower-Taf-WUP - MowArea Status - v1.1

## Ziel

Der normale WhatsApp-Status zeigt immer die aktuell gemähte Fläche und den aktuellen Mähfortschritt an. Das gilt auch dann, wenn der Mäher gerade lädt und `robot_state/json` selbst keinen neuen `current_action_progress` liefert. Der separate Befehl `Mobert: MowArea` liefert weiterhin nur die aktuellen Mähflächenwerte ohne zusätzliche Erklärung.

## Status-Ausgabe

Im normalen Status werden Flächenname und Bearbeitung immer ausgegeben:

```text
*Fläche:* Plantage
*Bearbeitung:* 72.0 %
```

Wenn keine aktive Fläche bekannt ist, bleibt die kompakte Ausgabe eindeutig:

```text
*Fläche:* keine aktive Fläche
*Bearbeitung:* nicht aktiv
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

## Datenquellen und Priorität

### robot_state/json

```text
robot_state/json
├── checkpoint_area_id    letzter gespeicherter Checkpoint-Flächenbezug
├── current_area_id       aktuell aktive Fläche
├── current_path          aktueller Pfad
└── current_path_index    aktueller Index im Pfad
```

### map/mowing_progress/status/json

```text
map/mowing_progress/status/json
├── current_area_id
├── areas[current_area_id].percent
├── areas[current_area_id].state
├── areas[current_area_id].current_path
├── areas[current_area_id].current_path_index
└── areas[current_area_id].paths[].completed_percent
```

Die aktive Fläche wird bevorzugt über `robot_state/json -> current_area_id` bestimmt. Danach folgt `map/mowing_progress/status/json -> current_area_id`. `checkpoint_area_id` bleibt nur ein Fallback, weil dieser Wert den letzten gespeicherten Checkpoint-Flächenbezug beschreibt.

Der Fortschritt wird bevorzugt direkt aus `map/mowing_progress/status/json -> areas[current_area_id].percent` gelesen. Wenn dort kein Prozentwert vorhanden ist, nutzt der Controller `paths[].completed_percent`, danach den älteren Fallback `current_path_index / areas[area_id].paths[].points` aus dem Area-Plan und zuletzt einen plausiblen `current_action_progress`.

## MQTT-Cache

Der Controller hält zwei getrennte Caches:

- Area-Queue/Plan: Name, `mowing_order` und Pfadpunkte.
- Mowing-Progress: Live-Fortschritt, Status, aktueller Pfad und Pfadindex.

Dadurch kann ein progress-only Payload aus `map/mowing_progress/status/json` nicht mehr versehentlich den Area-Queue-Cache überschreiben. Der lesbare Flächenname bleibt erhalten, während der Fortschritt aus der neueren Progress-Quelle kommt.

Standardmäßig werden neben den bisherigen Area-Queue-Topics jetzt auch folgende Progress-Topics abonniert:

```text
map/mowing_progress/status/json
mowing_progress/status/json
openmower/map/mowing_progress/status/json
openmower/mowing_progress/status/json
```

Falls die Installation ein anderes Topic nutzt, wird es in `OPENMOWER_STATUS_CACHE_TOPICS` ergänzt.
