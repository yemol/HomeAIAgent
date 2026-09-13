#!/usr/bin/env python3
"""Managed OpenClaw transport for HomeAIAgent.

HomeAIAgent runs on the Mac mini and owns the OpenClaw SSH local forward
inside the Python service process. The StickS3-facing server stays alive when
the SSH path is unavailable.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

try:
    import asyncssh  # type: ignore
except Exception:  # pragma: no cover - surfaced clearly at runtime
    asyncssh = None  # type: ignore


@dataclass(frozen=True)
class OpenClawTransportConfig:
    mode: str = "embedded_ssh"
    ssh_user: str = "yuanxiang"
    ssh_host: str = "100.105.66.46"
    local_host: str = "127.0.0.1"
    local_port: int = 18790
    remote_host: str = "127.0.0.1"
    remote_port: int = 18789
    connect_timeout_sec: float = 10.0
    reconnect_min_sec: float = 2.0
    reconnect_max_sec: float = 30.0
    keepalive_interval_sec: float = 30.0
    keepalive_count_max: int = 3


class OpenClawTransportManager:
    """Own the OpenClaw network path inside the HomeAIAgent process."""

    def __init__(self, config: OpenClawTransportConfig) -> None:
        self.config = config
        self.ready = asyncio.Event()
        self._stopping = False
        self._conn: Any | None = None
        self._listener: Any | None = None
        self._last_error = ""

    @property
    def last_error(self) -> str:
        return self._last_error

    async def wait_ready(self, timeout: float | None = None) -> bool:
        if self.config.mode == "direct":
            return True
        if self.ready.is_set():
            return True
        try:
            if timeout is None:
                await self.ready.wait()
            else:
                await asyncio.wait_for(self.ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def _local_port_is_open(self) -> bool:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.config.local_host, self.config.local_port),
                timeout=0.35,
            )
        except Exception:
            return False
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        return True

    async def run(self) -> None:
        mode = self.config.mode.strip().lower()
        if mode == "direct":
            self.ready.set()
            print("[OPENCLAW-TRANSPORT] mode=direct; embedded SSH disabled")
            await asyncio.Future()
            return
        if mode != "embedded_ssh":
            raise RuntimeError(f"unsupported OPENCLAW_TRANSPORT={self.config.mode!r}")
        if asyncssh is None:
            raise RuntimeError(
                "embedded SSH transport requires asyncssh==2.14.2; "
                "run ./run_full.sh so the persistent runtime can sync requirements"
            )

        delay = max(0.5, self.config.reconnect_min_sec)
        print(
            "[OPENCLAW-SSH] manager enabled "
            f"local={self.config.local_host}:{self.config.local_port} "
            f"remote={self.config.ssh_user}@{self.config.ssh_host}:"
            f"{self.config.remote_host}:{self.config.remote_port}"
        )

        while not self._stopping:
            self.ready.clear()
            self._last_error = ""
            try:
                if await self._local_port_is_open():
                    raise RuntimeError(
                        f"local port {self.config.local_host}:{self.config.local_port} "
                        "is already in use. Stop the legacy openclaw_air_tunnel.sh "
                        "or any old HomeAIAgent service before starting HomeAIAgent."
                    )

                print(
                    f"[OPENCLAW-SSH] connecting "
                    f"{self.config.ssh_user}@{self.config.ssh_host}"
                )
                self._conn = await asyncio.wait_for(
                    asyncssh.connect(
                        self.config.ssh_host,
                        username=self.config.ssh_user,
                        keepalive_interval=self.config.keepalive_interval_sec,
                        keepalive_count_max=self.config.keepalive_count_max,
                    ),
                    timeout=self.config.connect_timeout_sec,
                )
                print("[OPENCLAW-SSH] SSH connected")

                self._listener = await self._conn.forward_local_port(
                    self.config.local_host,
                    self.config.local_port,
                    self.config.remote_host,
                    self.config.remote_port,
                )
                self.ready.set()
                delay = max(0.5, self.config.reconnect_min_sec)
                print(
                    "[OPENCLAW-SSH] tunnel ready "
                    f"{self.config.local_host}:{self.config.local_port} -> "
                    f"{self.config.remote_host}:{self.config.remote_port}"
                )

                await self._conn.wait_closed()
                if not self._stopping:
                    print("[OPENCLAW-SSH-WARN] SSH connection closed; transport unavailable")

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                print(
                    f"[OPENCLAW-SSH-WARN] {self._last_error}; "
                    f"retry_in={delay:.1f}s"
                )
            finally:
                self.ready.clear()
                if self._listener is not None:
                    try:
                        self._listener.close()
                        await self._listener.wait_closed()
                    except Exception:
                        pass
                    self._listener = None
                if self._conn is not None:
                    try:
                        self._conn.close()
                        await self._conn.wait_closed()
                    except Exception:
                        pass
                    self._conn = None

            if not self._stopping:
                await asyncio.sleep(delay)
                delay = min(
                    max(self.config.reconnect_min_sec, delay * 2.0),
                    self.config.reconnect_max_sec,
                )

    async def close(self) -> None:
        self._stopping = True
        self.ready.clear()
        if self._listener is not None:
            try:
                self._listener.close()
                await self._listener.wait_closed()
            except Exception:
                pass
            self._listener = None
        if self._conn is not None:
            try:
                self._conn.close()
                await self._conn.wait_closed()
            except Exception:
                pass
            self._conn = None
