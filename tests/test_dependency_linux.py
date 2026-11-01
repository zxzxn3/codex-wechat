"""Run installer only against rewritten temp paths and fake dependency tools."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name != "nt" and shutil.which("bash"), "Linux shell fixture")
class LinuxDependencyTests(unittest.TestCase):
    def installer(self, *, missing=(), modules=(), options=(), bound=True):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            fixture_home = base / "fixture-home"
            project = fixture_home / ".local/share/codex-wechat"
            project.mkdir(parents=True)
            tools = base / "tools"
            tools.mkdir()
            calls = base / "calls"
            calls.touch()
            source = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
            # Rewrite fixture copies, never assign HOME or contact the real manager.
            source = source.replace("$HOME", str(fixture_home))
            override = '''
command() {
    if [[ "${1:-}" == -v && ",${TASK_MISSING:-}," == *",${2:-},"* ]]; then return 1; fi
    builtin command "$@"
}
'''
            source = source.replace("set -euo pipefail", "set -euo pipefail\n" + override, 1)
            (project / "install.sh").write_text(source)
            (project / "autostart.py").write_text("# fixture only\n")
            (project / "codex-wechat.service").write_text("[Unit]\nDescription=fixture\n")
            (project / "codex-wechat").write_text("#!/bin/sh\nexit 0\n")
            (project / "codex-wechat").chmod(0o700)
            if bound:
                (project / ".state").mkdir()
                (project / ".state/account.json").write_text("{}")
            scripts = {
                "python3": '''#!/bin/bash
printf 'python %s\\n' "$*" >> "$TASK_CALLS"
if [[ "${1:-}" == -c ]]; then
    for module in ${TASK_MODULES:-}; do
        [[ "$2" == *"$module"* ]] && exit 1
    done
    exit 0
fi
if [[ "${1:-}" == *autostart.py ]]; then
    [[ "${2:-}" == status ]] && echo off
    exit 0
fi
exit 0
''',
                "systemctl": '''#!/bin/bash
printf 'systemctl %s\\n' "$*" >> "$TASK_CALLS"
exit 0
''',
                "systemd-run": "#!/bin/bash\necho 'systemd 262'\n",
                "curl": "#!/bin/bash\necho 'curl 8.17.0 fixture'\n",
                "codex": "#!/bin/bash\nexit 0\n",
                "qrencode": "#!/bin/bash\nexit 0\n",
            }
            for name, content in scripts.items():
                p = tools / name
                p.write_text(content)
                p.chmod(0o700)
            env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"],
                       XDG_CONFIG_HOME=str(fixture_home / ".config"),
                       TASK_MISSING=",".join(missing), TASK_MODULES=" ".join(modules),
                       TASK_CALLS=str(calls))
            result = subprocess.run([shutil.which("bash"), str(project / "install.sh"), *options],
                                    capture_output=True, text=True, env=env, timeout=15)
            return result, calls.read_text(), (fixture_home / ".local/bin/codex-wechat").is_symlink()

    def test_missing_codex_warns_and_prepares_service_without_start(self):
        result, calls, linked = self.installer(missing=("codex",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(linked)
        self.assertIn("未发现 Codex CLI", result.stderr)
        self.assertNotIn("daemon-reload", calls)
        self.assertNotIn("systemctl --user start ", calls)

    def test_missing_python_still_installs_command_but_not_service(self):
        result, calls, linked = self.installer(missing=("python3",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(linked)
        self.assertIn("不注册服务", result.stdout)
        self.assertNotIn("daemon-reload", calls)
        self.assertNotIn("systemctl --user start ", calls)

    def test_missing_curl_and_manager_warn_without_aborting(self):
        result, calls, linked = self.installer(missing=("curl", "systemctl"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(linked)
        self.assertIn("缺少 curl", result.stderr)
        self.assertIn("用户 systemd 不可用", result.stderr)
        self.assertNotIn("systemctl ", calls)

    def test_missing_python_modules_warn_and_skip_start(self):
        result, calls, _ = self.installer(modules=("cryptography", "websockets"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("缺少 cryptography", result.stderr)
        self.assertIn("缺少 websockets", result.stderr)
        self.assertNotIn("systemctl --user start ", calls)

    def test_installation_never_registers_or_starts_a_service(self):
        result, calls, linked = self.installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(linked)
        self.assertIn("新安装默认不自启", result.stdout)
        self.assertNotIn("daemon-reload", calls)
        self.assertNotIn("autostart.py on", calls)
        self.assertNotIn("systemctl --user start ", calls)

    def test_old_service_installation_flags_are_rejected_without_mutation(self):
        result, calls, linked = self.installer(options=("--autostart",))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(linked)
        self.assertFalse(calls)
