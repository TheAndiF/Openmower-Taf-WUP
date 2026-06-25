# MQTT Topics

Default base topic:

```text
messenger
```

The public namespace is provider-aware. The current provider is WAHA:

```text
messenger/status/#       general Messenger status
messenger/waha/#         WAHA-/WhatsApp-specific data and commands
messenger/bot/#          Mobert bot, provider-neutral
```

## Complete topic tree

```text
messenger/
├── status/
│   ├── json
│   ├── online
│   ├── text
│   ├── description
│   ├── provider
│   └── protocol
│
├── waha/
│   ├── json
│   ├── enabled
│   ├── text
│   ├── set/
│   │   ├── session/
│   │   │   └── json
│   │   └── persistent/
│   │       └── json
│   ├── validation/
│   │   └── json
│   │
│   ├── session/
│   │   ├── json
│   │   ├── status
│   │   ├── text
│   │   ├── ready
│   │   ├── can_send
│   │   ├── can_read_groups
│   │   └── last_error
│   │
│   ├── groups/
│   │   ├── json
│   │   ├── count
│   │   ├── default/
│   │   │   ├── alias
│   │   │   └── name
│   │   ├── set/
│   │   │   ├── renew/
│   │   │   │   └── json
│   │   │   └── json
│   │   └── validation/
│   │       └── json
│   │
│   ├── contacts/
│   │   ├── json
│   │   ├── count
│   │   ├── status/
│   │   │   └── json
│   │   ├── set/
│   │   │   ├── renew/
│   │   │   │   └── json
│   │   │   └── json
│   │   └── validation/
│   │       └── json
│   │
│   ├── messages/
│   │   ├── json
│   │   ├── count
│   │   ├── history/
│   │   │   ├── enabled
│   │   │   ├── limit
│   │   │   ├── set/
│   │   │   │   ├── session/
│   │   │   │   │   └── json
│   │   │   │   └── persistent/
│   │   │   │       └── json
│   │   │   └── validation/
│   │   │       └── json
│   │   ├── in/
│   │   │   └── json
│   │   └── out/
│   │       ├── set/
│   │       │   └── json
│   │       └── validation/
│   │           └── json
│   │
│   ├── actions/
│   │   └── json
│   │
│   └── action
│
└── bot/
    ├── json
    ├── enabled
    ├── text
    ├── listener/
    │   ├── json
    │   ├── listening
    │   ├── wake_word
    │   ├── text
    │   ├── provider
    │   └── group/
    │       ├── alias
    │       └── name
    ├── commands/
    │   ├── json
    │   ├── xml
    │   ├── count
    │   ├── version
    │   ├── source
    │   ├── set/
    │   │   ├── xml
    │   │   └── renew/
    │   │       └── json
    │   └── validation/
    │       └── json
    ├── set/
    │   ├── session/
    │   │   └── json
    │   └── persistent/
    │       └── json
    ├── validation/
    │   └── json
    ├── confirmations/
    │   └── pending/
    │       └── json
    └── events/
        └── json
```

## Conventions

```text
*/json                         main JSON snapshot
*/set/renew/json               refresh current data from the backend
*/set/json                     set provider-specific state
*/set/session/json             set live/session state
*/set/persistent/json          persist a setting
*/validation/json              response for set/renew operations
```

JSON snapshots use a `d` wrapper.

## General status

```text
messenger/status/json
messenger/status/online
messenger/status/text
messenger/status/description
messenger/status/provider
messenger/status/protocol
```

`messenger/status/description` is a retained, human-readable, non-secret deployment hint. It includes the internal WAHA API URL, the configured dashboard URL and the host-side `.env` path where dashboard credentials are stored. It never publishes `WAHA_DASHBOARD_PASSWORD` or `WAHA_API_KEY`.

Example:

```json
{
  "d": {
    "online": true,
    "text": "waha WORKING: Mobert lauscht in g014 (OpenMower).",
    "provider": "waha",
    "protocol": "whatsapp",
    "description": {
      "waha_api_url": "http://waha:3000",
      "waha_dashboard_url": "http://<openmower-ip>:9629/dashboard",
      "credentials_file": "/opt/stacks/whatsapp/.env",
      "dashboard_password_variable": "WAHA_DASHBOARD_PASSWORD",
      "api_key_variable": "WAHA_API_KEY",
      "security_note": "Secrets are not published to MQTT. Read them on the host from the .env file."
    }
  }
}
```


## WAHA provider

```text
messenger/waha/json
messenger/waha/enabled
messenger/waha/text
messenger/waha/set/session/json
messenger/waha/set/persistent/json
messenger/waha/validation/json
```

`messenger/waha/json` is the retained provider snapshot. `enabled` controls whether the controller actively uses WAHA. When disabled, the controller does not query WAHA, does not refresh groups, does not send messages and the Bot listener cannot become active.

Enable WAHA live:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/set/session/json -m '{"enabled":true}'
```

Disable WAHA persistently:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/set/persistent/json -m '{"enabled":false}'
```

Validation is published to:

```text
messenger/waha/validation/json
```

## WAHA session

```text
messenger/waha/session/json
```

Contains the WAHA/WhatsApp session status. Mirror topics exist for MQTT Explorer:

```text
messenger/waha/session/status
messenger/waha/session/text
messenger/waha/session/ready
messenger/waha/session/can_send
messenger/waha/session/can_read_groups
messenger/waha/session/last_error
```

## Groups

```text
messenger/waha/groups/json
```

Contains the full group snapshot, including the group list and default target group.

Refresh groups:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/groups/set/renew/json -m '{}'
```

Set default group:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/groups/set/json -m '{"default_group_alias":"g014"}'
```

Validation is published to:

```text
messenger/waha/groups/validation/json
```

## Messages and history

```text
messenger/waha/messages/json
```

Retained snapshot of the last messages in both directions. Default history limit: `10`.

```text
messenger/waha/messages/in/json
```

Live event for each incoming WhatsApp message. Not retained.

```text
messenger/waha/messages/out/set/json
```

Send a message.

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/messages/out/set/json -m '{"request_id":"req-1","target":{"alias":"g014"},"text":"Testnachricht"}'
```

Configure message history live:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/messages/history/set/session/json -m '{"enabled":true,"limit":10}'
```

Configure message history persistently:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/messages/history/set/persistent/json -m '{"enabled":true,"limit":20}'
```

## Bot listener

```text
messenger/bot/listener/json
```

Contains whether Mobert is really listening, the wake word, the provider and the selected listening group.

Mirror topics:

```text
messenger/bot/listener/listening
messenger/bot/listener/wake_word
messenger/bot/listener/text
messenger/bot/listener/provider
messenger/bot/listener/group/alias
messenger/bot/listener/group/name
```

Configure Mobert live:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/set/session/json -m '{"enabled":true,"wake_word":"Mobert","listen_group_alias":"g014"}'
```

Configure Mobert persistently:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/set/persistent/json -m '{"enabled":true,"wake_word":"Mobert","listen_group_alias":"g014"}'
```

## Bot commands XML

The command file is loaded from `/data/bot_commands.xml` and exposed as:

```text
messenger/bot/commands/xml
messenger/bot/commands/json
messenger/bot/commands/count
messenger/bot/commands/version
messenger/bot/commands/source
messenger/bot/commands/validation/json
```

Reload XML commands:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/renew/json -m '{}'
```

The WhatsApp command syntax is intentionally colon-based:

```text
Mobert: Status
Mobert: ?
```

## Mobert XML flow engine

`messenger/bot/commands/#` exposes the loaded XML and the parsed flow command list. The controller accepts both the legacy `<mobertCommands>` format and the new `<mobertBotConfig>` flow format. The new format defines central modules and flow steps:

```text
modules: whatsapp_watchdog, mqtt_watchdog, whatsapp_output, mqtt_output
flow step: input -> processing -> output
```

Replace the XML file at runtime:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/xml -f bot_commands.xml
```

Reload the XML from `/data/bot_commands.xml`:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/commands/set/renew/json -m '{}'
```

Existing Bot settings remain available and override XML defaults where applicable:

```bash
mosquitto_pub -h Mosquitto -t messenger/bot/set/session/json -m '{"enabled":true,"wake_word":"Mobert","listen_group_alias":"g014"}'
mosquitto_pub -h Mosquitto -t messenger/bot/set/persistent/json -m '{"enabled":true,"wake_word":"Mobert","listen_group_alias":"g014"}'
```

MQTT confirmation steps are published while pending under:

```text
messenger/bot/confirmations/pending/json
```
