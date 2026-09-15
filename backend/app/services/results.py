import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import get_result_secret_box
from app.models import (
    GenerationJob,
    GenerationResult,
    JobStatus,
    VerificationStatus,
    utc_now,
)
from app.services.patterns import VERIFIER_VERSION, verify_candidate
from app.services.server_wallet import combine_private_key, verify_private_key_address

ACCEPTING_STATUSES = {JobStatus.QUEUED, JobStatus.SEARCHING, JobStatus.VERIFYING}


class CandidateJobNotFound(ValueError):
    pass


class CandidateStateConflict(ValueError):
    pass


class CandidateVerificationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FinalizedCandidate:
    accepted: bool
    job: GenerationJob
    address: str


async def finalize_candidate(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    address: str,
    offset: str,
) -> FinalizedCandidate:
    job = await db.scalar(
        select(GenerationJob)
        .where(GenerationJob.id == job_id)
        .options(selectinload(GenerationJob.result))
        .with_for_update()
    )
    if job is None:
        raise CandidateJobNotFound("Generation job not found.")
    if job.result is not None:
        return FinalizedCandidate(accepted=False, job=job, address=job.result.address)
    if job.status not in ACCEPTING_STATUSES:
        raise CandidateStateConflict(
            f"Job in {job.status.value} state does not accept candidates."
        )

    try:
        verified_address = verify_candidate(
            client_public_key=job.client_public_key,
            offset=offset,
            submitted_address=address,
            match_spec=job.match_spec,
        )
        if job.encrypted_base_private_key is None:
            raise ValueError("Job does not contain a server-generated private key share.")
        secret_box = get_result_secret_box()
        private_key = combine_private_key(
            secret_box.decrypt(job.encrypted_base_private_key), offset
        )
        verify_private_key_address(private_key, verified_address)
    except ValueError as exc:
        raise CandidateVerificationError(str(exc)) from exc

    now = utc_now()
    db.add(
        GenerationResult(
            job_id=job.id,
            worker_id=worker_id,
            address=verified_address,
            encrypted_offset=secret_box.encrypt(offset),
            encrypted_private_key=secret_box.encrypt(private_key),
            verification_status=VerificationStatus.VERIFIED,
            verifier_version=VERIFIER_VERSION,
            verified_at=now,
        )
    )
    job.status = JobStatus.READY
    job.completed_at = now
    return FinalizedCandidate(accepted=True, job=job, address=verified_address)
