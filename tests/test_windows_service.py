"""Offline contracts for the Windows per-user service; no Windows calls."""
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import service_control
import windows_service


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

        class Stream:
            def close(self):
                pass

        calls = []
        spawn = AsyncMock(side_effect=lambda *args, **kwargs: (
            calls.append(1) or (Process(), Stream())
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
        class Stream:
            def close(self):
                pass

        for returncode in (0, 78):
            class Process:
                pid = 1234

                def __init__(self, returncode):
                    self.returncode = returncode

                async def wait(self):
                    return self.returncode

            spawn = AsyncMock(side_effect=lambda *args, returncode=returncode, **kwargs:
                              (Process(returncode), Stream()))
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

        class Stream:
            def close(self):
                pass

        spawn = AsyncMock(side_effect=lambda *args, **kwargs: (Process(), Stream()))
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

        class Stream:
            def close(self):
                pass

        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / ".state"
            state.mkdir()
            status_path = windows_service.owner_status_path(state)
            status_path.write_text(json.dumps({
                "version": 1, "supervisor_pid": 111, "child_pid": 222,
                "token": "stale", "state_dir": str(state), "started_at": 0}),
                encoding="utf-8")
            seen = []

            async def spawn(command, project, env, log_path):
                # The new supervisor must not inherit the previous owner record.
                seen.append(status_path.exists())
                return Process(), Stream()

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
        service = (ROOT / "windows_service.py").read_text(encoding="utf-8")
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
        self.assertIn("[ValidateSet('on', 'off')]", entry)
        self.assertIn("[switch]$Json", entry)
        self.assertIn("only valid with the autostart command", entry)
        self.assertIn("[char]::IsWhiteSpace", entry)
        self.assertIn("[char]::IsWhiteSpace", install)
        self.assertIn("$trigger.Enabled = $enabled", entry)
        self.assertIn("Set-ScheduledTask -TaskName $TaskName -Trigger $triggers", entry)
        self.assertIn("Enable-ScheduledTask", entry)
        self.assertNotIn("Disable-ScheduledTask", entry)
        self.assertIn("service-start", entry)
        self.assertIn("service-stop", entry)
        self.assertIn("service-restart", entry)
        self.assertIn("logs", entry)
        self.assertIn("JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE", service)
        self.assertIn("JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK", service)
        self.assertNotIn("taskkill", service.lower())

    def test_documented_restart_does_not_restart_scheduled_task(self):
        service = (ROOT / "service_control.py").read_text(encoding="utf-8")
        windows = service[service.index("async def restart():"):]
        self.assertIn("RESTART_EXIT_CODE", windows)
        self.assertIn("raise_signal(signal.SIGINT)", windows)
        self.assertNotIn("schtasks", windows.lower())
        self.assertNotIn("Stop-ScheduledTask", windows)

    def test_uninstall_preserves_data_by_default(self):
        text = (ROOT / "uninstall.ps1").read_text(encoding="utf-8")
        self.assertIn("if ($PurgeData)", text)
        self.assertIn("data retained", text)
        self.assertIn("project files and the virtual environment were not removed", text)

    def test_uninstall_stops_shared_backend_only_when_purging(self):
        text = (ROOT / "uninstall.ps1").read_text(encoding="utf-8")
        self.assertEqual(text.count("Stop-SharedBackends"), 2)
        self.assertGreater(text.rindex("Stop-SharedBackends"),
                           text.index("if ($PurgeData)"))
        self.assertLess(text.rindex("Stop-SharedBackends"),
                        text.index("Remove-Item -LiteralPath $DataRoot"))
        self.assertIn("shared-backend-process.json", text)
        self.assertIn("recorded identity no longer matches", text)
        self.assertIn("still has attached clients", text)
        self.assertIn("a shared Codex backend under the data root is left running", text)

    def test_uninstall_removes_the_task_only_after_purging_data(self):
        text = (ROOT / "uninstall.ps1").read_text(encoding="utf-8")
        self.assertGreater(text.index("Unregister-ScheduledTask"),
                           text.index("Remove-Item -LiteralPath $DataRoot"))

    def test_powershell_entry_does_not_read_undefined_last_exit_code(self):
        entry = (ROOT / "codex-wechat.ps1").read_text(encoding="utf-8")
        uninstall = entry[entry.index("    'uninstall' {"):]
        self.assertIn("exit 0", uninstall)
        self.assertNotIn("$LASTEXITCODE", uninstall)

    def test_installer_requires_native_codex_exe(self):
        discovery = (ROOT / "native_codex.py").read_text(encoding="utf-8")
        self.assertIn(
            "Programs/OpenAI/Codex/bin/codex.exe", discovery)
        self.assertIn('shutil.which("codex.exe"', discovery)
        self.assertNotIn("Get-Command codex -ErrorAction", discovery)
