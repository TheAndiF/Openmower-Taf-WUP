# Changelog

## v0.3.3 - 2026-06-25

- Added retained `messenger/status/description` with non-secret deployment hints for MQTT Explorer.
- Added `d.description` to `messenger/status/json` with WAHA API URL, dashboard URL and `.env` credential location hints.
- Documented that `WAHA_DASHBOARD_PASSWORD` and `WAHA_API_KEY` must remain in `/opt/stacks/whatsapp/.env` and are not published to MQTT.
- Added `compose.override.yaml` with the Docker network alias `waha-mqtt-controller` required by the WAHA webhook.
- Updated `compose.example.yaml` to use the valid webhook hostname `waha-mqtt-controller` instead of `waha_mqtt_controller`.
- Added source comments explaining why WAHA must not call a hostname with underscores.
- Updated README and deployment/MQTT documentation for the webhook alias and status description topics.

## v0.3.1 - 2026-06-24

- Added switchable WAHA provider state below `messenger/waha/#`.
- Added `messenger/waha/json`, `messenger/waha/enabled` and `messenger/waha/text`.
- Added `messenger/waha/set/session/json` and `messenger/waha/set/persistent/json` for live or persistent WAHA enable/disable.
- Added `messenger/waha/validation/json` for WAHA setting feedback.
- When WAHA is disabled, the controller does not query WAHA, does not refresh groups, does not send messages and Mobert cannot listen.
- Added messenger actions `messenger:waha/enable` and `messenger:waha/disable`.

## v0.3.0 - 2026-06-24

- Reworked the MQTT namespace from `waha/#` to the provider-aware `messenger/#` tree.
- Added WAHA-specific provider subtree below `messenger/waha/#`.
- Moved Mobert into the provider-neutral `messenger/bot/#` subtree.
- Added `messenger/bot/listener/#` for listening status, wake word and listen group.
- Added XML-based Mobert command module.
- Added default `/data/bot_commands.xml` generation from `bridge/bot_commands.example.xml`.
- Added retained command publications: `messenger/bot/commands/xml`, `messenger/bot/commands/json`, `count`, `version`, `source` and `validation/json`.
- Added configurable message history under `messenger/waha/messages/json`; default history limit is 10 messages.
- Added `messenger/waha/messages/history/set/session/json` and `set/persistent/json`.
- Added OpenMower-style `set/session/json`, `set/persistent/json` and `validation/json` topics for bot settings.
- Enforced the bot command syntax `Mobert: Befehl`; the colon is required.
- Removed the old public legacy topic layout from the controller.


## v0.2.2 - 2026-06-24

- Reworked the Mobert MQTT Explorer status tree to use readable German status topics under `waha/status/bot/#`.
- Added `waha/status/bot/text` as a one-line human-readable summary.
- Moved retained bot enabled config state from `waha/config/bot/enabled` to `waha/config/bot/enabled/value`.
- Clear old retained v0.2.1 bot status/config leaf topics on controller refresh so the MQTT tree no longer shows a value and child topics on the same path.

## v0.2.1 - 2026-06-24

- Added mirrored Mobert listening status under `waha/status/bot/#` for MQTT Explorer.
- Added retained topics for bot enabled state, listening state, listen group, listen group subject and wake word.

## v0.2.0 - 2026-06-24

- Added Mobert WhatsApp bot webhook support.
- Added MQTT-configurable bot listening group.
- Added MQTT-configurable bot wake word and enabled flag.
- Added bot command handling for help, status, groups, target group, listen group, topics and test.
- Added internal HTTP webhook endpoint for WAHA incoming message events.
- Updated Compose example with WAHA webhook environment variables.
- Updated MQTT topic and OpenMower deployment documentation.

## v0.1.0 - 2026-06-24

- Added initial WAHA MQTT controller structure.
- Added retained MQTT status topics.
- Added WAHA session and group discovery.
- Added default group selection via MQTT.
- Added manual WhatsApp sending via MQTT.
