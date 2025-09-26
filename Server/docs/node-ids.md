# Opaque Node Identifiers

Node identities are now generated ahead of time and stored in the
`node_registrations` table. Each record reserves an opaque node ID, a firmware
Download ID, a provisioning bearer token (both the plaintext value and its
SHA-256 hash), and a JSON payload for hardware-specific metadata. Registrations
can optionally track which authenticated user or house eventually claims the
identifier, but they can remain unassigned indefinitely so manufacturing teams
can mint identifiers in bulk before any customer data exists.

## Batch pre-registration

Operators create registrations with the
[`Server/scripts/generate_node_ids.py`](../scripts/generate_node_ids.py) helper.
The CLI accepts a count and an optional JSON metadata file:

```bash
python Server/scripts/generate_node_ids.py 25 \
    --metadata-file tooling/batch-metadata.json > new_nodes.json
```

The command initialises the auth database (creating tables if necessary),
persists the requested number of registrations, and writes a machine-readable
summary to stdout (JSON by default, or CSV when `--format csv` is supplied).
Each entry includes the node ID, download ID, plaintext bearer token, hash, and
creation timestamp. The metadata file may contain either a single JSON object
(applied to every generated node) or a list of objects (applied positionally).
Those metadata blobs are stored verbatim in the `hardware_metadata` column so
future tooling—such as a firmware image generator—can inject per-device GPIO or
feature flags.

Because the plaintext token is stored alongside the hash in
`node_registrations.provisioning_token`, the provisioning workflow can retrieve
it later without rotating credentials. Treat the exported JSON/CSV like any
other secret material and store it in your password manager or build system
vault.

To inspect reserved identifiers and their current assignment state, run the
existing provisioning helper in list mode:

```bash
python Server/scripts/provision_node_firmware.py --list
```

The table now shows whether each node is still available, has been claimed for a
house/room, or was already provisioned.

## Provisioning firmware

When it is time to flash a device, call
[`Server/scripts/provision_node_firmware.py`](../scripts/provision_node_firmware.py)
with the pre-generated node ID:

```bash
python Server/scripts/provision_node_firmware.py abcd1234efgh5678 \
    --config UltraNodeV5/sdkconfig
```

The command refuses unknown node IDs and no longer generates new identifiers on
the fly. Instead it reads the download ID, manifest URL, provisioning token, and
metadata from the registration record. The CLI then:

* patches the requested `sdkconfig` files with
  `CONFIG_UL_NODE_ID`, `CONFIG_UL_OTA_MANIFEST_URL`,
  `CONFIG_UL_OTA_BEARER_TOKEN`, and (when metadata is present)
  `CONFIG_UL_NODE_METADATA` containing a compact JSON string,
* ensures the firmware download directory
  `${FIRMWARE_DIR}/<download_id>` exists, and
* updates the database to mark the node as provisioned unless
  `--no-mark-provisioned` is supplied.

`--rotate-download` remains available when you need to retire a compromised
manifest URL, and the command will fall back to `rotate_token` if the stored
plaintext token is missing. The summary printed at the end of the run now
highlights the node's status (available, assigned, or provisioned), current
assignment target, metadata payload, and download directory.

## Assigning registrations

The UI no longer creates nodes directly. The "Add node" button is disabled and
points administrators to the offline provisioning workflow, while the legacy
`/api/house/{house_id}/room/{room_id}/nodes` endpoint returns
`501 Not Implemented`. Future work will introduce an assignment flow that claims
an existing registration for a specific house, user, and room. Until then,
operators can use internal tooling (or direct database access) to populate the
`house_slug`, `room_id`, `assigned_house_id`, and `assigned_user_id` fields once
a device is tied to a customer.

## Why keep download identifiers?

Opaque node IDs removed the original privacy concern—we no longer leak a house
slug through the device identifier—but the dedicated download alias still buys a
few operational conveniences:

* The provisioning CLI can rotate the externally visible firmware URL by issuing
  a fresh download ID (`--rotate-download`) while leaving
  `CONFIG_UL_NODE_ID` untouched. That lets us retire a leaked manifest URL or
  move a node’s firmware folder without changing the identifier the device uses
  for MQTT and telemetry.
* Older builds and scripts created before the SQLModel migration still expect the
  download alias that lives in the registry. Maintaining the alias keeps those
  installations functional while we roll forward to firmware that understands
  opaque node IDs.
* The alias gives support staff a shareable handle for diagnostics—you can point
  someone at `/firmware/<download_id>/latest.bin` without also disclosing the
  node ID. Because the alias maps directly to an on-disk directory, you can
  rotate or delete it once the troubleshooting session is over.

If these use cases eventually stop mattering we can collapse the indirection and
serve binaries directly from the node ID, but for now the server and tooling are
still built around the download alias abstraction.
