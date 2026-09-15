import csv
import io
import json
import os
import subprocess
from pathlib import Path

from app.config import Settings
from app.generator.protocol import GpuInfo


class GpuDiscoveryError(RuntimeError):
    pass


def resolve_native_binary(settings: Settings) -> Path:
    configured = Path(settings.generator_native_binary).expanduser()
    if configured.is_absolute():
        return configured
    backend_root = Path(__file__).resolve().parents[2]
    return (backend_root / configured).resolve()


def benchmark_cuda_device(binary: Path, device_index: int, candidates: int) -> int:
    try:
        result = subprocess.run(
            [
                str(binary),
                "search-gpu-chained16",
                "--device",
                str(device_index),
                "--public-key",
                "0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
                "--pattern",
                "3x4",
                "--prefix",
                "ZZZ",
                "--suffix",
                "ZZZZ",
                "--start",
                "1",
                "--count",
                str(candidates),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        payload = json.loads(result.stdout)
        rate = int(float(payload["candidates_per_second"]))
    except (
        FileNotFoundError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        raise GpuDiscoveryError(
            f"CUDA benchmark failed for device {device_index}."
        ) from exc
    if rate < 1:
        raise GpuDiscoveryError(
            f"CUDA benchmark returned an invalid rate for device {device_index}."
        )
    return rate


def parse_nvidia_smi(output: str) -> list[GpuInfo]:
    devices: list[GpuInfo] = []
    for row in csv.reader(io.StringIO(output), skipinitialspace=True):
        if not row or not any(value.strip() for value in row):
            continue
        if len(row) != 4:
            raise GpuDiscoveryError("nvidia-smi returned an unexpected GPU record.")
        try:
            device_index = int(row[0].strip())
            memory_total_mb = int(row[3].strip())
        except ValueError as exc:
            raise GpuDiscoveryError("nvidia-smi returned invalid numeric GPU data.") from exc
        devices.append(
            GpuInfo(
                device_index=device_index,
                uuid=row[1].strip(),
                name=row[2].strip(),
                memory_total_mb=memory_total_mb,
                benchmark_rate=0,
            )
        )
    return devices


def discover_gpus(settings: Settings) -> list[GpuInfo]:
    if settings.generator_mode == "simulator":
        if settings.simulated_gpu_count < 1:
            raise GpuDiscoveryError(
                "Simulator mode requires TRONFORGE_SIMULATED_GPU_COUNT to be at least 1."
            )
        return [
            GpuInfo(
                device_index=index,
                uuid=f"SIM-GPU-{index}",
                name=f"Simulated GPU {index}",
                memory_total_mb=24_576,
                benchmark_rate=settings.simulated_gpu_rate,
            )
            for index in range(settings.simulated_gpu_count)
        ]

    if settings.generator_mode != "cuda":
        raise GpuDiscoveryError("Generator mode must be either 'simulator' or 'cuda'.")

    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,uuid,name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise GpuDiscoveryError("NVIDIA GPUs could not be discovered through nvidia-smi.") from exc

    devices = parse_nvidia_smi(result.stdout)
    if not devices:
        raise GpuDiscoveryError("No NVIDIA GPUs were discovered.")
    native_binary = resolve_native_binary(settings)
    if not native_binary.is_file() or not os.access(native_binary, os.X_OK):
        raise GpuDiscoveryError(
            f"Native CUDA generator is not executable: {native_binary}"
        )
    return [
        GpuInfo(
            device_index=device.device_index,
            uuid=device.uuid,
            name=device.name,
            memory_total_mb=device.memory_total_mb,
            benchmark_rate=benchmark_cuda_device(
                native_binary,
                device.device_index,
                settings.generator_cuda_benchmark_candidates,
            ),
            kernel_version="cuda-persistent-chained16-v1",
        )
        for device in devices
    ]
