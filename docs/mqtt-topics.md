# MQTT Topics

Default base topic:

```text
waha
```

## Published retained status topics

```text
waha/status/online
waha/status/last_update
waha/status/error
waha/session/name
waha/session/status
waha/session/account
waha/groups/list
waha/groups/g001/subject
waha/groups/g001/chatId_masked
waha/groups/g001/selected
waha/groups/g001/bot_listen
waha/config/default_group/value
waha/config/default_group/subject
waha/config/bot/enabled
waha/config/bot/wake_word/value
waha/config/bot/listen_group/value
waha/config/bot/listen_group/subject
waha/config/forward_topics/value
waha/config/templates/value
waha/result/last
waha/error/last
waha/bot/last_command
waha/bot/last_response
waha/bot/last_sender
waha/bot/last_chat
```

## Commands

Refresh WAHA data:

```text
Topic:   waha/cmd/refresh
Payload: 1
```

Set default target group for outgoing messages:

```text
Topic:   waha/config/default_group/set
Payload: g001
```

Set the WhatsApp group where Mobert listens for commands:

```text
Topic:   waha/config/bot/listen_group/set
Payload: g001
```

Enable or disable Mobert:

```text
Topic:   waha/config/bot/enabled/set
Payload: true
```

Set the wake word:

```text
Topic:   waha/config/bot/wake_word/set
Payload: Mobert
```

Send to default group:

```text
Topic:   waha/send
Payload: Test message
```

Send to selected group:

```text
Topic:   waha/send
Payload: {"group":"g001","text":"Test message"}
```

Set forwarded OpenMower topics:

```text
Topic:   waha/config/forward_topics/set
Payload: ["openmower/alerts/#", "openmower/status/error"]
```

Set templates:

```text
Topic:   waha/config/templates/set
Payload: {"openmower/alerts/#":"OpenMower Alarm: {payload}"}
```

## WhatsApp bot commands

The bot reacts only in the MQTT-configured listen group.

```text
Mobert ?
Mobert status
Mobert gruppen
Mobert ziel
Mobert ziel g001
Mobert lauschen
Mobert lauschen g001
Mobert topics
Mobert test
```
