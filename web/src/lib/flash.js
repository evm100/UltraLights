// Flashing over Web Serial with Espressif's esptool-js (Chrome / Edge, secure context).
// Writes every file of a firmware package at its manifest offset, then the ul_board blob.
import { ESPLoader, Transport } from "esptool-js";
import { api } from "./api.js";

export const webSerialAvailable = () => typeof navigator !== "undefined" && "serial" in navigator;

async function sha256hex(bytes) {
  const d = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Download the package files and check them against the manifest before touching the board. */
export async function fetchPackage(pkg, log) {
  const files = {};
  for (const f of pkg.manifest.files) {
    log(`Downloading ${f.name} (${f.size} bytes)`);
    const buf = new Uint8Array(await api("GET", `/api/admin/firmware/${pkg.id}/files/${encodeURIComponent(f.name)}`));
    if (buf.length !== f.size || (await sha256hex(buf)) !== f.sha256) throw new Error(`${f.name} failed its checksum`);
    files[f.name] = buf;
  }
  return files;
}

/**
 * @param {object} o
 * @param {object|null} o.pkg      firmware package ({id, manifest}); null = descriptor only
 * @param {Uint8Array} o.blob      ul_board blob
 * @param {number} o.boardOffset   where the blob goes (manifest.board_partition.offset)
 * @param {string} o.chip          expected chip (esp32c3 / esp32c6)
 * @param {boolean} o.eraseAll     erase the whole flash first (fresh install, clears old NVS)
 */
export async function flash({ pkg, blob, boardOffset, chip, eraseAll, log, progress }) {
  if (!webSerialAvailable()) throw new Error("Web Serial isn't available: use Chrome or Edge over https or localhost.");
  const files = pkg ? await fetchPackage(pkg, log) : {};
  const port = await navigator.serial.requestPort();
  const transport = new Transport(port, false);
  const terminal = { clean() {}, writeLine: (s) => log(s), write: (s) => log(s) };
  const loader = new ESPLoader({ transport, baudrate: 460800, terminal });
  try {
    log("Connecting…");
    await loader.main();
    const found = loader.chip.CHIP_NAME.replace("-", "").toLowerCase();
    if (found !== chip) throw new Error(`This board is an ${loader.chip.CHIP_NAME}, but the node is set up as ${chip}.`);
    const fileArray = pkg ? pkg.manifest.files.map((f) => ({ data: files[f.name], address: f.offset })) : [];
    fileArray.push({ data: blob, address: boardOffset });
    const m = pkg?.manifest.flash;
    await loader.writeFlash({
      fileArray,
      flashMode: m?.mode ?? "keep",
      flashFreq: m?.freq ?? "keep",
      flashSize: m?.size ?? "keep",
      eraseAll: !!eraseAll,
      compress: true,
      reportProgress: (i, written, total) => progress?.(i, fileArray.length, written / total),
    });
    log("Done. Resetting the board.");
    // Not after("hard_reset"): esptool-js's HardReset only drops RTS, and the connect sequence
    // already left RTS low, so there's no edge and the chip stays in the flasher stub until
    // something else (a serial monitor opening the port) resets it. Pulse it like esptool.py:
    // DTR low (IO9/IO0 released, so it boots the app), RTS high (EN low, in reset), then RTS low.
    await loader.after("custom_reset", undefined, "D0|R1|W100|R0");
  } finally {
    await transport.disconnect().catch(() => {});
  }
}

export function download(bytes, name) {
  const url = URL.createObjectURL(new Blob([bytes], { type: "application/octet-stream" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

export const b64ToBytes = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
