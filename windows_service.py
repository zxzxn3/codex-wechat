"""Per-user Windows supervisor for the codex-wechat bridge.

The Task Scheduler starts this process for the current interactive user.  The
supervisor owns one bridge child, restarts it after exit code 75, and keeps the
child in a kill-on-close Job Object so stopping the task cannot leave an orphan.
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

RESTART_EXIT_CODE = 75
CONFIG_EXIT_CODE = 78
FAILURE_WINDOW_SECONDS = 300.0
MAX_FAILURES = 5
OWNER_STATUS_NAME = "service-owner.json"
SUPERVISOR_LOCK_NAME = "supervisor.lock"
LOG_LIMIT_BYTES = 5 * 1024 * 1024


def data_root() -> Path:
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


def _rotate_log(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size < LOG_LIMIT_BYTES:
            return
    except OSError:
        return
    rotated = path.with_name(path.name + ".1")
    try:
        rotated.unlink()
    except OSError:
        pass
    try:
        os.replace(path, rotated)
    except OSError:
        pass


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        stream.write(f"{stamp} supervisor: {message}\n")


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
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    return env


async def _spawn_bridge(command: list[str], project: Path, env: dict[str, str],
                        log_path: Path):
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW
    stream = log_path.open("ab", buffering=0)
    try:
        process = await asyncio.create_subprocess_exec(
            *command, cwd=str(project), env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=stream, stderr=asyncio.subprocess.STDOUT,
            creationflags=flags)
    except BaseException:
        stream.close()
        raise
    return process, stream


async def _stop_process(process, timeout: float = 5.0) -> None:
    if process is None or process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()


def restart_delay(returncode: int | None, backoff: float) -> float:
    if returncode == RESTART_EXIT_CODE:
        return 0.2
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
    if not (project / "codex_wechat.py").is_file():
        raise FileNotFoundError("codex_wechat.py was not found in the project directory")

    guard = SingleInstance(state_dir)
    try:
        guard.acquire()
    except AlreadyRunning:
        _rotate_log(log_path)
        _append_log(log_path, "another supervisor is already running")
        return 0

    _rotate_log(log_path)
    _append_log(log_path, f"starting for {project}")
    job = ChildJob()
    status_path = owner_status_path(state_dir)
    token = secrets.token_urlsafe(32)
    python = bridge_python(project, str(python) if python else None)
    command = bridge_command(project, state_dir, config, python)
    backoff = 1.0
    failures: list[float] = []
    process = None
    stream = None
    try:
        job.open()
        while True:
            stream = None
            process = None
            try:
                process, stream = await _spawn_bridge(
                    command, project, child_environment(status_path, token), log_path)
                job.assign(process.pid)
                _atomic_json(status_path, {
                    "version": 1,
                    "supervisor_pid": os.getpid(),
                    "child_pid": process.pid,
                    "token": token,
                    "state_dir": str(state_dir),
                    "started_at": time.time(),
                })
                _append_log(log_path, f"bridge started pid={process.pid}")
                returncode = await process.wait()
                _append_log(log_path, f"bridge exited code={returncode}")
            except OSError as error:
                _append_log(log_path, f"bridge start failed: {error}")
                returncode = None
                if process is not None:
                    try:
                        await _stop_process(process)
                    except OSError:
                        pass
            finally:
                remove_owner_status(status_path, token)
                if stream is not None:
                    stream.close()
            if returncode == 0:
                _append_log(log_path, "bridge exited normally")
                return 0
            if returncode == CONFIG_EXIT_CODE:
                _append_log(log_path, "bridge configuration failed; not restarting")
                return CONFIG_EXIT_CODE
            now = time.monotonic()
            failures[:] = [stamp for stamp in failures
                           if now - stamp < FAILURE_WINDOW_SECONDS]
            if not failures:
                backoff = 1.0
            if returncode is None or returncode != RESTART_EXIT_CODE:
                if note_failure(failures, now):
                    _append_log(log_path, "bridge failure limit reached; giving up")
                    return 1
            delay = restart_delay(returncode, backoff)
            if returncode == RESTART_EXIT_CODE:
                backoff = 1.0
            else:
                backoff = min(backoff * 2, 60.0)
            _append_log(log_path, f"restarting in {delay:g}s")
            await asyncio.sleep(delay)
    finally:
        remove_owner_status(status_path, token)
        if stream is not None:
            stream.close()
        await _stop_process(process)
        job.close()
        guard.release()
        _append_log(log_path, "supervisor stopped")


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
    try:
        return asyncio.run(supervise(
            args.project, state_dir, config, log_path, python=args.python))
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 0
    except Exception as error:
        try:
            _append_log(log_path, f"fatal: {type(error).__name__}: {error}")
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
