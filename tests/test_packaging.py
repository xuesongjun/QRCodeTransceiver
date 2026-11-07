"""验证四种交付包、离线库角色/架构边界和安装时的断网约束."""

import contextlib
import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import build_exe
import install_package
from offline_wheels import verify_wheelhouse, write_manifest


class OfflineWheelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=build_exe.ROOT, prefix='wheel-test-')
        self.root = Path(self.temp.name)
        self.requirements = self.root / 'requirements-tx.txt'
        self.requirements.write_text('example==1\n', encoding='utf-8')
        self.folder = self.root / 'wheelhouse'
        self.folder.mkdir()
        self.wheel = self.folder / 'example-1-py3-none-any.whl'
        self.wheel.write_bytes(b'test wheel contents')
        write_manifest(self.folder, self.requirements, 'sender', '3.12', 'manylinux2014_x86_64')

    def tearDown(self):
        self.temp.cleanup()

    def verify(self, **kwargs):
        return verify_wheelhouse(self.folder, self.requirements, 'sender', 'Linux', **kwargs)

    def test_valid_manifest(self):
        self.assertEqual(self.verify()['target']['python'], '3.12')

    def test_changed_wheel_is_rejected(self):
        self.wheel.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '摘要错误'):
            self.verify()

    def test_stale_requirements_are_rejected(self):
        self.requirements.write_text('example==2\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '不匹配'):
            self.verify()

    def test_mixed_wheel_versions_are_rejected(self):
        (self.folder / 'example-2-py3-none-any.whl').write_bytes(b'old wheel')
        with self.assertRaisesRegex(ValueError, '混入'):
            self.verify()

    def test_invalid_json_is_rejected(self):
        (self.folder / 'manifest.json').write_text('{', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '无法读取'):
            self.verify()

    def test_wrong_role_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '角色错误'):
            verify_wheelhouse(self.folder, self.requirements, 'receiver', 'Linux')

    def test_wrong_operating_system_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '不能用于'):
            verify_wheelhouse(self.folder, self.requirements, 'sender', 'Windows')

    def test_wrong_host_cannot_install(self):
        with patch('offline_wheels.platform.machine', return_value='wrong-architecture'):
            with self.assertRaisesRegex(ValueError, '当前环境不匹配'):
                self.verify(check_host=True)

    def test_glibc_boundary_is_checked(self):
        with patch('offline_wheels.platform.system', return_value='Linux'), patch('offline_wheels.platform.machine', return_value='x86_64'), patch('offline_wheels.sys.maxsize', 2 ** 63 - 1), patch('offline_wheels.platform.libc_ver', return_value=('glibc', '2.16')):
            with self.assertRaisesRegex(ValueError, 'glibc >= 2.17'):
                self.verify(check_host=True)


class PackageTests(unittest.TestCase):
    def test_four_source_packages_contain_correct_role_and_launchers(self):
        with tempfile.TemporaryDirectory(dir=build_exe.ROOT, prefix='package-test-') as temp:
            output = Path(temp)
            for target in build_exe.TARGETS:
                system, role = target.split('-')
                with self.subTest(target=target), contextlib.redirect_stdout(io.StringIO()):
                    build_exe.build_source(target, output)
                    archive = output / f'{build_exe.ROLE_NAMES[role]}-{system.title()}-source.zip'
                    with zipfile.ZipFile(archive) as source:
                        top = build_exe.ROLE_NAMES[role] + '/'
                        names = source.namelist()
                        for path in build_exe.ROLE_FILES[role]:
                            self.assertIn(top + path, names)
                        self.assertFalse(any('wheelhouse/' in name or 'venv/' in name for name in names))
                        extension = 'cmd' if system == 'windows' else 'sh'
                        self.assertIn(top + 'install.' + extension, names)
                        entry_name = 'send' if role == 'sender' else 'receive'
                        self.assertIn(top + entry_name + '_cli.' + extension, names)
                        if extension == 'sh':
                            entry = source.getinfo(top + 'install.sh')
                            self.assertTrue(entry.external_attr >> 16 & 0o111)
                            self.assertNotIn(b'\r\n', source.read(entry.filename))
                    self.assertEqual(archive.with_suffix('.zip.sha256').read_text().split()[0], hashlib.sha256(archive.read_bytes()).hexdigest())

    def test_offline_build_validates_before_creating_packages(self):
        with tempfile.TemporaryDirectory(dir=build_exe.ROOT, prefix='package-test-') as temp:
            output = Path(temp)
            with self.assertRaises(ValueError):
                build_exe.build_source('linux-receiver', output, output / 'missing-wheelhouse')
            self.assertEqual(list(output.glob('*.zip')), [])

    def test_cross_os_native_build_is_rejected(self):
        with patch('build_exe.platform.system', return_value='Windows'):
            with self.assertRaisesRegex(ValueError, '必须在 Linux'):
                build_exe.build_native('linux-sender', build_exe.ROOT / 'dist')

    def test_wrong_process_architecture_is_rejected(self):
        with patch('build_exe.platform.system', return_value='Windows'), patch('build_exe.platform.machine', return_value='AMD64'), patch('build_exe.sys.maxsize', 2 ** 63 - 1):
            with self.assertRaisesRegex(ValueError, '当前 Python 进程为 x64'):
                build_exe.build_native('windows-sender', build_exe.ROOT / 'dist', 'x86')

    def test_linux_receiver_executable_bits_are_preserved(self):
        with tempfile.TemporaryDirectory(dir=build_exe.ROOT, prefix='package-test-') as temp:
            root = Path(temp)
            folder = root / 'QRCodeReceiver'
            folder.mkdir()
            (folder / 'QRReceiver').write_bytes(b'gui executable')
            (folder / 'QRReceiveCLI').write_bytes(b'cli executable')
            with contextlib.redirect_stdout(io.StringIO()):
                build_exe.archive_folder(folder, root / 'receiver.zip')
            with zipfile.ZipFile(root / 'receiver.zip') as package:
                for entry in package.infolist():
                    self.assertTrue(entry.external_attr >> 16 & 0o111)

    def test_offline_install_never_uses_index_or_system_packages(self):
        with tempfile.TemporaryDirectory(dir=build_exe.ROOT, prefix='install-test-') as temp:
            root = Path(temp)
            (root / 'venv').mkdir()
            with patch('install_package.platform.system', return_value='Windows'), patch('install_package.verify_wheelhouse') as verify, patch('install_package.subprocess.run') as run, contextlib.redirect_stdout(io.StringIO()):
                run.return_value.stdout = '3.12\n'
                install_package.install(root, 'windows-receiver', True)
                verify.assert_called_once()
                calls = [call.args[0] for call in run.call_args_list]
                pip = next(command for command in calls if 'pip' in command)
                self.assertIn('--no-index', pip)
                self.assertIn('--find-links', pip)
                self.assertIn('utf8', pip)
                self.assertEqual(pip[0], str(root / 'venv/Scripts/python.exe'))


if __name__ == '__main__':
    unittest.main()
