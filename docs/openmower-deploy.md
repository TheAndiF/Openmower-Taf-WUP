# OpenMower Deployment

## 1. Update the WhatsApp stack

The `waha` container must be able to call the controller webhook internally:

```yaml
- WHATSAPP_HOOK_URL=http://waha_mqtt_controller:8080/webhook
- WHATSAPP_HOOK_EVENTS=message
```

The controller does not need an external port. It listens only inside the Docker network.

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
docker exec -it Mosquitto mosquitto_sub -h localhost -t 'waha/#' -v
```

## 5. Configure default target group

```bash
docker exec -it Mosquitto mosquitto_pub \
  -h localhost \
  -t 'waha/config/default_group/set' \
  -m 'g001'
```

## 6. Configure the Mobert listen group

```bash
docker exec -it Mosquitto mosquitto_pub \
  -h localhost \
  -t 'waha/config/bot/listen_group/set' \
  -m 'g001'
```

## 7. Enable Mobert and set the wake word

```bash
docker exec -it Mosquitto mosquitto_pub \
  -h localhost \
  -t 'waha/config/bot/enabled/set' \
  -m 'true'

 docker exec -it Mosquitto mosquitto_pub \
  -h localhost \
  -t 'waha/config/bot/wake_word/set' \
  -m 'Mobert'
```

## 8. Test in WhatsApp

Write this inside the configured listen group:

```text
Mobert ?
```

The controller should reply with the available commands.
