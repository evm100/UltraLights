# Deployment and the v5 cutover

The server host runs Ubuntu Server 26.04 LTS and is set up from a clone of this repository. `install.sh` is idempotent and `update.sh` re-runs it after a pull. Neither script ever stops or disables a service it didn't create: v5's API, containers and units are yours to stop. The scripts print the commands for that.

| File | What |
|---|---|
| `install.sh` | Install or update everything. `--check` only reports. |
| `update.sh` | `git pull --ff-only`, then `install.sh --skip-admin` |
| `rollback-v5.sh` | Stop UltraLights, restore the v5 nginx site, free port 8883 |
| `nginx/ultralights.conf` | Site template: SPA, `/api/` proxy (REST + WebSocket), HTTP→HTTPS |
| `mosquitto/ultralights.conf`, `mosquitto/acl` | Broker: mutual TLS on 8883; the certificate CN is the username; each device is limited to its own topics. The dev broker uses the same ACL. |
| `systemd/` | `ultralights-api`, `ultralights-scheduler` (idle placeholder), `ultralights-broker-sync` (+ `.path` watching the CRL) |
| `bin/ultralights-broker-sync` | Copies the Let's Encrypt certificate and the device CA chain/CRL to `/etc/mosquitto/certs/ultralights/`, then restarts Mosquitto if anything changed |
| `tests/container-smoke.sh` | Runs `install.sh` twice in a throwaway `ubuntu:26.04` container and checks the result |

## What `install.sh` sets up

- **Packages:** nginx, certbot, PostgreSQL, Mosquitto, Python venv, Node.js (to build the web UI).
- **System user `ultralights`.** It runs the API and owns `/var/lib/ultralights` (firmware packages) and `/etc/ultralights/pki` (device CA).
- **`/etc/ultralights/ultralights.env`:** settings only. There are no passwords, because PostgreSQL uses peer authentication over its Unix socket.
- **App:** a Python venv at `/usr/local/lib/ultralights/venv`, with the server installed in editable mode from the repo. The repo must be readable by `ultralights`, so use `/opt/ultralights`.
- **Database:** database and role `ultralights`, with migrations applied.
- **Device CA:** created once (`ulctl ca init`), plus the bridge's client certificate and the CRL.
- **Web UI:** built and copied to `/var/www/ultralights`.
- **nginx:** any v5 site for the domain is backed up to `/var/backups/ultralights-v5/nginx/` and disabled. The existing Let's Encrypt certificate is reused; certbot (webroot) runs only if none exists.
- **Mosquitto:**
  - `/etc/mosquitto` is backed up once to `/var/backups/ultralights-v5/mosquitto.tar.gz`.
  - The listener goes in `conf.d/ultralights.conf`, with the broker files synced.
  - The host broker can be **shared** with other projects (on evmserver, NBE has `conf.d/nbe.conf` on 8884). Mosquitto 2.0 allows only one global `acl_file`, so a shared broker needs `per_listener_settings true` (in `mosquitto.conf`, above `include_dir`), and every file's `acl_file`/`allow_anonymous` must come *after* its own `listener` line. Otherwise those options attach to the previous file's listener. The preflight checks both and blocks with the exact edits; it never edits other projects' files.
  - Ubuntu's AppArmor profile for Mosquitto only allows reads from `/etc/mosquitto/*`, `conf.d/`, `certs/` and `ca_certificates/`, so the ACL is `/etc/mosquitto/ultralights.acl` and the TLS files live in `/etc/mosquitto/certs/ultralights/`. A denied read shows up as `Unable to open ...` in `/var/log/mosquitto/mosquitto.log` and as `apparmor="DENIED"` in `journalctl -k`.
  - Restarting Mosquitto briefly disconnects the other projects' clients too. If the broker won't start with our file, `install.sh` moves it to `/var/backups/ultralights-v5/ultralights.conf.rejected` and restarts the broker without it.
  - A certbot deploy hook keeps the broker certificate current.
- **Services:** `ultralights-api` (127.0.0.1:8100; v5 used 8000, so they don't clash) and `ultralights-scheduler`, both enabled and started.
- **Firewall:** if ufw is active, 80, 443 and 8883 are allowed.
- **First admin account:** prompted for once.

The device **root CA key** stays on the server until you move it. The install summary prints the two commands that do this (`ulctl ca export-root`, then `ulctl ca remove-root-key --yes`). The root key is needed only to create a new device CA.

## Cutover weekend runbook

Matches the blueprint's "Cutover from v5". Nothing from v5 is migrated: no users, node configs or certificates. Every node is recreated in the node builder as you inspect it.

1. **Before the weekend:** bench-test on the workstation with `server/tools/dev.sh` (see `server/README.md`). Reflash one C3 and drive it end to end.

2. **Clone** onto the server host:
   ```
   sudo git clone https://github.com/evm100/UltraLights.git /opt/ultralights
   cd /opt/ultralights && sudo git checkout beta
   ```

3. **Preflight.** This changes nothing:
   ```
   sudo ./deploy/install.sh --check
   ```
   It lists:
   - the v5 nginx site for `lights.evm100.org`;
   - whatever holds ports 8883, 8000 and 1883;
   - the v5 Mosquitto container and any v5 units;
   - changes a shared host Mosquitto needs (see above);
   - the commands to stop each of them.

4. **Stop v5 yourself** using the printed commands. Typically:
   - `docker stop <mosquitto> && docker update --restart=no <mosquitto>`
   - stop the old uvicorn on :8000

   Run `--check` again until nothing is blocking.

5. **Install:**
   ```
   sudo ./deploy/install.sh
   ```
   It prompts for your admin username and password at the end.

6. **Network:**
   - The router forwards 443 and 8883 to this host (80 too, for certificate renewal).
   - The LAN DNS at 192.168.1.99 answers `lights.evm100.org` with this host's LAN address.

7. **Sign in** at https://lights.evm100.org, then:
   - add your home and rooms under Admin → Homes & people;
   - upload the firmware packages (Admin → Firmware);
   - recreate each node in the node builder as you reflash it.

8. **Move the root CA key offline:**
   ```
   sudo env UL_ENV_FILE=/etc/ultralights/ultralights.env /usr/local/lib/ultralights/venv/bin/ulctl ca export-root /media/<usb>
   sudo -u ultralights env UL_ENV_FILE=/etc/ultralights/ultralights.env /usr/local/lib/ultralights/venv/bin/ulctl ca remove-root-key --yes
   ```

**Rollback:** `sudo ./deploy/rollback-v5.sh` stops the UltraLights units, restores the backed-up v5 nginx site and removes the 8883 listener. Then start v5 the way you stopped it. Running `install.sh` again switches back.

## Day to day

- **Update:** `sudo ./deploy/update.sh`
- **Logs:** `journalctl -u ultralights-api -f`, `journalctl -u mosquitto -f`
- **Revoking a certificate** in the admin panel rewrites the CRL. `ultralights-broker-sync.path` then copies it and restarts Mosquitto, which reads the CRL only at start. Every node reconnects within a few seconds.
- **Certificate renewal:** certbot's timer renews the certificate, and the deploy hook re-syncs Mosquitto and reloads nginx. If v5 obtained the certificate with certbot's nginx plugin, leave `python3-certbot-nginx` installed; the renewal keeps working against the new site.
