"""Offline contracts for the Windows per-user service; no Windows calls."""
import asyncio
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from codex_wechat import service_control
from codex_wechat import windows_service


ROOT = Path(__file__).resolve().parents[1]


class ServiceControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_requested = service_control._restart_requested
        service_control._restart_requested = False

    def tearDown(self):
        service_control._restart_requested = self.old_requested

    async def test_linux_restart_and_exit_code_are_unchanged(self):
        with patch.object(service_control, "WINDOWS", False), \
             patch.object(service_control, "systemctl", AsyncMock()) as systemctl:
            await service_control.restart()
        systemctl.assert_awaited_once_with("--no-block", "restart", service_control.UNIT)
        with patch.object(service_control, "WINDOWS", False):
            self.assertEqual(service_control.exit_code(), 0)

    async def test_windows_restart_requests_sigint_and_exit_75(self):
        with patch.object(service_control, "WINDOWS", True), \
             patch.object(service_control.signal, "raise_signal") as raise_signal:
            await service_control.restart()
            self.assertEqual(service_control.exit_code(), 75)
        raise_signal.assert_called_once_with(signal.SIGINT)

    def test_windows_owner_checks_token_pid_and_direct_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "owner.json"
            path.write_text(json.dumps({
                "token": "secret-token",
                "child_pid": os.getpid(),
                "supervisor_pid": 4321,
            }), encoding="utf-8")
            with patch.object(service_control, "WINDOWS", True), \
                 patch.dict(os.environ, {
                     "AGENT_WECHAT_SERVICE_OWNER": str(path),
                     "AGENT_WECHAT_SERVICE_TOKEN": "secret-token",
                 }, clear=False), \
                 patch.object(windows_service, "parent_pid", return_value=4321):
                service_control._require_windows_owner()
            with patch.dict(os.environ, {
                    "AGENT_WECHAT_SERVICE_OWNER": str(path),
                    "AGENT_WECHAT_SERVICE_TOKEN": "wrong",
                 }, clear=False), \
                 patch.object(windows_service, "parent_pid", return_value=4321):
                with self.assertRaises(ValueError):
                    service_control._require_windows_owner()
            with patch.dict(os.environ, {
                    "AGENT_WECHAT_SERVICE_OWNER": str(path),
                    "AGENT_WECHAT_SERVICE_TOKEN": "secret-token",
                 }, clear=False), \
                 patch.object(windows_service, "parent_pid", return_value=9999):
                with self.assertRaises(ValueError):
                    service_control._require_windows_owner()


    def test_windows_owner_accepts_only_the_recorded_venv_launcher_chain(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "owner.json"
            path.write_text(json.dumps({"token": "secret-token", "child_pid": 1234,
                                        "supervisor_pid": 4321}), encoding="utf-8")
            parent = {os.getpid(): 1234, 1234: 4321}
            with patch.dict(os.environ, {"AGENT_WECHAT_SERVICE_OWNER": str(path),
                                        "AGENT_WECHAT_SERVICE_TOKEN": "secret-token"}), \
                    patch.object(windows_service, "parent_pid", side_effect=parent.get), \
                    patch.object(service_control, "_is_venv_launcher", return_value=True) as launcher:
                service_control._require_windows_owner()
                launcher.assert_called_once_with(1234)
                launcher.return_value = False
                with self.assertRaises(ValueError):
                    service_control._require_windows_owner()
                launcher.return_value = True
                parent[1234] = 9999
                with self.assertRaises(ValueError):
                    service_control._require_windows_owner()


class WindowsServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch.object(windows_service, "ChildJob"))

    def test_bridge_command_and_environment_keep_token_out_of_argv(self):
        command = windows_service.bridge_command(
            Path("C:/codex-wechat"), Path("C:/data/.state"),
            Path("C:/data/config.json"), Path("C:/codex-wechat/.venv/Scripts/python.exe"))
        self.assertNotIn("token", " ".join(command).lower())
        env = windows_service.child_environment(Path("C:/data/owner.json"), "abc")
        self.assertEqual(env["AGENT_WECHAT_SERVICE_TOKEN"], "abc")
        self.assertEqual(env["CODEX_WECHAT_SERVICE_CHILD"], "1")
        self.assertEqual(env["PYTHONUTF8"], "1")

    def test_owner_status_atomic_write_and_token_scoped_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "owner.json"
            windows_service._atomic_json(path, {"token": "one", "child_pid": 1})
            self.assertEqual(windows_service.read_owner_status(path)["child_pid"], 1)
            windows_service.remove_owner_status(path, "wrong")
            self.assertTrue(path.exists())
            windows_service.remove_owner_status(path, "one")
            self.assertFalse(path.exists())

    def test_restart_delay(self):
        self.assertEqual(windows_service.restart_delay(4), 4)
        self.assertEqual(windows_service.restart_delay(0.05), 1)
        self.assertEqual(windows_service.restart_delay(100), 60)

    def test_failure_window_allows_five_then_resets_after_five_minutes(self):
        failures = []
        for now in range(4):
            self.assertFalse(windows_service.note_failure(failures, float(now)))
        self.assertTrue(windows_service.note_failure(failures, 4.0))
        self.assertEqual(len(failures), 5)

        old = [0.0, 1.0, 2.0, 3.0]
        self.assertFalse(windows_service.note_failure(old, 304.0))
        self.assertEqual(old, [304.0])

    async def test_child_stdout_is_pumped_into_the_service_log(self):
        class Stream:
            def __init__(self):
                self.chunks = [b"first\n", b"second\n", b""]

            async def read(self, size):
                return self.chunks.pop(0)

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "service.log"
            await windows_service._pump_output(Stream(), path, 3)
            self.assertEqual(path.read_text(encoding="utf-8"),
                             "first\nsecond\n")

    async def test_output_pump_wait_is_bounded_when_a_pipe_never_closes(self):
        event = asyncio.Event()
        pump = asyncio.create_task(event.wait())
        await windows_service._drain_pump(pump, timeout=0.01)
        self.assertTrue(pump.done())

    def test_single_supervisor_per_state_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            first = windows_service.SingleInstance(Path(temp))
            second = windows_service.SingleInstance(Path(temp))
            first.acquire()
            try:
                with self.assertRaises(windows_service.AlreadyRunning):
                    second.acquire()
            finally:
                first.release()
            second.acquire()
            second.release()

    async def test_user_restart_returns_switch_code_and_removes_owner_status(self):
        class Process:
            pid = 1234
            returncode = 75

            async def wait(self):
                return 75

        calls = []
        spawn = AsyncMock(side_effect=lambda *args, **kwargs: (
            calls.append(1) or (Process(), None)
        ))
        sleep = AsyncMock()

        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / ".state"
            log = Path(temp) / "service.log"
            owner = windows_service.owner_status_path(state)
            with patch.object(windows_service, "_spawn_bridge", spawn), \
                 patch.object(windows_service.asyncio, "sleep", sleep):
                result = await windows_service.supervise(
                    ROOT, state, Path(temp) / "config.json", log)
            self.assertEqual(result, windows_service.RELEASE_SWITCH_EXIT_CODE)
            self.assertEqual(spawn.await_count, 1)
            sleep.assert_not_awaited()
            self.assertFalse(owner.exists())
            self.assertIn("handing back to the bootstrap", log.read_text(encoding="utf-8"))

    async def test_supervisor_exits_without_restart_on_normal_or_config_exit(self):
        for returncode in (0, 78):
            class Process:
                pid = 1234

                def __init__(self, returncode):
                    self.returncode = returncode

                async def wait(self):
                    return self.returncode

            spawn = AsyncMock(side_effect=lambda *args, returncode=returncode, **kwargs:
                              (Process(returncode), None))
            sleep = AsyncMock()
            with tempfile.TemporaryDirectory() as temp:
                with patch.object(windows_service, "_spawn_bridge", spawn), \
                     patch.object(windows_service.asyncio, "sleep", sleep):
                    result = await windows_service.supervise(
                        ROOT, Path(temp) / ".state",
                        Path(temp) / "config.json", Path(temp) / "service.log")
            self.assertEqual(result, returncode)
            self.assertEqual(spawn.await_count, 1)
            sleep.assert_not_awaited()

    async def test_supervisor_stops_after_five_abnormal_exits(self):
        class Process:
            pid = 1234
            returncode = 1

            async def wait(self):
                return 1

        spawn = AsyncMock(side_effect=lambda *args, **kwargs: (Process(), None))
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(windows_service, "_spawn_bridge", spawn), \
                 patch.object(windows_service.asyncio, "sleep", AsyncMock()):
                result = await windows_service.supervise(
                    ROOT, Path(temp) / ".state",
                    Path(temp) / "config.json", Path(temp) / "service.log")
        self.assertEqual(result, 1)
        self.assertEqual(spawn.await_count, 5)

    async def test_supervisor_retries_spawn_failures_then_gives_up(self):
        spawn = AsyncMock(side_effect=OSError("python executable disappeared"))
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(windows_service, "_spawn_bridge", spawn), \
                 patch.object(windows_service.asyncio, "sleep", AsyncMock()):
                result = await windows_service.supervise(
                    ROOT, Path(temp) / ".state",
                    Path(temp) / "config.json", Path(temp) / "service.log")
        self.assertEqual(result, 1)
        self.assertEqual(spawn.await_count, 5)

    async def test_startup_discards_a_stale_owner_record(self):
        class Process:
            pid = 1234
            returncode = 0

            async def wait(self):
                return 0

        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / ".state"
            state.mkdir()
            status_path = windows_service.owner_status_path(state)
            status_path.write_text(json.dumps({
                "version": 1, "supervisor_pid": 111, "child_pid": 222,
                "token": "stale", "state_dir": str(state), "started_at": 0}),
                encoding="utf-8")
            seen = []

            async def spawn(command, project, env, log_path, keep):
                # The new supervisor must not inherit the previous owner record.
                seen.append(status_path.exists())
                return Process(), None

            with patch.object(windows_service, "_spawn_bridge", spawn):
                result = await windows_service.supervise(
                    ROOT, state, Path(temp) / "config.json",
                    Path(temp) / "service.log")
        self.assertEqual(result, 0)
        self.assertEqual(seen, [False])
        self.assertFalse(status_path.exists())

    def test_scripts_pin_current_user_task_and_power_settings(self):
        install = (ROOT / "install.ps1").read_text(encoding="utf-8")
        entry = (ROOT / "codex-wechat.ps1").read_text(encoding="utf-8")
        task = (ROOT / "windows/task-service.ps1").read_text(encoding="utf-8")
        service = (ROOT / "codex_wechat/windows_service.py").read_text(encoding="utf-8")
        adapter = (ROOT / "codex_wechat/service_adapter.py").read_text(encoding="utf-8")
        self.assertIn("New-ScheduledTaskTrigger -AtLogOn", install)
        self.assertIn("-LogonType Interactive", install)
        self.assertIn("-RunLevel Limited", install)
        self.assertIn("-AllowStartIfOnBatteries", install)
        self.assertIn("-DontStopIfGoingOnBatteries", install)
        self.assertIn("-ExecutionTimeLimit ([TimeSpan]::Zero)", install)
        self.assertIn("-MultipleInstances IgnoreNew", install)
        self.assertNotIn("-RestartCount", install)
        self.assertNotIn("-RestartInterval", install)
        self.assertNotIn("schtasks.exe", install.lower())
        self.assertIn("$trigger.Enabled = $autostart", install)
        self.assertIn("Set-ScheduledTask", install)
        self.assertIn("-LogonType Interactive", install)
        self.assertIn("[ValidateSet('true', 'false')]", entry)
        self.assertIn("autostart", entry)
        self.assertIn("windows/task-service.ps1", adapter)
        self.assertIn("[char]::IsWhiteSpace", task)
        self.assertIn(r"^[a-f0-9]{16}$", entry)
        self.assertIn("[char]::IsWhiteSpace", install)
        self.assertIn("$trigger.Enabled = $enabled", task)
        self.assertIn("Set-ScheduledTask -TaskName $TaskName -Trigger $allTriggers", task)
        self.assertIn("Enable-ScheduledTask", task)
        self.assertNotIn("Disable-ScheduledTask", task)
        self.assertIn("'service-start'", entry)
        self.assertIn("'service-restart'", entry)
        self.assertNotIn("'service-stop'", entry)
        self.assertNotIn("'service-status'", entry)
        self.assertIn("'autostart'", entry)
        self.assertIn("'uninstall'", entry)
        self.assertNotIn("Wait-ServiceStable", task)
        self.assertNotIn("'restart'", task)
        self.assertIn("Write-AutostartStatus $false $false", task)
        self.assertNotIn("Wait-TaskStopped", task)
        self.assertIn("Stop-ScheduledTask", task)
        self.assertIn("Start-ScheduledTask", task)
        # Get-Command returns every match, and Windows ships a 0-byte python.exe
        # alias under WindowsApps; taking the array produced two paths.
        self.assertIn("(Get-Item -LiteralPath $_.Source).Length -gt 0", entry)
        self.assertIn("Select-Object -First 1", entry)
        self.assertIn("JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE", service)
        self.assertIn("JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK", service)
        self.assertNotIn("taskkill", service.lower())

    def test_documented_restart_does_not_restart_scheduled_task(self):
        service = (ROOT / "codex_wechat/service_control.py").read_text(encoding="utf-8")
        windows = service[service.index("async def restart():"):]
        self.assertIn("RESTART_EXIT_CODE", windows)
        self.assertIn("raise_signal(signal.SIGINT)", windows)
        self.assertNotIn("schtasks", windows.lower())
        self.assertNotIn("Stop-ScheduledTask", windows)

    def test_uninstall_preserves_data_by_default(self):
        text = (ROOT / "uninstall.ps1").read_text(encoding="utf-8")
        self.assertIn("if ($PurgeData)", text)
        # Both leftovers are named: the tree it cannot remove while running out of
        # it, and the data it deliberately keeps.
        self.assertIn("程序文件仍在: $InstallRoot", text)
        self.assertIn("数据文件仍在: $DataRoot", text)
        self.assertIn("数据文件已删除: $DataRoot", text)
        self.assertIn("手动删除上述目录", text)
        # Same order as the Linux helper: program files, data, then the decision.
        self.assertLess(text.index("程序文件仍在: $InstallRoot"),
                        text.index("数据文件已删除: $DataRoot"))
        self.assertLess(text.index("数据文件仍在: $DataRoot"),
                        text.index("手动删除上述目录"))

    def test_supervisor_resolves_the_configured_data_root(self):
        # The marker, not the platform default, decides where state and logs live.
        with patch("codex_wechat.platform_support.data_root", return_value=Path("/custom root")):
            self.assertEqual(windows_service.data_root(), Path("/custom root"))

    def test_uninstall_refuses_a_live_foreground_bridge(self):
        text = (ROOT / "uninstall.ps1").read_text(encoding="utf-8")
        self.assertIn("Assert-NoForegroundBridge", text)

    def test_uninstall_validates_the_saved_data_root_before_task_changes(self):
        text = (ROOT / "uninstall.ps1").read_text(encoding="utf-8")
        self.assertIn("$SavedDataRoot = Read-SavedDataRoot $InstallRoot", text)
        self.assertIn("拒绝卸载", text)
        self.assertIn("没有显式数据根时清理数据", text)
        self.assertLess(text.index("拒绝卸载"), text.index("$task = Get-ScheduledTask"))
        self.assertIn("[IO.FileShare]::None", text)
        check = text.index("Assert-NoForegroundBridge\n\n# Data removal")
        self.assertLess(text.index("未安装计划任务"), check)
        self.assertLess(check, text.index("if ($PurgeData)"))

    def test_uninstall_treats_an_empty_data_root_as_missing(self):
        text = (ROOT / "uninstall.ps1").read_text(encoding="utf-8")
        self.assertIn(
            "$HasExplicitDataRoot = -not [string]::IsNullOrWhiteSpace($DataRoot)", text)
        self.assertIn(
            "$RequestedDataRoot = if (-not [string]::IsNullOrWhiteSpace($DataRoot))", text)

    def test_uninstall_only_forgets_the_data_root_when_purging(self):
        text = (ROOT / "uninstall.ps1").read_text(encoding="utf-8")
        guard = text.index("if ($PurgeData) {\n    # Only a purge forgets the root")
        removal = text.index("Remove-Item -LiteralPath (Join-Path $InstallRoot '.data-root')")
        self.assertGreater(removal, guard)
        self.assertEqual(text.count("'.data-root') -Force"), 1)

    def test_uninstall_checks_exact_task_ownership_before_stopping(self):
        text = (ROOT / "uninstall.ps1").read_text(encoding="utf-8")
        self.assertIn("ExpectedActionExecutables", text)
        self.assertIn("$execute = [string]$actions[0].Execute", text)
        self.assertIn("$ExpectedActionExecutables -contains $execute", text)
        self.assertIn("[char]::IsWhiteSpace($actionText[$prefix.Length])", text)
        self.assertNotIn("Join-Path $Project 'windows_service.py'", text)

    def test_uninstall_stops_shared_backend_only_when_purging(self):
        text = (ROOT / "uninstall.ps1").read_text(encoding="utf-8")
        self.assertEqual(text.count("Stop-SharedBackends"), 2)
        self.assertGreater(text.rindex("Stop-SharedBackends"),
                           text.index("if ($PurgeData)"))
        self.assertLess(text.rindex("Stop-SharedBackends"),
                        text.index("Remove-DataPath $name"))
        self.assertIn("shared-backend-process.json", text)
        self.assertIn("记录的身份已不匹配", text)
        self.assertIn("仍有已连接的客户端", text)
        self.assertIn("共享 Codex 后台仍在运行", text)

    def test_uninstall_removes_the_task_only_after_purging_data(self):
        text = (ROOT / "uninstall.ps1").read_text(encoding="utf-8")
        self.assertGreater(text.index("Unregister-ScheduledTask"),
                           text.index("Remove-DataPath"))
        self.assertIn("$DataPaths = @('config.json', '.state', 'workspace', 'logs', 'shared')", text)

    def test_powershell_entry_routes_uninstall_to_stable_helper(self):
        entry = (ROOT / "codex-wechat.ps1").read_text(encoding="utf-8")
        self.assertIn("$Command -eq 'uninstall'", entry)
        self.assertIn("uninstall.ps1", entry)
        self.assertIn("$PurgeData", entry)
        self.assertNotIn("--purge-data", entry)
        self.assertLess(entry.index("$Command -eq 'uninstall'"), entry.index("$CurrentFile"))
        self.assertIn("-cnotmatch", entry)
        desktop = (ROOT / "codex-desktop.ps1").read_text(encoding="utf-8")
        self.assertIn("-cnotmatch", desktop)

    def test_powershell_entry_splats_uninstall_by_name_not_position(self):
        # An array splat binds positionally and switches are skipped, so
        # @('-PurgeData') landed in -DataRoot: the purge silently did nothing
        # and the data root became a relative path. Only a hashtable binds by
        # name.
        entry = (ROOT / "codex-wechat.ps1").read_text(encoding="utf-8")
        block = entry[entry.index("$Command -eq 'uninstall'"):]
        block = block[:block.index("exit 0")]
        self.assertIn("$UninstallerArguments = @{}", block)
        self.assertIn("$UninstallerArguments.PurgeData = $true", block)
        self.assertIn("$UninstallerArguments.DataRoot =", block)
        self.assertNotIn("$UninstallerArguments += '-PurgeData'", block)
        self.assertNotIn("$UninstallerArguments += @('-DataRoot'", block)

    def test_installer_requires_native_codex_exe(self):
        discovery = (ROOT / "codex_wechat/native_codex.py").read_text(encoding="utf-8")
        self.assertIn(
            "Programs/OpenAI/Codex/bin/codex.exe", discovery)
        self.assertIn('shutil.which("codex.exe"', discovery)
        self.assertNotIn("Get-Command codex -ErrorAction", discovery)
