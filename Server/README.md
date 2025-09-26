# UltraLights Server Operations

This directory hosts the FastAPI application that powers the UltraLights hub.
The following sections document operational tooling and configuration that
operators need when provisioning or maintaining an installation.

## Authentication throttling

Login attempts are rate limited to slow down brute-force attacks. The
thresholds are configurable through environment variables:

| Variable | Description | Default |
| --- | --- | --- |
| `LOGIN_ATTEMPT_LIMIT` | Maximum failed attempts permitted before a block is enforced. | `5` |
| `LOGIN_ATTEMPT_WINDOW` | Rolling window, in seconds, used to count failed attempts. | `300` |
| `LOGIN_BACKOFF_SECONDS` | Duration, in seconds, of the backoff applied once the limit is reached. | `900` |

When the limit is exceeded the login form returns HTTP 429 and the audit log
records the event. Successful logins clear the counter for that client.

## Management CLI

The helper script at `Server/scripts/bootstrap_admin.py` exposes a small set of
commands to bootstrap the authentication store. Invoke it with the Python
interpreter, optionally pointing it at an alternate database using
`--database-url`.

### Create or update the server admin

```
python Server/scripts/bootstrap_admin.py create-admin --username admin --password changeme
```

If the user already exists, pass `--force` to rotate the password in place. An
audit log entry is recorded for both creation and password rotation.

### Rotate shared secrets

```
python Server/scripts/bootstrap_admin.py rotate-secrets --env-file /etc/ultralights/.env
```

The command writes new values for `SESSION_SECRET`, `API_BEARER` and
`MANIFEST_HMAC_SECRET` to the specified environment file and logs the rotation.

### Seed sample data

```
python Server/scripts/bootstrap_admin.py seed-sample-data --password demo-pass --prefix demo-
```

This creates demo users (with the provided password and prefix) for every house
discovered in the registry and grants them house-admin privileges. Rerunning the
command is safe; it skips houses that already have a matching demo user.

## MQTT broker configuration

TLS is enabled for MQTT connections by default. Configure the client credentials
and trust material through the following environment variables:

| Variable | Description | Default |
| --- | --- | --- |
| `BROKER_HOST` | MQTT broker hostname. | `lights.evm100.org` |
| `BROKER_PORT` | MQTT broker port. | `8883` |
| `BROKER_USERNAME` | Username used to authenticate with the broker. | empty |
| `BROKER_PASSWORD` | Password paired with `BROKER_USERNAME`. | empty |
| `BROKER_TLS_ENABLED` | Enable MQTT over TLS. Set to `0` to disable. | `1` (unless `EMBED_BROKER=1`) |
| `BROKER_TLS_CA_FILE` | Path to a CA bundle for broker verification. | empty (system defaults) |
| `BROKER_TLS_CERTFILE` | Client certificate for mutual TLS. | empty |
| `BROKER_TLS_KEYFILE` | Private key for the client certificate. | empty |
| `BROKER_TLS_INSECURE` | Accept invalid certificates (development only). | `0` |

When targeting the hosted Mosquitto instance (listener `8883` with TLS enforced)
the `.env` file should provide the following snippet so the server and helper
scripts authenticate correctly:

```
BROKER_HOST=lights.evm100.org
BROKER_PORT=8883
BROKER_USERNAME=uluser
BROKER_PASSWORD=ulpwd
BROKER_TLS_ENABLED=1
BROKER_TLS_INSECURE=0
```

The broker is configured with Let's Encrypt certificates at
`/etc/mosquitto/certs/fullchain.pem` and `privkey.pem` and only accepts TLS 1.2
connections. Clients rely on the system CA bundle, so additional certificate
paths are unnecessary unless a custom trust store is desired.

## Operational notes

- Authentication and administrative actions are persisted to the `audit_logs`
  table. Review these entries when troubleshooting access issues.
- The CLI respects the same SQLModel storage location as the API. Update the
  `AUTH_DB_URL` environment variable or pass `--database-url` to direct it at a
  different SQLite file or database server.
- When launching the app with `runServer.sh`, the script attempts to open a
  gnome-terminal window running `mosquitto_sub` for live MQTT monitoring. If no
  terminal emulator is available, it falls back to a background `mosquitto_sub`
  subscriber so message logging continues without blocking the server startup.
