"""Offline contracts for the pywin32 Mutex/Job Object adapters."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

import windows_service


class FakeHandle:
    def __init__(self):
        self.closed = 0

    def Close(self):
        self.closed += 1


def module(name, **values):
    value = ModuleType(name)
    for key, item in values.items():
        setattr(value, key, item)
    return value


class WindowsPywin32Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def test_mutex_is_released_and_existing_mutex_closes_its_handle(self):
        first = FakeHandle()
        event = module("win32event", CreateMutex=Mock(return_value=first))
        api = module("win32api", GetLastError=Mock(return_value=0))
        error = module("winerror", ERROR_ALREADY_EXISTS=183)
        guard = windows_service.SingleInstance(Path(self.temp.name) / "state")
        with patch.object(windows_service.os, "name", "nt"), \
                patch.object(windows_service, "_mutex_name", return_value="mutex"), \
                patch.dict(sys.modules, {
                    "win32event": event, "win32api": api, "winerror": error}):
            guard.acquire()
            self.assertIs(guard._handle, first)
            guard.release()
        self.assertEqual(first.closed, 1)

        second = FakeHandle()
        event.CreateMutex.return_value = second
        api.GetLastError.return_value = 183
        guard = windows_service.SingleInstance(Path(self.temp.name) / "state2")
        with patch.object(windows_service.os, "name", "nt"), \
                patch.object(windows_service, "_mutex_name", return_value="mutex"), \
                patch.dict(sys.modules, {
                    "win32event": event, "win32api": api, "winerror": error}), \
                self.assertRaises(windows_service.AlreadyRunning):
            guard.acquire()
        self.assertEqual(second.closed, 1)
        self.assertIsNone(guard._handle)

    def test_job_open_sets_kill_on_close_and_silent_breakaway(self):
        handle = FakeHandle()
        info = {"BasicLimitInformation": {"LimitFlags": 0}}
        query = Mock(return_value=info)
        configure = Mock()
        job_module = module(
            "win32job", CreateJobObject=Mock(return_value=handle),
            QueryInformationJobObject=query,
            SetInformationJobObject=configure,
            JobObjectExtendedLimitInformation=9,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=0x00002000,
            JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK=0x00001000)
        with patch.object(windows_service.os, "name", "nt"), \
                patch.dict(sys.modules, {"win32job": job_module}):
            job = windows_service.ChildJob()
            job.open()
            job.close()
        self.assertEqual(info["BasicLimitInformation"]["LimitFlags"],
                         0x00003000)
        configure.assert_called_once_with(
            handle, 9, info)
        self.assertEqual(handle.closed, 1)

    def test_job_configuration_failure_closes_the_handle(self):
        handle = FakeHandle()
        job_module = module(
            "win32job", CreateJobObject=Mock(return_value=handle),
            QueryInformationJobObject=Mock(return_value={
                "BasicLimitInformation": {"LimitFlags": 0}}),
            SetInformationJobObject=Mock(side_effect=OSError("failed")),
            JobObjectExtendedLimitInformation=9,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=0x00002000,
            JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK=0x00001000)
        with patch.object(windows_service.os, "name", "nt"), \
                patch.dict(sys.modules, {"win32job": job_module}), \
                self.assertRaises(OSError):
            windows_service.ChildJob().open()
        self.assertEqual(handle.closed, 1)

    def test_job_assignment_closes_process_handle_on_success_and_failure(self):
        job_handle = FakeHandle()
        process_handle = FakeHandle()
        api = module(
            "win32api", OpenProcess=Mock(return_value=process_handle))
        con = module("win32con", PROCESS_SET_QUOTA=0x0100,
                     PROCESS_TERMINATE=0x0001)
        assign = Mock()
        job_module = module("win32job", AssignProcessToJobObject=assign)
        job = windows_service.ChildJob()
        job._handle = job_handle
        with patch.object(windows_service.os, "name", "nt"), \
                patch.dict(sys.modules, {
                    "win32api": api, "win32con": con, "win32job": job_module}):
            job.assign(123)
        api.OpenProcess.assert_called_once_with(0x0101, False, 123)
        assign.assert_called_once_with(job_handle, process_handle)
        self.assertEqual(process_handle.closed, 1)

        second = FakeHandle()
        api.OpenProcess.return_value = second
        assign.side_effect = OSError("failed")
        with patch.object(windows_service.os, "name", "nt"), \
                patch.dict(sys.modules, {
                    "win32api": api, "win32con": con, "win32job": job_module}), \
                self.assertRaises(OSError):
            job.assign(456)
        self.assertEqual(second.closed, 1)
        self.assertEqual(job_handle.closed, 0)

    def test_windows_dependency_is_declared_for_windows_only(self):
        requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text()
        self.assertIn('-r requirements-windows.txt', requirements)
        native = (Path(__file__).resolve().parents[1] / "requirements-windows.txt").read_text()
        self.assertIn('pywin32; sys_platform == "win32"', native)


if __name__ == "__main__":
    unittest.main()
