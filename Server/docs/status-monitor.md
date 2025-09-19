# Status Monitor and Module Discovery

The UI now derives each node's module list from live MQTT status payloads. When a
node page is opened the server first checks the cached data collected by
`status_monitor`. If a node has published a full snapshot on
`ul/<node-id>/evt/status` the UI will render the modules reported in that
payload (including the number of strips or channels that were detected). The
legacy registry entries are used only as a fallback when no recent MQTT data is
available. The node detail page shows whether the current metadata came from a
fresh status snapshot or the registry and only offers drop-down options for the
channels that the snapshot confirmed.

Operators should ensure that every controller can answer a status request. The
hub publishes an empty JSON object to `ul/<node-id>/cmd/status` whenever it
needs fresh metadata; the node should reply with the snapshot described in the
firmware documentation. If a node stays silent the UI will continue to show the
static registry configuration and will warn that no modules are available yet.

You can manually trigger a refresh from the admin interface or by using the new
`MqttBus.refresh_capabilities()` helper in a Python shell. It publishes the
status request, waits for a reply, and returns a tuple of the parsed module
metadata plus a boolean indicating whether a fresh payload was observed. The API
layer consumes the cached metadata for validation immediately after each
refresh.
