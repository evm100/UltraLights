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

## Operational notes

- Authentication and administrative actions are persisted to the `audit_logs`
  table. Review these entries when troubleshooting access issues.
- The CLI respects the same SQLModel storage location as the API. Update the
  `AUTH_DB_URL` environment variable or pass `--database-url` to direct it at a
  different SQLite file or database server.
