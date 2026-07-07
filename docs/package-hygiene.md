# Paketbereinigung und Gitignore

Stand: 2026-06-25
Projekt: Openmower-Taf-WUP

Diese Datei dokumentiert die Paketbereinigung fuer die WhatsApp-MQTT-Bridge.

## Ziel

Das Auslieferungspaket soll keine lokalen Git-Daten, Python-Cachedateien oder privaten Laufzeitdaten enthalten. Gleichzeitig soll die aktive Bot-Flow-JSON weiterhin im Paket enthalten bleiben.

## Geaenderte Regeln

Die `.gitignore` enthaelt jetzt zusaetzlich:

```gitignore
.git/
```

Damit werden lokale Git-Metadaten nicht versehentlich in ein ZIP-Paket uebernommen.

## Bewusst nicht ignoriert

`controller_data/` bleibt bewusst nicht ignoriert. Der Ordner enthaelt die aktive Datei:

```text
controller_data/bot_commands.json
```

Diese Datei soll im Paket enthalten sein, damit die neue Flow-JSON beim Ausrollen direkt als aktive Konfiguration vorhanden ist.

## Vor einer Paketweitergabe pruefen

Vor der Weitergabe eines ZIP-Pakets sollten diese Befehle ausgefuehrt werden:

```bash
rm -rf .git
find . -type d -name "__pycache__" -prune -exec rm -rf {} +
find . -type f -name "*.pyc" -delete
```

Danach pruefen:

```bash
find . -name ".git" -o -name "__pycache__" -o -name "*.pyc"
```

Die Ausgabe sollte leer sein.
