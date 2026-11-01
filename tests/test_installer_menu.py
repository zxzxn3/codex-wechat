"""User-approved installer flow; all mutations stay inside temporary fixtures."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import installer


class InstallerMenuTests(unittest.TestCase):
    def test_menu_exit_does_not_inspect_or_modify_target(self):
        with patch('builtins.input', return_value='0'), \
             patch.object(installer.sys.stdin, 'isatty', return_value=True), \
             patch.object(installer.sys.stdout, 'isatty', return_value=True), \
             patch.object(installer, 'default_target', side_effect=AssertionError('target inspected')):
            self.assertEqual(installer.main([]), 0)

    def test_noninteractive_is_explicit_and_does_not_read_input(self):
        with patch.object(installer.sys.stdin, 'isatty', return_value=False), \
             patch('builtins.input', side_effect=AssertionError('unexpected input')), \
             patch.object(installer, 'default_target', return_value=Path('/fixture/no-touch')), \
             patch.object(installer, '_common_install', return_value=0) as core:
            with self.assertRaisesRegex(ValueError, 'non-interactive'):
                installer.main(['install'])
            self.assertEqual(installer.main(['install', '--dry-run', '--non-interactive']), 0)
            core.assert_called_once()
        self.assertFalse(installer.NON_INTERACTIVE)

    def test_component_dry_run_bypasses_conflicts_and_missing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root/'skills'
            target.write_text('keep')
            self.assertEqual(installer.component_install(root/'absent', 'wechat-attachments', target, dry_run=True), 0)
            self.assertEqual(target.read_text(), 'keep')

    def test_component_install_is_atomic_idempotent_and_preserves_custom_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'source'
            text = source/installer.COMPONENTS['wechat-attachments'][1]
            text.parent.mkdir(parents=True)
            text.write_text('approved skill')
            target = root/'skills'
            with patch('builtins.input', return_value='y'):
                installer.component_install(source, 'wechat-attachments', target)
            result = target/'wechat-attachments/SKILL.md'
            self.assertEqual(result.read_text(), 'approved skill')
            with patch('builtins.input', side_effect=AssertionError('noop prompt')):
                installer.component_install(source, 'wechat-attachments', target)
            result.write_text('user edit')
            with self.assertRaisesRegex(ValueError, '保留原文件'):
                installer.component_install(source, 'wechat-attachments', target)
            self.assertEqual(result.read_text(), 'user edit')

    def test_component_write_failure_cleans_empty_component_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'source'
            text = source/installer.COMPONENTS['wechat-attachments'][1]
            text.parent.mkdir(parents=True)
            text.write_text('skill')
            def fail(path, *args, **kwargs):
                path.parent.mkdir(parents=True)
                raise OSError('disk full')
            target = root/'skills'
            with patch('builtins.input', return_value='y'), patch.object(installer, '_atomic_write', side_effect=fail):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    installer.component_install(source, 'wechat-attachments', target)
            self.assertFalse((target/'wechat-attachments').exists())

    def test_noninteractive_components_require_explicit_selection(self):
        with patch('builtins.input', side_effect=AssertionError('prompt')):
            with self.assertRaisesRegex(ValueError, '需要 --component'):
                installer.main(['components', '--non-interactive'])

    def test_declarations_are_read_from_single_assets_without_placeholders(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as out:
            root = Path(directory)
            installer.declaration(root, 'start')
            self.assertEqual(out.getvalue(), '')
            path = root/installer.DECLARATION_PATHS['start']
            path.parent.mkdir(parents=True)
            path.write_text('Project notice')
            installer.declaration(root, 'start')
            self.assertEqual(out.getvalue().strip(), 'Project notice')

    def test_assets_do_not_follow_symlinked_parent_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'source'
            outside = root/'outside'
            source.mkdir()
            outside.mkdir()
            (outside/'notice.txt').write_text('do not print')
            (source/'assets').symlink_to(outside, target_is_directory=True)
            self.assertIsNone(installer.asset_path(source, Path('assets/notice.txt')))

    def test_declarations_do_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as out:
            root = Path(directory)
            secret = root/'secret.txt'
            secret.write_text('do not print')
            path = root/installer.DECLARATION_PATHS['start']
            path.parent.mkdir(parents=True)
            path.symlink_to(secret)
            installer.declaration(root, 'start')
            self.assertEqual(out.getvalue(), '')
