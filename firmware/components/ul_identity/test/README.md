# ul_identity tests

The request/response encoding is host-tested in `ul_contract/test`.

On-target checks still to write (against the dev server):
- first boot: key generated once (boot log shows the time), CSR subject CN = device_id,
  enrollment returns 200 and the chain is saved;
- reboot: no new key and no new enrollment;
- kill the network mid-response: the retry with the same token gets the same certificate;
- spent or wrong token: one 4xx, `UL_EV_IDENTITY_FAILED`, no retry loop;
- reflash the descriptor with another device_id and a new token: old identity dropped, new one enrolled.
