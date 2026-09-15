from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, DatabaseSession
from app.config import get_settings
from app.models import GpuDevice, GpuStatus
from app.schemas import GpuDeviceRead, GpuFleetRead

router = APIRouter(prefix="/gpus", tags=["GPU fleet"])


@router.get("", response_model=GpuFleetRead)
async def list_gpus(db: DatabaseSession, _user: CurrentUser) -> GpuFleetRead:
    devices = list(
        (
            await db.scalars(
                select(GpuDevice)
                .where(GpuDevice.status != GpuStatus.OFFLINE)
                .order_by(GpuDevice.device_index)
            )
        ).all()
    )
    return GpuFleetRead(
        mode=get_settings().generator_mode,
        total=len(devices),
        ready=sum(device.status == GpuStatus.READY for device in devices),
        searching=sum(device.status == GpuStatus.SEARCHING for device in devices),
        unhealthy=sum(device.status == GpuStatus.UNHEALTHY for device in devices),
        combined_benchmark_rate=sum(device.benchmark_rate for device in devices),
        devices=[GpuDeviceRead.model_validate(device) for device in devices],
    )
