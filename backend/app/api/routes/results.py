import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DatabaseSession, WorkerAuthenticated
from app.schemas import CandidateAccepted, CandidateResultCreate
from app.services.results import (
    CandidateJobNotFound,
    CandidateStateConflict,
    CandidateVerificationError,
    finalize_candidate,
)

router = APIRouter(prefix="/internal/results", tags=["worker results"])


@router.post("/{job_id}", response_model=CandidateAccepted)
async def submit_candidate(
    job_id: uuid.UUID,
    payload: CandidateResultCreate,
    db: DatabaseSession,
    _worker_auth: WorkerAuthenticated,
) -> CandidateAccepted:
    try:
        finalized = await finalize_candidate(
            db,
            job_id=job_id,
            worker_id=payload.worker_id,
            address=payload.address,
            offset=payload.offset,
        )
    except CandidateJobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Generation job not found."
        ) from exc
    except CandidateStateConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except CandidateVerificationError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    await db.commit()
    return CandidateAccepted(
        accepted=finalized.accepted,
        job_id=finalized.job.id,
        status=finalized.job.status,
        address=finalized.address,
    )
