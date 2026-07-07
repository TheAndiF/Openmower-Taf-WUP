# Openmower-Taf-WUP - JSON-generated Mobert help - v1.5

## Ziel

Die WhatsApp-Antwort auf `Mobert: ?` wird aus der aktiven `bot_commands.json` erzeugt. Dadurch muss die Hilfe nicht separat im Python-Code gepflegt werden. Die JSON-Datei ist die Quelle der Wahrheit.

## Mapping aus der Flow-JSON

| Angezeigter Hilfeteil | JSON-Quelle |
|---|---|
| Anzeigename | `flows.<flow_id>.head.name` |
| Beschreibung | `flows.<flow_id>.head.description` |
| Aktiv sichtbar | `flows.<flow_id>.head.enabled=true` und `flows.<flow_id>.head.show=true` |
| Befehl | `flows.<flow_id>.steps.<step_id>.input.expect.command` |
| Wake Word | `modules.whatsapp.wakeWord.text` oder MQTT Override |

Beispiel:

```json
{
  "flows": {
    "status": {
      "head": {
        "name": "Status",
        "description": "Status von Mobert und OpenMower anzeigen.",
        "enabled": true,
        "show": true
      },
      "steps": {
        "start": {
          "input": {
            "module": "whatsapp_watchdog",
            "type": "command",
            "expect": { "command": "Status" }
          },
          "processing": { "mode": "local_status" },
          "outputs": [
            {
              "module": "whatsapp_output",
              "type": "send",
              "target": "{replyTarget}",
              "message": "{processing.result}"
            }
          ]
        }
      }
    }
  }
}
```

wird in der Hilfe als `Mobert: Status` mit der hinterlegten Beschreibung angezeigt.

## MQTT

Die erzeugte Hilfe wird retained veröffentlicht:

```text
messenger/bot/help/text
messenger/bot/help/json
```

Die aktive JSON-Konfiguration kann per MQTT ersetzt werden:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/config/json -f bot_commands.json
```

Die Konfiguration kann vom Datenträger neu geladen werden:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/renew/json -m '{}'
```

## Verhalten

- Wird `enabled` auf `false` gesetzt, wird der Flow nicht ausgeführt und nicht in der Hilfe gezeigt.
- Wird `show` auf `false` gesetzt, wird der Flow nicht in der Hilfe gezeigt, kann aber bei passender Eingabe weiter ausgeführt werden.
- Wird die Beschreibung in der JSON geändert, erscheint der neue Text nach Reload in `Mobert: ?`.
- Die Reihenfolge folgt der Reihenfolge der Einträge in `bot_commands.json`.
