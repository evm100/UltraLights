"""Agent logic without a broker or OpenRGB: config, device assignment, color math, ordering and
fades, the source thread against a simulated OpenRGB, and every outgoing payload against the contract."""
import json
import threading
import time
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ul_openrgb import config as cf
from ul_openrgb.agent import Agent
from ul_openrgb.color import cie, target, to_rgb
from ul_openrgb.sources import Device, SimBackend, SourceStatus, SourceWorker, assign
from ul_openrgb.state import Parts, parse_update

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
_schemas = {p.name.removesuffix(".schema.json"): json.loads(p.read_text()) for p in (CONTRACTS / "schemas").glob("*.schema.json")}
_reg = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in _schemas.values())


def valid(name: str, doc) -> None:
    errs = list(Draft202012Validator(_schemas[name], registry=_reg).iter_errors(doc))
    assert not errs, f"{name}: {errs[0].message} in {doc}"


CONF = """
server = "lights.test"
device_id = "ul-4c2e19"

[sources.pve]
name = "Proxmox host"
host = "sim"

[sources.vm]
name = "GPU VM"
host = "sim"

[parts.gpu]
name = "Graphics card"
source = "vm"
match = ["type:gpu"]

[parts.ram]
name = "Memory"
source = "pve"
match = ["type:dram"]

[parts.fans]
name = "Fans"
source = "pve"
match = ["name:Lighting Node"]

[parts.board]
name = "Motherboard"
source = "pve"
match = ["*"]
"""


def conf(tmp_path: Path) -> cf.Config:
    p = tmp_path / "agent.toml"
    p.write_text(f'state_dir = "{tmp_path / "state"}"\n' + CONF)
    return cf.load(p)


def test_config_parses_and_round_trips(tmp_path):
    c = conf(tmp_path)
    assert list(c.parts) == ["gpu", "ram", "fans", "board"]
    assert c.parts["board"].part == "board" and c.parts["gpu"].source == "vm"
    again = cf.parse(__import__("tomllib").loads(cf.render(c)))
    assert {k: (p.source, p.match, p.name, p.part) for k, p in again.parts.items()} == \
           {k: (p.source, p.match, p.name, p.part) for k, p in c.parts.items()}


@pytest.mark.parametrize("bad, msg", [
    ('[sources.a]\nhost="x"\n[parts.p]\nsource="b"\nmatch=["*"]', "source 'b'"),
    ('[sources.a]\nhost="x"\n[parts.p]\nsource="a"\nmatch=["type:toaster"]', "unknown OpenRGB type"),
    ('[sources.a]\nhost="x"\n[parts.P]\nsource="a"\nmatch=["*"]', "part key"),
    ('device_id="pc"\n[sources.a]\nhost="x"\n[parts.p]\nsource="a"\nmatch=["*"]', "ul-xxxxxx"),
    ('[sources.a]\nhost="x"\n[parts.p]\nsource="a"\nmatch=["*"]\nstate_dir="/tmp"', r"\[parts.p\]: unknown setting"),
])
def test_config_errors(bad, msg):
    with pytest.raises(cf.ConfigError, match=msg):
        cf.parse(__import__("tomllib").loads(bad))


def test_set_device_id_keeps_the_rest(tmp_path):
    p = tmp_path / "agent.toml"
    c = conf(tmp_path)
    c.device_id = ""
    p.write_text(cf.render(c))
    cf.set_device_id(p, "lights.evm100.org", "ul-abcdef")
    c2 = cf.load(p)
    assert (c2.server, c2.device_id, list(c2.parts)) == ("lights.evm100.org", "ul-abcdef", list(c.parts))


def test_first_matching_part_wins(tmp_path):
    c = conf(tmp_path)
    devs = SimBackend().devices
    got = assign(c.parts_on("pve"), devs)
    assert [d.name for d in got["ram"]] == ["Sim DRAM"]
    assert [d.name for d in got["fans"]] == ["Sim Lighting Node"]
    # "*" takes whatever is left on its own source, never another source's devices.
    assert [d.name for d in got["board"]] == ["Sim GPU", "Sim Board"]
    assert [d.name for d in assign(c.parts_on("vm"), devs)["gpu"]] == ["Sim GPU"]


def test_suggestions():
    assert cf.suggest_part("Corsair Lighting Node Pro", "ledstrip")[:3] == ("fans", "fans", "Fans")
    assert cf.suggest_part("Corsair Dominator Platinum", "dram")[3] == "type:dram"
    assert cf.suggest_part("Gigabyte RGB Fusion 2.0 X570 AORUS MASTER", "motherboard")[0] == "board"


def test_color_math():
    assert cie(0) == 0 and cie(1) == pytest.approx(1)
    assert 0.17 < cie(0.5) < 0.2  # perceived half is ~18% luminance
    assert target(False, 1, (255, 0, 0)) == (0, 0, 0)
    assert to_rgb(target(True, 1, (255, 60, 200))) == (255, 60, 200)
    assert to_rgb(target(True, 0.02, (0, 0, 255))) == (0, 0, 1)  # dim but never black


def test_ordering_and_fades(tmp_path):
    now = [100.0]
    p = Parts(["gpu"], clock=lambda: now[0])
    assert p.apply("gpu", parse_update({"on": True, "level": 1.0, "color": [255, 0, 0], "seq": 5, "fade_ms": 1000}), 400)
    assert not p.apply("gpu", parse_update({"on": False, "seq": 5}), 400), "same seq must be dropped"
    assert not p.apply("gpu", parse_update({"on": False, "seq": 4}), 400), "older seq must be dropped"
    frame, busy = p.frame(["gpu"])
    assert busy and frame["gpu"] == (0, 0, 0)
    now[0] += 0.5
    frame, _ = p.frame(["gpu"])
    assert frame["gpu"][0] == pytest.approx(127.5)
    now[0] += 0.6
    frame, busy = p.frame(["gpu"])
    assert not busy and frame["gpu"] == (255, 0, 0)
    # Partial live update keeps the color, changes the level.
    assert p.apply("gpu", parse_update({"level": 0.5, "seq": 6}), 0)
    r = p.report("gpu")
    assert r == {"on": True, "level": 0.5, "color": [255, 0, 0], "src": "device", "seq": 6}
    valid("state", r)


@pytest.mark.parametrize("doc", [
    {"on": True}, {"seq": "1"}, {"seq": 1, "level": 2}, {"seq": 1, "color": [1, 2]}, {"seq": 1, "on": 1},
    {"seq": True},
])
def test_bad_updates_are_refused(doc):
    assert parse_update(doc) is None


def test_unknown_fields_are_ignored():
    assert parse_update({"seq": 3, "on": True, "cct": 3000, "effect": None}) == {"seq": 3, "on": True}


def test_state_survives_restart(tmp_path):
    p = Parts(["gpu"], tmp_path / "s.json")
    p.apply("gpu", parse_update({"on": True, "level": 0.3, "color": [0, 255, 0], "seq": 9}), 0)
    p.save()
    q = Parts(["gpu"], tmp_path / "s.json")
    r = q.report("gpu")
    assert (r["on"], r["level"], r["color"], r["seq"]) == (True, 0.3, [0, 255, 0], 0)


def _wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.01)


def test_source_worker_draws_reconnects_and_reapplies(tmp_path):
    c = conf(tmp_path)
    parts = Parts(list(c.parts))
    sim = SimBackend()
    seen: list[SourceStatus] = []
    lock = threading.Lock()

    def on_status(_k, st):
        with lock:
            seen.append(st)

    w = SourceWorker(c.sources["pve"], c.parts_on("pve"), parts, sim, on_status)
    w.start()
    try:
        _wait(lambda: seen and seen[-1].online)
        assert seen[-1].parts["ram"] == (12, 1) and seen[-1].parts["fans"] == (48, 1)
        parts.apply("ram", parse_update({"on": True, "level": 1.0, "color": [10, 20, 30], "seq": 1, "fade_ms": 0}), 0)
        w.wake()
        _wait(lambda: sim.colors.get("Sim DRAM") == (10, 20, 30))
        assert "Sim GPU" in sim.colors  # matched by the board's "*" on this source
        # The server goes away mid-write: the source reports offline and its parts unreachable.
        sim.up = False
        parts.apply("ram", parse_update({"level": 0.5, "seq": 2, "fade_ms": 0}), 0)
        w.wake()
        _wait(lambda: not seen[-1].online)
        assert seen[-1].parts["ram"][1] == 0
        # Back again (after the 2 s backoff): everything is redrawn, including the change made meanwhile.
        sim.colors.clear()
        sim.up = True
        _wait(lambda: seen[-1].online, timeout=6)
        _wait(lambda: sim.colors.get("Sim DRAM") == to_rgb(target(True, 0.5, (10, 20, 30))))
        assert "Sim Board" in sim.colors
    finally:
        w.stop()
        w.join(3)


def test_info_matches_contract(tmp_path):
    c = conf(tmp_path)
    a = Agent(c, backends={"pve": SimBackend(), "vm": SimBackend()})
    info = a.info()
    valid("info", info)
    assert [o["key"] for o in info["outputs"]] == ["gpu", "ram", "fans", "board"]
    assert not any(o["reachable"] for o in info["outputs"]), "nothing is reachable before the sources connect"
    a.status["pve"] = SourceStatus(online=True, devices=["Sim DRAM"], parts={"ram": (12, 1), "fans": (0, 0), "board": (8, 1)})
    a.status["vm"] = SourceStatus(online=False, error="ConnectionRefusedError: down")
    info = a.info()
    valid("info", info)
    reach = {o["key"]: o["reachable"] for o in info["outputs"]}
    assert reach == {"gpu": False, "ram": True, "fans": False, "board": True}
    assert info["sources"][1]["error"].startswith("ConnectionRefused")
    for ex in ("evt", "status"):
        assert ex in _schemas
    valid("evt", {"type": "cmd_result", "cmd_id": "abcdef12", "ok": False, "error": "unsupported"})
    valid("evt", {"type": "error", "code": "no_such_output", "out": "gpu"})
