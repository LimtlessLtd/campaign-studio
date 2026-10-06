"""Regression tests for worker survival and catalogue updates across processes."""

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
import campaign
import campaign_core as core
import storage
from campaign import Campaign
import job_service
from job_service import JobService


class JobsStorageTests(unittest.TestCase):
    def test_restart_fails_only_unfinished_jobs_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            restarted = []
            service = JobService(
                lambda: Campaign(os.path.join(temporary, 'DM')),
                threading.RLock(),
                lambda *_: None,
                lambda *_: None,
                lambda job: restarted.append(job['id']),
            )
            pending = service.new_job('forge', 'fixture', 'Pending', [])
            service.drop_launch(pending['id'])  # without a launch record it cannot be requeued
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

    def test_restart_requeues_unstarted_jobs_and_fails_started_ones(self):
        with tempfile.TemporaryDirectory() as temporary:
            restarted = []
            make = lambda: JobService(
                lambda: Campaign(os.path.join(temporary, 'DM')),
                threading.RLock(),
                lambda *_: None,
                lambda *_: None,
                lambda job: restarted.append(job['id']),
            )
            first = make()
            waiting = first.new_job('forge', 'fixture', 'Waiting', ['cmd', 'a'], 'prompt')
            started = first.new_job('art', 'fixture', 'Started', ['cmd', 'b'])
            started.update(status='running')
            first.save_job(started)
            first.drop_launch(started['id'])
            bare = first.new_job('claude', 'fixture', 'No launch record', ['cmd', 'c'])
            first.drop_launch(bare['id'])

            second = make()
            second.recover_unfinished()

            self.assertEqual(sorted(restarted), sorted([started['id'], bare['id']]))
            job, cmd, stdin = second.lanes['forge'].get_nowait()
            self.assertEqual((job['id'], cmd, stdin), (waiting['id'], ['cmd', 'a'], 'prompt'))
            self.assertTrue(second.lanes['art'].empty() and second.lanes['claude'].empty())
            saved = json.loads(Path(second.job_file(waiting['id'])).read_text())
            self.assertEqual(saved['status'], 'queued')

    def test_subprocess_job_records_completion_and_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            finished = []
            service = JobService(
                lambda: Campaign(os.path.join(temporary, 'DM')),
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

    def make_service(self, temporary, failures):
        return JobService(
            lambda: Campaign(os.path.join(temporary, 'DM')),
            threading.RLock(),
            lambda *_: None,
            lambda job, error: failures.append((job['id'], str(error))),
            lambda *_: None,
        )

    def test_cancel_queued_job_settles_it_and_worker_skips_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            failures = []
            service = self.make_service(temporary, failures)
            job = service.new_job('forge', 'fixture', 'Cancel me', [sys.executable, '-c', 'pass'])
            service.cancel(job['id'])
            queued_job, cmd, stdin = service.lanes['forge'].get_nowait()
            service.execute_job(queued_job, cmd, stdin)

            saved = json.loads(Path(service.job_file(job['id'])).read_text())
            self.assertEqual((saved['status'], saved['cancelled']), ('failed', True))
            self.assertEqual(failures, [(job['id'], 'Cancelled.')])
            self.assertFalse(Path(service.job_file(job['id'], 'log')).exists())
            self.assertEqual(service.cancelled, set())

    def test_cancel_running_job_terminates_the_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            failures = []
            service = self.make_service(temporary, failures)
            job = service.new_job(
                'forge', 'fixture', 'Sleep', [sys.executable, '-c', 'import time; time.sleep(60)']
            )
            queued_job, cmd, stdin = service.lanes['forge'].get_nowait()
            thread = threading.Thread(target=service.execute_job, args=(queued_job, cmd, stdin))
            thread.start()
            deadline = time.time() + 20
            while job['id'] not in service.running and time.time() < deadline:
                time.sleep(0.05)
            service.cancel(job['id'])
            thread.join(30)

            self.assertFalse(thread.is_alive())
            saved = json.loads(Path(service.job_file(job['id'])).read_text())
            self.assertEqual((saved['status'], saved['cancelled']), ('failed', True))
            self.assertEqual(failures, [(job['id'], 'Cancelled.')])
            self.assertEqual(service.running, {})

    def test_progress_reports_come_from_the_job_log(self):
        self.assertIsNone(job_service.parse_progress('working\n40% of nothing\n'))
        self.assertEqual(
            job_service.parse_progress('PROGRESS 1/4 start\nnoise\nPROGRESS 3/4 walls\n'),
            {'percent': 75, 'label': 'walls'},
        )
        self.assertEqual(job_service.parse_progress('PROGRESS 250%')['percent'], 100)
        self.assertEqual(job_service.parse_progress('PROGRESS 0/0')['percent'], 0)
        self.assertIsNone(job_service.parse_progress('PROGRESS ' + '9' * 5000 + '%'))
        with tempfile.TemporaryDirectory() as temporary:
            service = self.make_service(temporary, [])
            script = "print('PROGRESS 2/5 half', flush=True)"
            job = service.new_job('forge', 'fixture', 'Report', [sys.executable, '-c', script])
            queued_job, cmd, stdin = service.lanes['forge'].get_nowait()
            service.execute_job(queued_job, cmd, stdin)
            self.assertEqual(service.progress(job['id']), {'percent': 40, 'label': 'half'})
            with open(service.job_file(job['id'], 'log'), 'a', encoding='utf-8') as log:
                log.write('more work\n' * 250)
            self.assertEqual(service.progress(job['id']), {'percent': 40, 'label': 'half'})

    def test_cancel_rejects_unknown_and_finished_jobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = self.make_service(temporary, [])
            with self.assertRaises(job_service.JobNotFound):
                service.cancel('missing')
            job = service.new_job('art', 'fixture', 'Done', [])
            job['status'] = 'done'
            service.save_job(job)
            with self.assertRaises(job_service.JobFinished):
                service.cancel(job['id'])

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
            tempfile.TemporaryDirectory() as temporary,
            campaign.using(Campaign(temporary)),  # failure handling also replays the journal
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

    def test_remove_retries_a_temporary_windows_sharing_failure(self):
        sharing_error = PermissionError('Synthetic sharing violation')
        sharing_error.winerror = 32
        with (
            patch.object(storage.os, 'name', 'nt'),
            patch.object(storage.os, 'remove', side_effect=[sharing_error, None]) as remove,
        ):
            storage.remove('entry.pending.json')
            self.assertEqual(remove.call_count, 2)
        storage.remove(str(Path(tempfile.gettempdir()) / ('missing-' + os.urandom(6).hex())))

    def test_durable_write_flushes_the_file_and_its_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'doc.json'
            with patch.object(storage.os, 'fsync', wraps=os.fsync) as fsync:
                storage.atomic_json(str(path), {'entries': []})
                self.assertEqual(fsync.call_count, 0)
                storage.atomic_json(str(path), {'entries': []}, durable=True)
                self.assertEqual(fsync.call_count, 1 if os.name == 'nt' else 2)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'entries': []})

    def test_atomic_replace_does_not_hide_a_permanent_permission_failure(self):
        permission_error = PermissionError('Synthetic permanent denial')
        permission_error.winerror = 5
        with (
            patch.object(storage.os, 'name', 'nt'),
            patch.object(storage.os, 'replace', side_effect=permission_error),
        ):
            with self.assertRaises(PermissionError):
                storage.atomic_replace('source', 'target', timeout=0)
