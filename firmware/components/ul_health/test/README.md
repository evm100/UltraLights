# ul_health tests

Encoding is host-tested in `ul_contract/test` (health/typical.json). On target:
the published health message validates against `contracts/schemas/health.schema.json`
(capture it with `mosquitto_sub` and run it through `firmware/tools/check_encoded.py`).
