"""Async wrapper around the `containerlab` CLI.

One golden image = one fixed topology (settings.topology_path). deploy() runs
the real `containerlab deploy` and parses the same YAML file to discover each
device's name and management IP, so the Lab Agent can do real readiness
checks against them — no lab_type parameter, no stub mode.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import yaml

from app import state as state_mod
from app.config import get_settings
from app.logging import get_logger

_log = get_logger("containerlab")


class ContainerlabError(RuntimeError):
    pass


class ContainerlabWrapper:
    def __init__(self, *, topology_path: str) -> None:
        self._topology_path = topology_path

    @classmethod
    def from_settings(cls) -> "ContainerlabWrapper":
        return cls(topology_path=get_settings().topology_path)

    async def deploy(self) -> None:
        clab = shutil.which("containerlab") or shutil.which("clab")
        if clab is None:
            raise ContainerlabError("containerlab binary not found in PATH")

        devices = self._parse_devices(self._topology_path)
        if not devices:
            raise ContainerlabError(f"no nodes found in topology {self._topology_path}")

        _log.info("containerlab.deploy", binary=clab, topology=self._topology_path, devices=devices)
        proc = await asyncio.create_subprocess_exec(
            clab, "deploy", "-t", self._topology_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise ContainerlabError(f"containerlab deploy failed: {stderr.decode().strip()}")

        await state_mod.STORE.reset(topology_path=self._topology_path, devices=devices)

    async def destroy(self) -> None:
        clab = shutil.which("containerlab") or shutil.which("clab")
        if clab is None:
            raise ContainerlabError("containerlab binary not found in PATH")
        _log.info("containerlab.destroy", binary=clab, topology=self._topology_path)
        proc = await asyncio.create_subprocess_exec(
            clab, "destroy", "-t", self._topology_path, "--cleanup",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        await state_mod.STORE.stop()

    @staticmethod
    def _parse_devices(topology_path: str) -> dict[str, str]:
        """Returns {device_name: mgmt_ip} parsed from the topology's node definitions."""
        p = Path(topology_path)
        if not p.exists():
            raise ContainerlabError(f"topology file not found: {topology_path}")
        with p.open() as f:
            doc = yaml.safe_load(f)
        nodes = (doc or {}).get("topology", {}).get("nodes", {})
        devices: dict[str, str] = {}
        for name, cfg in nodes.items():
            ip = (cfg or {}).get("mgmt-ipv4")
            if not ip:
                _log.warning("containerlab.node.no_mgmt_ip", node=name)
                continue
            devices[name] = str(ip)
        return devices
