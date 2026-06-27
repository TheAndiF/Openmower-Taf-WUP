# WAHA Session Auto-Repair

The controller can now repair common WAHA session states without requiring shell commands.

## What the controller does

The watchdog and the send path both check the selected WAHA session:

| WAHA status | Controller action |
|---|---|
| `WORKING` | no action, WhatsApp is ready |
| `STOPPED` | call `/api/sessions/<session>/start` when enabled |
| `STARTING` / `OPENING` | wait; after `WAHA_STARTING_TIMEOUT_SECONDS`, call `/restart` |
| `FAILED` / `CRASHED` | call `/restart` |
| `SCAN_QR_CODE` | no automatic restart; publish MQTT status saying manual QR pairing is required |
| `NOT_FOUND` | no restart; publish configuration error |

The controller never calls `docker compose restart`. It only uses the already configured WAHA HTTP API and `WAHA_API_KEY`.

## Environment variables

```env
WAHA_SESSION=Wasserleberweg
WAHA_AUTO_REPAIR_SESSION=true
WAHA_START_STOPPED_SESSION=true
WAHA_STARTING_TIMEOUT_SECONDS=90
WAHA_REPAIR_COOLDOWN_SECONDS=300
WAHA_MAX_RESTARTS_PER_HOUR=3
WAHA_SEND_READY_WAIT_SECONDS=30
WAHA_WATCHDOG_SECONDS=60
```

`WHATSAPP_START_SESSION=${WAHA_SESSION}` should also be passed to the `waha` container. WAHA then tries to start the session when the WAHA API starts.

## MQTT status

The retained repair state is published below:

```text
messenger/waha/session/repair/json
messenger/waha/session/repair/enabled
messenger/waha/session/repair/action
messenger/waha/session/repair/reason
messenger/waha/session/repair/error
```

Example check:

```bash
docker exec -it Mosquitto mosquitto_sub \
  -h localhost \
  -t 'messenger/waha/session/repair/#' \
  -C 5 \
  -v
```

## Manual MQTT actions

Manual start and restart are exposed as generic actions:

```bash
mosquitto_pub -h Mosquitto -t messenger/waha/action -m 'messenger:waha/session/start'
mosquitto_pub -h Mosquitto -t messenger/waha/action -m 'messenger:waha/session/restart'
```

## Notes

If WAHA reports `SCAN_QR_CODE`, the controller cannot repair it automatically. Open the WAHA dashboard and pair WhatsApp again.

If WAHA itself is unreachable, the controller cannot use the WAHA API. Use Docker restart policies or an external host-level watchdog for that case.
