from datetime import UTC, datetime, timedelta

from app.models import GenerationJob
from app.services.jobs import observed_search_rate


def test_observed_rate_uses_actual_attempts_and_elapsed_time() -> None:
    started = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    job = GenerationJob(attempts=100_000_000, started_at=started)

    assert observed_search_rate(job, started + timedelta(seconds=2)) == 50_000_000


def test_observed_rate_waits_for_first_progress_and_freezes_at_completion() -> None:
    started = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    job = GenerationJob(attempts=0, started_at=started)
    assert observed_search_rate(job, started + timedelta(seconds=2)) is None

    job.attempts = 100_000_000
    job.completed_at = started + timedelta(seconds=4)
    assert observed_search_rate(job, started + timedelta(seconds=20)) == 25_000_000


def test_observed_rate_handles_naive_test_database_timestamps() -> None:
    started = datetime(2026, 9, 15, 12, 0)
    job = GenerationJob(attempts=20_000_000, started_at=started)

    assert observed_search_rate(job, started + timedelta(seconds=2)) == 10_000_000
