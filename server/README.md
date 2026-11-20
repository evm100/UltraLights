# UltraLights server

FastAPI app (`ulserver/`) with:
- PostgreSQL (SQLAlchemy 2 + Alembic);
- the device CA and HTTPS enrollment;
- an MQTT bridge to Mosquitto (mutual TLS);
- a WebSocket for the web UI (`../web`).

Every payload going to or coming from a device is validated against `../contracts/`.

## Layout

| Path | What |
|---|---|
| `ulserver/main.py` | App, startup (bridge task, CRL refresh) |
| `ulserver/api/` | REST: `auth`, `devices` (control), `admin` (builder, provisioning, config, commands, tokens, certs, firmware, users, homes, audit), `enroll_api` (`POST /api/v1/enroll`), `ws` (`/api/ws`) |
| `ulserver/pki.py` | Root and device CA, device and bridge certificates, CRL, dev-only TLS CA |
| `ulserver/enroll.py` | Token issue and contract-exact enrollment (idempotent retry, supersedes old certs) |
| `ulserver/bridge.py` | MQTT bridge: validates inbound messages, stores them, fans them out to browsers |
| `ulserver/control.py`, `realtime.py` | set / live / config / cmd; seq allocator; per-(device, output) coalescer at 20 Hz |
| `ulserver/simdev.py`, `tools/simdevice.py` | Simulated node that speaks the device contract |
| `ulserver/cli.py` | `ulctl` |
| `tools/dev.sh` | Dev stack |
| `tools/smoke.py` | End-to-end check against a running server |

## Dev stack (workstation, bench testing)

Needs Docker and Python 3.12+. It doesn't touch the system Mosquitto on 127.0.0.1:1883.

```
server/tools/dev.sh up       # PostgreSQL :5433, Mosquitto mTLS :8883, nginx :443 on the LAN IP; CA, dev TLS, migrations
server/tools/dev.sh admin    # once: create your admin account
server/tools/dev.sh api      # API on 127.0.0.1:8100 (auto-reload)
server/tools/dev.sh build    # build web/ -> served by the dev nginx at https://ul-dev.lan
server/tools/dev.sh web      # or: Vite dev server at http://localhost:5173 (proxies /api)
```

- **Name.** Nodes reach the dev server by name. Add `ul-dev.lan → <workstation LAN IP>` (192.168.1.41 today) to the LAN DNS at 192.168.1.99. Override with `UL_DEV_HOST` / `UL_DEV_IP`.
- **Dev TLS certificate.** The dev server uses its own TLS CA (`server/.dev/tls/tls-ca.crt`). The node builder automatically adds it to `provision.server_ca`, so flashed nodes trust it.
- **Browser.** To use https://ul-dev.lan, trust that CA in the browser. Otherwise use the Vite server on localhost, where Web Serial flashing also works.

Simulated node, no hardware needed:
- In the admin panel, create a node and press **Download blob**.
- Then run:
  ```
  server/.venv/bin/python server/tools/simdevice.py ~/Downloads/ul_board-ul-xxxxxx.bin --connect 192.168.1.41
  ```
- `--connect` is needed only until `ul-dev.lan` resolves.

End-to-end smoke test against any running server. It creates and deletes a node:

```
UL_SMOKE_PASSWORD=... server/.venv/bin/python server/tools/smoke.py https://ul-dev.lan \
    --connect 192.168.1.41 --ca server/.dev/tls/tls-ca.crt --user <admin>
```

## Tests

```
server/tools/dev.sh test           # pytest; uses database ul_test on the dev PostgreSQL
cd web && npm test                 # pin-rule port and live sender (vitest)
python3 contracts/tools/check.py   # contract examples
deploy/tests/container-smoke.sh    # install.sh in a throwaway ubuntu:26.04 container
```

The bridge integration test runs against the dev broker and is skipped when the broker isn't up. It covers:
- enrollment of a simulated device, then MQTT over mTLS;
- set, live, cmd and config;
- the cross-device ACL;
- rejection of a rogue CA.

## ulctl

```
ulctl db upgrade | create-admin NAME | ca init|status|export-root DEST|remove-root-key --yes|crl
ulctl issue-client CN OUTDIR | dev-tls DIR HOST... | seed
```

Settings come from `UL_*` environment variables or the file named by `UL_ENV_FILE`; see `ulserver/config.py`.
- Production: `/etc/ultralights/ultralights.env`
- Dev: `server/.dev/dev.env`
