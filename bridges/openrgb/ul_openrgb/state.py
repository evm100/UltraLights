"""Per-part state: what the server asked for (set/live), and the fade toward it.

Shared between the asyncio side (MQTT) and the source threads (OpenRGB), so every access
goes through Parts.lock.
"""
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .color import BLACK, FRGB, RGB, lerp, target

log = logging.getLogger("ul.openrgb.state")
FIELDS = ("on", "level", "color")


@dataclass
class PartState:
    on: bool = False
    level: float = 1.0
    color: RGB = (255, 255, 255)
    seq: int = 0  # last applied set/live; RAM only, like a node, so the first message after a restart applies
    src: str = "device"
    fade_from: FRGB = BLACK
    fade_to: FRGB = BLACK
    fade_t0: float = 0.0
    fade_s: float = 0.0
    identify_until: float = 0.0

    def shown(self, now: float) -> FRGB:
        """Color the LEDs should have right now."""
        if now < self.identify_until:
            return (255.0, 255.0, 255.0) if int((self.identify_until - now) * 4) % 2 else BLACK
        if self.fade_s <= 0 or now >= self.fade_t0 + self.fade_s:
            return self.fade_to
        return lerp(self.fade_from, self.fade_to, (now - self.fade_t0) / self.fade_s)

    def busy(self, now: float) -> bool:
        return now < self.identify_until or now < self.fade_t0 + self.fade_s

    def report(self) -> dict[str, Any]:
        """state payload (contracts/schemas/state.schema.json)."""
        return {"on": self.on, "level": round(self.level, 4), "color": list(self.color), "src": self.src, "seq": self.seq}


def parse_update(doc: Any) -> dict[str, Any] | None:
    """Known fields of a set/live payload, checked. Unknown fields are ignored (contracts/README:
    devices ignore unknown fields in server-to-device messages). None if seq or a value is bad."""
    if not isinstance(doc, dict) or not isinstance(doc.get("seq"), int) or isinstance(doc.get("seq"), bool):
        return None
    f: dict[str, Any] = {"seq": doc["seq"]}
    if "on" in doc:
        if not isinstance(doc["on"], bool):
            return None
        f["on"] = doc["on"]
    if "level" in doc:
        v = doc["level"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
            return None
        f["level"] = float(v)
    if "color" in doc:
        v = doc["color"]
        if not (isinstance(v, list) and len(v) == 3 and all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 255 for x in v)):
            return None
        f["color"] = (v[0], v[1], v[2])
    if "fade_ms" in doc:
        v = doc["fade_ms"]
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 60000:
            return None
        f["fade_ms"] = v
    if isinstance(doc.get("src"), str):
        f["src"] = doc["src"][:64]
    return f


class Parts:
    """All parts of the PC, keyed like the outputs."""

    def __init__(self, keys: list[str], save_path: Path | None = None, clock=time.monotonic):
        self.lock = threading.Lock()
        self.clock = clock
        self.states: dict[str, PartState] = {k: PartState() for k in keys}
        self.save_path = save_path
        self._load()

    def apply(self, key: str, f: dict[str, Any], default_fade_ms: int) -> bool:
        """Apply a parsed set/live. False if stale (seq not newer) or unknown part."""
        with self.lock:
            s = self.states.get(key)
            if s is None or f["seq"] <= s.seq:
                return False
            now = self.clock()
            start = s.shown(now) if now >= s.identify_until else s.fade_to
            for k in FIELDS:
                if k in f:
                    setattr(s, k, f[k])
            s.seq = f["seq"]
            s.src = f.get("src", "device")
            s.fade_from, s.fade_to = start, target(s.on, s.level, s.color)
            s.fade_t0, s.fade_s = now, f.get("fade_ms", default_fade_ms) / 1000.0
            return True

    def identify(self, keys: list[str], seconds: float) -> None:
        with self.lock:
            until = self.clock() + seconds
            for k in keys:
                if k in self.states:
                    self.states[k].identify_until = until

    def frame(self, keys: list[str]) -> tuple[dict[str, FRGB], bool]:
        """Colors to show now for these parts, and whether any is still moving."""
        with self.lock:
            now = self.clock()
            return ({k: self.states[k].shown(now) for k in keys if k in self.states},
                    any(self.states[k].busy(now) for k in keys if k in self.states))

    def report(self, key: str) -> dict[str, Any]:
        with self.lock:
            return self.states[key].report()

    # ---- last state on disk, so lights come back after a reboot before the broker is reached
    def save(self) -> None:
        if not self.save_path:
            return
        with self.lock:
            data = {k: {"on": s.on, "level": s.level, "color": list(s.color)} for k, s in self.states.items()}
        try:
            tmp = self.save_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(self.save_path)
        except OSError as e:
            log.warning("could not save state: %s", e)

    def _load(self) -> None:
        if not self.save_path or not self.save_path.exists():
            return
        try:
            data = json.loads(self.save_path.read_text())
        except (OSError, ValueError):
            return
        for k, v in data.items():
            s = self.states.get(k)
            if s is None:
                continue
            f = parse_update({**v, "seq": 0}) or {}
            for name in FIELDS:
                if name in f:
                    setattr(s, name, f[name])
            s.fade_to = target(s.on, s.level, s.color)
