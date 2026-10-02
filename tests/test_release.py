"""The downloaded app starts empty and can rebuild its own clean archive."""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
sys.path.insert(0, str(ROOT / 'tools'))
import packaging_source
import check_source


class ReleaseTests(unittest.TestCase):
    def test_download_startup_and_repackaging_without_campaign_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            clean = base / 'source'
            for name in packaging_source.source_files():
                target = clean / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, target)
            marker = 'synthetic-private-' + os.urandom(20).hex()
            for folder in ('data', 'maps', 'uploads'):
                private = clean / 'DM' / folder / 'private-fixture.json'
                private.parent.mkdir(parents=True, exist_ok=True)
                private.write_text(json.dumps({'secret': marker}), encoding='utf-8')
            built = packaging_source.build(clean)
            archive = clean / built['path']
            extracted = base / 'download'
            with zipfile.ZipFile(archive) as source_zip:
                self.assertEqual(set(source_zip.namelist()), set(packaging_source.source_files()))
                self.assertFalse(
                    any(marker in source_zip.read(n).decode('utf-8') for n in source_zip.namelist())
                )
                source_zip.extractall(extracted)
            check_source.check(extracted)
            rebuilt = packaging_source.build(extracted)
            self.assertTrue((extracted / rebuilt['path']).is_file())
            self.assertTrue((extracted / (rebuilt['path'] + '.sha256')).is_file())
            with socket.socket() as port_socket:
                port_socket.bind(('127.0.0.1', 0))
                port = port_socket.getsockname()[1]
            environment = dict(os.environ, DM_PORT=str(port), FOUNDRY_DATA='')
            proc = subprocess.Popen(
                [sys.executable, 'DM/server.py'],
                cwd=extracted,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        with opener.open(
                            f'http://127.0.0.1:{port}/api/state', timeout=1
                        ) as response:
                            state = json.load(response)
                        break
                    except OSError:
                        if proc.poll() is not None or time.monotonic() > deadline:
                            self.fail('Extracted app did not start.')
                        time.sleep(0.1)
                self.assertEqual(state['campaign'], 'Campaign Studio')
                self.assertEqual(state['public'], {'sessions': [], 'heroes': [], 'locations': []})
                self.assertEqual(state['prep'], [])
                with opener.open(f'http://127.0.0.1:{port}/api/settings', timeout=2) as response:
                    self.assertIsNone(json.load(response)['world'])
                request = urllib.request.Request(
                    f'http://127.0.0.1:{port}/api/package',
                    data=b'{}',
                    headers={'X-DM-Site': '1', 'Content-Type': 'application/json'},
                )
                with opener.open(request, timeout=5) as response:
                    self.assertEqual(
                        json.load(response)['files'], len(packaging_source.source_files())
                    )
            finally:
                proc.terminate()
                proc.wait(timeout=10)

    def test_private_path_in_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'DM/data').mkdir(parents=True)
            (root / 'DM/data/private.json').write_text('{}', encoding='utf-8')
            (root / 'source_manifest.json').write_text('["DM/data/private.json"]', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Private path'):
                check_source.check(root)

    def test_unsafe_archive_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'source_manifest.json').write_text('["../outside.py"]', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Unsafe source path'):
                packaging_source.source_files(root)
