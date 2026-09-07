"""Local Cloud Hypervisor backend.

Launches real Cloud Hypervisor microVMs on the host using per-lab copy-on-write
qcow2 overlays on top of a shared golden image.

IMPORTANT: TAP creation and bridge attachment is owned entirely by
NetworkingService.create_tap_and_attach(), called by the orchestrator *before*
start() runs (see orchestrator/workflow.py). This backend only references the
already-bridged tap by name when launching cloud-hypervisor — it does not
create, attach, or otherwise manage the tap's bridge membership. Do not add
`bridge=`, `ip=`, or `mask=` to the --net flag: cloud-hypervisor has no
`bridge=` net parameter, and ip=/mask= configure the *host* side of a tap that
isn't bridged, which does not apply here.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import signal
from pathlib import Path

import httpx

from app.hypervisor.base import HypervisorBackend, VMHandle, VMNotReady, VMStartError
from app.logging import get_logger

_log = get_logger("hypervisor.local_ch")

_CH_API_TIMEOUT = 5.0
_GRACEFUL_SHUTDOWN_TIMEOUT_SEC = 30.0
_READY_POLL_INTERVAL_SEC = 2.0

# TODO verify against a live cloud-hypervisor instance: some versions expose
# the API unprefixed (/vm.info) and some behind /api/v1/. Confirm with:
#   curl -s --unix-socket <sock> http://localhost/vm.info
#   curl -s --unix-socket <sock> http://localhost/api/v1/vm.info
# and fix this prefix if the second form is the one that actually responds.
_CH_API_PREFIX = "/api/v1"


class LocalCloudHypervisorBackend(HypervisorBackend):
    def __init__(
        self,
        *,
        ch_binary: str,
        kernel_path: str,
        image_dir: str,
        disk_dir: str,
        api_socket_dir: str,
        bridge: str,
        seed_dir: str,
        seed_file: str,
        console_bind: str = "127.0.0.1:0",
    ) -> None:
        self._ch_binary = ch_binary
        self._kernel_path = kernel_path
        self._image_dir = Path(image_dir)
        self._disk_dir = Path(disk_dir)
        self._disk_dir.mkdir(parents=True, exist_ok=True)
        self._socket_dir = Path(api_socket_dir)
        self._socket_dir.mkdir(parents=True, exist_ok=True)
        self._bridge = bridge  # informational only; tap is already attached by NetworkingService
        self._seed_path = str(Path(seed_dir) / seed_file) if seed_dir else ""
        self._console_bind = console_bind

    # ---------- internal helpers ----------

    def _socket_path(self, lab_id: str) -> str:
        return str(self._socket_dir / f"{lab_id}.sock")

    def _overlay_path(self, lab_id: str) -> str:
        return str(self._disk_dir / f"{lab_id}.qcow2")

    @staticmethod
    def _deterministic_mac(lab_id: str) -> str:
        digest = hashlib.md5(lab_id.encode()).hexdigest()
        return f"52:54:00:{digest[0:2]}:{digest[2:4]}:{digest[4:6]}"

    async def _run(self, cmd: list[str], *, check: bool = True) -> tuple[int, bytes, bytes]:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if check and proc.returncode != 0:
            raise VMStartError(
                f"{' '.join(cmd)} failed (rc={proc.returncode}): "
                f"{stderr.decode().strip() or stdout.decode().strip()}"
            )
        return proc.returncode, stdout, stderr

    def _ch_client(self, api_socket: str) -> httpx.AsyncClient:
        transport = httpx.AsyncHTTPTransport(uds=api_socket)
        return httpx.AsyncClient(transport=transport, base_url="http://localhost")

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # process exists but is owned by another user (e.g. launched via sudo)
            return True

    async def _signal_pid(self, pid: int, sig: int) -> None:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            # we're not root and the VM was launched via sudo — escalate the same way
            await self._run(["sudo", "kill", f"-{sig}", str(pid)], check=False)

    # ---------- HypervisorBackend ----------

    async def start(
        self,
        *,
        lab_id: str,
        image: str,
        cpu: int,
        memory_mb: int,
        tap_name: str,
        vm_ip: str,
        gateway: str,
        subnet: str,
    ) -> VMHandle:
        golden = self._image_dir / image
        if not golden.exists():
            raise VMStartError(f"golden image not found: {golden}")
        if not self._seed_path or not Path(self._seed_path).exists():
            raise VMStartError(f"seed image not found: {self._seed_path!r}")

        overlay = self._overlay_path(lab_id)
        if Path(overlay).exists():
            Path(overlay).unlink()  # stale overlay from a previous failed attempt

        # Full flat copy, not a COW overlay. Cloud Hypervisor >= v51.0
        # disables qcow2 backing files by default (CVE fix:
        # GHSA-jmr4-g2hv-mjj6, host file exfiltration via a maliciously
        # crafted backing-file pointer). Re-enabling them requires
        # --landlock for safe mitigation, which this host's kernel (5.15,
        # Landlock ABI v1) cannot support — cloud-hypervisor v53 requests
        # Refer/Truncate access rights that require ABI v2/v3 (kernel
        # >=5.19 / >=6.2) and fails hard rather than degrading gracefully.
        # So: full copy per lab, deleted in stop() below. Revisit once the
        # host kernel is deliberately upgraded (see project notes).
        await self._run(["qemu-img", "convert", "-O", "qcow2", str(golden), overlay])

        api_socket = self._socket_path(lab_id)
        if Path(api_socket).exists():
            Path(api_socket).unlink()

        mac = self._deterministic_mac(lab_id)

        cmd = [
            self._ch_binary,
            "--api-socket", f"path={api_socket}",
            "--kernel", self._kernel_path,
            "--disk", f"path={overlay}",
            "--disk", f"path={self._seed_path},readonly=on",
            "--cpus", f"boot={cpu},nested=on",
            "--memory", f"size={memory_mb}M",
            "--net", f"tap={tap_name},mac={mac}",
            "--console", "tty",
            "--serial", "tty",
            "--cmdline", "console=ttyS0 root=/dev/vda4 rw",
        ]
        if os.geteuid() != 0:
            cmd = ["sudo"] + cmd

        _log.info("ch.start", lab_id=lab_id, tap=tap_name, mac=mac, cmd=" ".join(cmd))
        log_dir = self._disk_dir.parent / "ch-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{lab_id}.log"
        log_file = open(log_path, "wb")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=log_file,
                stderr=log_file,
                start_new_session=True,
            )
        except Exception:
            log_file.close()
            raise
        except FileNotFoundError as e:
            raise VMStartError(f"cloud-hypervisor binary not found: {self._ch_binary}") from e

        # fail fast if the process dies immediately (bad flags, busy tap, etc.)
        await asyncio.sleep(0.5)
        if proc.returncode is not None:
            log_tail = ""
            try:
                log_tail = log_path.read_text()[-2000:]
            except OSError:
                pass
            raise VMStartError(
                f"cloud-hypervisor exited immediately with code {proc.returncode}; "
                f"log={log_path}; tail: {log_tail!r}"
            )

        _log.info("ch.started", lab_id=lab_id, pid=proc.pid, api_socket=api_socket)
        return VMHandle(
            pid=proc.pid,
            api_socket=api_socket,
            console_socket=None,
            tap=tap_name,
            vm_ip=vm_ip,
        )

    async def is_running(self, handle: VMHandle) -> bool:
        if not handle.api_socket or not Path(handle.api_socket).exists():
            return False
        try:
            async with self._ch_client(handle.api_socket) as client:
                resp = await client.get(f"{_CH_API_PREFIX}/vm.info", timeout=_CH_API_TIMEOUT)
            if resp.status_code != 200:
                return False
            return resp.json().get("state") == "Running"
        except (httpx.HTTPError, OSError, ValueError) as e:
            _log.warning("ch.is_running.error", pid=handle.pid, error=str(e))
            return False

    async def wait_ready(self, handle: VMHandle, *, timeout_sec: float) -> None:
        if not handle.vm_ip:
            raise VMNotReady("VMHandle has no vm_ip; cannot poll Lab Agent health")

        url = f"http://{handle.vm_ip}:9001/health"
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_sec
        last_error: str | None = None

        async with httpx.AsyncClient() as client:
            while loop.time() < deadline:
                try:
                    resp = await client.get(url, timeout=3.0)
                    if resp.status_code == 200:
                        _log.info("ch.wait_ready.ok", pid=handle.pid, vm_ip=handle.vm_ip)
                        return
                    last_error = f"http {resp.status_code}"
                except httpx.HTTPError as e:
                    last_error = str(e)
                await asyncio.sleep(_READY_POLL_INTERVAL_SEC)

        raise VMNotReady(
            f"Lab Agent at {url} did not become healthy within {timeout_sec}s "
            f"(last error: {last_error})"
        )

    async def stop(self, handle: VMHandle) -> None:
        # 1. Ask cloud-hypervisor to shut the guest down cleanly.
        if handle.api_socket and Path(handle.api_socket).exists():
            try:
                async with self._ch_client(handle.api_socket) as client:
                    await client.put(f"{_CH_API_PREFIX}/vm.shutdown", timeout=_CH_API_TIMEOUT)
            except httpx.HTTPError as e:
                _log.warning("ch.stop.shutdown_api.error", pid=handle.pid, error=str(e))

        # 2. Poll for actual process exit — the API call above returns immediately.
        loop = asyncio.get_event_loop()
        deadline = loop.time() + _GRACEFUL_SHUTDOWN_TIMEOUT_SEC
        while loop.time() < deadline and self._pid_alive(handle.pid):
            await asyncio.sleep(1.0)

        # 3. Escalate if it's still alive.
        if self._pid_alive(handle.pid):
            _log.warning("ch.stop.graceful_timeout", pid=handle.pid)
            await self._signal_pid(handle.pid, signal.SIGTERM)
            await asyncio.sleep(3.0)
        if self._pid_alive(handle.pid):
            await self._signal_pid(handle.pid, signal.SIGKILL)
            await asyncio.sleep(1.0)

        # 4. Tear down the TAP — NetworkingService owns tap lifecycle end-to-end.
        from app.networking.service import NetworkingService  # local import avoids a cycle

        try:
            await NetworkingService.from_settings().remove_tap(handle.tap)
        except Exception as e:  # noqa: BLE001
            _log.warning("ch.stop.remove_tap.error", tap=handle.tap, error=str(e))

        # 5. Clean up the API socket and the per-lab disk overlay.
        if handle.api_socket and Path(handle.api_socket).exists():
            try:
                Path(handle.api_socket).unlink()
            except OSError as e:
                _log.warning("ch.stop.unlink_socket.error", path=handle.api_socket, error=str(e))

        if handle.api_socket:
            lab_id = Path(handle.api_socket).stem  # socket is named <lab_id>.sock
            overlay = Path(self._overlay_path(lab_id))
            if overlay.exists():
                try:
                    overlay.unlink()
                except OSError as e:
                    _log.warning("ch.stop.unlink_overlay.error", path=str(overlay), error=str(e))

        _log.info("ch.stopped", pid=handle.pid, tap=handle.tap)
