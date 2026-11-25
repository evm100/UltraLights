"""Agent configuration (/etc/ul-openrgb/agent.toml).

    server = "lights.evm100.org"          # enrollment (HTTPS) and broker (MQTT 8883)
    device_id = "ul-4c2e19"               # written by `ul-openrgb enroll`

    [sources.pve]                         # one OpenRGB SDK server per machine
    name = "Proxmox host"
    host = "127.0.0.1"

    [sources.vm]
    name = "GPU VM"
    host = "192.168.1.41"

    [parts.gpu]                           # one UltraLights output per part
    name = "Graphics card"
    source = "vm"
    match = ["type:gpu"]                  # type:<openrgb type>, name:<substring>, or "*"

Parts are listed in the order the UI shows them. A device belongs to the first part on its
source whose matcher fits it; devices no part matches are left alone.
"""
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG = Path("/etc/ul-openrgb/agent.toml")
DEFAULT_STATE_DIR = Path("/var/lib/ul-openrgb")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
DEVICE_ID_RE = re.compile(r"^ul-[0-9a-f]{6}$")
PARTS = ("gpu", "ram", "board", "fans", "cooler", "strip", "keyboard", "mouse", "headset", "case", "other")
# OpenRGB device types (openrgb.utils.DeviceType names, lower case).
TYPES = ("motherboard", "dram", "gpu", "cooler", "ledstrip", "keyboard", "mouse", "mousemat", "headset",
         "headset_stand", "gamepad", "light", "speaker", "virtual", "storage", "case", "microphone",
         "accessory", "keypad", "unknown")


class ConfigError(ValueError):
    pass


@dataclass
class Source:
    key: str
    host: str
    port: int = 6742
    name: str = ""

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass
class Part:
    key: str
    source: str
    match: list[str]
    name: str = ""
    part: str = "other"

    def matches(self, dev_name: str, dev_type: str) -> bool:
        for m in self.match:
            if m == "*":
                return True
            kind, _, val = m.partition(":")
            if kind == "type" and val.lower() == dev_type.lower():
                return True
            if kind == "name" and val.lower() in dev_name.lower():
                return True
        return False


@dataclass
class Config:
    server: str = ""
    device_id: str = ""
    server_ca: str | None = None  # PEM file; only for a dev server with its own TLS CA
    connect: str | None = None  # dial this address but check the certificate against `server` (dev)
    https_port: int = 443
    mqtt_port: int = 8883
    state_dir: Path = DEFAULT_STATE_DIR
    sources: dict[str, Source] = field(default_factory=dict)
    parts: dict[str, Part] = field(default_factory=dict)
    path: Path | None = None

    @property
    def key_file(self) -> Path:
        return self.state_dir / "device.key"

    @property
    def cert_file(self) -> Path:
        return self.state_dir / "device.crt"

    def parts_on(self, source: str) -> list[Part]:
        return [p for p in self.parts.values() if p.source == source]


def load(path: Path = DEFAULT_CONFIG) -> Config:
    try:
        raw = tomllib.loads(Path(path).read_text())
    except FileNotFoundError:
        raise ConfigError(f"{path} not found; run `ul-openrgb discover --write` first") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None
    return parse(raw, Path(path))


TOP_KEYS = {"server", "device_id", "server_ca", "connect", "https_port", "mqtt_port", "state_dir", "sources", "parts"}
SOURCE_KEYS = {"host", "port", "name"}
PART_KEYS = {"name", "source", "match", "part"}


def _unknown(where: str, got: dict, allowed: set[str]) -> None:
    extra = sorted(set(got) - allowed)
    if extra:
        raise ConfigError(f"{where}: unknown setting(s) {', '.join(extra)} (allowed: {', '.join(sorted(allowed))})")


def parse(raw: dict, path: Path | None = None) -> Config:
    _unknown("top level", raw, TOP_KEYS)
    c = Config(path=path)
    c.server = str(raw.get("server", ""))
    c.device_id = str(raw.get("device_id", ""))
    c.server_ca = raw.get("server_ca") or None
    c.connect = raw.get("connect") or None
    c.https_port = int(raw.get("https_port", 443))
    c.mqtt_port = int(raw.get("mqtt_port", 8883))
    if "state_dir" in raw:
        c.state_dir = Path(raw["state_dir"])
    if c.device_id and not DEVICE_ID_RE.match(c.device_id):
        raise ConfigError(f"device_id {c.device_id!r} is not ul-xxxxxx")
    for key, s in (raw.get("sources") or {}).items():
        if not KEY_RE.match(key):
            raise ConfigError(f"source key {key!r}: use a-z, 0-9, _ (max 16)")
        _unknown(f"[sources.{key}]", s, SOURCE_KEYS)
        if "host" not in s:
            raise ConfigError(f"source {key}: host is required")
        c.sources[key] = Source(key=key, host=str(s["host"]), port=int(s.get("port", 6742)), name=str(s.get("name", ""))[:48])
    if not c.sources:
        raise ConfigError("no [sources.*] configured")
    for key, p in (raw.get("parts") or {}).items():
        if not KEY_RE.match(key):
            raise ConfigError(f"part key {key!r}: use a-z, 0-9, _ (max 16)")
        _unknown(f"[parts.{key}]", p, PART_KEYS)
        src = p.get("source")
        if src not in c.sources:
            raise ConfigError(f"part {key}: source {src!r} is not one of {sorted(c.sources)}")
        match = p.get("match", [])
        if isinstance(match, str):
            match = [match]
        if not match:
            raise ConfigError(f"part {key}: match is empty")
        for m in match:
            kind, _, val = m.partition(":")
            if m != "*" and (kind not in ("type", "name") or not val):
                raise ConfigError(f"part {key}: matcher {m!r} must be type:<t>, name:<text> or *")
            if kind == "type" and val.lower() not in TYPES:
                raise ConfigError(f"part {key}: unknown OpenRGB type {val!r} (one of {', '.join(TYPES)})")
        kind = p.get("part", key if key in PARTS else "other")
        if kind not in PARTS:
            raise ConfigError(f"part {key}: part = {kind!r} must be one of {', '.join(PARTS)}")
        c.parts[key] = Part(key=key, source=src, match=list(match), name=str(p.get("name", ""))[:48] or key, part=kind)
    if not c.parts:
        raise ConfigError("no [parts.*] configured")
    if len(c.parts) > 16:
        raise ConfigError("at most 16 parts")
    return c


# ---------------------------------------------------------------- writing (discover, enroll)
# Part suggested for each OpenRGB device type: (key, part, name).
SUGGEST = {
    "gpu": ("gpu", "gpu", "Graphics card"),
    "dram": ("ram", "ram", "Memory"),
    "motherboard": ("board", "board", "Motherboard"),
    "cooler": ("cooler", "cooler", "Cooler"),
    "ledstrip": ("strip", "strip", "Light strip"),
    "keyboard": ("keyboard", "keyboard", "Keyboard"),
    "mouse": ("mouse", "mouse", "Mouse"),
    "headset": ("headset", "headset", "Headset"),
    "case": ("case", "case", "Case"),
}
FAN_CONTROLLERS = ("lighting node", "commander", "fan", "ll120", "ql120", "sl120", "nzxt rgb")


def suggest_part(dev_name: str, dev_type: str) -> tuple[str, str, str, str]:
    """(key, part, name, matcher) for a discovered device."""
    t = dev_type.lower()
    if any(f in dev_name.lower() for f in FAN_CONTROLLERS):
        return "fans", "fans", "Fans", f"name:{_short(dev_name)}"
    key, part, name = SUGGEST.get(t, (re.sub(r"[^a-z0-9_]", "_", t)[:16] or "other", "other", dev_name[:48]))
    return key, part, name, f"type:{t}"


def _short(name: str) -> str:
    """A distinctive substring of a device name for a name: matcher."""
    words = name.split()
    return " ".join(words[:3]) if len(words) > 3 else name


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(c: Config) -> str:
    out = ["# UltraLights OpenRGB agent. Docs: bridges/openrgb/README.md in the UltraLights repo.",
           "# After editing: systemctl restart ul-openrgb", ""]
    out.append(f"server = {_q(c.server)}")
    out.append(f"device_id = {_q(c.device_id)}" if c.device_id else '# device_id is written by `ul-openrgb enroll`')
    if c.server_ca:
        out.append(f"server_ca = {_q(c.server_ca)}")
    if c.connect:
        out.append(f"connect = {_q(c.connect)}")
    if c.https_port != 443:
        out.append(f"https_port = {c.https_port}")
    if c.mqtt_port != 8883:
        out.append(f"mqtt_port = {c.mqtt_port}")
    if c.state_dir != DEFAULT_STATE_DIR:
        out.append(f"state_dir = {_q(str(c.state_dir))}")
    out.append("")
    out.append("# One OpenRGB SDK server per machine. Each runs `openrgb --server` for the hardware it can see.")
    for s in c.sources.values():
        out += [f"[sources.{s.key}]", f"name = {_q(s.name or s.key)}", f"host = {_q(s.host)}"]
        if s.port != 6742:
            out.append(f"port = {s.port}")
        out.append("")
    out.append("# One UltraLights output per part, in the order the UI lists them.")
    out.append("# match: type:<openrgb type>, name:<part of the device name>, or \"*\" for everything on the source.")
    for p in c.parts.values():
        out += [f"[parts.{p.key}]", f"name = {_q(p.name)}", f"part = {_q(p.part)}", f"source = {_q(p.source)}",
                "match = [" + ", ".join(_q(m) for m in p.match) + "]", ""]
    return "\n".join(out)


def set_device_id(path: Path, server: str, device_id: str) -> None:
    """Write server and device_id into an existing config without touching the rest."""
    text = path.read_text()
    for k, v in (("server", server), ("device_id", device_id)):
        line = f"{k} = {_q(v)}"
        pat = re.compile(rf"^#?\s*{k}\b.*$", re.M)
        if pat.search(text):
            text = pat.sub(line, text, count=1)
        else:
            text = line + "\n" + text
    path.write_text(text)
