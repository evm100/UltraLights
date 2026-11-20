"""Seq ordering, per-output coalescing, and the payloads Control publishes (all schema-checked)."""
import asyncio

import pytest

from ulserver import contracts
from ulserver.control import Control, ControlError, OutputInfo
from ulserver.realtime import Coalescer, SeqAllocator


def test_seq_monotonic_and_survives_restart():
    t = [1_000_000]
    a = SeqAllocator(lambda: t[0])
    k = ("ul-000001", "main")
    s1, s2, s3 = a.next(k), a.next(k), a.next(k)
    assert s1 == 1_000_000 and s2 == s1 + 1 and s3 == s2 + 1  # clock stalled: still increases
    t[0] = 5_000_000
    assert a.next(k) == 5_000_000
    # Restart: a new allocator starts from the clock, above anything issued before.
    assert SeqAllocator(lambda: 5_000_100).next(k) > s3
    # Independent per output.
    assert a.next(("ul-000001", "other")) == 5_000_000


async def test_coalescer_rate_and_trailing_value():
    sent = []

    async def flush(key, payload, src):
        sent.append((asyncio.get_running_loop().time(), key, dict(payload)))

    c = Coalescer(flush, interval=0.05)
    k = ("ul-000001", "main")
    for i in range(20):  # 20 updates in ~200 ms
        c.submit(k, {"level": i / 20}, "ui:1")
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.1)
    await c.drain()
    levels = [p["level"] for _, _, p in sent]
    assert levels[0] == 0.0  # first one immediately
    assert levels[-1] == 19 / 20  # trailing value always delivered
    assert len(sent) <= 6  # ~200 ms at 20 Hz, plus the first
    gaps = [b[0] - a[0] for a, b in zip(sent, sent[1:])]
    assert all(g >= 0.045 for g in gaps)


async def test_coalescer_is_per_output_not_per_device():
    """v5 coalesced per node and dropped the other outputs' commands."""
    sent = []

    async def flush(key, payload, src):
        sent.append((key, payload))

    c = Coalescer(flush, interval=0.05)
    c.submit(("ul-000001", "a"), {"level": 0.1})
    c.submit(("ul-000001", "b"), {"level": 0.2})
    c.submit(("ul-000001", "a"), {"cct": 3000})
    await asyncio.sleep(0.08)
    await c.drain()
    assert (("ul-000001", "b"), {"level": 0.2}) in sent
    a = [p for k, p in sent if k[1] == "a"]
    assert a[-1] == {"cct": 3000} or a[-1] == {"level": 0.1, "cct": 3000}


async def test_coalescer_cancel_drops_pending():
    sent = []

    async def flush(key, payload, src):
        sent.append(payload)

    c = Coalescer(flush, interval=0.05)
    k = ("ul-000001", "main")
    c.submit(k, {"level": 0.1})
    await asyncio.sleep(0.001)
    c.submit(k, {"level": 0.2})  # pending trailing
    c.cancel(k)
    await asyncio.sleep(0.08)
    assert sent == [{"level": 0.1}]


class Capture:
    def __init__(self):
        self.msgs = []

    async def __call__(self, topic, payload, *, schema, qos, retain):
        if payload is not None and schema:
            contracts.validate(schema, payload)  # every outgoing payload must match the contract
        self.msgs.append((topic, payload, qos, retain))


def make_control(cap):
    ctl = Control(cap, sm=None, live_interval=0.02)
    ctl.outputs[("ul-0a1b2c", "main")] = OutputInfo(1, None, "cct", ["onoff", "level", "cct"], {"cct_range": [2700, 6500]})
    ctl.outputs[("ul-0a1b2c", "fan")] = OutputInfo(1, None, "relay", ["onoff"], {})
    ctl.outputs[("ul-0a1b2c", "strip")] = OutputInfo(1, None, "argb", ["onoff", "level", "color"], {"pixels": 60})
    return ctl


async def test_set_payload_full_state_and_retained():
    cap = Capture()
    ctl = make_control(cap)
    p = await ctl.set("ul-0a1b2c", "main", {"level": 0.4, "color": [1, 2, 3]}, "ui:1")
    topic, payload, qos, retain = cap.msgs[-1]
    assert topic == "ul/v1/d/ul-0a1b2c/set/main" and qos == 1 and retain
    assert payload == p and payload["on"] is True and payload["level"] == 0.4 and payload["cct"] == 4600
    assert "color" not in payload  # cct output has no color cap
    p2 = await ctl.set("ul-0a1b2c", "main", {"cct": 9000}, "ui:1")
    assert p2["level"] == 0.4 and p2["cct"] == 6500 and p2["seq"] > p["seq"]  # merged + clamped
    r = await ctl.set("ul-0a1b2c", "fan", {"on": False, "level": 0.3}, "sched:4")
    assert r == {"on": False, "src": "sched:4", "seq": r["seq"]}


async def test_live_then_final_set_supersedes():
    cap = Capture()
    ctl = make_control(cap)
    for i in range(5):
        ctl.live("ul-0a1b2c", "strip", {"level": 0.1 * (i + 1), "on": True}, "ui:1")
    await asyncio.sleep(0.001)
    final = await ctl.set("ul-0a1b2c", "strip", {"level": 0.9}, "ui:1")
    await asyncio.sleep(0.05)
    live = [m for m in cap.msgs if "/live/" in m[0]]
    sets = [m for m in cap.msgs if "/set/" in m[0]]
    assert live and all(m[2] == 0 and not m[3] for m in live)
    assert sets[-1][1] == final and final["level"] == 0.9 and final["on"] is True
    assert all(m[1]["seq"] < final["seq"] for m in live)  # nothing live after the final set


async def test_live_rejects_bad_values_and_unknown_outputs():
    ctl = make_control(Capture())
    with pytest.raises(ControlError) as e:
        ctl.live("ul-0a1b2c", "main", {"level": 2}, "ui:1")
    assert e.value.code == "invalid"
    with pytest.raises(ControlError) as e:
        ctl.live("ul-0a1b2c", "nope", {"level": 0.5}, "ui:1")
    assert e.value.code == "no_such_output"


async def test_cmd_payload():
    cap = Capture()
    ctl = make_control(cap)
    r = await ctl.cmd("ul-0a1b2c", "identify", {"ms": 3000}, "admin:1")
    topic, payload, qos, retain = cap.msgs[-1]
    assert topic == "ul/v1/d/ul-0a1b2c/cmd" and qos == 1 and not retain and payload["id"] == r["id"]
    with pytest.raises(ControlError):
        await ctl.cmd("ul-0a1b2c", "factory_reset", {}, "admin:1")  # needs confirm
