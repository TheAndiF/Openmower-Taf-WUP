# WAHA QR Code Data via MQTT

## Purpose

When WAHA reports the selected WhatsApp session as `SCAN_QR_CODE` or `QR`, the controller reads the WAHA QR raw value and publishes it to MQTT. A second app on the same device can subscribe to the value and render it again as a WhatsApp pairing QR code.

The controller does not parse the Docker console QR output. It uses the WAHA HTTP API endpoint:

```text
GET /api/{session}/auth/qr?format=raw
```

## MQTT topics

Only these QR data topics are published:

```text
messenger/status/WAHA_QR_Code_Data
messenger/waha/QR_Code_Data
```

`messenger` is the default `MQTT_BASE_TOPIC`. If the base topic is changed, the prefixes change accordingly.

No QR metadata topics such as `Text`, `Status`, `Required`, `Available`, `Session` or `Error` are published. This keeps the status output compact and avoids displaying the long QR payload together with duplicate explanatory fields.

The controller clears retained QR metadata topics from earlier package versions, including:

```text
messenger/status/WAHA_QR_Code_Text
messenger/status/WAHA_QR_Code_Status
messenger/status/WAHA_QR_Code_Required
messenger/status/WAHA_QR_Code_Available
messenger/status/WAHA_QR_Code_Session
messenger/status/WAHA_QR_Code_Error
messenger/waha/QR_Code_Text
messenger/waha/QR_Code_Status
messenger/waha/QR_Code_Required
messenger/waha/QR_Code_Available
messenger/waha/QR_Code_Session
messenger/waha/QR_Code_Error
messenger/waha/session/qr/#
```

## Topic values

| Situation | Data topic value |
|---|---|
| WAHA status is `SCAN_QR_CODE` or `QR` and WAHA returns a value | raw QR pairing value |
| WAHA requires a QR code but the value is not available yet | empty |
| WAHA is `WORKING`, no QR is needed or `WAHA_QR_MQTT_ENABLED=false` | empty |

An empty value means that a consumer should not render a QR code.

## Configuration

```env
WAHA_QR_MQTT_ENABLED=true
WAHA_QR_RAW_RETAIN=false
WAHA_QR_REFRESH_SECONDS=20
```

`WAHA_QR_RAW_RETAIN=false` is the recommended setting. The active QR value can be used to pair WhatsApp while it is valid. For this reason the controller does not retain the active QR value by default. When no QR is needed, it publishes an empty retained value so an old QR value is cleared from the broker.

## Test commands

Subscribe to the WAHA QR data topic:

```bash
mosquitto_sub -h Mosquitto -t 'messenger/waha/QR_Code_Data' -v
```

Subscribe to the status QR data topic:

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
- If WAHA is already connected, the QR data topic is empty.
