# Opaque Node Identifiers

Nodes created through the admin UI or API now receive a random, opaque identifier
at creation time. The identifier is a 22-character string composed of lowercase
letters and digits (for example `1j1mmiwwxw2eg9jlbmjric`). It no longer contains
the house slug or any user-provided text, so capturing or guessing a node ID does
not reveal which house owns it.

Alongside the node ID the server issues:

* a unique download identifier used to expose OTA binaries via
  `/firmware/<download_id>/latest.bin`, and
* a per-node bearer token whose SHA-256 hash is stored in the authentication
  database (see [`NodeCredential`](../app/auth/models.py)).

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
   * updates the `/srv/firmware/<download_id>` symlink to point at the node’s
     firmware directory.

   The plaintext token and manifest URL are printed once so you can archive them
   securely.

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
