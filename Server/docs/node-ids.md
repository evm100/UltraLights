# House-Prefixed Node Identifiers

New nodes added through the admin UI or API automatically inherit a house-based
prefix.  The registry stores the `house_id` and human-readable name you provide,
then uses [`registry.slugify`](../app/registry.py) to lower-case and hyphenate
both values before joining them into a single identifier:

```
<house-slug>-<node-slug>
```

For example, adding the name **Kitchen Node** to the `del-sur` house produces
`del-sur-kitchen-node`.  The hyphenated string is how firmware identifies itself
over MQTT (`ul/<node-id>/...`) and how the OTA server locates binaries, so every
artifact created during provisioning needs to reuse the exact same value.

## Provisioning checklist

1. **Capture the generated node ID.** After adding the node, copy the slugged ID
   shown in the admin UI or the new entry in
   [`Server/app/device_registry.json`](../app/device_registry.json).  The first
   segment always matches the house ID.
2. **Mirror the ID in firmware defaults.** Edit
   [`UltraNodeV5/sdkconfig.defaults`](../../UltraNodeV5/sdkconfig.defaults) (or
   your checked-in `sdkconfig`) so `CONFIG_UL_NODE_ID` contains the same string.
   While editing, also replace the `<node-id>` placeholder in
   `CONFIG_UL_OTA_MANIFEST_URL` with the slug.  Example:
   ```
   CONFIG_UL_NODE_ID="del-sur-kitchen-node"
   CONFIG_UL_OTA_MANIFEST_URL="https://lights.evm100.org/firmware/UltraLights/del-sur-kitchen-node/latest.bin"
   ```
   If you customise settings through `idf.py menuconfig`, make the same edits
   there before building the firmware image.
3. **Publish OTA artifacts under the node ID.** The OTA endpoints resolve
   `latest.bin` using either `/srv/firmware/<node-id>/latest.bin` or the flat
   symlink `/srv/firmware/<node-id>_latest.bin`.  Create one of those paths with
   the freshly built binary so `/firmware/<node-id>/latest.bin` and
   `/api/firmware/v1/manifest?device_id=<node-id>` both succeed.
4. **Keep the manifest consistent.** When mirroring binaries to a CDN or
   generating static manifests, ensure any `device_id` fields, directory names or
   download URLs use the same slug.  Mixing IDs breaks OTA checks and leads to
   orphaned firmware slots on the server.

Following the checklist ensures the node you registered under a house continues
using the same identifier everywhere: the server registry, MQTT topics, firmware
build flags, and OTA distribution.
