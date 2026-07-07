# WAHA QR Code Data via MQTT

## Purpose

When WAHA reports the selected WhatsApp session as `SCAN_QR_CODE` or `QR`, the controller reads the WAHA QR raw value and publishes it to MQTT. A consuming UI can render this value again as a QR code.

The controller does not parse the Docker console QR output. It uses the WAHA HTTP API endpoint:

```text
GET /api/{session}/auth/qr?format=raw
```

## MQTT topics

The requested compact topics are:

```text
messenger/status/WAHA_QR_Code_Data
messenger/waha/QR_Code_Data
```

`messenger` is the default `MQTT_BASE_TOPIC`. If the base topic is changed, the prefixes change accordingly.

Additional status topics:

```text
messenger/status/WAHA_QR_Code_Required
messenger/status/WAHA_QR_Code_Available
messenger/status/WAHA_QR_Code_Text
messenger/status/WAHA_QR_Code_Session
messenger/status/WAHA_QR_Code_Status
messenger/status/WAHA_QR_Code_Error

messenger/waha/QR_Code_Required
messenger/waha/QR_Code_Available
messenger/waha/QR_Code_Text
messenger/waha/QR_Code_Session
messenger/waha/QR_Code_Status
messenger/waha/QR_Code_Error

messenger/waha/session/qr/raw
messenger/waha/session/qr/json
messenger/waha/session/qr/required
messenger/waha/session/qr/available
messenger/waha/session/qr/session
messenger/waha/session/qr/status
messenger/waha/session/qr/text
messenger/waha/session/qr/error
messenger/waha/session/qr/last_update
```

## Topic values

| Situation | Data topic value | Required | Available | Text |
|---|---|---:|---:|---|
| WAHA status is `SCAN_QR_CODE` or `QR` and WAHA returns a value | raw QR pairing value | `true` | `true` | `QR-Code zum Koppeln erforderlich` |
| WAHA requires a QR code but the value is not available yet | empty | `true` | `false` | `QR-Code erforderlich, aber noch nicht verfügbar` |
| WAHA is `WORKING` or no QR is needed | empty | `false` | `false` | `Kein QR-Code erforderlich` |
| `WAHA_QR_MQTT_ENABLED=false` | empty | `false` | `false` | `QR-MQTT-Ausgabe deaktiviert` |

## Configuration

```env
WAHA_QR_MQTT_ENABLED=true
WAHA_QR_RAW_RETAIN=false
WAHA_QR_REFRESH_SECONDS=20
```

`WAHA_QR_RAW_RETAIN=false` is the recommended setting. The active QR value can be used to pair WhatsApp while it is valid. For this reason the controller does not retain the active QR value by default. When no QR is needed, it publishes an empty retained value so an old QR value is cleared from the broker.

## Test commands

Subscribe to the requested WAHA topic:

```bash
mosquitto_sub -h Mosquitto -t 'messenger/waha/QR_Code_Data' -v
```

Subscribe to the requested status topic:

```bash
mosquitto_sub -h Mosquitto -t 'messenger/status/WAHA_QR_Code_Data' -v
```

Render the QR in a terminal:

```bash
mosquitto_sub -h Mosquitto -t 'messenger/waha/QR_Code_Data' | while IFS= read -r QR; do
  clear
  if [ -n "$QR" ]; then
    echo "WhatsApp QR-Code scannen"
    qrencode -t ANSIUTF8 "$QR"
  else
    echo "Kein QR-Code erforderlich."
  fi
done
```

## Operational notes

- The selected WAHA session is taken from `WAHA_SESSION`, `WHATSAPP_SESSION`, `WAHA_DEFAULT_SESSION` or the XML/default configuration.
- The QR loop refreshes the MQTT QR value every `WAHA_QR_REFRESH_SECONDS` seconds.
- The general controller refresh loop still publishes the full state using `CONTROLLER_REFRESH_SECONDS`.
- `compose.example.yaml` includes the QR MQTT environment variables for the controller service.
- If WAHA is already connected, the QR data topic is empty and `Required=false`.
