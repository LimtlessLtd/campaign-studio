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
import foundry_library
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

    def test_export_links_entries_imported_from_the_target_world(self):
        world = Path(self.temp.name) / 'Foundry' / 'Data' / 'worlds' / 'w1'
        world.mkdir(parents=True)
        (world / 'world.json').write_text(json.dumps({'id': 'w1', 'title': 'W'}))
        (world / 'hero.webp').write_bytes(b'x')
        Path(self.studio.settings).write_text(json.dumps({'world_path': str(world)}))
        mine = foundry_library.world_key(config.world_info(str(world)))
        origin = {'uuid': 'Actor.abc', 'world_key': mine}
        Path(self.studio.data, 'codex.json').write_text(
            json.dumps(
                {
                    'entries': [
                        {
                            'id': 'hero',
                            'name': 'Hero',
                            'type': 'npc',
                            'image': 'worlds/w1/hero.webp',
                            'foundry': origin,
                        },
                        {
                            'id': 'far',
                            'name': 'Far',
                            'type': 'npc',
                            'foundry': dict(origin, world_key='other'),
                        },
                        {'id': 'own', 'name': 'Own', 'type': 'npc'},
                    ]
                }
            )
        )
        key = {'areas': [{'n': 1, 'name': 'Hall', 'npcs': ['hero', 'far', 'own']}]}
        with campaign.using(self.studio):
            details = forge.key_for_foundry('hall', key, copy_art=False)['areas'][0]['npcs_detail']
        by_id = {d['id']: d for d in details}
        self.assertEqual(by_id['hero']['uuid'], 'Actor.abc')
        self.assertEqual(by_id['hero']['image'], 'worlds/w1/hero.webp')
        self.assertNotIn('uuid', by_id['far'])
        self.assertNotIn('uuid', by_id['own'])

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
