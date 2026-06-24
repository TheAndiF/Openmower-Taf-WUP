# OpenMower Deployment

This guide assumes that WAHA already runs in a separate Dockge stack called `whatsapp` and that the existing OpenMower Mosquitto broker is available in Docker network `openmower_default`.

## 1. Build image through GitHub Actions

Push the repository to GitHub. The workflow `.github/workflows/docker-image.yml` builds and publishes:

```text
ghcr.io/<github-owner>/openmower-taf-wup:latest
```

The image is built for:

```text
linux/amd64
linux/arm64
```

## 2. Use image in `/opt/stacks/whatsapp/compose.yaml`

Add the service below to the existing WhatsApp stack next to the `waha` service:

```yaml
  waha_mqtt_controller:
    image: ghcr.io/DEIN_GITHUB_NAME/openmower-taf-wup:latest
    container_name: waha_mqtt_controller
    restart: unless-stopped
    environment:
      - MQTT_HOST=Mosquitto
      - MQTT_PORT=1883
      - MQTT_BASE_TOPIC=waha
      - WAHA_URL=http://waha:3000
      - WAHA_API_KEY=${WAHA_API_KEY}
      - CONTROLLER_REFRESH_SECONDS=60
      - DATA_DIR=/data
    volumes:
      - ./controller_data:/data
    depends_on:
      - waha
    networks:
      - openmower_default
```

The stack must contain:

```yaml
networks:
  openmower_default:
    external: true
```

## 3. Start

```bash
cd /opt/stacks/whatsapp
docker compose pull
docker compose up -d
docker logs waha_mqtt_controller --tail=100
```

## 4. Test via MQTT

Show WAHA topics:

```bash
docker exec -it Mosquitto mosquitto_sub -h localhost -t 'waha/#' -v
```

Refresh:

```bash
docker exec -it Mosquitto mosquitto_pub -h localhost -t 'waha/cmd/refresh' -m '1'
```

Select default group:

```bash
docker exec -it Mosquitto mosquitto_pub -h localhost -t 'waha/config/default_group/set' -m 'g001'
```

Send test message:

```bash
docker exec -it Mosquitto mosquitto_pub -h localhost -t 'waha/send' -m 'Testnachricht über MQTT'
```
