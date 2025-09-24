# Opaque Node Identifiers

Nodes created through the admin UI or API now receive a random, opaque identifier
at creation time. The identifier is a 31-character string composed of lowercase
letters and digits (for example `dbrpr89wiexuejce52u9840juec77ul`). It no longer contains
the house slug or any user-provided text, so capturing or guessing a node ID does
not reveal which house owns it.

Alongside the node ID the server issues:

* a unique download identifier used to expose OTA binaries via
  `/firmware/<download_id>/latest.bin`, and
* a per-node bearer token whose SHA-256 hash is stored in the authentication
  database (see [`NodeCredential`](../app/auth/models.py)).

Download identifiers now take advantage of the full 48-character default, while
house external identifiers can stretch to 64 characters. The node ID remains at
31 characters to match the ESP32 firmware limit.

All three values live in the SQLModel database instead of the JSON registry.
`device_registry.json` continues to list houses, rooms and node metadata, but it
no longer contains hashed OTA tokens.

## Provisioning workflow

1. **Create the node in the UI.** When you add a node the admin API stores the
   opaque node ID, download ID and hashed bearer token in the credential table.
   The response includes the download alias so you can verify the record, but the
   plaintext token is only returned via provisioning tools.

2. **Generate firmware defaults with the provisioning CLI.** Use
   [`Server/scripts/provision_node_firmware.py`](../scripts/provision_node_firmware.py)
   to rotate the token, update `sdkconfig`, and manage the firmware symlink in a
   single command:

   ```bash
   python Server/scripts/provision_node_firmware.py provisioned-node \
       --config UltraNodeV5/sdkconfig --rotate-download
   ```

   The command:

   * generates a fresh bearer token and persists its hash,
   * optionally rotates the download alias (use `--rotate-download`),
   * writes `CONFIG_UL_NODE_ID`, `CONFIG_UL_OTA_MANIFEST_URL` and
     `CONFIG_UL_OTA_BEARER_TOKEN` into the selected `sdkconfig` files, and
   * updates the `/srv/firmware/<download_id>` symlink (under the default
     `/srv/firmware/UltraLights` root) to point at the node’s firmware
     directory.


   The plaintext token and manifest URL are printed once so you can archive them
   securely.

   > **Where to store the outputs.** Save the manifest URL, download ID and
   > bearer token in the same vault you use for long-lived service credentials
   > (for example 1Password, Bitwarden, or your infrastructure secrets manager).
   > The API only keeps the SHA-256 hash of the token, so you cannot recover the
   > plaintext later if it is misplaced.

   You will need the manifest URL and bearer token whenever you:

   * patch another `sdkconfig` (or rebuild the firmware) for this node,
   * re-flash hardware after a board replacement, or
   * perform incident response—for example revoking the current token and
     verifying that no other device is still using it.

   Keeping the download ID handy also lets you inspect the corresponding

   firmware folder on disk (`/srv/firmware/UltraLights/<download_id>`) during
   troubleshooting without revealing the node slug. The download directory is a
   symlink that always resolves to the real node folder (for example
   `/srv/firmware/UltraLights/<node-id>`), so nothing is stored separately inside
   the download ID itself.

3. **Build and publish firmware.** After the CLI patches `sdkconfig`, build the
   firmware and place the resulting `latest.bin` into
   `${FIRMWARE_DIR}/<node-id>/latest.bin`. The symlink maintained by the CLI
   exposes the binary through the opaque download alias.

4. **Audit provisioning status.** To see which nodes have already been
   provisioned, run the CLI with `--list`; provisioned entries are marked with an
   asterisk and include the timestamp the firmware was generated.

If you need to regenerate credentials manually, the
[`manage_node_credentials`](../scripts/manage_node_credentials.py) helper still
rotates tokens or download aliases and prints the new values, but the provisioning
CLI is the recommended path because it keeps firmware defaults, symlinks and the
database in sync.

## Why keep download identifiers?

Opaque node IDs removed the original privacy concern—we no longer leak a house
slug through the device identifier—but the dedicated download alias still buys
us a few operational conveniences:

* The provisioning CLI can rotate the externally visible firmware URL by issuing
  a fresh download ID (`--rotate-download`) while leaving `CONFIG_UL_NODE_ID`
  untouched. That lets us retire a leaked manifest URL or move a node’s firmware
  folder without changing the identifier the device uses for MQTT and telemetry.
* Older builds and scripts that were created before the SQLModel migration still
  expect the download alias that lives in the registry. Maintaining the alias
  keeps those installations functional while we roll forward to firmware that
  understands opaque node IDs.
* The alias gives support staff a shareable handle for diagnostics—you can point
  someone at `/firmware/<download_id>/latest.bin` without also disclosing the
  node ID. Because the alias is only a symlink, you can rotate or delete it once
  the troubleshooting session is over.

If these use cases eventually stop mattering we can collapse the indirection and
serve binaries directly from the node ID, but for now the server and tooling are
still built around the download alias abstraction.
