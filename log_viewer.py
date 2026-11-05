"""Bounded diagnostic log viewing and rotation following; no bridge lifecycle."""
import codecs
import os
import shutil
import subprocess
import time
import threading

from diagnostics import BACKUP_COUNT, log_path
from platform_support import IS_WINDOWS
from service_control import UNIT


def _journalctl_command(follow):
    return ["journalctl", "--user", "-u", UNIT, "--no-pager", "-n", "200"] + (
        ["-f"] if follow else [])


def _show_journal_service(follow):
    if IS_WINDOWS:
        return None
    executable = shutil.which("journalctl")
    if not executable:
        print("未找到 journalctl；无法显示 systemd 控制台日志。")
        return None
    command = [executable, *_journalctl_command(follow)[1:]]
    print("==> journalctl --user -u " + UNIT + " <==", flush=True)
    if not follow:
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        if result.stdout:
            print(result.stdout, end="", flush=True)
        return None
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None

    def pump():
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)

    threading.Thread(target=pump, daemon=True).start()
    return process


def _stop_journal(process):
    if process is None:
        return
    try:
        process.terminate()
    except OSError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()


def _log_files(include_service=False):
    try:
        primary = log_path()
    except (OSError, RuntimeError):
        return []
    candidates = [primary]
    candidates.extend(primary.with_name(f"{primary.name}.{number}")
                      for number in range(1, BACKUP_COUNT + 1))
    if include_service:
        candidates.extend((primary.parent / "service.log",
                           primary.parent / "service.log.1"))
    return [path for path in candidates if path.is_file()]


def _tail_stream(stream, limit=200):
    stream.seek(0, os.SEEK_END)
    end = stream.tell()
    boundary = end
    data = b""
    scanned = 0
    while end > 0 and scanned < 1024 * 1024:
        size = min(8192, end, 1024 * 1024 - scanned)
        end -= size
        scanned += size
        stream.seek(end)
        data = stream.read(size) + data
        if data.count(b"\n") > limit:
            break
    # Keep the cursor at the exact boundary whose tail was displayed, not a later stat().
    stream.seek(boundary)
    return data.decode("utf-8", errors="replace").splitlines()[-limit:]


def _tail_lines(path, limit=200):
    try:
        with path.open("rb") as stream:
            return _tail_stream(stream, limit)
    except (OSError, ValueError):
        return []


def _print_log_header(path):
    suffix = "（原始控制台输出，可能包含路径或错误文本）" \
        if path.name.startswith("service.log") else ""
    print(f"==> {path} <=={suffix}")


def _read_log_updates(files, positions, *, initial=False):
    # Rotated names are aliases for old file identities, not new streams to replay.
    for path in reversed(files):
        try:
            with path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                position = positions.get(identity)
                if initial:
                    _print_log_header(path)
                    print("\n".join(_tail_stream(stream)), flush=True)
                    position = stream.tell()
                else:
                    if position is None:
                        _print_log_header(path)
                        position = 0
                    if stat.st_size < position:
                        position = 0
                    stream.seek(position)
                    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                    while chunk := stream.read(65536):
                        print(decoder.decode(chunk), end="", flush=True)
                    # Retry an incomplete UTF-8 suffix on the next poll.
                    position = stream.tell() - len(decoder.getstate()[0])
                positions.pop(identity, None)
                positions[identity] = position
                while len(positions) > 2 * (BACKUP_COUNT + 3):
                    positions.pop(next(iter(positions)))
        except (OSError, ValueError):
            continue


def show_logs(follow=False, include_service=False):
    files = _log_files(include_service)
    if not files and not follow and (IS_WINDOWS or not include_service):
        label = "诊断日志" if include_service else "结构化诊断日志"
        print(f"暂无{label}；运行 start 或 service-start 后生成。")
        if not include_service:
            print("需要 supervisor 控制台输出时使用 --service / -Service。")
        return
    positions = {}
    _read_log_updates(files, positions, initial=True)
    journal = _show_journal_service(follow) if include_service else None
    if not follow:
        return
    try:
        if not files:
            hint = "" if include_service else "；supervisor 输出请加 --service / -Service"
            print(f"等待诊断日志{hint}；按 Ctrl+C 停止。", flush=True)
        while True:
            time.sleep(0.5)
            _read_log_updates(_log_files(include_service), positions)
    finally:
        _stop_journal(journal)
