# Opaque Node Identifiers

Node identities are now generated ahead of time and stored in the
`node_registrations` table. Each record reserves an opaque node ID, a firmware
download ID, the hashed OTA bearer token, and a JSON payload for hardware-
specific metadata. Registrations can optionally track which authenticated user
or house eventually claims the identifier, but they can remain unassigned
indefinitely so manufacturing teams can mint identifiers in bulk before any
customer data exists.

## Batch pre-registration

Operators can mint registrations either from the server-admin "Node factory"
panel or with the
[`Server/scripts/generate_node_ids.py`](../scripts/generate_node_ids.py) helper.
The web UI wraps the same APIs exposed to the CLI: choose the ESP-IDF target
(ESP32, ESP32-C3, or ESP32-S3), enable the strips you plan to populate, specify
GPIO assignments, and supply any configuration overrides that should appear in
`sdkconfig`. When you submit the form the server pre-generates the requested
number of node IDs, records the metadata, and renders a download manifest URL
plus the plaintext OTA token for each identifier. The sidebar tracks which
registrations remain unclaimed so manufacturing can grab the next available
identifier.

The CLI accepts a count and an optional JSON metadata file:

```bash
python Server/scripts/generate_node_ids.py 25 \
    --metadata-file tooling/batch-metadata.json > new_nodes.json
```

The command initialises the auth database (creating tables if necessary),
persists the requested number of registrations, and writes a machine-readable
summary to stdout (JSON by default, or CSV when `--format csv` is supplied).

Each entry includes the node ID, download ID, plaintext bearer token (returned
only in the CLI response), hash, and creation timestamp. The metadata file may
contain either a single JSON object
(applied to every generated node) or a list of objects (applied positionally).
Those metadata blobs are stored verbatim in the `hardware_metadata` column so
future tooling—such as a firmware image generator—can inject per-device GPIO or
feature flags. The Node factory UI simply builds these JSON objects for you.

The plaintext token is **not** stored in the database—only the hash is
persisted—so the exported JSON/CSV is the sole record of the secret. Treat it
like any other credential and keep it in your password manager or build system
vault. If the token is lost you can mint a replacement during provisioning.

To inspect reserved identifiers and their current assignment state, run the
existing provisioning helper in list mode:

```bash
python Server/scripts/provision_node_firmware.py --list
```

The table now shows whether each node is still available, has been claimed for a
house/room, or was already provisioned.

## Provisioning firmware

When it is time to flash a device you have two options:

1. Use the server-admin Node factory to build or flash firmware directly. The
   "Build firmware" action updates an `sdkconfig` snapshot using the stored
   metadata (board type, enabled channels, overrides, etc.) and runs `idf.py`
   with the correct `IDF_TARGET`. The "Build & flash" action performs
   `idf.py -p <port> build flash` so you can program a device connected to the
   server's USB port. Both actions accept an optional OTA token value (so you
   can reuse the pre-generated secret) and otherwise mint a fresh credential
   before writing `CONFIG_UL_NODE_ID`,
   `CONFIG_UL_OTA_MANIFEST_URL`, `CONFIG_UL_OTA_BEARER_TOKEN`, and the compact
   metadata string (`CONFIG_UL_NODE_METADATA`) into the generated `sdkconfig`.
   Results are streamed back to the browser so you can review `idf.py` output
   without leaving the console.
2. Call [`Server/scripts/provision_node_firmware.py`](../scripts/provision_node_firmware.py)
   with the pre-generated node ID when you prefer a CLI workflow:

```bash
python Server/scripts/provision_node_firmware.py abcd1234efgh5678 \
    --config UltraNodeV5/sdkconfig \
    --ota-token $(jq -r '.[0].ota_token' new_nodes.json)
```

Both approaches refuse unknown node IDs and no longer generate new identifiers
on the fly. Instead they read the download ID, manifest URL, and metadata from
the registration record. The CLI expects the pre-generated OTA token (or
`--rotate-token` to mint a replacement) and then:

* patches the requested `sdkconfig` files with
  `CONFIG_UL_NODE_ID`, `CONFIG_UL_OTA_MANIFEST_URL`,
  `CONFIG_UL_OTA_BEARER_TOKEN`, `CONFIG_UL_TARGET_CHIP`, and (when metadata is
  present) `CONFIG_UL_NODE_METADATA` containing a compact JSON string,
* ensures the firmware download directory `${FIRMWARE_DIR}/<download_id>` exists,
  and
* updates the database to mark the node as provisioned unless
  `--no-mark-provisioned` is supplied.

`--rotate-download` remains available when you need to retire a compromised
manifest URL. Use `--rotate-token` to generate a brand new OTA token when the
pre-generated secret is lost or needs to be rotated. The summary printed at the
end of the run (and the Node factory build panel) highlights the node's status
(available, assigned, or provisioned), current assignment target, metadata
payload, and download directory. If the tool encounters a legacy record that
still contains a plaintext token it will consume and erase it, warning you to
switch to the explicit `--ota-token` workflow.

The Node factory also exposes the legacy `UltraNodeV5/updateAllNodes.sh` script
as a single click: enter the firmware version string, press "Run updateAllNodes",
and the server executes the helper, archiving the previous `latest.bin` files and
rolling manifests just like the original shell script. Output and return codes
are streamed to the browser for easy auditing, and an audit log entry records who
triggered the rotation.

## End-to-end workflow

Putting the pieces together, an end-to-end bring-up session typically looks like
this:

1. **Generate a batch of identifiers.** An operator calls
   `Server/scripts/generate_node_ids.py <count>` (or uses the Node factory
   "Generate IDs" dialog) to mint the desired number of opaque node IDs. The
   command prints JSON/CSV that includes the plaintext bearer tokens—store that
   output securely because it will not be shown again.
2. **Stage hardware metadata.** If you already know which GPIO channels, enable
   lines, or other hardware toggles belong to each physical device, embed that
   information in the metadata column while generating the IDs. Otherwise you can
   leave the metadata blank and attach it later from the admin tools before
   building firmware.
3. **Build the firmware image.** When you are ready to program a specific board,
   choose one of the pre-generated registrations in the Node factory UI (or pass
   its node ID to `provision_node_firmware.py`). The tooling patches
   `sdkconfig` with the stored metadata, target chip, manifest URL, and bearer
   token so the compiled binary already knows how to authenticate.
4. **Flash the ESP32.** Use the "Build & flash" control from the Node factory or
   run `idf.py -p <port> build flash` manually against the generated configuration.
   For brand-new boards you can also invoke the `firstTimeFlash` helper exposed in
   the admin tooling to automate the initial erase/build/flash sequence.
5. **Hand the device to the installer or customer.** On first boot the firmware
   opens the captive portal (SoftAP). The user connects to the UltraLights access
   point, submits their Wi-Fi SSID/password, and provides their UltraLights
   account credentials (or the mutually agreed token). These inputs are stored in
   NVS alongside the node ID metadata.
6. **Automatic account association.** After the portal closes the node connects
   to the broker via MQTTS using the captured UltraLights credentials. It publishes
   the `ul/<node_id>/evt/account` event, which the server validates against the
   `node_registrations` table and the user directory. When the credentials match,
   the node registration is marked as assigned to the authenticated user and the
   user’s house. Future room-assignment tooling will then let house administrators
   place the node into a specific room menu.

The `updateAllNodes.sh` wrapper remains available for fleet-wide OTA refreshes
after the initial provisioning. Because the registrations already contain the
download ID and metadata, the update script can build new images for every node
without regenerating identifiers or touching customer associations.

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
