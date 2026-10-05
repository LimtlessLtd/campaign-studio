"""Campaign document storage, map workflows and job result handling."""

import datetime
import functools
import json
import os
import re
import shutil
import sys
import threading
import campaign
import commits
import config
import foundry_library
import request_workflow
import workflow
import revisions
import schema
import shapes
import storage
from job_service import JobService

APP = os.path.join(campaign.INSTALL, 'app')
FORGE = os.path.join(campaign.INSTALL, 'forge')
PORT = int(os.environ.get('DM_PORT', 8766))

# campaign folders the site may show files from (read-only)
FILE_ROOTS = (
    'PCs',
    'Gods',
    'Handouts',
    'Maps & Assets',
    'Tokens',
    'Dungeon Alchemist',
    'Session recordings',
    'Arena Teams',
    'Icons',
    'Landing Page & Social Media stuff',
    'Website',
    'DM',
)
DOC_NAME = re.compile(r'^[a-z0-9][a-z0-9_-]*(?:/[a-z0-9][a-z0-9_-]*)?$')
SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')
# Documents the application owns: only their own routes and services change them.
APP_OWNED_DOC = re.compile(r'^(?:settings|foundry-library|(?:workflows|jobs)/.+)$')
IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
IMAGE_SIGNATURES = {
    'image/png': ('.png', lambda b: b.startswith(b'\x89PNG\r\n\x1a\n')),
    'image/jpeg': ('.jpg', lambda b: b.startswith(b'\xff\xd8\xff')),
    'image/webp': ('.webp', lambda b: b.startswith(b'RIFF') and b[8:12] == b'WEBP'),
    'image/gif': ('.gif', lambda b: b.startswith((b'GIF87a', b'GIF89a'))),
}
KEEP_VERSIONS = 50
LOCK = threading.RLock()  # re-entrant: handlers hold it while saving jobs


# ---------- documents ----------
def doc_path(name):
    if not DOC_NAME.match(name):
        raise ValueError('bad document name')
    if name.startswith('mapkey/'):  # a map's DM key lives beside its plan
        slug = name.split('/', 1)[1]
        return os.path.join(campaign.active().map_folder(slug), 'key.json')
    return os.path.join(campaign.active().data, *name.split('/')) + '.json'


def editable_doc_path(name):
    """Path of a document the browser may replace through the generic document save."""
    path = doc_path(name)
    if APP_OWNED_DOC.match(name):
        raise PermissionError(f'{name} is managed by Campaign Studio and cannot be saved directly')
    return path


def rev_of(path):
    try:
        return str(os.stat(path).st_mtime_ns)
    except FileNotFoundError:
        return '0'


def campaign_path(rel):
    rel = rel.replace('\\', '/').lstrip('/')
    here = campaign.active()
    if rel.split('/', 1)[0] not in FILE_ROOTS + (os.path.basename(here.home),):
        raise PermissionError(rel)
    files = here.files
    full = os.path.realpath(os.path.join(files, rel))
    if not full.startswith(os.path.realpath(files) + os.sep):
        raise PermissionError(rel)
    return full


def read_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def stamp_new_campaign():
    """Record this build's schema just before a new campaign's first document is written.

    Startup leaves an empty campaign unstamped, so data copied in before first use is still migrated.
    """
    data, maps = campaign.active().data, campaign.active().maps
    if not os.path.isfile(schema.marker_path(data)) and schema.version(data, maps) is None:
        schema.write_marker(data, schema.CURRENT, 'New campaign')


def write_doc(name, value, durable=False):
    stamp_new_campaign()
    path = doc_path(name)
    with storage.file_lock(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            keep = os.path.join(campaign.active().history, *name.split('/'))
            os.makedirs(keep, exist_ok=True)
            shutil.copy2(
                path,
                os.path.join(keep, datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json'),
            )
            for old in sorted(os.listdir(keep))[:-KEEP_VERSIONS]:
                os.remove(os.path.join(keep, old))
        storage.atomic_json(path, value, durable)
        return rev_of(path)


def target_path(name):
    """Journal target names are document names, plus plan/<slug> for a map's text plan."""
    if name.startswith('plan/'):
        if not SLUG.fullmatch(name[5:]):
            raise ValueError('bad map name')
        return os.path.join(campaign.active().map_folder(name[5:]), 'plan.txt')
    return doc_path(name)


def write_target(name, value):
    """Write one journaled document, flushed to disk before the journal entry is deleted."""
    path = target_path(name)
    if name.startswith('plan/'):
        with storage.file_lock(path):
            storage.atomic_text(path, value, durable=True)
    elif name.startswith('workflows/'):
        workflow.write(path, value, durable=True)  # workflow records keep no document history
    else:
        write_doc(name, value, durable=True)


def forge_follow_up(slug, label, populate=False):
    return {'type': 'forge', 'slug': slug, 'label': label, 'populate': populate}


def run_follow_ups(actions):
    """Steps recorded with a change and run once its documents are written, also after a crash.

    Each step is idempotent and best effort: a failure is logged and never blocks the change.
    """
    results = []
    for action in actions:
        try:
            if action['type'] == 'forge':
                results.append(queue_forge(action['slug'], action['label'], action['populate']))
            elif action['type'] == 'mark_stocked':
                results.append(mark_stocked(action['slug']))
            else:
                raise ValueError('unknown follow-up')
        except Exception as error:  # the journal contract: follow-ups never raise
            sys.stderr.write(f'Follow-up {action} after a change failed: {error}\n')
            results.append(None)
    return results


JOURNAL = commits.Journal(
    lambda: campaign.active().commits, target_path, write_target, run_follow_ups
)


def commit_docs(label, changes, after=()):
    """Write several documents so an interruption is completed rather than left half applied."""
    return JOURNAL.commit(label, changes, after)


def import_foundry_snapshot(snapshot):
    """Commit a World Library snapshot and its codex changes as one recoverable operation."""
    with LOCK:
        codex = read_json(doc_path('codex'), {'entries': []})
        report = foundry_library.import_into_codex(snapshot, codex)
        commit_docs('Import Foundry world', [('foundry-library', snapshot), ('codex', codex)])
        return report


def recover_commits():
    """Finish interrupted changes. Call before any read/modify/write of campaign documents."""
    with LOCK:
        return JOURNAL.recover()


def queue_forge(slug, label, populate=False):
    plan = os.path.join(campaign.active().map_folder(slug), 'plan.txt')
    cmd = [sys.executable, '-u', os.path.join(FORGE, 'forge.py'), plan]
    if not os.path.isfile(plan):
        cmd.append('--key-only')  # imported artwork has a scene and key but no grid plan
    return new_job('forge', 'forge', label, cmd, slug=slug, populate=populate)


def mark_stocked(slug):
    """Mirror a populated key in the shared map catalogue, which forge processes also update."""
    if not read_json(doc_path('mapkey/' + slug), {}).get('stocked'):
        return

    def mark(index):
        for entry in index['items']:
            if entry['slug'] == slug:
                entry['stocked'] = True

    storage.update_json(
        doc_path('maps/index'),
        {'items': []},
        mark,
        lambda value: write_doc('maps/index', value),
    )


def list_docs(prefix):
    folder = os.path.join(campaign.active().data, prefix)
    if not os.path.isdir(folder):
        return []
    return sorted(f[:-5] for f in os.listdir(folder) if f.endswith('.json'))


def list_images(rel):
    full = campaign_path(rel)
    out = []
    for dirpath, dirs, files in os.walk(full):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        for f in sorted(files):
            if os.path.splitext(f)[1].lower() in IMAGE_EXT:
                p = os.path.join(dirpath, f)
                out.append(
                    dict(
                        path=os.path.relpath(p, campaign.active().files).replace('\\', '/'),
                        size=os.path.getsize(p),
                        mtime=int(os.path.getmtime(p)),
                    )
                )
    return out


def public_content():
    """What the players can already see, from the public site's content files."""
    if not config.settings().get('legacy_references', False):
        return dict(sessions=[], heroes=[], locations=[])
    c = lambda *p: os.path.join(campaign.active().files, 'Website', 'content', *p)
    sessions = read_json(c('sessions.json'), {'chapters': []})
    flat = [dict(s, chapter=ch['title']) for ch in sessions['chapters'] for s in ch['sessions']]
    return dict(
        sessions=flat,
        heroes=read_json(c('heroes.json'), []),
        locations=read_json(c('locations.json'), {}).get('locations', []),
    )


# ---------- background jobs ----------
def settle_failed_job(job, message):
    """Mark what a failed job was producing as failed, unless that record has moved on."""
    if job.get('workflow'):
        value = workflow.get(job['workflow'])
        if value.get('status') == 'running' and value.get('job') == job['id']:
            value.update(status='failed', error=message)
            workflow.save(value)
    if job.get('art'):
        art = read_json(doc_path('art'), {'items': []})
        item = next((i for i in art['items'] if i['id'] == job['art']), None)
        if item and item.get('status') == 'generating':
            item.update(status='failed', error=message)
            write_doc('art', art)
    if job.get('request'):
        fail_request(job, message)


def fail_job(job, error):
    try:
        recover_commits()
    except OSError as pending:
        # Settle the job regardless: a record left 'running' could never be retried.
        sys.stderr.write(f'An interrupted change is still pending: {pending}\n')
    settle_failed_job(job, str(error))


def recover_interrupted_job(job):
    settle_failed_job(job, 'The server stopped before this finished. You can retry it.')


def finish_job(job, code, tail):
    try:
        recover_commits()
    except OSError as pending:
        # Keep the finished result (often a paid draft). The pending change completes later; if this
        # result touches one of its documents, that change is set aside for GM review, not overwritten.
        sys.stderr.write(f'An interrupted change is still pending: {pending}\n')
    if job['kind'] == 'request-draft':
        finish_request(job, code, tail)
    if job.get('art'):
        with LOCK:
            art = read_json(doc_path('art'), {'items': []})
            target = next((i for i in art['items'] if i['id'] == job['art']), None)
            if target:
                changes = []
                try:
                    if code:
                        raise ValueError(tail[-1000:] or 'The image provider failed.')
                    image_path = json.loads(tail)['path']
                    target.update(image=image_path, status='ready', error='')
                    if target.get('map'):
                        doc = 'mapkey/' + target['map']
                        keydoc = read_json(doc_path(doc), {})
                        area = (
                            keydoc
                            if target.get('area') is None
                            else next(
                                (a for a in keydoc.get('areas', []) if a['n'] == target['area']),
                                None,
                            )
                        )
                        if area is not None:
                            area.setdefault('images', []).append(image_path)
                            changes.append((doc, keydoc))
                    if target.get('codex'):
                        codex = read_json(doc_path('codex'), {'entries': []})
                        entry = next(
                            (e for e in codex['entries'] if e['id'] == target['codex']), None
                        )
                        if entry:
                            entry['image'] = image_path
                            changes.append(('codex', codex))
                except (ValueError, KeyError, TypeError, OSError) as e:
                    changes = []
                    target.update(status='failed', error=str(e))
                    job.update(status='failed', note=str(e))
                commit_docs('Link generated image ' + job['art'], changes + [('art', art)])
    if job.get('workflow') and job['kind'] == 'ai-workflow':
        value = workflow.get(job['workflow'])
        try:
            with open(job_file(job['id'], 'log'), encoding='utf-8') as f:
                raw = f.read()
            if code:
                try:
                    message = json.loads(raw).get('result')
                except (ValueError, AttributeError):
                    message = None
                raise ValueError(message or tail[-1500:] or 'The AI command failed.')
            workflow.stage(value, workflow.parse_output(raw))
        except (ValueError, KeyError, TypeError, OSError) as e:
            value.update(status='failed', error=str(e))
            workflow.save(value)
            job.update(status='failed', note=str(e))
    if job['status'] == 'done' and job.get('populate'):
        brief = read_json(doc_path('mapbrief/' + job['slug']), {})
        value = workflow.create(job['slug'], brief)
        if brief.get('auto_content'):
            try:
                start_workflow(value)
            except ValueError as e:
                value.update(status='ready', error=str(e))
                workflow.save(value)
        else:
            value['status'] = 'ready'
            workflow.save(value)


def request_item(box, rid):
    return next((x for x in box['items'] if x.get('id') == rid), None)


def request_read(name):
    return read_json(doc_path(name))


def fail_request(job, message):
    box = read_json(doc_path('inbox'), {'items': []})
    item = request_item(box, job['request'])
    if item and item.get('status') == 'doing' and item.get('job') == job['id']:
        item.update(status='new', error=message)
        write_doc('inbox', box)


def finish_request(job, code, tail):
    box = read_json(doc_path('inbox'), {'items': []})
    item = request_item(box, job['request'])
    if not item or item.get('status') != 'doing' or item.get('job') != job['id']:
        job.update(status='failed', note='The request was removed or changed during drafting.')
        return
    try:
        if code:
            raise ValueError(tail[-1000:] or 'The AI command failed.')
        with open(job_file(job['id'], 'log'), encoding='utf-8') as file:
            raw = file.read()
        request_workflow.stage(item, workflow.parse_output(raw), request_read)
        if item['draft_source'] != job['source']:
            raise ValueError('The request changed during drafting. Make a new proposal.')
        write_doc('inbox', box)
    except (ValueError, KeyError, TypeError, OSError) as error:
        item.update(status='new', draft=None, error=str(error))
        write_doc('inbox', box)
        job.update(status='failed', note=str(error))


JOBS_SERVICE = JobService(campaign.active, LOCK, finish_job, fail_job, recover_interrupted_job)
LANES = JOBS_SERVICE.lanes
RUNNING = JOBS_SERVICE.running
job_file = JOBS_SERVICE.job_file
save_job = JOBS_SERVICE.save_job
new_job = JOBS_SERVICE.new_job
worker = JOBS_SERVICE.worker
execute_job = JOBS_SERVICE.execute_job
log_tail = JOBS_SERVICE.log_tail
list_jobs = JOBS_SERVICE.list_jobs


def generate_cmd(p):
    sys.path.insert(0, FORGE)
    import generate  # The generator registry lives here.

    kind = p.get('type')
    if kind not in generate.GENERATORS:
        raise ValueError('unknown map type')
    W, H = int(p.get('width', 60)), int(p.get('height', 60))
    if not (20 <= W <= 160 and 20 <= H <= 160):
        raise ValueError('each side must be 20 to 160 squares')
    name = str(p.get('name') or '').strip()[:60]
    cmd = [
        sys.executable,
        '-u',
        os.path.join(FORGE, 'generate.py'),
        kind,
        '--size',
        f'{W}x{H}',
        '--forge',
    ]
    if name:
        cmd += ['--name', name]
    if str(p.get('seed', '')).strip():
        cmd += ['--seed', str(int(p['seed']))]
    for k in ('density', 'alleys', 'darkness'):
        if p.get(k) not in (None, ''):
            cmd += [f'--{k}', str(max(0.0, min(1.0, float(p[k]))))]
    if int(p.get('cell', 150)) in (100, 120, 140, 150, 200):
        cmd += ['--cell', str(int(p.get('cell', 150)))]
    for flag in ('canal', 'wall'):
        if p.get(flag):
            cmd.append(f'--{flag}')
    if p.get('market') is False:
        cmd.append('--no-market')
    if p.get('session') and DOC_NAME.match(str(p['session'])):
        cmd += ['--session', str(p['session'])]
    if p.get('summary'):
        cmd += ['--summary', str(p['summary'])[:200]]
    return cmd, f'{name or kind} ({W}x{H})'


def structured_claude_cmd(schema):
    exe = shutil.which('claude')
    if not exe:
        raise ValueError('Claude Code (the claude command) is not installed or not on PATH')
    command = [
        exe,
        '-p',
        '--output-format',
        'json',
        '--json-schema',
        json.dumps(schema),
        '--tools',
        '',
        '--restricted',
        '--strict-mcp-config',
    ]
    model = config.settings()['ai'].get('model')
    if model:
        command += ['--model', model]
    return command


def request_pack(item):
    cfg = config.settings()
    campaign_info = {'name': cfg['campaign_name']}
    if cfg.get('world_path'):
        campaign_info['world'] = config.world_info(cfg['world_path'])
    return request_workflow.prompt_pack(item, request_read, campaign_info)


def start_request(rid):
    with LOCK:
        box = read_json(doc_path('inbox'), {'items': []})
        item = request_item(box, rid)
        if not item:
            raise ValueError('No such request.')
        if item.get('applied'):
            raise ValueError(
                'This request was already applied. Start a new request for further changes.'
            )
        if item.get('status') == 'doing':
            raise ValueError('This request is already being drafted.')
        if item.get('status') == 'done':
            raise ValueError('Reopen this request before drafting again.')
        pack = request_pack(item)
        cmd = structured_claude_cmd(pack['schema'])
        job = new_job(
            'claude',
            'request-draft',
            'Draft: ' + item.get('text', item['kind'])[:60],
            cmd,
            pack['prompt'],
            request=rid,
            source=request_workflow.input_hash(item),
        )
        item.update(status='doing', job=job['id'], draft=None, error='')
        write_doc('inbox', box)
        return job


def start_workflow(value):
    with LOCK:
        value = workflow.get(value['id'])
        if value['status'] in ('running', 'applied'):
            raise ValueError('This workflow is already running or applied.')
        workflow.check_base(value)
        cmd = structured_claude_cmd(workflow.schema(value['kind']))
        cfg = config.settings()
        campaign_info = {'name': cfg['campaign_name']}
        if cfg.get('world_path'):
            campaign_info['world'] = config.world_info(cfg['world_path'])
        job = new_job(
            'claude',
            'ai-workflow',
            'AI: ' + value['kind'] + ' · ' + value['brief']['name'],
            cmd,
            workflow.prompt(value, campaign_info),
            workflow=value['id'],
            slug=value['map'],
        )
        value.update(status='running', job=job['id'], error='')
        workflow.save(value)
        return job


def normal_brief(p, existing=False):
    name = str(p.get('name', '')).strip()[:60]
    if not name:
        raise ValueError('Give the map a name.')
    kind = p.get('type', 'city')
    if kind not in ('city', 'custom'):
        raise ValueError('Choose a city district or an AI designed layout.')
    w, h = int(p.get('width', 60)), int(p.get('height', 60))
    low, high = (1, 320) if existing else (20, 160)
    if not (low <= w <= high and low <= h <= high):
        raise ValueError('Each side must be 20–160 squares.')
    theme = p.get('theme', 'city' if kind == 'city' else 'outdoor')
    if theme not in ('city', 'outdoor', 'dungeon', 'cellar', 'temple', 'tavern', 'ship', 'cave'):
        raise ValueError('Unknown environment theme.')
    c = p.get('content') or {}
    counts = {
        k: max(0, min(30, int(c.get(k, v))))
        for k, v in (('npcs', 5), ('items', 4), ('journals', 4), ('events', 5))
    }
    counts.update(art=bool(c.get('art', True)), threads=bool(c.get('threads', True)))
    return dict(
        name=name,
        prompt=str(p.get('prompt', '')).strip()[:10000],
        type=kind,
        theme=theme,
        width=w,
        height=h,
        cell=int(p.get('cell', 100)) if int(p.get('cell', 100)) in (100, 150) else 100,
        seed=int(p['seed'])
        if str(p.get('seed', '')).strip()
        else int.from_bytes(os.urandom(3), 'big'),
        density=max(0.2, min(1, float(p.get('density', 0.7)))),
        alleys=max(0, min(1, float(p.get('alleys', 0.5)))),
        darkness=max(0, min(1, float(p.get('darkness', 0.15)))),
        canal=bool(p.get('canal')),
        wall=bool(p.get('wall')),
        market=bool(p.get('market', True)),
        session=str(p.get('session', ''))[:30],
        content=counts,
        tone=str(p.get('tone', 'Grounded fantasy'))[:200],
        party_level=max(1, min(30, int(p.get('party_level', 5)))),
        threads=list(p.get('threads', []))[:50],
        auto_content=bool(p.get('auto_content', True)),
    )


def map_busy(slug):
    return any(
        j.get('slug') == slug and j.get('status') in ('queued', 'running') for j in list_jobs(200)
    )


def apply_content(wid):
    """Link a reviewed content draft to the map, codex, threads and art queue."""
    with LOCK:
        value = workflow.get(wid)
        if value['status'] != 'review':
            raise ValueError('There is no draft awaiting review.')
        workflow.check_base(value)
        revisions.checkpoint(value['map'], 'Before applying AI content')
        stocked = {'type': 'mark_stocked', 'slug': value['map']}
        return workflow.apply_content(value, functools.partial(commit_docs, after=[stocked]))


def restore_revision(slug, rid):
    """Restore a map checkpoint and re-render it; recovery re-renders after a crash too."""
    rerender = forge_follow_up(slug, 'Restore ' + slug)
    with LOCK:
        [job] = revisions.restore(slug, rid, functools.partial(commit_docs, after=[rerender]))
    if job is None:
        raise ValueError('The revision was restored, but its render could not be queued.')
    return job


def apply_layout(wid):
    with LOCK:
        value = workflow.get(wid)
        slug = value['map']
        if value['status'] != 'review' or map_busy(slug):
            raise ValueError('There is no available layout draft awaiting review.')
        draft = workflow.validate(value, value['draft'])
        folder = campaign.active().map_folder(slug)
        if os.path.exists(os.path.join(folder, 'plan.txt')):
            revisions.checkpoint(slug, 'Before AI revision')
        os.makedirs(folder, exist_ok=True)
        key = read_json(doc_path('mapkey/' + slug))
        if key is None:
            key = shapes.MAP_KEY.new(map=value['brief']['name'])
        existing = {a['n']: a for a in key['areas']}
        for area in draft['areas']:
            if area['n'] in existing:
                existing[area['n']].update(area)
            else:
                key['areas'].append(shapes.AREA.new(**area))
        key['session'] = value['brief'].get('session', key.get('session', ''))
        value.update(status='applied', draft=draft)
        # The render is recorded with the change, so recovery after a crash still queues it.
        [job] = commit_docs(
            'Apply layout ' + wid,
            [('plan/' + slug, draft['plan']), ('mapkey/' + slug, key), ('workflows/' + wid, value)],
            after=[forge_follow_up(slug, 'Render ' + slug, populate=value['kind'] == 'layout')],
        )
        if job is None:
            raise ValueError('The layout was applied, but its render could not be queued.')
        return job
