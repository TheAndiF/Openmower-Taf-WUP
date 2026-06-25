# OpenMower Deployment

## 1. Update the WhatsApp stack

The `waha` container must be able to call the controller webhook internally:

```yaml
- WHATSAPP_HOOK_URL=http://waha-mqtt-controller:8080/webhook
- WHATSAPP_HOOK_EVENTS=message
```

Do not use `waha_mqtt_controller` in the webhook URL. WAHA validates webhook URLs strictly and rejects hostnames with underscores. The required DNS alias `waha-mqtt-controller` is provided by `compose.override.yaml`.

The controller does not need an external port. It listens only inside the Docker network.

Create or keep the local override file on the OpenMower host:

```yaml
# /opt/stacks/whatsapp/compose.override.yaml
services:
  waha_mqtt_controller:
    networks:
      openmower_default:
        aliases:
          - waha-mqtt-controller

networks:
  openmower_default:
    external: true
```

Check the webhook route from the WAHA container:

```bash
docker exec -it waha sh -c 'wget -S -O- http://waha-mqtt-controller:8080/health; echo EXIT:$?'
```

Expected result: `HTTP/1.0 200 OK` and `EXIT:0`.

## 2. Pull and start

```bash
cd /opt/stacks/whatsapp
docker compose pull
docker compose up -d
```

## 3. Check logs

```bash
docker logs waha_mqtt_controller --tail=100
```

## 4. Check MQTT topics

```bash
docker exec -it Mosquitto mosquitto_sub -h localhost -t 'messenger/#' -v
```

## 5. Refresh groups

```bash
docker exec -it Mosquitto mosquitto_pub \
  -h localhost \
  -t 'messenger/waha/groups/set/renew/json' \
  -m '{}'
```

## 6. Configure default target group

```bash
docker exec -it Mosquitto mosquitto_pub \
  -h localhost \
  -t 'messenger/waha/groups/set/json' \
  -m '{"default_group_alias":"g001"}'
```

## 7. Configure the Mobert listen group

Live until restart:

```bash
docker exec -it Mosquitto mosquitto_pub \
  -h localhost \
  -t 'messenger/bot/set/session/json' \
  -m '{"listen_group_alias":"g001"}'
```

Persistent:

```bash
docker exec -it Mosquitto mosquitto_pub \
  -h localhost \
  -t 'messenger/bot/set/persistent/json' \
  -m '{"enabled":true,"wake_word":"Mobert","listen_group_alias":"g001"}'
```

## 8. Configure message history

The default is the last 10 messages. To set it explicitly:

```bash
docker exec -it Mosquitto mosquitto_pub \
  -h localhost \
  -t 'messenger/waha/messages/history/set/persistent/json' \
  -m '{"enabled":true,"limit":10}'
```

## 9. Reload command XML

```bash
docker exec -it Mosquitto mosquitto_pub \
  -h localhost \
  -t 'messenger/bot/commands/set/renew/json' \
  -m '{}'
```

## 10. Test in WhatsApp

Write this inside the configured listen group:

```text
Mobert: ?
```

The colon is required. The controller should reply with the available commands from `/data/bot_commands.xml`.

## Useful status topics

```text
messenger/status/text
messenger/status/description
messenger/waha/session/text
messenger/waha/groups/count
messenger/waha/groups/default/alias
messenger/waha/groups/default/name
messenger/bot/listener/listening
messenger/bot/listener/group/alias
messenger/bot/listener/group/name
messenger/bot/commands/count
messenger/waha/messages/count
```

## Deployment description status

The controller publishes a non-secret deployment description under:

```text
messenger/status/description
```

The same information is included in `messenger/status/json` under `d.description`. It contains the internal WAHA API URL, the configured dashboard URL and a hint that the dashboard username, dashboard password and API key are stored on the OpenMower host in `/opt/stacks/whatsapp/.env`. The actual password and API key are never published to MQTT.
