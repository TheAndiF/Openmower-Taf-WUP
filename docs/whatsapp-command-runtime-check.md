# WhatsApp command runtime check

Date: 2026-07-07

## Cause of the runtime error

The uploaded package used the OpenMower status cache in several WhatsApp command paths, but the cache globals were not initialized in `bridge/controller.py`.

Affected names:

- `OPENMOWER_STATE_UPDATED`
- `OPENMOWER_STATE`
- `MQTT_TOPIC_CACHE`
- `GPS_LOSS_ALERT_ACTIVE`

This could raise `NameError` when `Mobert: Status` waited for fresh MQTT status samples, when `Mobert: MowArea` or `Mobert: GPS` read cached status values, or when an OpenMower MQTT event was processed before the cache existed.

## Fix

`bridge/controller.py` now initializes the runtime cache before the first MQTT or webhook event can use it. Command parameter validation was also added so commands with integer parameters reject invalid values.

## Checked WhatsApp commands

All visible WhatsApp commands defined in `controller_data/bot_commands.json` were checked against the JSON flow executor.

| Command | Flow | Result |
| --- | --- | --- |
| `Mobert: ?` | `help` | OK |
| `Mobert: Status` | `status` | OK after cache initialization fix |
| `Mobert: MowArea` | `mow_area` | OK after cache initialization fix |
| `Mobert: GPS` | `gps_details` | OK after cache initialization fix |
| `Mobert: GPS Status` | `gps_status_details` | OK after cache initialization fix |
| `Mobert: Status alle 10` | `status_push_set` | OK |
| `Mobert: Status automatisch aus` | `status_push_off` | OK |
| `Mobert: Status automatisch` | `status_push_info` | OK |
| `Mobert: Status nach Befehl ein` | `append_status_on` | OK |
| `Mobert: Status nach Befehl aus` | `append_status_off` | OK |
| `Mobert: Status nach Befehl` | `append_status_info` | OK |
| `Mobert: Gruppen` | `groups` | OK |
| `Mobert: Ziel` | `target` | OK |
| `Mobert: Lauschen g014` | `set_listener_group` | OK, if group alias exists |
| `Mobert: Start` | `start_mowing` | OK |
| `Mobert: Pause` | `pause_mowing` | OK |
| `Mobert: Stop` | `stop_mowing` | OK |
| `Mobert: Start 1` | `start_area` | OK, integer area >= 1 required |
| `Mobert: Zeitplan ein` | `schedule_on` | OK |
| `Mobert: Zeitplan aus` | `schedule_off` | OK |

## Additional validation notes

- `Mobert: Start abc` is rejected.
- `Mobert: Start 0` is rejected because the JSON minimum is `1`.
- `Mobert: Status alle abc` is rejected.
- `Mobert: Status alle 0` is rejected because the JSON minimum is `1`.
