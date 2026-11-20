# Contracts

The shared language between firmware, server and web. Each schema in `schemas/` describes exactly one message or file, and `examples/` holds valid and invalid instances. Run `python3 contracts/tools/check.py` after every change here, since both sides test against these examples.

Rules:

- A field means the same thing everywhere. If firmware and server disagree, the schema decides, and the schema is fixed first.
- `v: 1` is the format version. Adding an optional field is allowed within v1. Renaming, removing or changing the meaning of a field needs v2.
- Unknown fields are rejected (`additionalProperties: false`), so typos fail loudly in tests.

## Files

| File | What |
|---|---|
| `schemas/common.schema.json` | Shared types: device id, keys, level, kelvin, rgb, src, seq |
| `schemas/board.schema.json` | Board descriptor: channels, pins, reset button |
| `schemas/partition.schema.json` | `ul_board` partition payload: board + provisioning (see `board-partition.md`) |
| `schemas/output.schema.json` | One output built from channels (used by config and info) |
| `schemas/{info,status,config,set,state,live,cmd,evt,health}.schema.json` | MQTT payloads |
| `schemas/enroll-{request,response}.schema.json` | HTTPS enrollment |
| `schemas/firmware-manifest.schema.json` | `manifest.json` inside a firmware package |
| `chips.json` | Per-chip pin rules and limits for the builder and `ul_board` |
| `tools/pins.py` | Reference pin check; `examples/board-rules/` hold expected results |
| `tools/ulboard.py` | Reference encoder/decoder for the `ul_board` blob; `examples/vectors/` hold a byte-exact test vector |

## MQTT

Broker `lights.evm100.org:8883`, TLS 1.2+, mutual TLS. The client certificate CN is the device id, and the broker uses it as the username (`use_identity_as_username`). The client id is also the device id. Keepalive is 30 s and the session is clean.

| Topic | Direction | Retained | QoS | Schema |
|---|---|---|---|---|
| `ul/v1/d/{id}/info` | device → server | yes | 1 | `info` |
| `ul/v1/d/{id}/status` | device → server | yes | 1 | `status` (also the last will) |
| `ul/v1/d/{id}/state/{out}` | device → server | yes | 1 | `state` |
| `ul/v1/d/{id}/evt` | device → server | no | 1 | `evt` |
| `ul/v1/d/{id}/health` | device → server | no | 0 | `health` |
| `ul/v1/d/{id}/config` | server → device | yes | 1 | `config` |
| `ul/v1/d/{id}/set/{out}` | server → device | yes | 1 | `set` |
| `ul/v1/d/{id}/live/{out}` | server → device | no | 0 | `live` |
| `ul/v1/d/{id}/cmd` | server → device | never | 1 | `cmd` |

Broker ACL: a device may write only its own `info`, `status`, `state/+`, `evt` and `health`, and read only its own `config`, `set/+`, `live/+` and `cmd`. The server bridge connects with its own certificate (CN `ul-server`) and can read and write `ul/v1/#`.

### Connection sequence

1. The device connects with the last will set to `status` = `{"online":false}`, retained.
2. It subscribes to `config`, `set/+`, `live/+` and `cmd`.
3. It publishes `status` `{"online":true}`, then `info`, `health`, and `state` for each output.
4. Retained `config` and `set` messages arrive. The device applies config first, so it publishes `info` again if config changed the outputs. Then it applies each `set`.

### Ordering

`seq` is one counter per (device, output), shared by `set` and `live`. The server issues `max(last + 1, unix time in µs)`, so values keep increasing across server restarts. The device drops any `set` or `live` whose `seq` is not greater than the last one it applied to that output. It keeps that value in RAM only, so the first message after boot always applies.

### Live control

- The browser sends at most one update every 50 ms per output, and always sends the final value when the finger lifts.
- The server coalesces per (device, output), never per device (v5 did, and dropped other outputs' commands). It publishes `live` at most 20 Hz per output and then the final value as a retained `set`.
- The device fades with LEDC hardware over `fade_ms` (default 60 ms for `live`).

### Default mapping

With no retained `config`, the device builds one output per channel, keyed by the channel id: `pwm` becomes `white`, `argb` becomes `argb`, `switch` becomes `relay`, `motion` becomes `motion`. A fresh node is therefore controllable before anyone configures it.

## Enrollment

`POST https://{server}/api/v1/enroll`, details in `schemas/enroll-request.schema.json` and `schemas/enroll-response.schema.json`.

- The device generates an EC P-256 key on first boot and stores it in NVS. The key never leaves the chip.
- The CSR's CN is `device_id`.
- The token is single use, tied to one device id, and expires after 7 days.
- Retrying with the same token and the same public key within 24 h returns the same certificate.
- The server's TLS certificate is checked against ESP-IDF's public CA bundle, or against `provision.server_ca` when a dev server needs it.
- If the `device_id` in the partition differs from the certificate's CN (the node was rebuilt as a different device), the device discards its identity and enrolls with the new token.
- Reflashing only the descriptor, with no token, keeps the identity.

## Ownership

`contracts/` changes on `beta` through small reviewed commits. Firmware and server tracks propose a change in their branch, with matching examples, and note it in their summary.
