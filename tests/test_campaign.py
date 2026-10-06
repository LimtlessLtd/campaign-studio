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
import config
import maps_io
import revisions

sys.path.insert(0, str(ROOT / 'DM' / 'forge'))
import forge
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

    def test_settings_and_slugs_use_the_campaign_they_are_given(self):
        other = campaign.Campaign(Path(self.temp.name) / 'Other')
        Path(other.data).mkdir(parents=True)
        Path(other.settings).write_text(json.dumps({'campaign_name': 'Given'}))
        Path(self.studio.settings).write_text(json.dumps({'campaign_name': 'Active'}))
        Path(other.map_folder('keep')).mkdir(parents=True)
        with campaign.using(self.studio):
            self.assertEqual(config.settings()['campaign_name'], 'Active')
            self.assertEqual(config.settings(other)['campaign_name'], 'Given')
            self.assertEqual(maps_io.unique_slug('Keep', other), 'keep-2')
            self.assertEqual(maps_io.unique_slug('Keep'), 'keep')

    def test_forge_exports_use_the_campaign_they_are_given(self):
        other = campaign.Campaign(Path(self.temp.name) / 'Other')
        Path(other.data).mkdir(parents=True)
        Path(other.data, 'codex.json').write_text(
            json.dumps({'entries': [{'id': 'ogre', 'name': 'Given Ogre', 'type': 'npc'}]})
        )
        Path(other.map_index).parent.mkdir(parents=True, exist_ok=True)
        Path(other.map_index).write_text(json.dumps({'items': []}))
        key = {'areas': [{'n': 1, 'name': 'Hall', 'npcs': ['ogre']}]}
        with campaign.using(self.studio):
            snapshot = forge.key_for_foundry('hall', key, copy_art=False, here=other)
            forge.update_index({'slug': 'hall', 'name': 'Hall'}, other)
        self.assertIn('Given Ogre', json.dumps(snapshot))
        self.assertEqual(json.loads(Path(other.map_index).read_text())['items'][0]['slug'], 'hall')
        self.assertFalse(Path(self.studio.map_index).exists())

    def test_revisions_use_the_campaign_they_are_given(self):
        other = campaign.Campaign(Path(self.temp.name) / 'Other')
        folder = Path(other.map_folder('keep'))
        folder.mkdir(parents=True)
        (folder / 'plan.txt').write_text('plan')
        with campaign.using(self.studio):
            made = revisions.checkpoint('keep', 'Saved', other)
            listed = revisions.listing('keep', other)
            with self.assertRaises(ValueError):
                revisions.listing('keep')
        self.assertEqual([r['id'] for r in listed], [made['id']])
        self.assertTrue((folder / 'revisions' / made['id'] / 'plan.txt').is_file())
        self.assertFalse(Path(self.studio.map_folder('keep')).exists())

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


if __name__ == '__main__':
    unittest.main()
