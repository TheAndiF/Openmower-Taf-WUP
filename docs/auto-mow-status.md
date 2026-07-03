# Openmower-Taf-WUP - Auto Mow Status - v1.0

## Ziel

`Mobert: Status` zeigt den Auto-Mow-Zustand dauerhaft an. Sichtbar ist, ob Auto Mow aktiviert ist oder ob Auto Mow bis zu einem Zeitpunkt ausgesetzt wurde.

## Status-Ausgabe

Beispiele:

```text
*Auto Mow:* aktiviert
*Auto Mow:* deaktiviert
*Auto Mow:* ausgesetzt bis 26.06.2026 18:30
*Auto Mow:* ausgesetzt unendlich
```

## Datenquelle

Der Controller verwendet bevorzugt diese Felder aus `robot_state/json`:

```text
robot_state/json
├── AutoMow
└── AutoMowSuspension
```

Zusätzlich werden normalisierte Aliasnamen akzeptiert, zum Beispiel `auto_mow`, `automow`, `auto_mow_suspension`, `automow_suspension`, `suspension_until` und `schedule_suspension`.

## Auswertung

- `AutoMow=true` und keine aktive Suspension: `aktiviert`
- `AutoMow=false`: `deaktiviert`
- `AutoMowSuspension=0`, `false` oder `0.0`: nicht ausgesetzt, also `aktiviert`, wenn Auto Mow aktiv ist
- ISO-Zeitwert in `AutoMowSuspension`: `ausgesetzt bis <Datum Uhrzeit>`
- Jahr `9999`, `forever`, `unlimited`, `dauerhaft`, `infinite`, `infinity` oder `unendlich`: `ausgesetzt unendlich`

Die Darstellung mit Jahr `9999` wird absichtlich nicht als konkretes Datum formatiert, sondern als unendliche Aussetzung angezeigt.
