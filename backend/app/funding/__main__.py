import asyncio
import fcntl
import logging
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import get_settings
from app.funding.gateway import FundingGatewayError
from app.funding.processor import FundingProcessor


@contextmanager
def single_instance_lock(path: str) -> Iterator[None]:
    lock_path = Path(path)
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another funding processor is already running.") from exc
        yield


async def run_processor() -> None:
    settings = get_settings()
    if settings.funding_mode == "disabled":
        raise RuntimeError("Wallet funding is disabled.")
    processor = FundingProcessor(settings)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_number, stop_event.set)
    await processor.run(stop_event)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    try:
        with single_instance_lock(settings.funding_lock_file):
            asyncio.run(run_processor())
    except (FundingGatewayError, RuntimeError) as exc:
        logging.getLogger(__name__).error("Funding processor startup failed: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
