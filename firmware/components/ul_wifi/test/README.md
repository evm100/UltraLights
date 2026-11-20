# ul_wifi tests

On-target checks still to write:
- with DNS 192.168.1.99 saved, `health.dns` reports it after the first lease and after a DHCP renewal;
- pulling the AP's power gives backoff retries of 1, 2, 4 ... 60 s, and the node rejoins on its own;
- a descriptor with Wi-Fi seeds NVS once; a button Wi-Fi reset afterwards stays reset.
