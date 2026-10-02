"""Campaign document storage, map workflows and job result handling."""

import datetime
import json
import os
import re
import shutil
import sys
import threading
import config
import workflow
import revisions
import storage
from job_service import JobService

HERE = os.path.dirname(os.path.abspath(__file__))
CAMPAIGN = os.path.dirname(HERE)
APP = os.path.join(HERE, 'app')
DATA = os.path.join(HERE, 'data')
MAPS = os.path.join(HERE, 'maps')
FORGE = os.path.join(HERE, 'forge')
HISTORY = os.path.join(DATA, '.history')
JOBS = os.path.join(DATA, 'jobs')
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
IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
UPLOADS = os.path.join(HERE, 'uploads')
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
        return os.path.join(MAPS, slug, 'key.json')
    return os.path.join(DATA, *name.split('/')) + '.json'


def rev_of(path):
    try:
        return str(os.stat(path).st_mtime_ns)
    except FileNotFoundError:
        return '0'


def campaign_path(rel):
    rel = rel.replace('\\', '/').lstrip('/')
    if rel.split('/', 1)[0] not in FILE_ROOTS:
        raise PermissionError(rel)
    full = os.path.realpath(os.path.join(CAMPAIGN, rel))
    if not full.startswith(os.path.realpath(CAMPAIGN) + os.sep):
        raise PermissionError(rel)
    return full


def read_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_doc(name, value):
    path = doc_path(name)
    with storage.file_lock(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            keep = os.path.join(HISTORY, *name.split('/'))
            os.makedirs(keep, exist_ok=True)
            shutil.copy2(
                path,
                os.path.join(keep, datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json'),
            )
            for old in sorted(os.listdir(keep))[:-KEEP_VERSIONS]:
                os.remove(os.path.join(keep, old))
        tmp = path + '.tmp-' + os.urandom(6).hex()
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(value, f, ensure_ascii=False, indent=1)
        storage.atomic_replace(tmp, path)
        return rev_of(path)


def list_docs(prefix):
    folder = os.path.join(DATA, prefix)
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
                        path=os.path.relpath(p, CAMPAIGN).replace('\\', '/'),
                        size=os.path.getsize(p),
                        mtime=int(os.path.getmtime(p)),
                    )
                )
    return out


def public_content():
    """What the players can already see, from the public site's content files."""
    if not config.settings().get('legacy_references', False):
        return dict(sessions=[], heroes=[], locations=[])
    c = lambda *p: os.path.join(CAMPAIGN, 'Website', 'content', *p)
    sessions = read_json(c('sessions.json'), {'chapters': []})
    flat = [dict(s, chapter=ch['title']) for ch in sessions['chapters'] for s in ch['sessions']]
    return dict(
        sessions=flat,
        heroes=read_json(c('heroes.json'), []),
        locations=read_json(c('locations.json'), {}).get('locations', []),
    )


# ---------- background jobs ----------
def fail_job(job, error):
    if job.get('workflow'):
        value = workflow.get(job['workflow'])
        value.update(status='failed', error=str(error))
        workflow.save(value)
    if job.get('art'):
        art = read_json(doc_path('art'), {'items': []})
        for item in art['items']:
            if item['id'] == job['art']:
                item.update(status='failed', error=str(error))
        write_doc('art', art)


def recover_interrupted_job(job):
    if job.get('workflow'):
        value = workflow.get(job['workflow'])
        value.update(
            status='failed', error='The server stopped during this draft. You can retry it.'
        )
        workflow.save(value)
    if job.get('art'):
        art = read_json(doc_path('art'), {'items': []})
        for item in art['items']:
            if item['id'] == job['art']:
                item.update(
                    status='failed',
                    error='The server stopped during image generation. You can retry it.',
                )
        write_doc('art', art)


def finish_job(job, code, tail):
    if job['kind'] == 'claude':
        settle_request(job, code, tail)
    if job.get('art'):
        with LOCK:
            art = read_json(doc_path('art'), {'items': []})
            target = next((i for i in art['items'] if i['id'] == job['art']), None)
            if target:
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
                            write_doc(doc, keydoc)
                    if target.get('codex'):
                        codex = read_json(doc_path('codex'), {'entries': []})
                        entry = next(
                            (e for e in codex['entries'] if e['id'] == target['codex']), None
                        )
                        if entry:
                            entry['image'] = image_path
                            write_doc('codex', codex)
                except (ValueError, KeyError, TypeError, OSError) as e:
                    target.update(status='failed', error=str(e))
                    job.update(status='failed', note=str(e))
                write_doc('art', art)
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


def settle_request(job, code, tail):
    """If Claude stopped without marking its request, say so on the request instead of leaving it 'doing'."""
    path = doc_path('inbox')
    box = read_json(path, {'items': []})
    item = next((x for x in box['items'] if x.get('id') == job.get('request')), None)
    if item and item.get('status') == 'doing':
        item['status'] = 'new'
        item['result'] = (
            (item.get('result') or '')
            + f'\n\n[Claude stopped (exit {code}) before finishing. Last output:]\n'
            + tail[-1500:]
        )
        write_doc('inbox', box)


JOBS_SERVICE = JobService(
    lambda: JOBS, lambda: CAMPAIGN, LOCK, finish_job, fail_job, recover_interrupted_job
)
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


CLAUDE_PROMPT = """You are doing one request from Campaign Studio (the Dungeon Master's private campaign
manager). First read DM/CLAUDE.md: it explains the data files and the rules. Then do the request with id
"{rid}" in DM/data/inbox.json. Mark it "doing" as you start, do the work, write a short plain summary of what you
made (with where to find it) into its "result", and set it to "done". If you cannot do it, set it back to "new"
and explain why in "result". Only change files under DM/. The request text is the DM's own words."""


def claude_cmd():
    exe = shutil.which('claude')
    if not exe:
        raise ValueError('Claude Code (the claude command) is not installed or not on PATH')
    tools = [
        'Read',
        'Write',
        'Edit',
        'Glob',
        'Grep',
        'Bash(python DM/forge/forge.py:*)',
        'Bash(python DM/forge/generate.py:*)',
    ]
    return [
        exe,
        '-p',
        '--output-format',
        'text',
        '--permission-mode',
        'acceptEdits',
        '--allowedTools',
        *tools,
    ]


def start_workflow(value):
    with LOCK:
        value = workflow.get(value['id'])
        if value['status'] in ('running', 'applied'):
            raise ValueError('This workflow is already running or applied.')
        workflow.check_base(value)
        exe = shutil.which('claude')
        if not exe:
            raise ValueError(
                'Install Claude Code, or export the prompt pack and import an AI proposal.'
            )
        cfg = config.settings()
        cmd = [
            exe,
            '-p',
            '--output-format',
            'json',
            '--json-schema',
            json.dumps(workflow.schema(value['kind'])),
            '--tools',
            '',
            '--restricted',
            '--strict-mcp-config',
        ]
        if cfg['ai'].get('model'):
            cmd += ['--model', cfg['ai']['model']]
        campaign = {'name': cfg['campaign_name']}
        if cfg.get('world_path'):
            campaign['world'] = config.world_info(cfg['world_path'])
        job = new_job(
            'claude',
            'ai-workflow',
            'AI: ' + value['kind'] + ' · ' + value['brief']['name'],
            cmd,
            workflow.prompt(value, campaign),
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


def apply_layout(wid):
    with LOCK:
        value = workflow.get(wid)
        slug = value['map']
        if value['status'] != 'review' or map_busy(slug):
            raise ValueError('There is no available layout draft awaiting review.')
        draft = workflow.validate(value, value['draft'])
        folder = os.path.join(MAPS, slug)
        if os.path.exists(os.path.join(folder, 'plan.txt')):
            revisions.checkpoint(slug, 'Before AI revision')
        os.makedirs(folder, exist_ok=True)
        key = read_json(
            doc_path('mapkey/' + slug),
            {
                'map': value['brief']['name'],
                'areas': [],
                'events': [],
                'notes': '',
                'stocked': False,
            },
        )
        existing = {a['n']: a for a in key['areas']}
        for area in draft['areas']:
            if area['n'] in existing:
                existing[area['n']].update(area)
            else:
                key['areas'].append(
                    dict(
                        area,
                        text='',
                        creatures='',
                        loot=[],
                        events=[],
                        journal=[],
                        npcs=[],
                        items=[],
                        images=[],
                    )
                )
        plan_path = os.path.join(folder, 'plan.txt')
        with open(plan_path + '.tmp', 'w', encoding='utf-8', newline='\n') as f:
            f.write(draft['plan'])
        storage.atomic_replace(plan_path + '.tmp', plan_path)
        key['session'] = value['brief'].get('session', key.get('session', ''))
        write_doc('mapkey/' + slug, key)
        value.update(status='applied', draft=draft)
        workflow.save(value)
        cmd = [sys.executable, '-u', os.path.join(FORGE, 'forge.py'), plan_path]
        return new_job(
            'forge', 'forge', 'Render ' + slug, cmd, slug=slug, populate=value['kind'] == 'layout'
        )
