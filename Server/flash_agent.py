#!/usr/bin/env python3
"""Standalone flash agent — accepts firmware binaries over HTTP and flashes
them to a connected ESP32 via esptool.

Run on a Raspberry Pi (or any machine with a USB-connected ESP32):

    FLASH_AGENT_SECRET=<token> python flash_agent.py

Dependencies: pip install fastapi uvicorn python-multipart esptool pyserial
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse

app = FastAPI(title="UltraLights Flash Agent")

FLASH_AGENT_SECRET = os.getenv("FLASH_AGENT_SECRET", "")
LISTEN_PORT = int(os.getenv("FLASH_AGENT_PORT", "8901"))

_flash_lock = asyncio.Lock()


def _check_auth(request: Request) -> None:
    if not FLASH_AGENT_SECRET:
        return
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != FLASH_AGENT_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _detect_serial_port() -> str:
    """Auto-detect a connected ESP32 serial port."""
    candidates = sorted(
        glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
    )
    if not candidates:
        raise RuntimeError("No serial devices found (/dev/ttyUSB* or /dev/ttyACM*)")
    return candidates[0]


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/flash")
async def flash(
    request: Request,
    flasher_args: UploadFile = File(...),
    files: list[UploadFile] = File(...),
):
    _check_auth(request)

    if _flash_lock.locked():
        raise HTTPException(
            status_code=409,
            detail="Another flash job is already in progress.",
        )

    # Parse flasher_args JSON
    raw = await flasher_args.read()
    try:
        args = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid flasher_args JSON: {exc}")

    # Save uploaded binary files to a temp directory
    tmp_dir = Path(tempfile.mkdtemp(prefix="flash_agent_"))
    file_map: dict[str, Path] = {}
    try:
        for upload in files:
            if not upload.filename or ":" not in upload.filename:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid file name format: {upload.filename!r} (expected 'address:name')",
                )
            address, name = upload.filename.split(":", 1)
            dest = tmp_dir / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            content = await upload.read()
            dest.write_bytes(content)
            file_map[address] = dest
    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    async def generate():
        async with _flash_lock:
            try:
                port = _detect_serial_port()
                yield f"data: Detected serial port: {port}\n\n"
            except RuntimeError as exc:
                yield f"data: Error: {exc}\n\n"
                yield "event: done\ndata: error\n\n"
                return

            # Build esptool command
            extra = args.get("extra_esptool_args", {})
            chip = extra.get("chip", "auto")
            before = extra.get("before", "default_reset")
            after = extra.get("after", "hard_reset")

            write_flash_args = args.get("write_flash_args", [])

            cmd = [
                "esptool.py",
                "--chip", chip,
                "--port", port,
                "--before", before,
                "--after", after,
                "write_flash",
            ] + write_flash_args

            # Append address:file pairs
            for address, file_path in sorted(file_map.items()):
                cmd.extend([address, str(file_path)])

            yield f"data: Running: {' '.join(cmd)}\n\n"

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )

                assert proc.stdout is not None
                while True:
                    try:
                        line = await asyncio.wait_for(
                            proc.stdout.readline(), timeout=20.0
                        )
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                    if text:
                        yield f"data: {text}\n\n"

                await proc.wait()

                if proc.returncode == 0:
                    yield "data: Flash completed successfully.\n\n"
                    yield "event: done\ndata: success\n\n"
                else:
                    yield f"data: Flash failed (exit code {proc.returncode}).\n\n"
                    yield "event: done\ndata: error\n\n"
            except FileNotFoundError:
                yield "data: Error: esptool.py not found. Install with: pip install esptool\n\n"
                yield "event: done\ndata: error\n\n"
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=LISTEN_PORT)
