"""Local Campaign Studio HTTP server, document history and background jobs.

Run: python DM/server.py; open http://127.0.0.1:8766.
Runtime campaign files are stored in ignored directories under DM/.
"""

import datetime
import json
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import config
import workflow
import revisions
import maps_io
import packaging_source
import storage
from pathlib import Path

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
NO_WINDOW = 0x08000000 if os.name == 'nt' else 0
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
        os.replace(tmp, path)
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
LANES = {'forge': queue.Queue(), 'claude': queue.Queue(), 'art': queue.Queue()}
RUNNING = {}


def job_file(job_id, ext='json'):
    return os.path.join(JOBS, f'{job_id}.{ext}')


def save_job(job):
    os.makedirs(JOBS, exist_ok=True)
    with LOCK:
        path = job_file(job['id'])
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(job, f, indent=1)
        os.replace(path + '.tmp', path)


def new_job(lane, kind, label, cmd, stdin_text=None, **extra):
    job_id = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-') + os.urandom(2).hex()
    job = dict(
        id=job_id, lane=lane, kind=kind, label=label, status='queued', created=time.time(), **extra
    )
    save_job(job)
    LANES[lane].put((job, cmd, stdin_text))
    return job


def child_env():
    env = dict(os.environ)
    for k in list(env):  # a Claude started from here must not think it is nested in another session
        if k == 'CLAUDECODE' or k.startswith('CLAUDE_CODE_'):
            env.pop(k)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['FOUNDRY_DATA'] = config.foundry_data()
    return env


def worker(lane):
    while True:
        job, cmd, stdin_text = LANES[lane].get()
        try:
            execute_job(job, cmd, stdin_text)
        except Exception as error:
            # Record failures and continue to the next queued job.
            with LOCK:
                job.update(status='failed', ended=time.time(), note=str(error))
                save_job(job)
                try:
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
                except Exception as recovery_error:
                    sys.stderr.write(f'Job {job["id"]} recovery failed: {recovery_error}\n')
        finally:
            LANES[lane].task_done()


def execute_job(job, cmd, stdin_text):
    job.update(status='running', started=time.time())
    save_job(job)
    log = open(job_file(job['id'], 'log'), 'w', encoding='utf-8', errors='replace')
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=CAMPAIGN,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=child_env(),
            stdin=subprocess.PIPE if stdin_text else subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )
        RUNNING[job['id']] = proc
        if stdin_text:
            proc.stdin.write(stdin_text.encode('utf-8'))
            proc.stdin.close()
        code = proc.wait()
    except OSError as e:
        log.write(f'\ncould not start: {e}\n')
        code = -1
    finally:
        log.close()
        RUNNING.pop(job['id'], None)
    with LOCK:
        tail = log_tail(job['id'], 400)
        slug = re.findall(r'^SLUG (\S+)', tail, re.M)
        job.update(status='done' if code == 0 else 'failed', ended=time.time(), returncode=code)
        if slug:
            job['slug'] = slug[-1]
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
                                    (
                                        a
                                        for a in keydoc.get('areas', [])
                                        if a['n'] == target['area']
                                    ),
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
        save_job(job)


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


def log_tail(job_id, lines=60):
    try:
        with open(job_file(job_id, 'log'), encoding='utf-8', errors='replace') as f:
            return ''.join(f.readlines()[-lines:])
    except FileNotFoundError:
        return ''


def list_jobs(limit=30):
    if not os.path.isdir(JOBS):
        return []
    out = []
    for f in sorted(os.listdir(JOBS), reverse=True):
        if f.endswith('.json'):
            out.append(read_json(os.path.join(JOBS, f)))
            if len(out) >= limit:
                break
    return out


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
        os.replace(plan_path + '.tmp', plan_path)
        key['session'] = value['brief'].get('session', key.get('session', ''))
        write_doc('mapkey/' + slug, key)
        value.update(status='applied', draft=draft)
        workflow.save(value)
        cmd = [sys.executable, '-u', os.path.join(FORGE, 'forge.py'), plan_path]
        return new_job(
            'forge', 'forge', 'Render ' + slug, cmd, slug=slug, populate=value['kind'] == 'layout'
        )


# ---------- HTTP ----------
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=APP, **k)

    def log_message(self, fmt, *args):
        if args and '/api/' in str(args[0]) and not '/api/jobs' in str(args[0]):
            sys.stderr.write('%s\n' % (fmt % args))

    def send_json(self, value, status=200, headers=None):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def fail(self, status, message):
        self.send_json({'error': message}, status)

    def local_host(self):
        # a web page that re-points its own domain at 127.0.0.1 still sends its own Host
        host = (self.headers.get('Host') or '').rsplit(':', 1)[0]
        if host in ('127.0.0.1', 'localhost'):
            return True
        self.fail(403, 'the DM site only answers on 127.0.0.1')
        return False

    def body(self, limit=20 * 1024 * 1024):
        length = int(self.headers.get('Content-Length', 0))
        if length < 0 or length > limit:
            raise ValueError('too large')
        return self.rfile.read(length)

    def do_GET(self):
        if not self.local_host():
            return
        url = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(url.path)
        query = urllib.parse.parse_qs(url.query)
        try:
            if path == '/api/state':
                notes = os.path.join(DATA, 'notes.txt')
                sys.path.insert(0, FORGE)
                import generate

                return self.send_json(
                    dict(
                        public=public_content(),
                        claude=bool(shutil.which('claude')),
                        campaign=config.settings()['campaign_name'],
                        generators={k: v['title'] for k, v in generate.GENERATORS.items()},
                        notes=Path(notes).read_text(encoding='utf-8', errors='replace')
                        if os.path.exists(notes)
                        else '',
                        prep=list_docs('prep'),
                    )
                )
            if path == '/api/settings':
                cfg = config.settings()
                world = None
                error = ''
                if cfg.get('world_path'):
                    try:
                        world = config.world_info(cfg['world_path'])
                    except ValueError as e:
                        error = str(e)
                return self.send_json(
                    dict(
                        settings=cfg,
                        world=world,
                        error=error,
                        claude=bool(shutil.which('claude')),
                        image_key_available=bool(os.environ.get(cfg['images']['key_env'])),
                    )
                )
            if path == '/api/maps/pending':
                built = {
                    m['slug'] for m in read_json(doc_path('maps/index'), {'items': []})['items']
                }
                return self.send_json(
                    [
                        dict(
                            slug=slug,
                            brief=read_json(doc_path('mapbrief/' + slug)),
                            workflows=workflow.for_map(slug),
                        )
                        for slug in list_docs('mapbrief')
                        if slug not in built
                    ]
                )
            if path.startswith('/api/workflow/'):
                parts = path.split('/')
                if len(parts) not in (4, 5):
                    raise ValueError('Invalid workflow route.')
                value = workflow.get(parts[3])
                if len(parts) == 5 and parts[4] == 'pack':
                    campaign = {'name': config.settings()['campaign_name']}
                    if config.settings().get('world_path'):
                        campaign['world'] = config.world_info(config.settings()['world_path'])
                    return self.send_json(
                        {
                            'workflow': value['id'],
                            'prompt': workflow.prompt(value, campaign),
                            'schema': workflow.schema(value['kind']),
                        }
                    )
                return self.send_json(value)
            if path.startswith('/api/maps/') and path.endswith('/workspace'):
                slug = path.split('/')[3]
                if not SLUG.fullmatch(slug):
                    raise ValueError('Invalid map id.')
                return self.send_json(
                    {
                        'brief': read_json(doc_path('mapbrief/' + slug), {}),
                        'workflows': workflow.for_map(slug),
                        'has_plan': os.path.isfile(os.path.join(MAPS, slug, 'plan.txt')),
                        'revisions': revisions.listing(slug)
                        if os.path.isdir(os.path.join(MAPS, slug))
                        else [],
                    }
                )
            if path.startswith('/api/doc/'):
                name = path[9:]
                p = doc_path(name)
                value = read_json(p)
                if value is None:
                    return self.fail(404, 'no such document')
                return self.send_json(value, headers={'X-Rev': rev_of(p)})
            if path == '/api/revs':
                names = [n for n in query.get('names', [''])[0].split(',') if n]
                return self.send_json({n: rev_of(doc_path(n)) for n in names})
            if path.startswith('/api/plan/'):
                slug = path[10:]
                if not SLUG.match(slug):
                    return self.fail(400, 'bad map name')
                p = os.path.join(MAPS, slug, 'plan.txt')
                if not os.path.exists(p):
                    return self.fail(404, 'no such plan')
                return self.send_json({'text': Path(p).read_text(encoding='utf-8')})
            if path == '/api/jobs':
                return self.send_json(list_jobs())
            if path.startswith('/api/jobs/'):
                job_id = path[10:]
                if not re.fullmatch(r'[0-9a-f-]+', job_id):
                    return self.fail(400, 'bad job id')
                job = read_json(job_file(job_id))
                if not job:
                    return self.fail(404, 'no such job')
                return self.send_json(dict(job, log=log_tail(job_id)))
            if path == '/api/images':
                return self.send_json(list_images(query.get('dir', ['Handouts'])[0]))
            if path.startswith('/files/'):
                full = campaign_path(path[7:])
                if not os.path.isfile(full):
                    return self.fail(404, 'no such file')
                self.send_response(200)
                self.send_header(
                    'Content-Type', mimetypes.guess_type(full)[0] or 'application/octet-stream'
                )
                self.send_header('Content-Length', str(os.path.getsize(full)))
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                with open(full, 'rb') as f:
                    shutil.copyfileobj(f, self.wfile)
                return
        except (ValueError, PermissionError, KeyError, OSError) as e:
            return self.fail(403, str(e))
        except (ConnectionError, BrokenPipeError):
            return
        if path.startswith('/api/'):
            return self.fail(404, 'unknown endpoint')
        return super().do_GET()

    def writable(self):
        # a page on another site cannot send this header without a CORS preflight we never answer
        if self.headers.get('X-DM-Site') != '1':
            self.fail(403, 'missing X-DM-Site header')
            return False
        return self.local_host()

    def do_PUT(self):
        if not self.writable():
            return
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        try:
            if path.startswith('/api/doc/'):
                name = path[9:]
                p = doc_path(name)
                value = json.loads(self.body().decode('utf-8'))
                with LOCK:
                    base = self.headers.get('X-Rev')
                    current = rev_of(p)
                    if base and base != current and current != '0':
                        # someone (usually Claude) changed it since this page loaded it: let the page merge
                        return self.send_json(
                            {'error': 'conflict', 'rev': current, 'doc': read_json(p)}, 409
                        )
                    rev = write_doc(name, value)
                return self.send_json({'ok': True, 'rev': rev})
            if path.startswith('/api/plan/'):
                slug = path[10:]
                if not SLUG.match(slug) or not os.path.isdir(os.path.join(MAPS, slug)):
                    return self.fail(400, 'bad map name')
                text = json.loads(self.body(2 * 1024 * 1024).decode('utf-8'))['text']
                if map_busy(slug):
                    return self.fail(
                        409, "Wait for this map's current job to finish before editing its plan."
                    )
                sys.path.insert(0, FORGE)
                import forge

                forge.parse_plan(text)
                revisions.checkpoint(slug, 'Before plan edit')
                with open(
                    os.path.join(MAPS, slug, 'plan.txt'), 'w', encoding='utf-8', newline='\n'
                ) as f:
                    f.write(text)
                return self.send_json({'ok': True})
        except (ValueError, KeyError) as e:
            return self.fail(400, str(e))
        return self.fail(404, 'unknown endpoint')

    def do_POST(self):
        if not self.writable():
            return
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        try:
            if path == '/api/upload-image':
                mime = (self.headers.get('Content-Type') or '').split(';', 1)[0].lower()
                if mime not in IMAGE_SIGNATURES:
                    return self.fail(400, 'choose a PNG, JPEG, WebP or GIF image')
                data = self.body(25 * 1024 * 1024)
                ext, valid = IMAGE_SIGNATURES[mime]
                if not data or not valid(data):
                    return self.fail(400, 'the file does not match its image type')
                name = urllib.parse.unquote(self.headers.get('X-File-Name') or 'image')
                stem = (
                    re.sub(r'[^a-z0-9-]+', '-', os.path.splitext(name)[0].lower()).strip('-')[:48]
                    or 'image'
                )
                filename = f'{stem}-{os.urandom(6).hex()}{ext}'
                os.makedirs(UPLOADS, exist_ok=True)
                full = os.path.join(UPLOADS, filename)
                with open(full, 'xb') as f:
                    f.write(data)
                return self.send_json({'path': 'DM/uploads/' + filename})
            p = json.loads(self.body(2 * 1024 * 1024).decode('utf-8') or '{}')
            if not isinstance(p, dict):
                raise ValueError('The request must be a JSON object.')
            if path == '/api/package':
                return self.send_json(packaging_source.build())
            if path == '/api/maps/import':
                with LOCK:
                    return self.send_json(maps_io.import_image(p, write_doc))
            if path == '/api/settings':
                cfg = config.settings()
                name = str(p.get('campaign_name') or 'Campaign Studio').strip()[:100]
                world_path = str(p.get('world_path') or '').strip()
                if world_path:
                    world_path = config.world_info(world_path)['path']
                images = p.get('images') or cfg['images']
                endpoint = str(images.get('endpoint') or '').strip()
                if endpoint:
                    url = urllib.parse.urlparse(endpoint)
                    if (
                        url.scheme not in ('http', 'https')
                        or not url.hostname
                        or url.username
                        or url.password
                    ):
                        raise ValueError(
                            'Use an HTTP image generation endpoint without credentials in its URL.'
                        )
                    if url.scheme == 'http' and url.hostname not in (
                        'localhost',
                        '127.0.0.1',
                        '::1',
                    ):
                        raise ValueError('Remote image providers require HTTPS.')
                key_env = str(images.get('key_env') or 'IMAGE_API_KEY')
                if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', key_env):
                    raise ValueError('Use an environment variable name such as IMAGE_API_KEY.')
                cfg.update(
                    campaign_name=name,
                    world_path=world_path,
                    ai={
                        'provider': 'claude',
                        'model': str((p.get('ai') or {}).get('model') or '')[:100],
                    },
                    images={
                        'endpoint': endpoint,
                        'model': str(images.get('model') or '')[:100],
                        'key_env': key_env,
                        'size': images.get('size')
                        if images.get('size') in ('1024x1024', '1536x1024', '1024x1536')
                        else '1024x1024',
                    },
                )
                write_doc('settings', cfg)
                return self.send_json({'ok': True, 'settings': cfg})
            if path == '/api/art/generate':
                cfg = config.settings()['images']
                if not cfg.get('endpoint') or not cfg.get('model'):
                    raise ValueError('Configure an image endpoint and model in Settings first.')
                rid = str(p.get('id', ''))
                with LOCK:
                    art = read_json(doc_path('art'), {'items': []})
                    item = next((i for i in art['items'] if i['id'] == rid), None)
                    if not item:
                        raise ValueError('Artwork request not found.')
                    if item.get('status') == 'generating':
                        return self.fail(409, 'This image is already generating.')
                    item.update(status='generating', error='')
                    write_doc('art', art)
                    job = new_job(
                        'art',
                        'image',
                        'Image: ' + item['title'],
                        [sys.executable, '-u', os.path.join(HERE, 'tools', 'image_worker.py'), rid],
                        slug=item.get('map', ''),
                        art=rid,
                    )
                return self.send_json(job)
            if path == '/api/maps/create':
                brief = normal_brief(p)
                sys.path.insert(0, FORGE)
                import generate

                slug = generate.slugify(brief['name'])
                base = slug
                number = 2
                while os.path.exists(os.path.join(MAPS, slug)) or os.path.exists(
                    doc_path('mapbrief/' + slug)
                ):
                    slug = base + '-' + str(number)
                    number += 1
                write_doc('mapbrief/' + slug, brief)
                if brief['type'] == 'city':
                    cmd, label = generate_cmd(dict(brief, summary=brief['prompt'][:200]))
                    cmd += ['--slug', slug]
                    job = new_job(
                        'forge', 'generate', 'Build ' + label, cmd, slug=slug, populate=True
                    )
                    return self.send_json({'slug': slug, 'job': job})
                value = workflow.create(slug, brief, 'layout')
                job = start_workflow(value) if shutil.which('claude') else None
                return self.send_json({'slug': slug, 'workflow': value, 'job': job})
            if path.startswith('/api/maps/'):
                parts = path.split('/')
                if len(parts) != 5:
                    raise ValueError('Invalid map route.')
                slug, action = parts[3], parts[4] if len(parts) > 4 else ''
                if not SLUG.fullmatch(slug):
                    raise ValueError('Invalid map id.')
                brief = read_json(doc_path('mapbrief/' + slug), {})
                if action == 'export':
                    if map_busy(slug):
                        return self.fail(
                            409, 'Wait for this map’s current job to finish before exporting.'
                        )
                    with LOCK:
                        return self.send_json(maps_io.export(slug, write_doc))
                if action in ('populate', 'revise'):
                    if map_busy(slug):
                        return self.fail(409, 'This map already has a job running.')
                    if not brief:
                        index = read_json(doc_path('maps/index'), {'items': []})
                        m = next((m for m in index['items'] if m['slug'] == slug), None)
                        if not m:
                            raise ValueError('Map not found.')
                        brief = normal_brief(
                            dict(
                                name=m['name'],
                                prompt=m.get('summary', ''),
                                width=m['cells'][0],
                                height=m['cells'][1],
                                theme=m.get('theme', 'outdoor'),
                                type='city' if m.get('theme') == 'city' else 'custom',
                            ),
                            existing=True,
                        )
                        write_doc('mapbrief/' + slug, brief)
                    if action == 'revise' and not os.path.isfile(
                        os.path.join(MAPS, slug, 'plan.txt')
                    ):
                        raise ValueError(
                            'Imported images have no editable grid plan. Add locations and generate content on this image instead.'
                        )
                    if action == 'populate' and not read_json(doc_path('mapkey/' + slug), {}).get(
                        'areas'
                    ):
                        raise ValueError(
                            'Add numbered locations to this map before drafting campaign content.'
                        )
                    if action == 'populate' and p.get('area') is not None:
                        area = int(p['area'])
                        key = read_json(doc_path('mapkey/' + slug), {'areas': []})
                        if not any(a['n'] == area for a in key['areas']):
                            raise ValueError('Location not found.')
                        category = {
                            'npc': 'npcs',
                            'item': 'items',
                            'journal': 'journals',
                            'event': 'events',
                        }.get(p.get('kind'))
                        if not category:
                            raise ValueError('Choose NPC, item, journal or event.')
                        brief = dict(
                            brief,
                            area=area,
                            content={
                                **brief['content'],
                                'npcs': 0,
                                'items': 0,
                                'journals': 0,
                                'events': 0,
                                'threads': False,
                            },
                        )
                        brief['content'][category] = 1
                    instruction = str(p.get('instruction') or '').strip()[:10000]
                    if action == 'revise' and not instruction:
                        raise ValueError('Describe the change you want to make.')
                    value = workflow.create(
                        slug, brief, 'content' if action == 'populate' else 'revision', instruction
                    )
                    job = (
                        start_workflow(value)
                        if p.get('run', True) and shutil.which('claude')
                        else None
                    )
                    return self.send_json({'workflow': value, 'job': job})
                if action == 'checkpoint':
                    return self.send_json(
                        revisions.checkpoint(slug, p.get('label') or 'Saved checkpoint')
                    )
                if action == 'restore':
                    if map_busy(slug):
                        return self.fail(
                            409,
                            'Wait for the current map job to finish before restoring a revision.',
                        )
                    revisions.restore(slug, p.get('revision', ''), write_doc)
                    cmd = [
                        sys.executable,
                        '-u',
                        os.path.join(FORGE, 'forge.py'),
                        os.path.join(MAPS, slug, 'plan.txt'),
                    ]
                    if not os.path.isfile(cmd[-1]):
                        cmd += ['--key-only']
                    return self.send_json(
                        new_job('forge', 'forge', 'Restore ' + slug, cmd, slug=slug)
                    )
            if path.startswith('/api/workflow/'):
                parts = path.split('/')
                if len(parts) != 5:
                    raise ValueError('Invalid workflow route.')
                value = workflow.get(parts[3])
                action = parts[4] if len(parts) > 4 else ''
                if action == 'feedback':
                    with LOCK:
                        value = workflow.get(value['id'])
                        if value['status'] != 'review':
                            raise ValueError('Only a draft awaiting review can be refined.')
                        workflow.check_base(value)
                        instruction = str(p.get('instruction') or '').strip()[:10000]
                        if not instruction:
                            raise ValueError('Describe what the AI should change.')
                        value.update(
                            previous_draft=value['draft'],
                            draft=None,
                            status='ready',
                            error='',
                            instruction=value.get('instruction', '')
                            + '\nRefinement: '
                            + instruction,
                        )
                        workflow.save(value)
                        job = (
                            start_workflow(value)
                            if p.get('run', True) and shutil.which('claude')
                            else None
                        )
                        return self.send_json({'workflow': value, 'job': job})
                if action == 'run':
                    if value['status'] in ('running', 'applied'):
                        return self.fail(409, 'This workflow is already running or applied.')
                    with LOCK:
                        value = workflow.get(value['id'])
                        if value['status'] in ('running', 'applied'):
                            raise ValueError('This workflow is already running or applied.')
                        return self.send_json(start_workflow(value))
                if action == 'stage':
                    if value['status'] in ('running', 'applied'):
                        return self.fail(409, 'This workflow is already running or applied.')
                    with LOCK:
                        value = workflow.get(value['id'])
                        if value['status'] in ('running', 'applied'):
                            raise ValueError('This workflow is already running or applied.')
                        return self.send_json(workflow.stage(value, p.get('draft')))
                if action == 'apply':
                    slug = value['map']
                    if map_busy(slug):
                        return self.fail(
                            409, 'Wait for the current job to finish before applying this proposal.'
                        )
                    if value['kind'] == 'content':
                        with LOCK:
                            value = workflow.get(value['id'])
                            if value['status'] != 'review':
                                raise ValueError('There is no draft awaiting review.')
                            workflow.check_base(value)
                            revisions.checkpoint(slug, 'Before applying AI content')
                            result = workflow.apply_content(value, write_doc)

                            def mark_stocked(index):
                                for entry in index['items']:
                                    if entry['slug'] == slug:
                                        entry['stocked'] = True

                            storage.update_json(
                                doc_path('maps/index'),
                                {'items': []},
                                mark_stocked,
                                lambda value: write_doc('maps/index', value),
                            )
                        return self.send_json(result)
                    return self.send_json(apply_layout(value['id']))
            if path == '/api/generate':
                cmd, label = generate_cmd(p)
                return self.send_json(
                    new_job(
                        'forge', 'generate', 'Generate ' + label, cmd, session=p.get('session', '')
                    )
                )
            if path.startswith('/api/forge/'):
                slug = path[11:]
                plan = os.path.join(MAPS, slug, 'plan.txt')
                if not SLUG.match(slug) or (
                    not os.path.exists(plan)
                    and not (
                        p.get('key_only')
                        and os.path.isfile(os.path.join(MAPS, slug, slug + '.foundry.json'))
                    )
                ):
                    return self.fail(400, 'no such map')
                if map_busy(slug):
                    return self.fail(409, 'This map already has a job running.')
                cmd = [sys.executable, '-u', os.path.join(FORGE, 'forge.py'), plan]
                if p.get('key_only'):
                    cmd.append('--key-only')
                return self.send_json(
                    new_job(
                        'forge',
                        'forge',
                        ('Update key ' if p.get('key_only') else 'Forge ') + slug,
                        cmd,
                        slug=slug,
                        populate=bool(p.get('populate')) and not p.get('key_only'),
                    )
                )
            if path == '/api/claude':
                rid = str(p.get('request', ''))
                if not re.fullmatch(r'[a-z0-9-]{1,60}', rid):
                    return self.fail(400, 'bad request id')
                with LOCK:
                    box = read_json(doc_path('inbox'), {'items': []})
                    item = next((x for x in box['items'] if x.get('id') == rid), None)
                    if not item:
                        return self.fail(404, 'no such request')
                    if item.get('status') == 'doing':
                        return self.fail(409, 'already being done')
                    item['status'] = 'doing'
                    job = new_job(
                        'claude',
                        'claude',
                        'Claude: ' + (item.get('text') or item.get('kind', ''))[:60],
                        claude_cmd(),
                        CLAUDE_PROMPT.format(rid=rid),
                        request=rid,
                    )
                    item['job'] = job['id']
                    write_doc('inbox', box)
                return self.send_json(job)
        except (ValueError, KeyError, TypeError, OSError) as e:
            return self.fail(400, str(e))
        return self.fail(404, 'unknown endpoint')


def main():
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(JOBS, exist_ok=True)
    # jobs that were running when the server last stopped did not finish
    for job in list_jobs(200):
        if job and job.get('status') in ('queued', 'running'):
            job['status'] = 'failed'
            job['note'] = 'the DM site stopped while this was running'
            save_job(job)
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
    for lane in LANES:
        threading.Thread(target=worker, args=(lane,), daemon=True).start()
    print(f'DM site: http://127.0.0.1:{PORT}  (Ctrl+C to stop)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
