"""Build a HypervisorBackend from settings."""
from __future__ import annotations

from app.config import Settings, get_settings
from app.hypervisor.base import HypervisorBackend


def build_backend(settings: Settings | None = None) -> HypervisorBackend:
    s = settings or get_settings()
    if s.hypervisor_backend == "mock":
        from app.hypervisor.mock import MockHypervisorBackend

        return MockHypervisorBackend()
    if s.hypervisor_backend == "local_ch":
        from app.hypervisor.local_ch import LocalCloudHypervisorBackend

        return LocalCloudHypervisorBackend(
            ch_binary=s.ch_binary,
            kernel_path=s.ch_kernel,
            image_dir=s.ch_image_dir,
            disk_dir=s.ch_disk_dir,
            api_socket_dir=s.ch_api_socket_dir,
            bridge=s.ch_bridge,
            seed_dir=s.ch_seed_dir,
            seed_file=s.ch_seed_file,
        )
    raise ValueError(f"unknown hypervisor backend: {s.hypervisor_backend}")
