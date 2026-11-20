"""ultralights-scheduler: placeholder until schedules arrive (Homes and people phase).

Runs as its own systemd unit so the deployment shape is final now; for the moment it
only logs that it is idle.
"""
import asyncio
import logging
import signal


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("ul.scheduler")
    log.info("scheduler started; no schedules are implemented yet")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    log.info("scheduler stopped")


if __name__ == "__main__":
    asyncio.run(main())
