import asyncio
import fcntl
import logging
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import get_settings
from app.generator.discovery import GpuDiscoveryError, discover_gpus
from app.generator.scheduler import GenerationScheduler


@contextmanager
def single_instance_lock(path: str) -> Iterator[None]:
    lock_path = Path(path)
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another GPU scheduler is already running.") from exc
        yield


async def run_scheduler() -> None:
    settings = get_settings()
    devices = discover_gpus(settings)
    scheduler = GenerationScheduler(settings, devices)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_number, stop_event.set)
    await scheduler.run(stop_event)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    try:
        with single_instance_lock(settings.generator_lock_file):
            asyncio.run(run_scheduler())
    except (GpuDiscoveryError, RuntimeError) as exc:
        logging.getLogger(__name__).error("Generator startup failed: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
