# Openmower-Taf-WUP - XML-generated Mobert help - v1.4

## Zweck

Die WhatsApp-Antwort auf `Mobert: ?` wird aus der aktiven `bot_commands.xml` erzeugt. Dadurch muss die Hilfe nicht mehr separat im Python-Code gepflegt werden. Die XML-Datei ist die Quelle der Wahrheit.

## Mapping aus der Flow-XML

Fuer Flow-Konfigurationen mit dem Root-Element `mobertBotConfig` wird die Hilfe aus aktivierten WhatsApp-Command-Flows aufgebaut.

| Angezeigter Hilfeteil | XML-Quelle |
| --- | --- |
| Bot-Praefix | `<modules><whatsappModule><wakeWord><text>` |
| Startbefehl | `<step><input module="whatsapp_watchdog" type="command"><expect><command>` |
| Beschreibung | `<flow><head><description>` |
| Sichtbarkeit | `<flow><head><enabled>true</enabled>` |

Nur aktivierte Flows mit einem WhatsApp-Command-Eingang erscheinen in der normalen Hilfe. MQTT-interne Watchdog-Flows werden nicht angezeigt.

## Beispiel

Aus diesem XML-Auszug:

```xml
<flow id="status">
  <head>
    <name>Status</name>
    <description>Status von Mobert und OpenMower anzeigen.</description>
    <enabled>true</enabled>
  </head>
  <step id="start">
    <input module="whatsapp_watchdog" type="command">
      <expect>
        <command>Status</command>
      </expect>
    </input>
  </step>
</flow>
```

wird dieser Hilfeeintrag:

```text
*Mobert: Status*
Status von Mobert und OpenMower anzeigen.
```

## MQTT-Aktualisierung

Die XML kann per MQTT ersetzt werden:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/xml -f bot_commands.xml
```

Oder vom Datentraeger neu geladen werden:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/renew/json -m '{}'
```

Nach beiden Aktionen werden Befehle, Flows und Hilfe neu aufgebaut. Die erzeugte Hilfe wird retained auf MQTT bereitgestellt:

```bash
mosquitto_sub -h Mosquitto -C 1 -v -t messenger/bot/help/text
mosquitto_sub -h Mosquitto -C 1 -v -t messenger/bot/help/json
```

## Erwartete Hilfeausgabe

```text
*Mobert Hilfe*
────────────

*Mobert: ?*
Hilfe anzeigen.

*Mobert: Status*
Status von Mobert und OpenMower anzeigen.

*Mobert: Start {area}*
Bestimmte Flaeche starten und MQTT-Bestaetigung abwarten.
```

## Hinweise

- Wird ein Flow deaktiviert, verschwindet er nach Reload aus der Hilfe.
- Wird die Beschreibung in der XML geaendert, erscheint der neue Text nach Reload in `Mobert: ?`.
- Wird das Wake Word geaendert, wird der Praefix in der Hilfe entsprechend neu erzeugt.
- Die Reihenfolge folgt der Reihenfolge der aktivierten WhatsApp-Command-Flows in der XML.
