# MQTT broker TLS migration

To move UltraLights to an encrypted MQTT deployment (`mqtts`), update both the
server configuration and the Mosquitto broker so that the application can
negotiate TLS 1.2+ with the broker while still allowing older, non-TLS clients
to connect during the transition window.

## Application configuration

Set the following keys in `Server/.env` (or the equivalent environment
variables) so every MQTT client the application creates uses the same TLS
policy:

```dotenv
BROKER_HOST=lights.evm100.org
BROKER_PORT=8883
BROKER_TLS_ENABLED=1
BROKER_TLS_CA_FILE=            # leave empty to rely on system trust store
BROKER_TLS_VERSION=1.2         # pin while migrating; bump to 1.3 once ready
BROKER_TLS_CIPHERS=            # optional OpenSSL cipher list
BROKER_TLS_INSECURE=0          # keep verification enabled in production
```

When using certificates issued by a public CA such as Let's Encrypt, it is safe
to leave `BROKER_TLS_CA_FILE` blank.  If you deploy a private CA, provide the
path to the CA bundle so the clients can validate the broker certificate.

## Mosquitto broker configuration

Starting from the existing `/etc/mosquitto/conf.d/ultralights.conf`, apply the
following adjustments and reload Mosquitto:

```conf
# Require authentication once testing finishes.
allow_anonymous false
password_file /etc/mosquitto/passwd

# TLS listener for UltraLights clients.
listener 8883 0.0.0.0
protocol mqtt

tls_version tlsv1.2
# Provide the full certificate chain and private key.
certfile /etc/mosquitto/certs/fullchain.pem
keyfile  /etc/mosquitto/certs/privkey.pem

# (Optional) Advertise a plaintext listener for legacy devices that
# do not yet support TLS.  Remove once every client speaks mqtts.
listener 1883 0.0.0.0
protocol mqtt
```

Enable `allow_anonymous true` temporarily if you must support unauthenticated
testing, but plan to disable it and use the password file once the migration is
complete.  With this configuration the broker presents the same certificate the
application expects, negotiates TLS 1.2 (matching the default client pinning),
and still allows an unencrypted listener on port 1883 for a short-term
compatibility window.
