"""Persistent background job lanes and subprocess lifecycle.

Campaign-specific result handling is supplied by the application. The service owns
queueing, process execution, job records, logs, and restart recovery.
"""

import datetime
import json
import os
import queue
import re
import subprocess
import sys
import time

import config
import storage

NO_WINDOW = 0x08000000 if os.name == 'nt' else 0
CANCELLED_NOTE = 'Cancelled.'
# A child process reports progress by printing `PROGRESS 3/10 label` or `PROGRESS 40% label`.
PROGRESS_LINE = re.compile(r'^PROGRESS (?:(\d{1,9})/(\d{1,9})|(\d{1,9})%)(?: (.*))?\r?$', re.M)


def parse_progress(text):
    """The last PROGRESS report in `text` as {percent, label}, or None. Percent is clamped to 0-100."""
    found = PROGRESS_LINE.findall(text)
    if not found:
        return None
    done, total, percent, label = found[-1]
    if total:
        value = round(100 * int(done) / int(total)) if int(total) else 0
    else:
        value = int(percent)
    return {'percent': max(0, min(100, value)), 'label': label.strip()}


class SubprocessProcess:
    """A started child process: the three operations the service needs from any provider."""

    def __init__(self, popen):
        self.popen = popen

    def feed(self, text):
        self.popen.stdin.write(text.encode('utf-8'))
        self.popen.stdin.close()

    def wait(self):
        return self.popen.wait()

    def terminate(self):
        self.popen.terminate()


class SubprocessRunner:
    """Runs a job's command as a child process. A provider with the same `start` is a drop-in replacement.

    `start(cmd, cwd, env, log, has_stdin)` returns an object with `feed(text)`, `wait()` returning the
    exit code and `terminate()`. It raises OSError when the job cannot be started.
    """

    def start(self, cmd, cwd, env, log, has_stdin):
        return SubprocessProcess(
            subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                stdin=subprocess.PIPE if has_stdin else subprocess.DEVNULL,
                creationflags=NO_WINDOW,
            )
        )


class JobNotFound(LookupError):
    pass


class JobFinished(RuntimeError):
    pass


class JobService:
    def __init__(self, campaign, lock, on_finish, on_failure, on_restart, runner=None):
        self.campaign = campaign  # callable returning the Campaign whose jobs this service runs
        self.lock = lock
        self.on_finish = on_finish
        self.on_failure = on_failure
        self.on_restart = on_restart
        self.runner = runner or SubprocessRunner()
        self.lanes = {'forge': queue.Queue(), 'claude': queue.Queue(), 'art': queue.Queue()}
        self.running = {}
        self.cancelled = set()  # ids cancelled while queued or running

    def jobs_dir(self):
        return self.campaign().jobs

    def job_file(self, job_id, ext='json'):
        return os.path.join(self.jobs_dir(), f'{job_id}.{ext}')

    def save_job(self, job):
        os.makedirs(self.jobs_dir(), exist_ok=True)
        with self.lock:
            path = self.job_file(job['id'])
            with open(path + '.tmp', 'w', encoding='utf-8') as file:
                json.dump(job, file, indent=1)
            storage.atomic_replace(path + '.tmp', path)

    def new_job(self, lane, kind, label, cmd, stdin_text=None, **extra):
        job_id = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-') + os.urandom(2).hex()
        job = dict(
            id=job_id,
            lane=lane,
            kind=kind,
            label=label,
            status='queued',
            created=time.time(),
            **extra,
        )
        self.save_job(job)
        self.save_launch(job['id'], cmd, stdin_text)
        self.lanes[lane].put((job, cmd, stdin_text))
        return job

    def save_launch(self, job_id, cmd, stdin_text):
        """Keep what a queued job needs to start, so a restart can requeue it. Never holds the environment."""
        path = self.job_file(job_id, 'launch')
        with open(path + '.tmp', 'w', encoding='utf-8') as file:
            json.dump({'cmd': cmd, 'stdin': stdin_text}, file)
        storage.atomic_replace(path + '.tmp', path)

    def drop_launch(self, job_id):
        try:
            os.remove(self.job_file(job_id, 'launch'))
        except FileNotFoundError:
            pass

    def load_launch(self, job_id):
        try:
            with open(self.job_file(job_id, 'launch'), encoding='utf-8') as file:
                data = json.load(file)
        except (FileNotFoundError, ValueError):
            return None
        if isinstance(data.get('cmd'), list) and all(isinstance(part, str) for part in data['cmd']):
            return data['cmd'], data.get('stdin')
        return None

    def child_env(self):
        env = dict(os.environ)
        for key in list(env):
            if key == 'CLAUDECODE' or key.startswith('CLAUDE_CODE_'):
                env.pop(key)
        env['PYTHONIOENCODING'] = 'utf-8'
        env['FOUNDRY_DATA'] = config.foundry_data()
        env.update(self.campaign().child_env())  # child processes work on the same campaign
        return env

    def worker(self, lane):
        while True:
            job, cmd, stdin_text = self.lanes[lane].get()
            try:
                self.execute_job(job, cmd, stdin_text)
            except Exception as error:
                with self.lock:
                    job.update(status='failed', ended=time.time(), note=str(error))
                    self.save_job(job)
                    try:
                        self.on_failure(job, error)
                    except Exception as recovery_error:
                        sys.stderr.write(f'Job {job["id"]} recovery failed: {recovery_error}\n')
            finally:
                self.lanes[lane].task_done()

    def cancel(self, job_id):
        """Stop a queued or running job and settle it as failed with `cancelled` set."""
        with self.lock:
            try:
                with open(self.job_file(job_id), encoding='utf-8') as file:
                    job = json.load(file)
            except FileNotFoundError:
                raise JobNotFound(job_id) from None
            if job.get('status') not in ('queued', 'running'):
                raise JobFinished(job_id)
            if job_id in self.cancelled:
                return job
            self.cancelled.add(job_id)
            if job['status'] == 'running':
                proc = self.running.get(job_id)
                if proc:
                    proc.terminate()
                # Otherwise execute_job is about to start the process: it stops it and settles the record.
                return job
            self.settle_cancelled(job)
            return job

    def settle_cancelled(self, job):
        job.update(status='failed', ended=time.time(), note=CANCELLED_NOTE, cancelled=True)
        self.save_job(job)
        self.drop_launch(job['id'])
        self.on_failure(job, RuntimeError(CANCELLED_NOTE))

    def execute_job(self, job, cmd, stdin_text):
        with self.lock:
            if job['id'] in self.cancelled:
                self.cancelled.discard(job['id'])
                return  # cancel() already settled it while it was queued
            job.update(status='running', started=time.time())
            self.save_job(job)
            self.drop_launch(job['id'])  # a started job has side effects, so it is never replayed
        with open(self.job_file(job['id'], 'log'), 'w', encoding='utf-8', errors='replace') as log:
            try:
                proc = self.runner.start(
                    cmd, self.campaign().files, self.child_env(), log, bool(stdin_text)
                )
                with self.lock:
                    self.running[job['id']] = proc
                    if job['id'] in self.cancelled:
                        proc.terminate()
                if stdin_text:
                    proc.feed(stdin_text)
                code = proc.wait()
            except OSError as error:
                log.write(f'\ncould not start: {error}\n')
                code = -1
            finally:
                with self.lock:
                    self.running.pop(job['id'], None)
        with self.lock:
            if job['id'] in self.cancelled:
                self.cancelled.discard(job['id'])
                self.settle_cancelled(job)
                return
            tail = self.log_tail(job['id'], 400)
            slug = re.findall(r'^SLUG (\S+)', tail, re.M)
            job.update(status='done' if code == 0 else 'failed', ended=time.time(), returncode=code)
            if slug:
                job['slug'] = slug[-1]
            self.on_finish(job, code, tail)
            self.save_job(job)

    def log_tail(self, job_id, lines=60):
        try:
            with open(self.job_file(job_id, 'log'), encoding='utf-8', errors='replace') as file:
                return ''.join(file.readlines()[-lines:])
        except FileNotFoundError:
            return ''

    def progress(self, job_id):
        latest = None
        try:
            with open(self.job_file(job_id, 'log'), encoding='utf-8', errors='replace') as file:
                for line in file:
                    latest = parse_progress(line) or latest
        except FileNotFoundError:
            pass
        return latest

    def list_jobs(self, limit=30):
        if not os.path.isdir(self.jobs_dir()):
            return []
        out = []
        for name in sorted(os.listdir(self.jobs_dir()), reverse=True):
            if name.endswith('.json'):
                with open(os.path.join(self.jobs_dir(), name), encoding='utf-8') as file:
                    out.append(json.load(file))
                if len(out) >= limit:
                    break
        return out

    def recover_unfinished(self, before=None):
        """Settle jobs a stopped server left unfinished; keep any queued at or after before.

        A job that never started is requeued when its launch record survives; one that was running is
        failed, because replaying it could repeat its side effects. Call before the workers start.
        """
        for job in reversed(self.list_jobs(200)):  # oldest first, so lanes keep their order
            if (
                job
                and job.get('status') in ('queued', 'running')
                and (before is None or job.get('created', 0) < before)
            ):
                launch = self.load_launch(job['id']) if job['status'] == 'queued' else None
                if launch and job.get('lane') in self.lanes:
                    self.lanes[job['lane']].put((job, *launch))
                    continue
                self.drop_launch(job['id'])
                job['status'] = 'failed'
                job['note'] = 'the DM site stopped while this was running'
                self.save_job(job)
                self.on_restart(job)
