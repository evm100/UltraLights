#!/usr/bin/env python3
"""UltraLights build worker: claims firmware build jobs from the server and runs them.

It dials out over HTTPS only (contracts/schemas/worker), so the workstation needs no open
port. Each job is built from a clean checkout in the worker's own clone, never from your
working tree:

    git fetch -> checkout <ref> -> git clean -> firmware/tools/build.sh <profile>
    -> firmware/tools/package.py -> upload the package

Standard library only. Run it in a terminal or as a systemd user service:

    workers/build/ulworker.py --server https://lights.evm100.org          (token from the token file)
    workers/build/ulworker.py --once                                       (one job, then exit)

The token comes from $UL_WORKER_TOKEN or the token file (default
~/.config/ultralights/worker.token, mode 600). Create it in the admin panel under Builds.
"""
import argparse
import json
import os
import secrets
import signal
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORT_EVERY = 5        # seconds between log reports (heartbeat; the server's limit is 3 min)
CHUNK = 60000           # characters per report (contract max is 65536)
BUILD_TIMEOUT = 45 * 60


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class Stop(Exception):
    """The server said stop (cancelled), or we were asked to exit."""


class Api:
    def __init__(self, server: str, token: str, ca_file: str | None):
        self.base = server.rstrip("/")
        self.token = token
        self.ctx = ssl.create_default_context(cafile=ca_file)

    def _req(self, method: str, path: str, body: bytes | None = None, ctype: str | None = None, timeout=60):
        req = urllib.request.Request(self.base + path, data=body, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("User-Agent", "ultralights-build-worker/1")
        if ctype:
            req.add_header("Content-Type", ctype)
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=timeout) as r:
                data = r.read()
                return r.status, (json.loads(data) if data else None)
        except urllib.error.HTTPError as e:
            data = e.read()
            try:
                return e.code, json.loads(data)
            except ValueError:
                return e.code, {"detail": data.decode(errors="replace")[:300]}

    def json(self, method: str, path: str, doc=None, timeout=60):
        body = json.dumps(doc).encode() if doc is not None else None
        return self._req(method, path, body, "application/json" if body else None, timeout)

    def upload(self, path: str, field: str, filename: str, data: bytes):
        b = "----ul" + secrets.token_hex(12)
        body = (f"--{b}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
                f"Content-Type: application/zip\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
        return self._req("POST", path, body, f"multipart/form-data; boundary={b}", timeout=300)


class Job:
    """Runs one build job, streaming output to the server."""

    def __init__(self, api: Api, doc: dict, work: Path, repo_url: str):
        self.api, self.doc, self.work, self.repo_url = api, doc, work, repo_url
        self.id = doc["id"]
        self.buf: list[str] = []
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.cancelled = threading.Event()
        self.done = threading.Event()

    # -- reporting
    def say(self, text: str) -> None:
        with self.lock:
            self.buf.append(text if text.endswith("\n") else text + "\n")

    def _take(self) -> str:
        with self.lock:
            s, self.buf = "".join(self.buf), []
        return s

    def report(self, failed: str | None = None) -> None:
        text = self._take()
        parts = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)] or [""]
        for i, part in enumerate(parts):
            doc = {"v": 1, "type": "report"}
            if part:
                doc["log"] = part
            if failed and i == len(parts) - 1:
                doc["failed"] = failed[:500]
            try:
                code, ack = self.api.json("POST", f"/api/worker/jobs/{self.id}/report", doc)
            except OSError as e:  # network blip: keep the text for the next report
                with self.lock:
                    self.buf.insert(0, "".join(parts[i:]))
                log(f"report failed: {e}")
                return
            if code != 200 or (ack or {}).get("cancel"):
                self.cancelled.set()
                if self.proc and self.proc.poll() is None:
                    try:
                        os.killpg(self.proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass

    def _reporter(self) -> None:
        while not self.done.wait(REPORT_EVERY):
            self.report()

    # -- running
    def run(self, *cmd: str, cwd: Path | None = None, timeout: int = 600, quiet=False) -> str:
        if self.cancelled.is_set():
            raise Stop()
        if not quiet:
            self.say("$ " + " ".join(cmd))
        out: list[str] = []
        self.proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     errors="replace", start_new_session=True, env={**os.environ, "TERM": "dumb"})
        killer = threading.Timer(timeout, lambda: os.killpg(self.proc.pid, signal.SIGKILL))
        killer.start()
        try:
            for line in self.proc.stdout:
                out.append(line)
                if not quiet:
                    self.say(line)
            rc = self.proc.wait()
        finally:
            killer.cancel()
        if self.cancelled.is_set():
            raise Stop()
        if rc != 0:
            raise RuntimeError(f"{Path(cmd[0]).name} {cmd[1] if len(cmd) > 1 else ''} exited with {rc}".strip())
        return "".join(out).strip()

    def checkout(self) -> Path:
        repo = self.work / "repo"
        if not (repo / ".git").is_dir():
            self.work.mkdir(parents=True, exist_ok=True)
            self.run("git", "clone", "--no-checkout", "--", self.repo_url, str(repo), timeout=900)
        else:
            self.run("git", "-C", str(repo), "fetch", "--prune", "--force", "--tags", "origin", timeout=900)
        ref = self.doc["ref"]
        sha = None
        for cand in (f"refs/remotes/origin/{ref}", f"refs/tags/{ref}", ref):
            try:
                sha = self.run("git", "-C", str(repo), "rev-parse", "--verify", "--quiet", "--end-of-options",
                               f"{cand}^{{commit}}", quiet=True)
                break
            except RuntimeError:
                continue
        if not sha:
            raise RuntimeError(f"ref {ref!r} not found in {self.repo_url}")
        self.run("git", "-C", str(repo), "checkout", "--force", "--detach", sha)
        self.run("git", "-C", str(repo), "clean", "-ffdxq")
        self.say(f"-- {self.doc['profile']} at {ref} = {sha[:10]}")
        return repo

    def execute(self) -> None:
        t = threading.Thread(target=self._reporter, daemon=True)
        t.start()
        try:
            repo = self.checkout()
            profile = self.doc["profile"]
            self.run(str(repo / "firmware/tools/build.sh"), profile, cwd=repo, timeout=BUILD_TIMEOUT)
            out = self.work / f"job-{self.id}.zip"
            self.run(sys.executable, str(repo / "firmware/tools/package.py"), profile, "-o", str(out), cwd=repo)
            self.say(f"-- uploading {out.name} ({out.stat().st_size // 1024} KB)")
            self.done.set()
            t.join()
            self.report()
            if self.cancelled.is_set():
                raise Stop()
            code, res = self.api.upload(f"/api/worker/jobs/{self.id}/package", "file", out.name, out.read_bytes())
            out.unlink(missing_ok=True)
            if code != 200:
                detail = (res or {}).get("detail")
                log(f"job {self.id}: upload refused ({code}): {detail}")
            else:
                log(f"job {self.id}: done, package {res.get('package_id')}")
        except Stop:
            log(f"job {self.id}: stopped (cancelled)")
        except Exception as e:  # noqa: BLE001 - every failure is reported, then we carry on
            log(f"job {self.id}: failed: {e}")
            self.done.set()
            t.join()
            self.say(f"-- failed: {e}")
            self.report(failed=str(e) or e.__class__.__name__)
        finally:
            self.done.set()


def read_token(path: Path) -> str:
    t = os.environ.get("UL_WORKER_TOKEN", "").strip()
    if t:
        return t
    if not path.is_file():
        sys.exit(f"no token: set UL_WORKER_TOKEN or write it to {path} (admin panel > Builds > Add worker)")
    if path.stat().st_mode & 0o077:
        sys.exit(f"{path} must not be readable by others: chmod 600 {path}")
    return path.read_text().strip()


def default_repo() -> str:
    try:
        return subprocess.check_output(["git", "-C", str(HERE), "remote", "get-url", "origin"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "git@github.com:evm100/UltraLights.git"


def main() -> None:
    cfg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ultralights"
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ultralights-worker"
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default=os.environ.get("UL_WORKER_SERVER", "https://lights.evm100.org"))
    ap.add_argument("--token-file", type=Path, default=cfg / "worker.token")
    ap.add_argument("--ca-file", default=os.environ.get("UL_WORKER_CA_FILE"),
                    help="CA bundle for the server certificate (dev servers); default: system trust")
    ap.add_argument("--repo", default=os.environ.get("UL_WORKER_REPO") or default_repo(),
                    help="repository to clone (default: this checkout's origin)")
    ap.add_argument("--work", type=Path, default=cache, help=f"worker clone and scratch (default {cache})")
    ap.add_argument("--poll", type=float, default=10, help="seconds between claims when idle")
    ap.add_argument("--once", action="store_true", help="run at most one job, then exit")
    args = ap.parse_args()

    api = Api(args.server, read_token(args.token_file), args.ca_file)
    code, me = api.json("GET", "/api/worker/me")
    if code != 200:
        sys.exit(f"{args.server}: worker sign-in failed ({code}): {(me or {}).get('detail')}")
    log(f"worker {me['name']} on {args.server}, repo {args.repo}, work dir {args.work}")

    stopping = threading.Event()
    current: list[Job] = []

    def on_stop(*_):
        stopping.set()
        for j in current:  # tell the server, so the job doesn't wait for the stale sweep
            j.cancelled.set()
            if j.proc and j.proc.poll() is None:
                os.killpg(j.proc.pid, signal.SIGTERM)
            try:
                api.json("POST", f"/api/worker/jobs/{j.id}/report",
                         {"v": 1, "type": "report", "failed": "worker was stopped"}, timeout=10)
            except OSError:
                pass

    signal.signal(signal.SIGTERM, on_stop)
    signal.signal(signal.SIGINT, on_stop)

    backoff = args.poll
    while not stopping.is_set():
        try:
            code, doc = api.json("POST", "/api/worker/claim")
        except OSError as e:
            log(f"server unreachable: {e}")
            stopping.wait(backoff)
            backoff = min(backoff * 2, 300)
            continue
        backoff = args.poll
        if code == 200 and doc and doc.get("type") == "job":
            log(f"job {doc['id']}: {doc['profile']} at {doc['ref']}")
            job = Job(api, doc, args.work, args.repo)
            current[:] = [job]
            job.execute()
            current.clear()
            if args.once:
                break
            continue
        if code not in (200, 204):
            log(f"claim refused ({code}): {(doc or {}).get('detail')}")
            if code == 401:
                sys.exit("token rejected: it was rotated or the worker was deleted")
        if args.once:
            break
        stopping.wait(args.poll)


if __name__ == "__main__":
    main()
