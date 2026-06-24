# MQTT Topics

Default base topic:

```text
waha
```

## Published retained status topics

| Topic | Payload | Description |
|---|---|---|
| `waha/status/online` | `true` / `false` | Controller availability. Retained MQTT LWT is used. |
| `waha/status/last_update` | ISO timestamp | Last successful WAHA refresh. |
| `waha/status/error` | Text | Last status refresh error, empty if OK. |
| `waha/sessions/list` | JSON | All sessions as detected by WAHA. |
| `waha/session/name` | Text | Selected active session name. |
| `waha/session/status` | Text | Selected active session status. |
| `waha/session/account_masked` | Text | Masked connected WhatsApp account. |
| `waha/groups/list` | JSON | All groups with aliases and masked chat IDs. |
| `waha/groups/<key>/subject` | Text | Group name from `groupMetadata.subject`. |
| `waha/groups/<key>/chatId_masked` | Text | Masked internal WhatsApp group ID. |
| `waha/groups/<key>/selected` | `true` / `false` | Whether this group is the default target. |
| `waha/config/default_group/value` | Text | Current default group alias, for example `g001`. |
| `waha/config/default_group/subject` | Text | Human-readable subject of selected default group. |
| `waha/config/forward_topics/value` | JSON | List of MQTT topic filters to forward. |
| `waha/config/templates/value` | JSON | Message templates for forwarded topics. |
| `waha/result/last` | JSON | Last successful send result. |
| `waha/error/last` | JSON | Last command error. |

## Command topics

### Refresh WAHA data

```text
Topic:   waha/cmd/refresh
Payload: 1
```

### Set default group

Payload may be a group alias, subject, or real chat ID.

```text
Topic:   waha/config/default_group/set
Payload: g001
```

### Send to default group

```text
Topic:   waha/send
Payload: Test message
```

### Send to selected group

```text
Topic:   waha/send
Payload: {"group":"g001","text":"Test message"}
```

### Configure forwarded OpenMower topics

```text
Topic:   waha/config/forward_topics/set
Payload: ["openmower/alerts/#", "openmower/status/error"]
```

### Configure templates

```text
Topic:   waha/config/templates/set
Payload: {"openmower/alerts/#":"OpenMower Alarm: {payload}"}
```

## Template placeholders

Available generic placeholders:

```text
{topic}
{payload}
```

If the MQTT payload is JSON and contains object fields, those fields can also be used directly. Example:

```json
{"percent": 18}
```

Template:

```text
OpenMower Akku: {percent} %
```
