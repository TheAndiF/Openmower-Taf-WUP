# Changelog

## 2026-07-07 - v1.1 - Compact WAHA QR data topics

- Reduced the visible MQTT QR output to the two required data topics: `messenger/status/WAHA_QR_Code_Data` and `messenger/waha/QR_Code_Data`.
- Removed QR text/status/required/available/session/error publications from the status and WAHA topic areas.
- Cleared retained QR metadata topics from earlier package versions so old status entries disappear from MQTT views.
- Kept active QR values non-retained by default and retained empty values when no QR is available.
- Updated README and MQTT documentation for the compact QR handover used by another local app.

## 2026-07-07 - v1.0 - WAHA QR data via MQTT

- Added MQTT publication of WAHA WhatsApp pairing QR raw values while the selected session is in `SCAN_QR_CODE` or `QR`.
- Added requested compact QR data topics `messenger/status/WAHA_QR_Code_Data` and `messenger/waha/QR_Code_Data`.
- Added QR metadata topics for required/available/text/session/status/error below both `messenger/status/` and `messenger/waha/`.
- Added structured QR topics below `messenger/waha/session/qr/#`.
- Added `WAHA_QR_MQTT_ENABLED`, `WAHA_QR_RAW_RETAIN` and `WAHA_QR_REFRESH_SECONDS` environment settings.
- Added the QR MQTT environment settings to `compose.example.yaml`.
- Added documentation for MQTT QR topics, security behavior and terminal QR rendering.


## 2026-07-03 - Status area progress and Auto Mow suspension

- Added a dedicated `mowing_progress` cache for `map/mowing_progress/status/json` and related prefixed topics.
- Changed Status and MowArea to prefer `current_area_id` from `robot_state/json` or mowing-progress payloads before falling back to `checkpoint_area_id`.
- Changed mowing progress to prefer `areas[current_area_id].percent` from `map/mowing_progress/status/json`, with path-level and legacy fallbacks.
- Kept area queue/plan data separate from progress data so progress-only payloads do not overwrite readable area names.
- Updated the WhatsApp status line from `Automatik` to `Auto Mow` and display Auto Mow suspension until a date or as `ausgesetzt unendlich` for year 9999 values.
- Added documentation for the new MowArea and Auto Mow status behavior.


## v0.6.1 - 2026-06-27 - MowArea status progress

- Added the `Mobert: MowArea` command with compact current area values: area name, mowing order, progress, path and path index.
- Changed `Mobert: Status` to show only area name and calculated mowing progress instead of appending `00%` from `current_action_progress`.
- Added Area-Queue cache support for payloads with `area_queue` and `areas` and default subscriptions for common MowArea topics.
- Preferred `checkpoint_area_id` as the active mowing area.
- Prevented append-status suffixes for status-like replies, including `Status`, GPS and `MowArea`, to avoid duplicated output.
- Updated README and MQTT/status documentation.

## v0.6.0 - 2026-06-27 - WAHA session auto-repair

- Added configurable WAHA session selection via `WAHA_SESSION`.
- Added automatic WAHA session repair: start `STOPPED` sessions and restart `FAILED`/`CRASHED` or long-running `STARTING` sessions.
- Added watchdog and send-path readiness checks with cooldown and maximum restarts per hour.
- Added retained MQTT diagnostics under `messenger/waha/session/repair/#`.
- Added manual actions `messenger:waha/session/start` and `messenger:waha/session/restart`.
- Added `WHATSAPP_START_SESSION=${WAHA_SESSION:-}` to the WAHA compose example so WAHA can start the configured session at API startup.
- Updated GPS coordinate extraction to prefer valid `world_pose` WGS84 coordinates for Google Maps and never use local `pose.x/y`.
- Added `docs/waha-session-auto-repair.md`.

## v0.5.0 - 2026-06-26 - WhatsApp status GPS automation

- Extended `Mobert: Status` with AutoMow/Zeitplan information, suspension end time handling, compact GPS readiness and GPS position/map placeholders.
- Added GPS detail submenu commands `Mobert: GPS` and `Mobert: GPS Status` using cached `gps_state/json` diagnostics.
- Added status push commands for automatic status messages every configurable X minutes.
- Added WhatsApp commands to enable, show or disable appending the compact status below normal command confirmations.
- Replaced the old drive-off/charging-finished default notifications with the requested `UNDOCKING` and `DOCKING -> IDLE` notifications.
- Kept emergency notification and added Python-side GPS-loss-while-mowing detection because it requires combined `robot_state` and `gps_state` context.
- Prepared placeholder GPS position topics for a later MQTT interface that provides real WGS84 latitude/longitude values.
- Updated README, MQTT documentation and added `docs/whatsapp-status-gps-automation.md`.

## 2026-06-26 - v1.4 - XML-generated help snapshots

- Rebuilt `Mobert: ?` as an explicit help artifact generated from the active `bot_commands.xml`.
- Mapped visible help entries from enabled WhatsApp command flows using `<expect><command>` as the start command and `<head><description>` as the displayed description.
- Rebuilt the help automatically after MQTT XML replacement (`messenger/bot/commands/set/xml`) and XML reload (`messenger/bot/commands/set/renew/json`).
- Published retained MQTT help snapshots on `messenger/bot/help/text` and `messenger/bot/help/json`.
- Added generated help metadata to `messenger/bot/commands/json` for verification and diagnostics.

## 2026-06-25 - v1.3 - Status formatting and XML-based help

- Reformatted `Mobert: Status` for WhatsApp with bold field labels, a visual title underline and local human-readable time via `STATUS_TIMEZONE`.
- Added active mowing progress behind the area as `(00%)` through `(100%)` using `current_action_progress`.
- Added always-visible `Emergency` and `Fehler` lines to the status reply.
- Reworked `Mobert: ?` so the help reply is generated from the active loaded XML command model. The XML remains the source of truth.
- Added `STATUS_TIMEZONE=Europe/Berlin` to the environment and Compose examples.


## 2026-06-25 - v1.2 - Restrict WiFi cache to data topic

- Changed the default OpenMower status-cache subscriptions from wildcard WiFi filters to concrete `/data` topics: `sensors/om_system_wifi_signal_percent/data` and `openmower/sensors/om_system_wifi_signal_percent/data`.
- Kept robot-state subscriptions on the concrete JSON topics `robot_state/json` and `openmower/robot_state/json`.
- Added numeric validation for WLAN cache updates so binary payloads such as `sensors/om_system_wifi_signal_percent/bson` cannot overwrite the last valid percentage value.
- Documented the WiFi `/data` topic requirement and the updated verification commands.


## 2026-06-25 - v1.1 - OpenMower status cache subscriptions

- Added default MQTT subscriptions for `robot_state/#`, `sensors/om_system_wifi_signal_percent/#`, `openmower/robot_state/#` and `openmower/sensors/om_system_wifi_signal_percent/#`.
- This allows `Mobert: Status` to fill status, area, battery and WLAN even when the mounted `/data/bot_commands.xml` is still in legacy command format and contains no MQTT watchdog flows.
- Reverted the delivered XML topics for the target installation to the unprefixed OpenMower topics shown by live Mosquitto tests: `robot_state/json` and `sensors/om_system_wifi_signal_percent/data`.
- Added documentation for replacing the mounted `controller_data/bot_commands.xml` file after deployment.

## 2026-06-25 - v0.3 - OpenMower MQTT prefix status fix

- Changed active and example Flow XML to use `openmower/` ROS MQTT topics for action, timetable, robot state and WLAN status.
- Kept already-prefixed `openmower/cmd/start_area` topics unchanged to avoid double prefixing.
- Added prefix-tolerant status-cache matching in `bridge/controller.py` so `Mobert: Status` recognizes matching `robot_state/#` and WLAN topics with or without a ROS MQTT prefix.
- Documented the deployment steps and verification commands for the status-topic prefix fix.

## 2026-06-25 - v0.1 - Package hygiene

- Added `.git/` to `.gitignore`.
- Removed local `.git/` metadata from the deliverable package.
- Removed Python cache files and `__pycache__` directories from the deliverable package.
- Kept `controller_data/bot_commands.xml` included intentionally because it is the active Flow XML configuration.
- Added `docs/package-hygiene.md` with cleanup and verification steps.

## 2026-06-25 - Status-Frische, Stop-Befehl und Nachrichtenhistorie

## v0.2 - 2026-06-25 - Include active Flow XML in controller data

- Added `controller_data/bot_commands.xml` with the current Flow XML so deployments no longer fall back to the legacy XML after copying the package.
- Kept `bridge/bot_commands.example.xml` synchronized as the packaged default.
- Documented that existing installations should replace the mounted `/data/bot_commands.xml` or reload it via MQTT.


- Added enabled `Mobert: Stop` command as the start counterpart. It publishes `mower_logic:mowing/abort_mowing` to `action`.
- Removed the placeholder Dock/Home/Docking command from the default flow XML until a confirmed docking MQTT payload exists.
- `Mobert: Status` now waits briefly for fresh `robot_state` and WLAN MQTT samples before sending the WhatsApp reply.
- Status area text now prefers the numeric `current_area`, so mowing area 1 is shown as `Fläche 1` instead of the raw UUID.
- Battery text no longer adds `(lädt nicht)` during mowing; it only appends `(lädt)` while charging.
- Outgoing WAHA webhook echoes are marked as `direction=out` and mirrored to `messenger/waha/messages/out/json`.
- Message history now deduplicates entries by `message_id` when available, while bridge-sent WhatsApp replies continue to be stored as outgoing messages.

# Changelog

## v0.4.2 - 2026-06-25

- Added a central `whatsappModule` XML block for shared WAHA session, group routing and wake-word configuration.
- Changed `whatsapp_watchdog` and `whatsapp_output` to reference the central WhatsApp module through `<moduleRef>whatsapp</moduleRef>`.
- Updated the flow parser to resolve module references while preserving per-module `enabled` states.
- Added MQTT support for selecting the WAHA session through `messenger/waha/set/session/json` and `messenger/waha/set/persistent/json`.

## v0.4.1 - 2026-06-25

- Enabled default ROS MQTT watchdog flows for OpenMower drive-off, charging-finished and emergency/error WhatsApp notifications.
- Added a WLAN cache flow for `sensors/om_system_wifi_signal_percent/data`.
- Extended the Mobert status text with WLAN strength, current area or dock/charging status, MQTT connection and timestamp.
- Documented the ROS MQTT source topics and default WhatsApp notifications.

## v0.4.0 - 2026-06-25

- Added XML-driven Mobert flow architecture with central `whatsapp_watchdog`, `mqtt_watchdog`, `whatsapp_output` and `mqtt_output` modules.
- Replaced the example command XML with the new `mobertBotConfig` flow structure using `head`, `input`, `processing` and `output` blocks.
- Added a compatibility parser for legacy `mobertCommands` XML files.
- Added flow execution for local replies, status, group listing, default target, listener-group changes, MQTT publish outputs and WhatsApp send outputs.
- Added MQTT confirmation handling for flow steps with `mqtt_watchdog` confirmation inputs and timeout outputs.
- Added runtime XML replacement through `messenger/bot/commands/set/xml`.
- Kept existing MQTT settings topics for WAHA, group selection, message history and Bot configuration. Bot MQTT settings override XML defaults where applicable.

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

## 2026-06-25 - Statusanzeige kompakt und ROS-MQTT-Topics verbreitert

- Statusausgabe von `Mobert: Status` auf eine kompakte WhatsApp-Ansicht umgestellt.
- Akku und Ladezustand werden zusammen ausgegeben, z. B. `95 % (lädt)`.
- Dock wird nicht mehr als eigener Status ausgegeben; Laden wird ausschließlich über `is_charging` angezeigt.
- Fehlerzeile erscheint im Status nur noch bei aktivem Emergency-/Notfallzustand.
- ROS-MQTT-Erkennung akzeptiert jetzt `robot_state` sowie `robot_state/#`.
- WLAN-Cache akzeptiert jetzt `sensors/om_system_wifi_signal_percent` sowie Untertopics wie `/data`.
- Standard-WhatsApp-Meldungen für Losfahren, Laden beendet und Fehler wurden auf kompakte Texte angepasst.

## 2026-06-25 - v0.2 - MQTT outgoing message visibility

- Published outgoing WhatsApp messages explicitly on `messenger/waha/messages/out/json`.
- Added retained last-message topics below `messenger/waha/messages/out/last/#` so MQTT tools can show the latest outgoing message without catching the live event.
- Added retained incoming/outgoing history snapshots below `messenger/waha/messages/in/history/json` and `messenger/waha/messages/out/history/json`.
- Replaced ROS MQTT wildcard topics in active and example XML with exact topics:
  - `robot_state/json`
  - `sensors/om_system_wifi_signal_percent/data`
- Adjusted ROS JSON matching to numeric OpenMower values `0`/`1` for `emergency` and `is_charging`.
