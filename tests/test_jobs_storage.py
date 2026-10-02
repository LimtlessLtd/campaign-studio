"""Regression tests for worker survival and catalogue updates across processes."""

import json
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
import campaign_core as core
import storage
from job_service import JobService


class JobsStorageTests(unittest.TestCase):
    def test_restart_fails_only_unfinished_jobs_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            restarted = []
            service = JobService(
                lambda: temporary,
                lambda: temporary,
                threading.RLock(),
                lambda *_: None,
                lambda *_: None,
                lambda job: restarted.append(job['id']),
            )
            pending = service.new_job('forge', 'fixture', 'Pending', [])
            finished = service.new_job('art', 'fixture', 'Finished', [])
            finished['status'] = 'done'
            service.save_job(finished)

            service.recover_unfinished()
            service.recover_unfinished()

            self.assertEqual(restarted, [pending['id']])
            self.assertEqual(
                json.loads(Path(service.job_file(finished['id'])).read_text())['status'], 'done'
            )
            self.assertEqual(
                json.loads(Path(service.job_file(pending['id'])).read_text())['status'], 'failed'
            )

    def test_subprocess_job_records_completion_and_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            finished = []
            service = JobService(
                lambda: temporary,
                lambda: temporary,
                threading.RLock(),
                lambda job, code, tail: finished.append((job['id'], code, tail)),
                lambda *_: None,
                lambda *_: None,
            )
            job = service.new_job(
                'forge', 'fixture', 'Run fixture', [sys.executable, '-c', "print('SLUG fixture')"]
            )
            queued_job, cmd, stdin = service.lanes['forge'].get_nowait()
            service.execute_job(queued_job, cmd, stdin)
            service.lanes['forge'].task_done()

            saved = json.loads(Path(service.job_file(job['id'])).read_text())
            self.assertEqual(saved['status'], 'done')
            self.assertEqual(saved['slug'], 'fixture')
            self.assertEqual(saved['returncode'], 0)
            self.assertEqual(finished, [(job['id'], 0, 'SLUG fixture\n')])

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
            patch.dict(core.LANES, fixture=lane),
            patch.object(lane, 'get', take),
            patch.object(core.JOBS_SERVICE, 'execute_job', execute),
            patch.object(core.JOBS_SERVICE, 'save_job') as saved,
        ):
            with self.assertRaises(StopIteration):
                core.worker('fixture')
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
            try:
                codes = [process.wait(timeout=30) for process in processes]
                self.assertEqual(codes, [0] * len(processes))
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=10)
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

    def test_atomic_replace_retries_a_temporary_windows_sharing_failure(self):
        sharing_error = PermissionError('Synthetic sharing violation')
        sharing_error.winerror = 32
        with (
            patch.object(storage.os, 'name', 'nt'),
            patch.object(storage.os, 'replace', side_effect=[sharing_error, None]) as replace,
        ):
            storage.atomic_replace('source', 'target')
            self.assertEqual(replace.call_count, 2)

    def test_atomic_replace_does_not_hide_a_permanent_permission_failure(self):
        permission_error = PermissionError('Synthetic permanent denial')
        permission_error.winerror = 5
        with (
            patch.object(storage.os, 'name', 'nt'),
            patch.object(storage.os, 'replace', side_effect=permission_error),
        ):
            with self.assertRaises(PermissionError):
                storage.atomic_replace('source', 'target', timeout=0)
