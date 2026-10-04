"""HTTP transport and route handlers for Campaign Studio."""

import json
import mimetypes
import os
import re
import shutil
import sys
import urllib.parse
from http.server import SimpleHTTPRequestHandler
from pathlib import Path

import config
import foundry_backup
import foundry_library
import foundry_upgrade
import maps_io
import packaging_source
import request_workflow
import revisions
import workflow
from campaign_core import (
    APP,
    DATA,
    FORGE,
    HERE,
    IMAGE_SIGNATURES,
    JOURNAL,
    LOCK,
    MAPS,
    SLUG,
    UPLOADS,
    apply_content,
    apply_layout,
    campaign_path,
    commit_docs,
    doc_path,
    generate_cmd,
    job_file,
    list_docs,
    list_images,
    list_jobs,
    log_tail,
    map_busy,
    new_job,
    normal_brief,
    public_content,
    read_json,
    recover_commits,
    request_item,
    request_pack,
    request_read,
    restore_revision,
    rev_of,
    start_request,
    start_workflow,
    write_doc,
    write_target,
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
                        onboarding_needed=not os.path.isfile(config.CONFIG_PATH),
                        generators={k: v['title'] for k, v in generate.GENERATORS.items()},
                        notes=Path(notes).read_text(encoding='utf-8', errors='replace')
                        if os.path.exists(notes)
                        else '',
                        prep=list_docs('prep'),
                        interrupted_changes=JOURNAL.conflicts(),
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
            if path == '/api/foundry/backup/plan':
                return self.send_json(foundry_backup.plan())
            if path == '/api/foundry/worlds':
                return self.send_json(foundry_library.discover(query.get('root', [''])[0] or None))
            if path == '/api/foundry/library':
                return self.send_json(
                    foundry_library.library(
                        read_json(doc_path('foundry-library')),
                        kind=query.get('kind', ['scenes'])[0],
                        query=query.get('q', [''])[0][:200],
                        offset=int(query.get('offset', ['0'])[0]),
                        limit=int(query.get('limit', ['60'])[0]),
                    )
                )
            if path == '/api/foundry/asset':
                file, mime = foundry_library.media_file(query.get('path', [''])[0])
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(file.stat().st_size))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                with file.open('rb') as stream:
                    shutil.copyfileobj(stream, self.wfile)
                return
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
            if path.startswith('/api/requests/') and path.endswith('/pack'):
                rid = path.split('/')[3]
                if not request_workflow.REQUEST_ID.fullmatch(rid):
                    raise ValueError('Invalid request ID.')
                box = read_json(doc_path('inbox'), {'items': []})
                item = request_item(box, rid)
                if not item:
                    return self.fail(404, 'No such request.')
                return self.send_json(request_pack(item))
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
        if not self.local_host():
            return False
        try:
            # Every change starts from complete documents, never from a half-applied one.
            recover_commits()
        except OSError as error:
            self.fail(503, 'An interrupted change could not be completed yet: ' + str(error))
            return False
        return True

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
                with LOCK:
                    write_target('plan/' + slug, text)
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
            body_limit = (
                20 * 1024 * 1024 if path == '/api/foundry/library/import' else 2 * 1024 * 1024
            )
            p = json.loads(self.body(body_limit).decode('utf-8') or '{}')
            if not isinstance(p, dict):
                raise ValueError('The request must be a JSON object.')
            if path == '/api/package':
                return self.send_json(packaging_source.build())
            if path.startswith('/api/commits/') and path.endswith('/dismiss'):
                with LOCK:
                    JOURNAL.dismiss(path.split('/')[3])
                return self.send_json({'ok': True, 'interrupted_changes': JOURNAL.conflicts()})
            if path == '/api/foundry/library/import':
                world = foundry_library.selected_world()
                if not world:
                    raise ValueError(
                        'Connect a Foundry world before importing its library snapshot.'
                    )
                snapshot = foundry_library.normalize_snapshot(p, world)
                with LOCK:
                    write_doc('foundry-library', snapshot)
                return self.send_json(
                    {
                        'ok': True,
                        'counts': {
                            kind: len(snapshot['documents'][kind]) for kind in foundry_library.KINDS
                        },
                    }
                )
            if path == '/api/foundry/backup/create':
                return self.send_json(
                    foundry_backup.create(p.get('destination'), p.get('confirmed_closed') is True)
                )
            if path == '/api/foundry/backup/verify':
                return self.send_json(foundry_backup.verify(p.get('path')))
            if path == '/api/foundry/backup/rehearse':
                return self.send_json(foundry_backup.rehearse(p.get('path'), p.get('destination')))
            if path == '/api/foundry/upgrade/report':
                return self.send_json(
                    foundry_upgrade.report(
                        p.get('inventory'),
                        p.get('backup_path'),
                        p.get('disabled_modules') or [],
                        p.get('approved_dependencies') or [],
                    )
                )
            if path == '/api/foundry/upgrade/prepare-clone':
                return self.send_json(
                    foundry_upgrade.prepare_clone(
                        p.get('report_path'),
                        p.get('restore_receipt_path'),
                        p.get('destination'),
                        p.get('confirmed_v12_restore') is True,
                        p.get('confirmed_report') is True,
                    )
                )
            if path == '/api/foundry/upgrade/review-clone':
                return self.send_json(
                    foundry_upgrade.review_clone(
                        p.get('plan_path'),
                        p.get('inventory'),
                        p.get('confirmed_clone') is True,
                    )
                )
            if path == '/api/foundry/upgrade/audit-migration':
                return self.send_json(
                    foundry_upgrade.audit_migration(
                        p.get('review_path'),
                        p.get('inventory'),
                        p.get('confirmed_clone') is True,
                        p.get('manual_checks'),
                    )
                )
            if path.startswith('/api/requests/'):
                parts = path.split('/')
                if len(parts) != 5 or not request_workflow.REQUEST_ID.fullmatch(parts[3]):
                    raise ValueError('Invalid request route.')
                rid, action = parts[3:]
                if action == 'run':
                    return self.send_json(start_request(rid))
                if action not in ('stage', 'apply'):
                    raise ValueError('Unknown request action.')
                with LOCK:
                    box = read_json(doc_path('inbox'), {'items': []})
                    item = request_item(box, rid)
                    if not item:
                        return self.fail(404, 'No such request.')
                    if item.get('status') in ('doing', 'done') or item.get('applied'):
                        return self.fail(
                            409, 'Reopen or wait for this request before changing its draft.'
                        )
                    if action == 'stage':
                        request_workflow.stage(item, p['draft'], request_read)
                        write_doc('inbox', box)
                    else:
                        request_workflow.apply(item, request_read, commit_docs, box)
                    return self.send_json(item)
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
                    return self.send_json(restore_revision(slug, p.get('revision', '')))
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
                        return self.send_json(apply_content(value['id']))
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
                if not request_workflow.REQUEST_ID.fullmatch(rid):
                    return self.fail(400, 'bad request id')
                return self.send_json(start_request(rid))
        except (ValueError, KeyError, TypeError, OSError) as e:
            return self.fail(400, str(e))
        return self.fail(404, 'unknown endpoint')
