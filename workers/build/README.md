# Build worker

Builds firmware packages for the server's build queue (admin panel → **Builds**). It runs on
the workstation, dials out to `https://lights.evm100.org` over HTTPS and needs no open port.
Messages follow `contracts/schemas/worker.schema.json`.

For each job it:

1. fetches its own clone (default `~/.cache/ultralights-worker/repo`; never your working tree),
2. checks out the job's ref (`origin/<branch>`, a tag or a commit) and runs `git clean -ffdx`,
3. runs `firmware/tools/build.sh <profile>` and `firmware/tools/package.py`,
4. uploads the package, which the server checks like a manual upload and stores under Firmware.

The log streams to the admin panel every 5 s. Those reports are also the heartbeat: a job
with no report for 3 minutes is marked failed. Cancelling in the panel kills the build at the
next report. Stopping the worker (Ctrl-C, `systemctl --user stop`) reports its job as failed.

## Versions

The package version is `firmware/version.txt` at that ref. A second build of the same
version from a different commit is refused (OTA compares versions, so two different images
must not share one), unless the build was queued with **Replace same version**. Bump
`version.txt` for anything you mean to roll out.

## Setup

Needs ESP-IDF 6.1 at `~/.espressif` (what `firmware/tools/idfenv.sh` activates), git, and
Python 3.11+. Standard library only.

1. Admin panel → Builds → **Add worker**, e.g. `workstation`. Copy the command it shows; it
   writes the token to `~/.config/ultralights/worker.token` with mode 600. The token is shown
   once; **New token** replaces it.
2. Try one job in a terminal: queue a build, then
   ```
   workers/build/ulworker.py --once
   ```
3. Run it as a user service: see the comments in `ulworker.service`.

The repository defaults to this checkout's `origin`. The service has no SSH agent, so an
SSH remote needs a key without a passphrase (or a read-only deploy key); otherwise pass
`--repo https://github.com/evm100/UltraLights.git` if the repository is public.

| Option | Default | |
|---|---|---|
| `--server` | `$UL_WORKER_SERVER` or `https://lights.evm100.org` | |
| `--token-file` | `~/.config/ultralights/worker.token` | or `$UL_WORKER_TOKEN` |
| `--ca-file` | system trust | for a dev server with its own CA |
| `--repo` | this checkout's origin | `$UL_WORKER_REPO` |
| `--work` | `~/.cache/ultralights-worker` | clone and scratch |
| `--poll` | 10 s | idle claim interval; 90 s of silence shows the worker offline |
| `--once` | | one job, then exit |

## Signing

Not yet. Images are unsigned until secure boot is set up in the fabrication phase; then the
worker signs with the firmware signing key kept on this workstation (blueprint, Identity).
