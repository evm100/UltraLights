# OpenRGB bridge agent

Makes a PC's internal lighting one UltraLights device. Each lit part (GPU, memory, motherboard,
fans) is an `rgb` output with its own state. Users see the PC as **one light**: the usual ON pill,
brightness slider and color sliders, plus an **Applies to** picker (All · GPU · Memory · …) under
the color sliders that narrows the controls to one part.

```
                 ┌──────────────────────── Proxmox host (always on with the PC) ───────────────────────┐
 lights.evm100.org│  ul-openrgb agent ──SDK──▶ openrgb --server :6742 ──SMBus/USB──▶ RAM, board, fans │
   MQTT 8883 ◀────┤        │                                                                             │
   (mutual TLS)   │        └──────────SDK over LAN──────────┐                                            │
                 └──────────────────────────────────────────│────────────────────────────────────────────┘
                                                            ▼
                                     GPU VM (PCIe passthrough): openrgb --server :6742 ──I2C──▶ RTX 4080
```

The GPU is passed through to the VM, so only the VM's OpenRGB can see it; the RAM, board and fans
stay on the host, so only the host's OpenRGB can see them. The agent talks to both SDK servers
and presents them as one device. If the VM is off, only the GPU part shows as unreachable; it
gets its last setting back when the VM's OpenRGB returns.

The agent runs on the Proxmox host because the host is up whenever the PC is, while the VM may
not be. It could run anywhere that can reach both SDK servers; only `agent.toml` changes.

## Install (Proxmox host)

1. Both machines run the OpenRGB SDK server, **same OpenRGB version** on both (1.0rc2 today):
   `openrgb --server --server-port 6742` as a systemd service. The VM's port 6742 must be reachable
   from the host (`ufw allow from <host-ip> to any port 6742 proto tcp`). Give the VM a fixed
   address (DHCP reservation), because the agent dials it by address.
2. In the admin panel: **Nodes → Add PC (OpenRGB)**. It creates the device and shows the install
   line with a one-time pairing code (valid 7 days, shown once).
3. On the Proxmox host, from a clone of this repo (or `scp -r bridges/openrgb root@pve:`):

   ```
   sudo bridges/openrgb/install.sh --server lights.evm100.org --device ul-xxxxxx --token CODE \
        --source pve=127.0.0.1,"Proxmox host" --source vm=192.168.1.41,"GPU VM"
   ```

   It installs the agent into `/opt/ul-openrgb` (a venv), discovers the OpenRGB devices and writes
   `/etc/ul-openrgb/agent.toml`, enrolls, and starts `ul-openrgb.service` as the `ul-openrgb` user.
   Re-running it updates the code and keeps the config and identity. `--check` changes nothing.
4. Look at `/etc/ul-openrgb/agent.toml`, rename or merge parts if you like, then
   `systemctl restart ul-openrgb`. In the admin panel, give the PC a home and room.

The installer leaves the old `rgb-control` and `ul-bridge` services alone and tells you about
them. Once the agent works, `systemctl disable --now rgb-control ul-bridge`: both would otherwise
drive the same LEDs.

## Config

```toml
server = "lights.evm100.org"
device_id = "ul-4c2e19"            # written by `ul-openrgb enroll`

[sources.pve]                      # one per OpenRGB SDK server
name = "Proxmox host"
host = "127.0.0.1"                 # port = 6742 by default

[sources.vm]
name = "GPU VM"
host = "192.168.1.41"

[parts.gpu]                        # one UltraLights output per part, in UI order
name = "Graphics card"
part = "gpu"                       # icon: gpu ram board fans cooler strip keyboard mouse headset case other
source = "vm"
match = ["type:gpu"]               # type:<openrgb type>, name:<text in the device name>, or "*"

[parts.ram]
name = "Memory"
source = "pve"
match = ["type:dram"]              # all four DIMMs are one part
```

A device belongs to the first part on its source whose matcher fits. Devices no part matches are
left alone. Unknown settings are an error, so typos don't pass silently.

## How it behaves

- **Contract.** Bridge branch of `contracts/schemas/info.schema.json`: `combine: true` (one light),
  each output's `part`, `source`, `leds`, and `reachable`. Set, live, state, status and cmd are
  the same as a node's. There is no config topic.
- **Brightness** is perceived lightness (CIE 1931), like a node: the agent scales the color before
  sending it, since OpenRGB has no brightness of its own in Direct mode.
- **Fades** (`fade_ms`, 400 ms for a set) are drawn at up to 30 frames a second. Frames are
  coalesced, so a slow SMBus device skips frames instead of lagging behind a slider.
- **Devices** are put in their most direct mode (`Direct` where they have one) on every connect.
- **Restarts.** The last state is kept in `/var/lib/ul-openrgb/state.json`, so the lights come back
  before the broker is reached; retained `set` messages then bring them up to date.
- **Commands.** `identify` blinks a part (or all) white. `reboot` restarts the agent, never the PC.
  Others answer `unsupported`.

## Commands

```
ul-openrgb discover [--source KEY=HOST[:PORT][,NAME]]... [--write [--force]]
ul-openrgb enroll --server lights.evm100.org --device ul-xxxxxx --token CODE
ul-openrgb check          config, identity, each source and its parts, broker reachability
ul-openrgb run            what the service runs
journalctl -u ul-openrgb -f
```

To re-pair (moved to another host, lost key): **New pairing code** on the PC's admin page, then
the `enroll` line it shows.

## Troubleshooting

| Symptom | Check |
|---|---|
| A source shows *unreachable* | Is `openrgb --server` running there? Same OpenRGB version on both (`recv_select failed receiving magic` means a mismatch)? Port 6742 open to the agent's machine? |
| RAM missing on the host | SMBus drivers: `i2c-dev` plus `i2c-piix4` on AMD boards like the X570 (`i2c-i801` is the Intel one). `i2cdetect -l` should list the SMBus adapters. |
| A part says *no devices match* in `check` | Fix its `match` in `agent.toml`; `ul-openrgb discover` prints every device's name and type. |
| Colors jump back | Another OpenRGB client (the old `rgb-control`, a profile, the OpenRGB GUI) is also setting them. |
| Device offline in the UI | `journalctl -u ul-openrgb`; `ul-openrgb check` shows whether the broker is reachable and the identity loads. |

## Development

```
cd bridges/openrgb && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest
```

`host = "sim"` in a source gives a simulated OpenRGB server (four devices) for dry runs. The
server's `tests/test_bridge_integration.py` runs this agent against the dev broker.
