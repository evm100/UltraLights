# ul_provision tests

On-target checks still to write (phone plus node):
- a fresh node shows `UltraLights-XXXX`; joining it on iOS and Android pops up the page;
- the scan list fills; saving with DNS 192.168.1.99 restarts the node and it joins;
- bad inputs (7-character password, DNS "1.2.3") are refused with a readable message;
- holding the reset button for `hold_ms` forgets Wi-Fi and reopens the page, keeping the identity;
- with a wrong saved password the page reopens after 3 minutes and closes once the station joins.

`form_field` and `html_escape` are pure and could move to a host test if they grow.
