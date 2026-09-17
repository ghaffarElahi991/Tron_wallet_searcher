from html import escape

from app.models import FundingStatus, JobStatus
from app.schemas import FundingRead, GenerationJobRead, GpuFleetRead


def format_count(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:,}"


def job_progress(job: GenerationJobRead) -> str:
    status_labels = {
        JobStatus.QUEUED: "Waiting in queue",
        JobStatus.SEARCHING: "Searching",
        JobStatus.VERIFYING: "Verifying result",
        JobStatus.READY: "Wallet ready",
        JobStatus.OWNERSHIP_VERIFIED: "Wallet ready",
        JobStatus.FAILED: "Failed",
        JobStatus.CANCELED: "Canceled",
        JobStatus.TIMED_OUT: "Timed out",
    }
    middle = "•" * max(5, 34 - len(job.prefix) - len(job.suffix))
    if job.observed_rate is not None:
        rate_line = f"Observed average: <b>{format_count(job.observed_rate)}/s</b>"
    elif job.status == JobStatus.QUEUED:
        rate_line = "Observed average: <b>waiting for GPU</b>"
    else:
        rate_line = "Observed average: <b>measuring…</b>"
    lines = [
        "<b>TRON wallet generation</b>",
        "",
        f"Status: <b>{status_labels[job.status]}</b>",
        f"Pattern: <code>{escape(job.prefix)}{middle}{escape(job.suffix)}</code>",
        f"Candidates checked: <b>{format_count(job.attempts)}</b>",
        rate_line,
        f"Job: <code>{str(job.id)[:8]}</code>",
    ]
    if job.failure_message:
        lines.extend(["", f"Reason: {escape(job.failure_message)}"])
    return "\n".join(lines)


def wallet_result(job: GenerationJobRead) -> str:
    if job.result is None:
        raise ValueError("Job does not contain a wallet result.")
    return "\n".join(
        [
            "<b>Wallet generated successfully</b>",
            "",
            "Address:",
            f"<code>{escape(job.result.address)}</code>",
            "",
            "Private key:",
            f"<code>{escape(job.result.private_key)}</code>",
            "",
            "Final observed average: <b>unavailable</b>"
            if job.observed_rate is None
            else f"Final observed average: <b>{format_count(job.observed_rate)}/s</b>",
            "",
            "<b>Security warning:</b> Import the private key into your wallet, store an offline "
            "backup, and delete this Telegram message. Anyone with this key controls the wallet.",
        ]
    )


def gpu_fleet_message(fleet: GpuFleetRead) -> str:
    lines = [
        "<b>GPU fleet</b>",
        "",
        f"Mode: <b>{escape(fleet.mode)}</b>",
        f"Devices: <b>{fleet.total}</b>",
        f"Ready: <b>{fleet.ready}</b>",
        f"Searching: <b>{fleet.searching}</b>",
        f"Combined benchmark: <b>{format_count(fleet.combined_benchmark_rate)}/s</b>",
    ]
    for device in fleet.devices:
        lines.extend(
            [
                "",
                f"GPU {device.device_index}: <b>{escape(device.name)}</b>",
                f"Status: {escape(device.status.value)} · "
                f"benchmark {format_count(device.benchmark_rate)}/s",
            ]
        )
    return "\n".join(lines)


def funding_progress(funding: FundingRead) -> str:
    labels = {
        FundingStatus.REQUESTED: "Queued",
        FundingStatus.PREPARING: "Preparing transaction",
        FundingStatus.SIGNED: "Signed securely",
        FundingStatus.BROADCAST: "Waiting for solidification",
        FundingStatus.CONFIRMED: "Confirmed",
        FundingStatus.FAILED: "Failed",
        FundingStatus.UNKNOWN: "Reconciling uncertain broadcast",
    }
    lines = [
        "<b>TRON USDT funding</b>",
        "",
        f"Status: <b>{labels[funding.status]}</b>",
        f"Amount: <b>{escape(funding.amount_usdt)} USDT</b>",
        f"Destination: <code>{escape(funding.destination_address)}</code>",
        f"Network: <b>{escape(funding.network.upper())}</b>",
    ]
    if funding.txid:
        lines.extend(["", f"Transaction: <code>{escape(funding.txid)}</code>"])
    if funding.failure_message:
        lines.extend(["", f"Detail: {escape(funding.failure_message)}"])
    if funding.status == FundingStatus.UNKNOWN:
        lines.extend(
            [
                "",
                "The original transaction is still being reconciled. Do not create a replacement.",
            ]
        )
    return "\n".join(lines)
