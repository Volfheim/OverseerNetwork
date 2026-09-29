import asyncio
import contextlib
from typing import Dict, List, Optional

import asyncssh
from fastapi import WebSocket
from loguru import logger

from app.config import ServerConfig
from app.system_probe import collect_local_metrics
from app.ssh_transport import connection_options


class VaultSSHClient(asyncssh.SSHClient):
    pass


class TerminalSession:
    def __init__(self, server_id: str, server: ServerConfig):
        self.server_id = server_id
        self.server = server
        self.websockets: List[WebSocket] = []
        self.history = ""
        self.process = None
        self.stdin_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        self.task: Optional[asyncio.Task] = None
        self.is_running = False
        self.cols = 80
        self.rows = 24

    def start(self) -> None:
        self.is_running = True
        if self.server.type == "local":
            self.task = asyncio.create_task(self._run_local())
        else:
            self.task = asyncio.create_task(self._run_ssh())

    async def broadcast(self, data: str) -> None:
        self.history += data
        if len(self.history) > 15000:
            self.history = self.history[-15000:]

        for ws in list(self.websockets):
            try:
                await ws.send_text(data)
            except Exception:
                with contextlib.suppress(ValueError):
                    self.websockets.remove(ws)

    async def _run_ssh(self) -> None:
        conn = None
        worker_tasks: List[asyncio.Task] = []
        try:
            conn = await asyncssh.connect(**connection_options(self.server), client_factory=VaultSSHClient)

            async with conn.create_process(
                term_type="xterm-256color",
                term_size=(self.cols, self.rows),
            ) as process:
                self.process = process

                async def read_stdout() -> None:
                    try:
                        while not process.stdout.at_eof():
                            data = await process.stdout.read(4096)
                            if data:
                                await self.broadcast(data)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(f"stdout closed on {self.server_id}: {exc}")

                async def read_stderr() -> None:
                    try:
                        while not process.stderr.at_eof():
                            data = await process.stderr.read(4096)
                            if data:
                                await self.broadcast(f"\x1b[31m{data}\x1b[0m")
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(f"stderr closed on {self.server_id}: {exc}")

                async def forward_stdin() -> None:
                    try:
                        while self.is_running:
                            data = await self.stdin_queue.get()
                            if data is None:
                                break
                            process.stdin.write(data)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(f"stdin closed on {self.server_id}: {exc}")

                strict_note = "STRICT HOST KEY" if self.server.strict_host_key else "TRUST-ON-USE DISABLED"
                await self.broadcast(
                    f"\x1b[32m[SYSTEM] SECURE UPLINK ESTABLISHED: "
                    f"{self.server.name.upper()} [{strict_note}]\x1b[0m\r\n"
                )

                worker_tasks = [
                    asyncio.create_task(read_stdout()),
                    asyncio.create_task(read_stderr()),
                    asyncio.create_task(forward_stdin()),
                ]
                await asyncio.wait(worker_tasks, return_when=asyncio.FIRST_COMPLETED)

                for task in worker_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*worker_tasks, return_exceptions=True)

                returncode = process.returncode
                if returncode is None:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(process.wait(), timeout=2)
                    returncode = process.returncode

                if returncode is not None:
                    await self.broadcast(
                        f"\r\n\x1b[32m[SYSTEM] SESSION TERMINATED "
                        f"(EXIT CODE: {returncode}). DISCONNECTING...\x1b[0m\r\n"
                    )
                    await asyncio.sleep(0.5)
                    await self.broadcast("OVERSEER_ACTION_CLOSE_TERMINAL")

        except asyncio.CancelledError:
            raise
        except (asyncssh.Error, OSError) as exc:
            await self.broadcast(
                "\r\n\x1b[31m[CRITICAL] UPLINK LOST. "
                f"NETWORK, AUTH OR HOST KEY ERROR:\r\n{exc}\x1b[0m\r\n"
            )
            logger.warning(f"SSH connection error on {self.server_id}: {exc}")
        except Exception as exc:
            await self.broadcast(
                "\r\n\x1b[31m[FATAL ERROR] OVERSEER CORE SESSION FAULT:\r\n"
                f"{exc}\x1b[0m\r\n"
            )
            logger.exception(f"Fatal error in SSH session on {self.server_id}")
        finally:
            for task in worker_tasks:
                if not task.done():
                    task.cancel()
            self._cleanup()
            if conn:
                conn.close()
                with contextlib.suppress(Exception):
                    await conn.wait_closed()

    def _generate_bar(self, percentage: float, length: int = 20) -> str:
        value = max(0, min(100, percentage))
        filled_length = int(length * value // 100)
        return f"[{'|' * filled_length}{'.' * (length - filled_length)}]"

    async def _run_local(self) -> None:
        input_task: Optional[asyncio.Task] = None
        try:
            await self.broadcast(
                f"\x1b[33m[OVERSEER HQ] LOCAL DIAGNOSTICS CHANNEL - "
                f"{self.server.name.upper()}\x1b[0m\r\n"
            )
            await asyncio.sleep(0.5)

            async def listen_for_exit() -> None:
                while self.is_running:
                    data = await self.stdin_queue.get()
                    if data is None or "\x03" in data:
                        self.is_running = False
                        break

            input_task = asyncio.create_task(listen_for_exit())

            while self.is_running:
                metrics = await asyncio.to_thread(collect_local_metrics)
                gpu = metrics.get("gpu")

                output = "\x1b[2J\x1b[H"
                output += "\x1b[32m" + "=" * 58 + "\r\n"
                output += "  ROBCO INDUSTRIES UNIFIED OPERATING SYSTEM\r\n"
                output += "  OVERSEER HQ - LOCAL HARDWARE DIAGNOSTICS\r\n"
                output += "=" * 58 + "\r\n\r\n"
                output += f"  HOST: {metrics['hostname']}\r\n"
                output += f"  OS:   {metrics['platform']}\r\n\r\n"
                output += f"  CPU: {metrics['cpu_name']}\r\n"
                output += f"  {self._generate_bar(metrics['cpu_percent'])} LOAD: {metrics['cpu_percent']:.1f}%\r\n\r\n"
                if gpu:
                    output += f"  GPU: {gpu['name']}\r\n"
                    output += f"  {self._generate_bar(gpu['load_percent'])} LOAD: {gpu['load_percent']:.1f}%\r\n"
                    output += f"  VRAM: {gpu['vram_used_mb']:.0f} MB / {gpu['vram_total_mb']:.0f} MB\r\n\r\n"
                else:
                    output += "  GPU: N/A\r\n\r\n"
                output += f"  SYS RAM: {metrics['ram_used_gb']:.1f} GB / {metrics['ram_total_gb']:.1f} GB\r\n"
                output += f"  {self._generate_bar(metrics['ram_percent'])} RAM: {metrics['ram_percent']:.1f}%\r\n\r\n"
                output += f"  STORAGE: {metrics['disk_used_gb']:.1f} GB / {metrics['disk_total_gb']:.1f} GB\r\n"
                output += f"  {self._generate_bar(metrics['disk_percent'])} DISK: {metrics['disk_percent']:.1f}%\r\n\r\n"
                output += "=" * 58 + "\r\n"
                output += "\x1b[33m  STATUS: NOMINAL. CTRL+C TERMINATES LOCAL FEED.\x1b[0m\r\n"

                self.history = output
                for ws in list(self.websockets):
                    try:
                        await ws.send_text(output)
                    except Exception:
                        with contextlib.suppress(ValueError):
                            self.websockets.remove(ws)

                await asyncio.sleep(2)

            await self.broadcast("\r\n\x1b[31m[SYSTEM] LOCAL FEED CLOSED.\x1b[0m\r\n")
            await asyncio.sleep(0.5)
            await self.broadcast("OVERSEER_ACTION_CLOSE_TERMINAL")

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(f"Local session error on {self.server_id}")
        finally:
            if input_task and not input_task.done():
                input_task.cancel()
            self._cleanup()

    def resize_pty(self, cols: int, rows: int) -> None:
        self.cols = max(20, min(int(cols), 300))
        self.rows = max(5, min(int(rows), 100))
        if self.process:
            try:
                self.process.change_terminal_size(self.cols, self.rows)
            except Exception as exc:
                logger.warning(f"Failed to resize PTY on {self.server_id}: {exc}")

    def _cleanup(self) -> None:
        self.is_running = False
        self.process = None


class SessionManager:
    def __init__(self):
        self.active_sessions: Dict[str, TerminalSession] = {}

    def get_or_create_session(self, server_id: str, server: ServerConfig) -> TerminalSession:
        if server_id not in self.active_sessions or not self.active_sessions[server_id].is_running:
            session = TerminalSession(server_id, server)
            self.active_sessions[server_id] = session
            session.start()
        return self.active_sessions[server_id]

    def kill_session(self, server_id: str) -> None:
        if server_id not in self.active_sessions:
            return

        session = self.active_sessions.pop(server_id)
        session.is_running = False
        session.stdin_queue.put_nowait(None)

        for ws in list(session.websockets):
            try:
                asyncio.create_task(ws.send_text("OVERSEER_ACTION_CLOSE_TERMINAL"))
                asyncio.create_task(ws.close())
            except Exception:
                pass
        session.websockets.clear()

        if session.process:
            with contextlib.suppress(Exception):
                session.process.terminate()

        if session.task and not session.task.done():
            session.task.cancel()

    def kill_all_for_server(self, server_id: str) -> None:
        self.kill_session(server_id)


session_manager = SessionManager()
