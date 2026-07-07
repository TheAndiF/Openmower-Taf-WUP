# Openmower-Taf-WUP - JSON Flow Configuration - v1.2

## Zweck

Mobert verwendet ab Version v1.2 nur noch die JSON-Datei `/data/bot_commands.json`. Die alte XML-Konfiguration wurde entfernt. Dadurch kann eine andere lokale App die Flow-Konfiguration direkt lesen, ändern und wieder schreiben, ohne XML parsen zu müssen.

## Aktive Dateien

| Datei | Zweck |
|---|---|
| `controller_data/bot_commands.json` | Aktive Beispiel-/Volume-Konfiguration für `/data`. |
| `bridge/bot_commands.example.json` | Fallback-Datei im Docker-Image, wenn `/data/bot_commands.json` fehlt. |
| `/data/bot_commands.json` | Laufzeitdatei im Container. |

## Version

Die Konfigurationsversion steht in der JSON-Datei:

```json
{
  "format": "mobertBotConfig",
  "version": "0.5",
  "language": "de"
}
```

## Grundstruktur

```json
{
  "format": "mobertBotConfig",
  "version": "0.5",
  "language": "de",
  "head": {
    "name": "Mobert OpenMower Flow Configuration",
    "description": "JSON-gesteuerte Flow-Konfiguration mit zentralen Watchdog- und Output-Modulen.",
    "enabled": true
  },
  "modules": {},
  "flows": {}
}
```

## Module

Die Modulstruktur bleibt weitgehend gleich. `whatsapp_watchdog` und `whatsapp_output` verweisen weiterhin auf das zentrale `whatsapp`-Modul.

```json
"modules": {
  "whatsapp": {
    "kind": "whatsappModule",
    "enabled": true,
    "session": "Wasserleberweg",
    "defaultGroup": "g014",
    "listenerGroup": "g014",
    "wakeWord": {
      "text": "Mobert",
      "required": true,
      "syntax": "colon",
      "caseSensitive": false
    }
  },
  "whatsapp_watchdog": {
    "kind": "inputModule",
    "enabled": true,
    "moduleRef": "whatsapp"
  },
  "whatsapp_output": {
    "kind": "outputModule",
    "enabled": true,
    "moduleRef": "whatsapp"
  },
  "mqtt_watchdog": {
    "kind": "inputModule",
    "enabled": true,
    "subscribeMode": "enabled_flows"
  },
  "mqtt_output": {
    "kind": "outputModule",
    "enabled": true
  }
}
```

## Flows

Jeder Flow besitzt `head` und `steps`. `enabled` steuert die Ausführung. `show` steuert nur die Anzeige in `Mobert: ?` und `messenger/bot/help/json`.

```json
"flows": {
  "status": {
    "head": {
      "name": "Status",
      "description": "Status von Mobert und OpenMower anzeigen.",
      "category": "general",
      "enabled": true,
      "show": true
    },
    "steps": {
      "start": {
        "input": {
          "module": "whatsapp_watchdog",
          "type": "command",
          "expect": {
            "command": "Status"
          }
        },
        "processing": {
          "mode": "local_status",
          "responseTemplate": "status_short"
        },
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
```

## MQTT

Aktive JSON lesen:

```bash
mosquitto_sub -h Mosquitto -C 1 -v -t messenger/bot/commands/source/json
```

Parsed command metadata lesen:

```bash
mosquitto_sub -h Mosquitto -C 1 -v -t messenger/bot/commands/json
```

JSON ersetzen:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/config/json -f bot_commands.json
```

JSON vom Datenträger neu laden:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/renew/json -m '{}'
```

Validierung lesen:

```bash
mosquitto_sub -h Mosquitto -C 1 -v -t messenger/bot/commands/validation/json
```

## Verhalten bei alter XML

Der Controller lädt keine XML-Datei mehr. Für bestehende Installationen muss die aktive Datei im gemounteten Volume auf `controller_data/bot_commands.json` umgestellt werden. Alte retained MQTT-Werte auf `messenger/bot/commands/xml` werden beim Veröffentlichen der Bot-Kommandos geleert.
