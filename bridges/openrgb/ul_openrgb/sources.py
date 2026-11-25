"""OpenRGB SDK servers. One thread per source owns its connection, renders the parts that live
on it and reconnects with backoff. A source that's down only takes its own parts with it.

Frames are coalesced: the thread always draws the latest state, so a slow device (SMBus RAM)
drops intermediate slider positions instead of queueing them.
"""
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from .color import RGB, to_rgb
from .config import Part, Source
from .state import Parts

log = logging.getLogger("ul.openrgb.source")
FRAME_S = 1 / 30  # while fading or identifying
PING_S = 10.0  # liveness check while idle
BACKOFF_MAX_S = 30.0


@dataclass
class Device:
    index: int
    name: str
    type: str  # openrgb DeviceType name, lower case
    leds: int


class Backend(Protocol):
    def connect(self) -> list[Device]: ...
    def set_color(self, dev: Device, rgb: RGB) -> None: ...
    def ping(self) -> None: ...
    def close(self) -> None: ...


class OpenRGBBackend:
    """openrgb-python against `openrgb --server` (SDK protocol 4 on OpenRGB 1.0rc2)."""

    def __init__(self, src: Source, client_name: str = "UltraLights"):
        self.src = src
        self.client_name = client_name
        self.client = None
        self.devs: dict[int, object] = {}

    def connect(self) -> list[Device]:
        from openrgb import OpenRGBClient  # imported here so tests and `discover --help` don't need it

        self.client = OpenRGBClient(self.src.host, self.src.port, self.client_name)
        out = []
        for d in self.client.devices:
            self.devs[d.id] = d
            out.append(Device(d.id, d.name, d.type.name.lower(), len(d.leds)))
        return out

    def take(self, dev: Device) -> None:
        """Put a device in its most direct mode (Direct where it has one)."""
        d = self.devs[dev.index]
        try:
            d.set_custom_mode()
        except Exception as e:  # noqa: BLE001 - some controllers refuse; set_color still works in many modes
            log.warning("%s: could not switch to a direct mode: %s", dev.name, e)

    def set_color(self, dev: Device, rgb: RGB) -> None:
        from openrgb.utils import RGBColor

        self.devs[dev.index].set_color(RGBColor(*rgb), fast=True)

    def ping(self) -> None:
        self.client.comms.requestDeviceNum()

    def close(self) -> None:
        try:
            if self.client is not None:
                self.client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self.client = None
        self.devs = {}


class SimBackend:
    """In-memory source for tests and dry runs (host = "sim" in the config)."""

    def __init__(self, devices: list[Device] | None = None):
        self.devices = devices or [Device(0, "Sim GPU", "gpu", 1), Device(1, "Sim DRAM", "dram", 12),
                                   Device(2, "Sim Board", "motherboard", 8), Device(3, "Sim Lighting Node", "ledstrip", 48)]
        self.colors: dict[str, RGB] = {}
        self.writes = 0
        self.up = True

    def connect(self) -> list[Device]:
        if not self.up:
            raise ConnectionRefusedError("sim source is down")
        return list(self.devices)

    def take(self, dev: Device) -> None:
        pass

    def set_color(self, dev: Device, rgb: RGB) -> None:
        if not self.up:
            raise ConnectionResetError("sim source went down")
        self.colors[dev.name] = rgb
        self.writes += 1

    def ping(self) -> None:
        if not self.up:
            raise ConnectionResetError("sim source went down")

    def close(self) -> None:
        pass


@dataclass
class SourceStatus:
    online: bool = False
    devices: list[str] = field(default_factory=list)
    error: str | None = None
    # part key -> (leds, devices found)
    parts: dict[str, tuple[int, int]] = field(default_factory=dict)


def assign(parts: list[Part], devices: list[Device]) -> dict[str, list[Device]]:
    """Each device goes to the first part (in config order) whose matcher fits it."""
    out: dict[str, list[Device]] = {p.key: [] for p in parts}
    for d in devices:
        for p in parts:
            if p.matches(d.name, d.type):
                out[p.key].append(d)
                break
    return out


class SourceWorker(threading.Thread):
    def __init__(self, src: Source, parts: list[Part], state: Parts, backend: Backend,
                 on_status: Callable[[str, SourceStatus], None]):
        super().__init__(name=f"source-{src.key}", daemon=True)
        self.src = src
        self.parts = parts
        self.state = state
        self.backend = backend
        self.on_status = on_status
        self.status = SourceStatus()
        self._wake = threading.Event()
        self._stop = threading.Event()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            try:
                devices = self.backend.connect()
                backoff = 2.0
                self._serve(devices)
            except Exception as e:  # noqa: BLE001 - any failure means: drop the connection and retry
                err = f"{e.__class__.__name__}: {e}"[:200] if str(e) else e.__class__.__name__
                if self.status.online or self.status.error != err:
                    log.warning("%s (%s) unavailable: %s", self.src.key, self.src.address, err)
                self.status = SourceStatus(online=False, error=err,
                                           parts={p.key: (self.status.parts.get(p.key, (0, 0))[0], 0) for p in self.parts})
                self.on_status(self.src.key, self.status)
            finally:
                self.backend.close()
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, BACKOFF_MAX_S)

    def _serve(self, devices: list[Device]) -> None:
        by_part = assign(self.parts, devices)
        for devs in by_part.values():
            for d in devs:
                self.backend.take(d)
        self.status = SourceStatus(online=True, devices=[d.name[:80] for d in devices][:32],
                                   parts={k: (sum(d.leds for d in v), len(v)) for k, v in by_part.items()})
        log.info("%s (%s) connected: %s", self.src.key, self.src.address,
                 ", ".join(f"{k}={len(v)} device(s)" for k, v in by_part.items()))
        unmatched = [d.name for d in devices if not any(d in v for v in by_part.values())]
        if unmatched:
            log.info("%s: not part of any output: %s", self.src.key, ", ".join(unmatched))
        self.on_status(self.src.key, self.status)

        last: dict[int, RGB] = {}  # empty after every (re)connect, so the state is re-applied in full
        keys = [p.key for p in self.parts]
        last_io = time.monotonic()
        while not self._stop.is_set():
            frame, busy = self.state.frame(keys)
            for k, c in frame.items():
                rgb = to_rgb(c)
                for d in by_part.get(k, []):
                    if last.get(d.index) != rgb:
                        self.backend.set_color(d, rgb)
                        last[d.index] = rgb
                        last_io = time.monotonic()
            if busy:
                self._stop.wait(FRAME_S)
                continue
            self._wake.wait(PING_S)
            self._wake.clear()
            if time.monotonic() - last_io >= PING_S:
                self.backend.ping()
                last_io = time.monotonic()


def make_backend(src: Source) -> Backend:
    return SimBackend() if src.host == "sim" else OpenRGBBackend(src)
