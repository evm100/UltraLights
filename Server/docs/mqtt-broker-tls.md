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
BROKER_HOST=lights.evm100.org       # hostname that matches the broker cert
BROKER_CONNECT_HOST=127.0.0.1       # optional: local tunnel/loopback target
BROKER_PORT=8883
BROKER_TLS_ENABLED=1
BROKER_TLS_CA_FILE=            # leave empty to rely on system trust store
BROKER_TLS_SERVERNAME=         # set when connecting via IP but validating a hostname
BROKER_TLS_VERSION=1.2         # pin while migrating; bump to 1.3 once ready
BROKER_TLS_CIPHERS=            # optional OpenSSL cipher list
BROKER_TLS_INSECURE=0          # keep verification enabled in production
```

When using certificates issued by a public CA such as Let's Encrypt, it is safe
to leave `BROKER_TLS_CA_FILE` blank.  If you deploy a private CA, provide the
path to the CA bundle so the clients can validate the broker certificate.

The server now establishes MQTT sessions asynchronously and will keep retrying
in the background while the broker restarts.  The application logs its MQTT
state transitions so you can monitor the cutover from plaintext `mqtt` to
encrypted `mqtts` without dropping motion events or UI commands.

If the broker is reached through an IP address but presents a certificate for a
DNS name, populate `BROKER_TLS_SERVERNAME` with that DNS name. When
`BROKER_CONNECT_HOST` is set, the application still opens the TCP connection
against that address while validating the certificate against the configured
server name, keeping hostname verification enabled without relying on
`/etc/hosts` hacks.

## Mosquitto broker configuration

Starting from the existing `/etc/mosquitto/conf.d/ultralights.conf`, apply the
following adjustments and reload Mosquitto. The first stanza keeps username /
password authentication available while you test TLS connectivity; the second
stanza shows the final mutual-TLS configuration that production deployments
should converge on once every node has a certificate issued by the UltraLights
CA:

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

## Mutual TLS rollout

Once every device runs firmware that supports client certificates you can lock
Mosquitto down to mutual TLS. The high-level steps are:

1. Issue a private certificate authority (CA) dedicated to UltraLights. Store
   the CA key offline and publish the root certificate to the provisioning
   service.
2. Replace the broker listener stanza with one that enforces client
   certificates and references the CA bundle. The configuration below expects
   each certificate's Common Name to equal the UltraLights node ID so Mosquitto
   can map the TLS identity straight to your existing ACL model:

   ```conf
   listener 8883 0.0.0.0
   protocol mqtt

   cafile /etc/mosquitto/certs/ultralights-ca.pem
   certfile /etc/mosquitto/certs/broker-fullchain.pem
   keyfile  /etc/mosquitto/certs/broker-key.pem

   require_certificate true
   use_identity_as_username true
   crlfile /etc/mosquitto/certs/ultralights.crl
   capath /etc/mosquitto/issuers.d
   tls_version tlsv1.2
   ```

   * `require_certificate true` forces every client to present a valid
     certificate before the TLS handshake completes.
   * `cafile` / `capath` point at the private CA chain that signs node
     certificates. Populate `issuers.d` with any intermediate certificates so
     Mosquitto can build the full chain.
   * `use_identity_as_username true` maps the certificate subject (typically the
     Common Name) to the MQTT username. Set the CN equal to the UltraLights node
     ID so existing ACL rules continue to reference the familiar identifier.
   * `crlfile` lets you drop revoked identities without replacing the broker
     certificate; regenerate the CRL whenever you rotate or retire a node and
     reload Mosquitto so the revocation takes effect immediately.

3. Update the server deployment (`Server/.env`) to point
   `BROKER_TLS_CA_FILE` at the new CA and provide the UltraLights CA to the
   provisioning portal so it can sign per-node certificates.

### Issuing node certificates

When a node authenticates during captive portal provisioning the server should
return a JSON payload containing base64-encoded fields `mqtt_client_certificate`
and `mqtt_client_key`. The firmware decodes and persists those blobs in NVS,
injecting them into the MQTT client on startup. Keep the private key encrypted
at rest on the server and only deliver the encrypted blob to the node.

Recommended issuance procedure:

1. Generate a new key pair per node (for example using `openssl ecparam -genkey`)
   and encrypt the private key with an installation-specific wrapping key.
2. Issue a client certificate signed by the UltraLights CA with the node ID as
   the certificate subject/Common Name so the Mosquitto `use_identity_as_username`
   mapping resolves to the node ID automatically.
3. Base64-encode both blobs before returning them through the provisioning API.

### Rotating per-node identities

To rotate a compromised or expiring identity:

1. Revoke the old certificate by adding it to Mosquitto's CRL (`crlfile` in the
   listener configuration) and reloading the broker.
2. Trigger the provisioning portal for the affected node. Once the owner
   re-authenticates, issue a fresh key pair and certificate bundle.
3. The firmware overwrites the stored blobs and will present the new identity on
   the next MQTT reconnect.

Document rotations in the operations log so downstream services know when a
node identity changed and can audit retained messages or ACLs accordingly.
