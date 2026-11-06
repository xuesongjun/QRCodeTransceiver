"""验证离线包环境边界、摘要和发送端 ZIP 的实际交付结构."""

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import build_exe
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
        write_manifest(self.folder, self.requirements, '3.12', 'manylinux2014_x86_64')

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_manifest(self):
        manifest = verify_wheelhouse(self.folder, self.requirements)
        self.assertEqual(manifest['target']['python'], '3.12')

    def test_changed_wheel_is_rejected(self):
        self.wheel.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '摘要错误'):
            verify_wheelhouse(self.folder, self.requirements)

    def test_stale_requirements_are_rejected(self):
        self.requirements.write_text('example==2\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '不匹配'):
            verify_wheelhouse(self.folder, self.requirements)

    def test_mixed_wheel_versions_are_rejected(self):
        (self.folder / 'example-2-py3-none-any.whl').write_bytes(b'old wheel')
        with self.assertRaisesRegex(ValueError, '混入'):
            verify_wheelhouse(self.folder, self.requirements)

    def test_invalid_json_is_rejected(self):
        (self.folder / 'manifest.json').write_text('{', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '无法读取'):
            verify_wheelhouse(self.folder, self.requirements)

    def test_wrong_host_cannot_install(self):
        with patch('offline_wheels.platform.machine', return_value='wrong-architecture'):
            with self.assertRaisesRegex(ValueError, '当前环境不匹配'):
                verify_wheelhouse(self.folder, self.requirements, check_host=True)


class PackageTests(unittest.TestCase):
    def test_source_package_contains_launchers_and_no_old_wheels(self):
        with tempfile.TemporaryDirectory(dir=build_exe.ROOT, prefix='package-test-') as temp:
            output = Path(temp)
            with contextlib.redirect_stdout(io.StringIO()):
                build_exe.build_sender_source(output, None)
            archive = output / 'QRCodeSender-Linux-source.zip'
            with zipfile.ZipFile(archive) as source:
                names = source.namelist()
                for path in build_exe.SENDER_FILES:
                    self.assertIn('QRCodeSender/' + path, names)
                self.assertFalse(any('wheelhouse/' in name or 'venv/' in name for name in names))
                entry = source.getinfo('QRCodeSender/send.sh')
                self.assertTrue(entry.external_attr >> 16 & 0o111)
                self.assertNotIn(b'\r\n', source.read('QRCodeSender/install_offline.sh'))
            self.assertEqual(archive.with_suffix('.zip.sha256').read_text().split()[0], hashlib.sha256(archive.read_bytes()).hexdigest())

    def test_offline_build_validates_before_creating_packages(self):
        with tempfile.TemporaryDirectory(dir=build_exe.ROOT, prefix='package-test-') as temp:
            output = Path(temp)
            with self.assertRaises(ValueError):
                build_exe.build_sender_source(output, output / 'missing-wheelhouse')
            self.assertEqual(list(output.glob('*.zip')), [])

    def test_cross_os_native_build_is_rejected(self):
        with patch('build_exe.platform.system', return_value='Windows'):
            with self.assertRaisesRegex(ValueError, '必须在 Linux'):
                build_exe.build_native('linux-sender', build_exe.ROOT / 'dist')


if __name__ == '__main__':
    unittest.main()
