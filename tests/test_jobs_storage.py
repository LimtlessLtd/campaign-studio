"""Regression tests for worker survival and catalogue updates across processes."""

import json
import queue
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
import server
import storage


class JobsStorageTests(unittest.TestCase):
    def test_worker_survives_result_processing_failure(self):
        lane = queue.Queue()
        first = {'id': 'first', 'kind': 'fixture', 'status': 'queued'}
        second = {'id': 'second', 'kind': 'fixture', 'status': 'queued'}
        lane.put((first, [], None))
        lane.put((second, [], None))
        get = lane.get

        def take():
            if lane.empty():
                raise StopIteration
            return get()

        def execute(job, cmd, stdin):
            if job['id'] == 'first':
                raise ValueError('Synthetic postprocessing failure')
            job['status'] = 'done'

        with (
            patch.dict(server.LANES, fixture=lane),
            patch.object(lane, 'get', take),
            patch.object(server, 'execute_job', execute),
            patch.object(server, 'save_job') as saved,
        ):
            with self.assertRaises(StopIteration):
                server.worker('fixture')
            saved.assert_called_once_with(first)
        self.assertEqual(first['status'], 'failed')
        self.assertEqual(second['status'], 'done')
        self.assertEqual(lane.unfinished_tasks, 0)

    def test_concurrent_processes_preserve_every_catalogue_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / 'index.json')
            code = """import sys
sys.path.insert(0, sys.argv[1])
import storage
for number in range(20):
    storage.update_json(sys.argv[2], {'items': []}, lambda value: value['items'].append(sys.argv[3] + '-' + str(number)))
"""
            processes = [
                subprocess.Popen([sys.executable, '-c', code, str(ROOT / 'DM'), path, str(n)])
                for n in range(4)
            ]
            for process in processes:
                self.assertEqual(process.wait(timeout=30), 0)
            entries = json.loads(Path(path).read_text(encoding='utf-8'))['items']
            self.assertEqual(len(entries), 80)
            self.assertEqual(len(set(entries)), 80)

    def test_nested_document_lock_and_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / 'doc.json')
            with storage.file_lock(path):
                storage.update_json(
                    path, {'items': []}, lambda value: value['items'].append('fixture')
                )
            self.assertEqual(
                json.loads(Path(path).read_text(encoding='utf-8')), {'items': ['fixture']}
            )
            self.assertFalse(list(Path(temporary).glob('*.tmp-*')))
