"""Per-user Windows supervisor for the codex-wechat bridge.

The Task Scheduler starts this process for the current interactive user.  The
supervisor owns one bridge child, restarts it after a crash, and keeps the
child in a kill-on-close Job Object so stopping the task cannot leave an orphan.
A user restart returns 76 so the bootstrap supervisor re-resolves the active
release; this module never parses the install layout itself.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

from log_rotation import (DEFAULT_KEEP, KeepSource, append as append_log,
                          keep_from_config)

RESTART_EXIT_CODE = 75
RELEASE_SWITCH_EXIT_CODE = 76
CONFIG_EXIT_CODE = 78
FAILURE_WINDOW_SECONDS = 300.0
MAX_FAILURES = 5
OWNER_STATUS_NAME = "service-owner.json"
SUPERVISOR_LOCK_NAME = "supervisor.lock"
OUTPUT_DRAIN_TIMEOUT_SECONDS = 5.0


def data_root() -> Path:
    """Resolve the configured data root; the marker is the only source of truth."""
    try:
        from platform_support import data_root as resolve_data_root
    except ImportError:
        pass
    else:
        try:
            return resolve_data_root()
        except RuntimeError:
            pass
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "codex-wechat"
    return Path.home() / "AppData" / "Local" / "codex-wechat"


def owner_status_path(state_dir: Path) -> Path:
    return Path(state_dir) / OWNER_STATUS_NAME


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    os.replace(temp, path)


def read_owner_status(path: Path) -> dict | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def remove_owner_status(path: Path, token: str) -> None:
    current = read_owner_status(path)
    if current and hmac.compare_digest(str(current.get("token", "")), token):
        try:
            Path(path).unlink()
        except OSError:
            pass


def discard_owner_status(path: Path) -> None:
    """Drop a record left by a stop or crash that skipped the normal cleanup.

    Only called while the single-instance guard is held, so no live supervisor
    owns the record.
    """
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _append_log(path: Path, message: str, *, keep: KeepSource = DEFAULT_KEEP) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    append_log(path, f"{stamp} supervisor: {message}\n", keep)


def _mutex_name(state_dir: Path) -> str:
    normalized = str(Path(state_dir).resolve()).casefold()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"Local\\CodexWeChat-{digest}"


class AlreadyRunning(RuntimeError):
    pass


class SingleInstance:
    """One supervisor per state directory; Local namespace avoids admin rights."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self._handle = None
        self._stream = None

    def acquire(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            import win32api
            import win32event
            import winerror
            handle = win32event.CreateMutex(None, False, _mutex_name(self.state_dir))
            if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
                handle.Close()
                raise AlreadyRunning("another supervisor owns this state directory")
            self._handle = handle
            return
        import fcntl
        path = self.state_dir / SUPERVISOR_LOCK_NAME
        self._stream = path.open("a+")
        try:
            fcntl.flock(self._stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self._stream.close()
            self._stream = None
            raise AlreadyRunning("another supervisor owns this state directory") from error

    def release(self) -> None:
        if self._handle is not None:
            self._handle.Close()
            self._handle = None
        if self._stream is not None:
            try:
                import fcntl
                fcntl.flock(self._stream, fcntl.LOCK_UN)
            finally:
                self._stream.close()
                self._stream = None


class ChildJob:
    """Kill only the bridge child when the supervisor process disappears.

    Silent breakaway keeps the child's own Codex processes outside this Job
    Object; the bridge owns that lifecycle and must not kill a shared daemon.
    """

    def __init__(self):
        self._handle = None

    def open(self) -> None:
        if os.name != "nt":
            return
        import win32job
        # pywin32 requires a string name (unlike Win32's nullable lpName).
        handle = win32job.CreateJobObject(None, "Local\\CodexWeChatChild-" + secrets.token_hex(16))
        try:
            info = win32job.QueryInformationJobObject(
                handle, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] = (
                win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE |
                win32job.JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK)
            win32job.SetInformationJobObject(
                handle, win32job.JobObjectExtendedLimitInformation, info)
        except BaseException:
            handle.Close()
            raise
        self._handle = handle

    def assign(self, pid: int) -> None:
        if self._handle is None:
            return
        import win32api
        import win32con
        import win32job
        process = win32api.OpenProcess(
            win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, int(pid))
        try:
            win32job.AssignProcessToJobObject(self._handle, process)
        finally:
            process.Close()  # pyright: ignore[reportAttributeAccessIssue]

    def close(self) -> None:
        if self._handle is not None:
            self._handle.Close()
            self._handle = None


def parent_pid(pid: int) -> int | None:
    """Return the parent process id on Windows using a read-only snapshot."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel32.Process32FirstW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None
        while True:
            if entry.th32ProcessID == int(pid):
                return int(entry.th32ParentProcessID)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                return None
    finally:
        kernel32.CloseHandle(snapshot)


def bridge_python(project: Path, explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    candidate = Path(project) / ".venv" / "Scripts" / "python.exe"
    return candidate if candidate.exists() else Path(sys.executable)


def bridge_command(project: Path, state_dir: Path, config: Path,
                   python: Path) -> list[str]:
    return [
        str(python), "-u", str(Path(project) / "codex_wechat.py"), "start",
        "--state-dir", str(state_dir), "--config", str(config),
    ]


def child_environment(status_path: Path, token: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "AGENT_WECHAT_SERVICE_OWNER": str(status_path),
        "AGENT_WECHAT_SERVICE_TOKEN": token,
        "CODEX_WECHAT_SERVICE_CHILD": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    return env


async def _spawn_bridge(command: list[str], project: Path, env: dict[str, str],
                        log_path: Path, keep: KeepSource):
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW
    process = await asyncio.create_subprocess_exec(
        *command, cwd=str(project), env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        creationflags=flags)
    pump = asyncio.create_task(_pump_output(process.stdout, log_path, keep))
    return process, pump


async def _pump_output(stream, log_path: Path, keep: KeepSource) -> None:
    if stream is None:
        return
    try:
        while chunk := await stream.read(65536):
            append_log(log_path, chunk, keep)
    except (OSError, ValueError):
        return


async def _drain_pump(pump, timeout: float | None = None) -> None:
    if pump is None:
        return
    if timeout is None:
        timeout = OUTPUT_DRAIN_TIMEOUT_SECONDS
    try:
        await asyncio.wait_for(pump, timeout)
    except TimeoutError:
        # A descendant can keep the inherited stdout pipe open after the
        # bridge itself exits. Do not let log draining block supervision.
        await _cancel_pump(pump)
    except (OSError, ValueError):
        pass


async def _cancel_pump(pump) -> None:
    if pump is None:
        return
    if not pump.done():
        pump.cancel()
    try:
        await pump
    except (asyncio.CancelledError, OSError, ValueError):
        pass


async def _stop_process(process, timeout: float = 5.0) -> None:
    if process is None or process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()


def restart_delay(backoff: float) -> float:
    return min(max(1.0, backoff), 60.0)


def note_failure(failures: list[float], now: float) -> bool:
    """Record an abnormal exit and report whether the retry budget is spent."""
    failures[:] = [stamp for stamp in failures
                   if now - stamp < FAILURE_WINDOW_SECONDS]
    failures.append(now)
    return len(failures) >= MAX_FAILURES


async def supervise(project: Path, state_dir: Path, config: Path, log_path: Path,
                    *, python: Path | None = None) -> int:
    project = Path(project).resolve()
    state_dir = Path(state_dir).resolve()
    config = Path(config).resolve()
    log_path = Path(log_path).resolve()

    def log_keep():
        return keep_from_config(config)

    if not (project / "codex_wechat.py").is_file():
        raise FileNotFoundError("codex_wechat.py was not found in the project directory")

    guard = SingleInstance(state_dir)
    try:
        guard.acquire()
    except AlreadyRunning:
        _append_log(log_path, "another supervisor is already running", keep=log_keep)
        return 0

    _append_log(log_path, f"starting for {project}", keep=log_keep)
    job = ChildJob()
    status_path = owner_status_path(state_dir)
    discard_owner_status(status_path)
    token = secrets.token_urlsafe(32)
    python = bridge_python(project, str(python) if python else None)
    command = bridge_command(project, state_dir, config, python)
    backoff = 1.0
    failures: list[float] = []
    process = None
    pump = None
    try:
        job.open()
        while True:
            pump = None
            process = None
            try:
                process, pump = await _spawn_bridge(
                    command, project, child_environment(status_path, token),
                    log_path, log_keep)
                job.assign(process.pid)
                _atomic_json(status_path, {
                    "version": 1,
                    "supervisor_pid": os.getpid(),
                    "child_pid": process.pid,
                    "token": token,
                    "state_dir": str(state_dir),
                    "started_at": time.time(),
                })
                _append_log(log_path, f"bridge started pid={process.pid}", keep=log_keep)
                returncode = await process.wait()
                await _drain_pump(pump)
                pump = None
                _append_log(log_path, f"bridge exited code={returncode}", keep=log_keep)
            except OSError as error:
                _append_log(log_path, f"bridge start failed: {error}", keep=log_keep)
                returncode = None
                if process is not None:
                    try:
                        await _stop_process(process)
                    except OSError:
                        pass
                await _cancel_pump(pump)
                pump = None
            finally:
                remove_owner_status(status_path, token)
            if returncode == 0:
                _append_log(log_path, "bridge exited normally", keep=log_keep)
                return 0
            if returncode == CONFIG_EXIT_CODE:
                _append_log(log_path, "bridge configuration failed; not restarting",
                            keep=log_keep)
                return CONFIG_EXIT_CODE
            if returncode == RESTART_EXIT_CODE:
                # Only the bootstrap knows the install layout; hand the restart back.
                _append_log(log_path, "user restart requested; handing back to the bootstrap",
                            keep=log_keep)
                return RELEASE_SWITCH_EXIT_CODE
            now = time.monotonic()
            failures[:] = [stamp for stamp in failures
                           if now - stamp < FAILURE_WINDOW_SECONDS]
            if not failures:
                backoff = 1.0
            if note_failure(failures, now):
                _append_log(log_path, "bridge failure limit reached; giving up",
                            keep=log_keep)
                return 1
            delay = restart_delay(backoff)
            backoff = min(backoff * 2, 60.0)
            _append_log(log_path, f"restarting in {delay:g}s", keep=log_keep)
            await asyncio.sleep(delay)
    finally:
        remove_owner_status(status_path, token)
        await _cancel_pump(pump)
        await _stop_process(process)
        job.close()
        guard.release()
        _append_log(log_path, "supervisor stopped", keep=log_keep)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="action", required=True)
    run = sub.add_parser(
        "supervise", aliases=["supervisor"], help="supervise the bridge child")
    run.add_argument("--project", type=Path, required=True)
    run.add_argument("--state-dir", type=Path)
    run.add_argument("--config", type=Path)
    run.add_argument("--log", type=Path)
    run.add_argument("--python", type=Path)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    root = data_root()
    state_dir = args.state_dir or root / ".state"
    config = args.config or root / "config.json"
    log_path = args.log or root / "logs" / "service.log"

    def log_keep():
        return keep_from_config(config)

    try:
        return asyncio.run(supervise(
            args.project, state_dir, config, log_path, python=args.python))
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 0
    except Exception as error:
        try:
            _append_log(log_path, f"fatal: {type(error).__name__}: {error}",
                        keep=log_keep)
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
