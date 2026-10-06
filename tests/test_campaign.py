"""One Campaign object locates a campaign's files, for this process and the jobs it starts."""

import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
import campaign
import campaign_core
from job_service import JobService

FOLDERS = re.compile(r"""os\.path\.join\([^)]*'(data|maps|uploads|backups)'""")


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # A campaign outside the app folder, whose folder is not named DM.
        self.studio = campaign.Campaign(Path(self.temp.name) / 'Studio')
        Path(self.studio.data).mkdir(parents=True)

    def test_only_the_campaign_module_names_campaign_folders(self):
        found = [
            f'{path.relative_to(ROOT)}:{n}'
            for path in sorted((ROOT / 'DM').rglob('*.py'))
            if path.name != 'campaign.py' and 'tools' not in path.relative_to(ROOT / 'DM').parts
            for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1)
            if FOLDERS.search(line)
        ]
        self.assertEqual(found, [], 'Use campaign.active() for campaign paths.')

    def test_documents_and_stored_paths_follow_the_active_campaign(self):
        with campaign.using(self.studio):
            campaign_core.write_doc('codex', {'entries': []})
            upload = Path(self.studio.uploads) / 'token.png'
            upload.parent.mkdir()
            upload.write_bytes(b'')
            stored = self.studio.relative(str(upload))
            self.assertEqual(stored, 'Studio/uploads/token.png')
            self.assertEqual(campaign_core.campaign_path(stored), str(upload.resolve()))
        self.assertTrue((Path(self.studio.data) / 'codex.json').is_file())
        self.assertNotEqual(campaign.active(), self.studio)

    def test_jobs_run_in_the_campaign_that_started_them(self):
        Path(self.studio.settings).write_text(json.dumps({'campaign_name': 'Elsewhere'}))
        finished = []
        service = JobService(
            lambda: self.studio,
            threading.RLock(),
            lambda job, code, tail: finished.append(code),
            lambda *_: None,
            lambda *_: None,
        )
        code = (
            f'import sys; sys.path.insert(0, {str(ROOT / "DM")!r}); import campaign, config; '
            "print(campaign.active().home); print(config.settings()['campaign_name'])"
        )
        job = service.new_job('forge', 'fixture', 'Report campaign', [sys.executable, '-c', code])
        service.execute_job(*service.lanes['forge'].get_nowait())

        log = Path(service.job_file(job['id'], 'log')).read_text(encoding='utf-8').splitlines()
        self.assertEqual(finished, [0])
        self.assertEqual(log[:2], [self.studio.home, 'Elsewhere'])
        self.assertTrue(Path(self.studio.jobs, job['id'] + '.json').is_file())

    def test_an_old_queued_map_job_stays_busy_after_many_newer_jobs(self):
        with campaign.using(self.studio):
            service = campaign_core.JOBS_SERVICE
            service.new_job('forge', 'fixture', 'Old map', ['cmd'], slug='old-map')
            for n in range(205):
                Path(service.job_file(f'newer-{n:04d}')).write_text(
                    json.dumps({'id': f'newer-{n:04d}', 'status': 'done'})
                )
            self.assertTrue(campaign_core.map_busy('old-map'))


if __name__ == '__main__':
    unittest.main()
