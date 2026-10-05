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

import campaign
import config
import foundry_backup
import foundry_library
import foundry_upgrade
import maps_io
import packaging_source
import remote_access
import request_workflow
import revisions
import shapes
import workflow
from campaign_core import (
    APP,
    FORGE,
    IMAGE_SIGNATURES,
    JOURNAL,
    LOCK,
    SLUG,
    apply_content,
    apply_layout,
    campaign_path,
    commit_docs,
    doc_path,
    editable_doc_path,
    generate_cmd,
    import_foundry_snapshot,
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

ROUTES = {
    'GET': (
        (lambda path: path == '/api/state', '_get_state'),
        (lambda path: path == '/api/shapes', '_get_shapes'),
        (lambda path: path == '/api/settings', '_get_settings'),
        (lambda path: path == '/api/foundry/backup/plan', '_get_backup_plan'),
        (lambda path: path == '/api/foundry/worlds', '_get_worlds'),
        (lambda path: path == '/api/foundry/library', '_get_library'),
        (lambda path: path == '/api/foundry/asset', '_get_asset'),
        (lambda path: path == '/api/maps/pending', '_get_pending_maps'),
        (lambda path: path.startswith('/api/workflow/'), '_get_workflow'),
        (
            lambda path: path.startswith('/api/requests/') and path.endswith('/pack'),
            '_get_request_pack',
        ),
        (
            lambda path: path.startswith('/api/maps/') and path.endswith('/workspace'),
            '_get_map_workspace',
        ),
        (lambda path: path.startswith('/api/doc/'), '_get_doc'),
        (lambda path: path == '/api/revs', '_get_revs'),
        (lambda path: path.startswith('/api/plan/'), '_get_plan'),
        (lambda path: path == '/api/jobs', '_get_jobs'),
        (lambda path: path.startswith('/api/jobs/'), '_get_job'),
        (lambda path: path == '/api/images', '_get_images'),
        (lambda path: path.startswith('/files/'), '_get_file'),
    ),
    'PUT': (
        (lambda path: path.startswith('/api/doc/'), '_put_doc'),
        (lambda path: path.startswith('/api/plan/'), '_put_plan'),
    ),
    'POST': (
        (lambda path: path == '/api/upload-image', '_post_upload'),
        (lambda path: path == '/api/package', '_post_package'),
        (
            lambda path: path.startswith('/api/commits/') and path.endswith('/dismiss'),
            '_post_dismiss_commit',
        ),
        (lambda path: path == '/api/foundry/library/import', '_post_import_library'),
        (lambda path: path == '/api/foundry/library/read', '_post_read_library'),
        (lambda path: path == '/api/foundry/world/import', '_post_import_world'),
        (lambda path: path == '/api/foundry/backup/create', '_post_backup_create'),
        (lambda path: path == '/api/foundry/backup/verify', '_post_backup_verify'),
        (lambda path: path == '/api/foundry/backup/rehearse', '_post_backup_rehearse'),
        (lambda path: path == '/api/foundry/upgrade/report', '_post_upgrade_report'),
        (lambda path: path == '/api/foundry/upgrade/prepare-clone', '_post_prepare_clone'),
        (lambda path: path == '/api/foundry/upgrade/review-clone', '_post_review_clone'),
        (lambda path: path == '/api/foundry/upgrade/audit-migration', '_post_audit_migration'),
        (lambda path: path == '/api/foundry/upgrade/review-cutover', '_post_review_cutover'),
        (lambda path: path.startswith('/api/requests/'), '_post_request'),
        (lambda path: path == '/api/maps/import', '_post_map_import'),
        (lambda path: path == '/api/settings', '_post_settings'),
        (lambda path: path == '/api/art/generate', '_post_art_generate'),
        (lambda path: path == '/api/maps/create', '_post_map_create'),
        (lambda path: path.startswith('/api/maps/'), '_post_map'),
        (lambda path: path.startswith('/api/workflow/'), '_post_workflow'),
        (lambda path: path == '/api/generate', '_post_generate'),
        (lambda path: path.startswith('/api/forge/'), '_post_forge'),
        (lambda path: path == '/api/claude', '_post_claude'),
    ),
}


GATE = remote_access.AccessGate(os.environ.get('DM_ACCESS_CODE', ''))


class RouteError(Exception):
    status = 400

    def __init__(self, message, payload=None):
        super().__init__(message)
        self.payload = payload or {'error': message}


class Invalid(RouteError):
    pass


class NotFound(RouteError):
    status = 404


class Conflict(RouteError):
    status = 409


class Forbidden(RouteError):
    status = 403


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
        if self.admitted():
            return True
        if GATE.enabled:
            self.send_login(401, 'Enter the access code to continue.')
        else:
            self.fail(403, 'the DM site only answers on 127.0.0.1')
        return False

    def admitted(self):
        peer = self.client_address[0]
        return GATE.local_request(peer, self.headers.get('Host')) or GATE.has_session(
            self.headers.get('Cookie')
        )

    def send_login(self, status, message=''):
        if self.path.startswith('/api/'):
            return self.fail(status, message)
        body = remote_access.login_page(message)
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _post_login(self):
        peer = self.client_address[0]
        try:
            # Consume a modest oversized form before replying; unread request bytes can reset the
            # response connection on Windows.
            raw = self.body(64 * 1024)
            if len(raw) > 4096:
                raise Invalid('too large')
            form = urllib.parse.parse_qs(raw.decode('utf-8', 'replace'))
        except (Invalid, ValueError):
            return self.send_login(400, 'That request could not be read.')
        wait = GATE.locked_for(peer)
        if wait:
            return self.send_login(429, f'Too many attempts. Try again in {wait} seconds.')
        if not GATE.try_code(peer, form.get('code', [''])[0]):
            return self.send_login(401, 'That code is not right.')
        secure = self.headers.get('X-Forwarded-Proto') == 'https'
        self.send_response(303)
        self.send_header('Location', '/')
        self.send_header('Set-Cookie', GATE.session_cookie(secure))
        self.send_header('Content-Length', '0')
        self.end_headers()

    def body(self, limit=20 * 1024 * 1024):
        length = int(self.headers.get('Content-Length', 0))
        if length < 0 or length > limit:
            raise Invalid('too large')
        return self.rfile.read(length)

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

    def _dispatch(self, method):
        if method == 'POST' and GATE.enabled and self.path == '/login':
            return self._post_login()
        if method == 'GET':
            if not self.local_host():
                return
        elif not self.writable():
            return
        url = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(url.path)
        query = urllib.parse.parse_qs(url.query)
        try:
            p = None
            if method == 'POST' and path != '/api/upload-image':
                limit = (
                    20 * 1024 * 1024 if path == '/api/foundry/library/import' else 2 * 1024 * 1024
                )
                p = json.loads(self.body(limit).decode('utf-8') or '{}')
                if not isinstance(p, dict):
                    raise Invalid('The request must be a JSON object.')
            for matches, name in ROUTES[method]:
                if matches(path):
                    return getattr(self, name)(path, query, p)
            if method == 'GET' and not path.startswith('/api/') and not path.startswith('/files/'):
                return super().do_GET()
            raise NotFound('unknown endpoint')
        except RouteError as error:
            return self.send_json(error.payload, error.status)
        except (ConnectionError, BrokenPipeError):
            return
        except PermissionError as error:
            forbidden = Forbidden(str(error))
            return self.send_json(forbidden.payload, forbidden.status)
        except (ValueError, KeyError, TypeError, OSError) as error:
            invalid = Invalid(str(error))
            return self.send_json(invalid.payload, invalid.status)

    def do_GET(self):
        return self._dispatch('GET')

    def do_PUT(self):
        return self._dispatch('PUT')

    def do_POST(self):
        return self._dispatch('POST')

    def _get_state(self, path, query, p):
        notes = os.path.join(campaign.active().data, 'notes.txt')
        cfg = config.settings()
        try:
            world = foundry_library.selected_world() if cfg.get('world_path') else None
        except (OSError, ValueError):
            world = None
        sys.path.insert(0, FORGE)
        import generate

        return self.send_json(
            dict(
                public=public_content(),
                claude=bool(shutil.which('claude')),
                campaign=cfg['campaign_name'],
                world_key=foundry_library.world_key(world) if world else '',
                onboarding_needed=not os.path.isfile(campaign.active().settings),
                generators={k: v['title'] for k, v in generate.GENERATORS.items()},
                notes=Path(notes).read_text(encoding='utf-8', errors='replace')
                if os.path.exists(notes)
                else '',
                prep=list_docs('prep'),
                interrupted_changes=JOURNAL.conflicts(),
            )
        )

    def _get_shapes(self, path, query, p):
        return self.send_json(shapes.describe())

    def _get_settings(self, path, query, p):
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

    def _get_backup_plan(self, path, query, p):
        return self.send_json(foundry_backup.plan())

    def _get_worlds(self, path, query, p):
        return self.send_json(foundry_library.discover(query.get('root', [''])[0] or None))

    def _get_library(self, path, query, p):
        return self.send_json(
            foundry_library.library(
                read_json(doc_path('foundry-library')),
                kind=query.get('kind', ['scenes'])[0],
                query=query.get('q', [''])[0][:200],
                offset=int(query.get('offset', ['0'])[0]),
                limit=int(query.get('limit', ['60'])[0]),
            )
        )

    def _get_asset(self, path, query, p):
        file, mime = foundry_library.media_file(
            query.get('path', [''])[0], query.get('world_key', [''])[0]
        )
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

    def _get_pending_maps(self, path, query, p):
        built = {m['slug'] for m in read_json(doc_path('maps/index'), {'items': []})['items']}
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

    def _get_workflow(self, path, query, p):
        parts = path.split('/')
        if len(parts) not in (4, 5):
            raise Invalid('Invalid workflow route.')
        value = workflow.get(parts[3])
        if len(parts) == 5 and parts[4] == 'pack':
            campaign_info = {'name': config.settings()['campaign_name']}
            if config.settings().get('world_path'):
                campaign_info['world'] = config.world_info(config.settings()['world_path'])
            return self.send_json(
                {
                    'workflow': value['id'],
                    'prompt': workflow.prompt(value, campaign_info),
                    'schema': workflow.schema(value['kind']),
                }
            )
        return self.send_json(value)

    def _get_request_pack(self, path, query, p):
        rid = path.split('/')[3]
        if not request_workflow.REQUEST_ID.fullmatch(rid):
            raise Invalid('Invalid request ID.')
        box = read_json(doc_path('inbox'), {'items': []})
        item = request_item(box, rid)
        if not item:
            raise NotFound('No such request.')
        try:
            return self.send_json(request_pack(item))
        except ValueError as error:
            # The existing read API treats map requests as inaccessible prompt packs.
            raise Forbidden(str(error)) from error

    def _get_map_workspace(self, path, query, p):
        slug = path.split('/')[3]
        if not SLUG.fullmatch(slug):
            raise Invalid('Invalid map id.')
        return self.send_json(
            {
                'brief': read_json(doc_path('mapbrief/' + slug), {}),
                'workflows': workflow.for_map(slug),
                'has_plan': os.path.isfile(
                    os.path.join(campaign.active().map_folder(slug), 'plan.txt')
                ),
                'revisions': revisions.listing(slug)
                if os.path.isdir(campaign.active().map_folder(slug))
                else [],
            }
        )

    def _get_doc(self, path, query, p):
        name = path[9:]
        p = doc_path(name)
        value = read_json(p)
        if value is None:
            raise NotFound('no such document')
        return self.send_json(value, headers={'X-Rev': rev_of(p)})

    def _get_revs(self, path, query, p):
        names = [n for n in query.get('names', [''])[0].split(',') if n]
        return self.send_json({n: rev_of(doc_path(n)) for n in names})

    def _get_plan(self, path, query, p):
        slug = path[10:]
        if not SLUG.match(slug):
            raise Invalid('bad map name')
        p = os.path.join(campaign.active().map_folder(slug), 'plan.txt')
        if not os.path.exists(p):
            raise NotFound('no such plan')
        return self.send_json({'text': Path(p).read_text(encoding='utf-8')})

    def _get_jobs(self, path, query, p):
        return self.send_json(list_jobs())

    def _get_job(self, path, query, p):
        job_id = path[10:]
        if not re.fullmatch(r'[0-9a-f-]+', job_id):
            raise Invalid('bad job id')
        job = read_json(job_file(job_id))
        if not job:
            raise NotFound('no such job')
        return self.send_json(dict(job, log=log_tail(job_id)))

    def _get_images(self, path, query, p):
        return self.send_json(list_images(query.get('dir', ['Handouts'])[0]))

    def _get_file(self, path, query, p):
        full = campaign_path(path[7:])
        if not os.path.isfile(full):
            raise NotFound('no such file')
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

    def _put_doc(self, path, query, p):
        name = path[9:]
        p = editable_doc_path(name)
        value = json.loads(self.body().decode('utf-8'))
        with LOCK:
            base = self.headers.get('X-Rev')
            current = rev_of(p)
            if base and base != current and current != '0':
                # someone (usually Claude) changed it since this page loaded it: let the page merge
                raise Conflict(
                    'conflict', {'error': 'conflict', 'rev': current, 'doc': read_json(p)}
                )
            rev = write_doc(name, value)
        return self.send_json({'ok': True, 'rev': rev})

    def _put_plan(self, path, query, p):
        slug = path[10:]
        if not SLUG.match(slug) or not os.path.isdir(campaign.active().map_folder(slug)):
            raise Invalid('bad map name')
        text = json.loads(self.body(2 * 1024 * 1024).decode('utf-8'))['text']
        if map_busy(slug):
            raise Conflict("Wait for this map's current job to finish before editing its plan.")
        sys.path.insert(0, FORGE)
        import forge

        forge.parse_plan(text)
        revisions.checkpoint(slug, 'Before plan edit')
        with LOCK:
            write_target('plan/' + slug, text)
        return self.send_json({'ok': True})

    def _post_upload(self, path, query, p):
        mime = (self.headers.get('Content-Type') or '').split(';', 1)[0].lower()
        if mime not in IMAGE_SIGNATURES:
            raise Invalid('choose a PNG, JPEG, WebP or GIF image')
        data = self.body(25 * 1024 * 1024)
        ext, valid = IMAGE_SIGNATURES[mime]
        if not data or not valid(data):
            raise Invalid('the file does not match its image type')
        name = urllib.parse.unquote(self.headers.get('X-File-Name') or 'image')
        stem = (
            re.sub(r'[^a-z0-9-]+', '-', os.path.splitext(name)[0].lower()).strip('-')[:48]
            or 'image'
        )
        filename = f'{stem}-{os.urandom(6).hex()}{ext}'
        os.makedirs(campaign.active().uploads, exist_ok=True)
        full = os.path.join(campaign.active().uploads, filename)
        with open(full, 'xb') as f:
            f.write(data)
        return self.send_json({'path': campaign.active().relative(full)})

    def _post_package(self, path, query, p):
        return self.send_json(packaging_source.build())

    def _post_dismiss_commit(self, path, query, p):
        with LOCK:
            JOURNAL.dismiss(path.split('/')[3])
        return self.send_json({'ok': True, 'interrupted_changes': JOURNAL.conflicts()})

    def _post_import_library(self, path, query, p):
        world = foundry_library.selected_world()
        if not world:
            raise Invalid('Connect a Foundry world before importing its library snapshot.')
        snapshot = foundry_library.normalize_snapshot(p, world)
        return self._save_foundry_import(snapshot, world)

    def _post_read_library(self, path, query, p):
        world = foundry_library.selected_world()
        if not world:
            raise Invalid('Connect a Foundry world before reading its documents.')
        snapshot = foundry_library.read_world(world)
        with LOCK:
            write_doc('foundry-library', snapshot)
        return self.send_json(self._library_counts(snapshot))

    def _post_import_world(self, path, query, p):
        """One action: read the world folder, refresh the snapshot and bring its documents into the codex."""
        world = foundry_library.selected_world()
        if not world:
            raise Invalid('Connect a Foundry world before importing it.')
        snapshot = foundry_library.read_world(world)
        return self._save_foundry_import(snapshot, world)

    def _save_foundry_import(self, snapshot, world):
        media = foundry_library.assets(world, '', 0, 1)
        report = import_foundry_snapshot(snapshot)
        report.update(self._library_counts(snapshot))
        report['media'] = media['total']
        report['media_truncated'] = media['truncated']
        return self.send_json(report)

    @staticmethod
    def _library_counts(snapshot):
        return {
            'ok': True,
            'counts': {kind: len(snapshot['documents'][kind]) for kind in foundry_library.KINDS},
            'omitted': snapshot['omitted'],
        }

    def _post_backup_create(self, path, query, p):
        return self.send_json(
            foundry_backup.create(p.get('destination'), p.get('confirmed_closed') is True)
        )

    def _post_backup_verify(self, path, query, p):
        return self.send_json(foundry_backup.verify(p.get('path')))

    def _post_backup_rehearse(self, path, query, p):
        return self.send_json(foundry_backup.rehearse(p.get('path'), p.get('destination')))

    def _post_upgrade_report(self, path, query, p):
        return self.send_json(
            foundry_upgrade.report(
                p.get('inventory'),
                p.get('backup_path'),
                p.get('disabled_modules') or [],
                p.get('approved_dependencies') or [],
            )
        )

    def _post_prepare_clone(self, path, query, p):
        return self.send_json(
            foundry_upgrade.prepare_clone(
                p.get('report_path'),
                p.get('restore_receipt_path'),
                p.get('destination'),
                p.get('confirmed_v12_restore') is True,
                p.get('confirmed_report') is True,
            )
        )

    def _post_review_clone(self, path, query, p):
        return self.send_json(
            foundry_upgrade.review_clone(
                p.get('plan_path'),
                p.get('inventory'),
                p.get('confirmed_clone') is True,
            )
        )

    def _post_audit_migration(self, path, query, p):
        return self.send_json(
            foundry_upgrade.audit_migration(
                p.get('review_path'),
                p.get('inventory'),
                p.get('confirmed_clone') is True,
                p.get('manual_checks'),
            )
        )

    def _post_review_cutover(self, path, query, p):
        return self.send_json(
            foundry_upgrade.review_cutover(p.get('audit_path'), p.get('confirmed_closed') is True)
        )

    def _post_request(self, path, query, p):
        parts = path.split('/')
        if len(parts) != 5 or not request_workflow.REQUEST_ID.fullmatch(parts[3]):
            raise Invalid('Invalid request route.')
        rid, action = parts[3:]
        if action == 'run':
            return self.send_json(start_request(rid))
        if action not in ('stage', 'apply'):
            raise Invalid('Unknown request action.')
        with LOCK:
            box = read_json(doc_path('inbox'), {'items': []})
            item = request_item(box, rid)
            if not item:
                raise NotFound('No such request.')
            if item.get('status') in ('doing', 'done') or item.get('applied'):
                raise Conflict('Reopen or wait for this request before changing its draft.')
            if action == 'stage':
                request_workflow.stage(item, p['draft'], request_read)
                write_doc('inbox', box)
            else:
                request_workflow.apply(item, request_read, commit_docs, box)
            return self.send_json(item)

    def _post_map_import(self, path, query, p):
        with LOCK:
            return self.send_json(maps_io.import_image(p, write_doc))

    def _post_settings(self, path, query, p):
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
                raise Invalid(
                    'Use an HTTP image generation endpoint without credentials in its URL.'
                )
            if url.scheme == 'http' and url.hostname not in (
                'localhost',
                '127.0.0.1',
                '::1',
            ):
                raise Invalid('Remote image providers require HTTPS.')
        key_env = str(images.get('key_env') or 'IMAGE_API_KEY')
        if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', key_env):
            raise Invalid('Use an environment variable name such as IMAGE_API_KEY.')
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

    def _post_art_generate(self, path, query, p):
        cfg = config.settings()['images']
        if not cfg.get('endpoint') or not cfg.get('model'):
            raise Invalid('Configure an image endpoint and model in Settings first.')
        rid = str(p.get('id', ''))
        with LOCK:
            art = read_json(doc_path('art'), {'items': []})
            item = next((i for i in art['items'] if i['id'] == rid), None)
            if not item:
                raise Invalid('Artwork request not found.')
            if item.get('status') == 'generating':
                raise Conflict('This image is already generating.')
            item.update(status='generating', error='')
            write_doc('art', art)
            job = new_job(
                'art',
                'image',
                'Image: ' + item['title'],
                [
                    sys.executable,
                    '-u',
                    os.path.join(campaign.INSTALL, 'tools', 'image_worker.py'),
                    rid,
                ],
                slug=item.get('map', ''),
                art=rid,
            )
        return self.send_json(job)

    def _post_map_create(self, path, query, p):
        brief = normal_brief(p)
        sys.path.insert(0, FORGE)
        import generate

        slug = generate.slugify(brief['name'])
        base = slug
        number = 2
        while os.path.exists(campaign.active().map_folder(slug)) or os.path.exists(
            doc_path('mapbrief/' + slug)
        ):
            slug = base + '-' + str(number)
            number += 1
        write_doc('mapbrief/' + slug, brief)
        if brief['type'] == 'city':
            cmd, label = generate_cmd(dict(brief, summary=brief['prompt'][:200]))
            cmd += ['--slug', slug]
            job = new_job('forge', 'generate', 'Build ' + label, cmd, slug=slug, populate=True)
            return self.send_json({'slug': slug, 'job': job})
        value = workflow.create(slug, brief, 'layout')
        job = start_workflow(value) if shutil.which('claude') else None
        return self.send_json({'slug': slug, 'workflow': value, 'job': job})

    def _post_map(self, path, query, p):
        parts = path.split('/')
        if len(parts) != 5:
            raise Invalid('Invalid map route.')
        slug, action = parts[3:]
        if not SLUG.fullmatch(slug):
            raise Invalid('Invalid map id.')
        if action == 'export':
            if map_busy(slug):
                raise Conflict('Wait for this map’s current job to finish before exporting.')
            with LOCK:
                return self.send_json(maps_io.export(slug, write_doc))
        if action in ('populate', 'revise'):
            return self._post_map_workflow(slug, action, p)
        if action == 'checkpoint':
            return self.send_json(revisions.checkpoint(slug, p.get('label') or 'Saved checkpoint'))
        if action == 'restore':
            if map_busy(slug):
                raise Conflict(
                    'Wait for the current map job to finish before restoring a revision.'
                )
            return self.send_json(restore_revision(slug, p.get('revision', '')))
        raise NotFound('Unknown map action.')

    def _map_brief(self, slug):
        brief = read_json(doc_path('mapbrief/' + slug), {})
        if brief:
            return brief
        index = read_json(doc_path('maps/index'), {'items': []})
        entry = next((item for item in index['items'] if item['slug'] == slug), None)
        if not entry:
            raise Invalid('Map not found.')
        brief = normal_brief(
            dict(
                name=entry['name'],
                prompt=entry.get('summary', ''),
                width=entry['cells'][0],
                height=entry['cells'][1],
                theme=entry.get('theme', 'outdoor'),
                type='city' if entry.get('theme') == 'city' else 'custom',
            ),
            existing=True,
        )
        write_doc('mapbrief/' + slug, brief)
        return brief

    def _target_area_brief(self, brief, slug, p):
        area = int(p['area'])
        key = read_json(doc_path('mapkey/' + slug), {'areas': []})
        if not any(item['n'] == area for item in key['areas']):
            raise Invalid('Location not found.')
        category = {
            'npc': 'npcs',
            'item': 'items',
            'journal': 'journals',
            'event': 'events',
        }.get(p.get('kind'))
        if not category:
            raise Invalid('Choose NPC, item, journal or event.')
        scoped = dict(
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
        scoped['content'][category] = 1
        return scoped

    def _post_map_workflow(self, slug, action, p):
        if map_busy(slug):
            raise Conflict('This map already has a job running.')
        brief = self._map_brief(slug)
        if action == 'revise' and not os.path.isfile(
            os.path.join(campaign.active().map_folder(slug), 'plan.txt')
        ):
            raise Invalid(
                'Imported images have no editable grid plan. Add locations and generate content on this image instead.'
            )
        if action == 'populate':
            if not read_json(doc_path('mapkey/' + slug), {}).get('areas'):
                raise Invalid(
                    'Add numbered locations to this map before drafting campaign content.'
                )
            if p.get('area') is not None:
                brief = self._target_area_brief(brief, slug, p)
        instruction = str(p.get('instruction') or '').strip()[:10000]
        if action == 'revise' and not instruction:
            raise Invalid('Describe the change you want to make.')
        value = workflow.create(
            slug, brief, 'content' if action == 'populate' else 'revision', instruction
        )
        job = start_workflow(value) if p.get('run', True) and shutil.which('claude') else None
        return self.send_json({'workflow': value, 'job': job})

    def _post_workflow(self, path, query, p):
        parts = path.split('/')
        if len(parts) != 5:
            raise Invalid('Invalid workflow route.')
        value = workflow.get(parts[3])
        action = parts[4] if len(parts) > 4 else ''
        if action == 'feedback':
            with LOCK:
                value = workflow.get(value['id'])
                if value['status'] != 'review':
                    raise Invalid('Only a draft awaiting review can be refined.')
                workflow.check_base(value)
                instruction = str(p.get('instruction') or '').strip()[:10000]
                if not instruction:
                    raise Invalid('Describe what the AI should change.')
                value.update(
                    previous_draft=value['draft'],
                    draft=None,
                    status='ready',
                    error='',
                    instruction=value.get('instruction', '') + '\nRefinement: ' + instruction,
                )
                workflow.save(value)
                job = (
                    start_workflow(value) if p.get('run', True) and shutil.which('claude') else None
                )
                return self.send_json({'workflow': value, 'job': job})
        if action == 'run':
            if value['status'] in ('running', 'applied'):
                raise Conflict('This workflow is already running or applied.')
            with LOCK:
                value = workflow.get(value['id'])
                if value['status'] in ('running', 'applied'):
                    raise Conflict('This workflow is already running or applied.')
                return self.send_json(start_workflow(value))
        if action == 'stage':
            if value['status'] in ('running', 'applied'):
                raise Conflict('This workflow is already running or applied.')
            with LOCK:
                value = workflow.get(value['id'])
                if value['status'] in ('running', 'applied'):
                    raise Conflict('This workflow is already running or applied.')
                return self.send_json(workflow.stage(value, p.get('draft')))
        if action == 'apply':
            slug = value['map']
            if map_busy(slug):
                raise Conflict('Wait for the current job to finish before applying this proposal.')
            if value['kind'] == 'content':
                return self.send_json(apply_content(value['id']))
            return self.send_json(apply_layout(value['id']))
        raise NotFound('Unknown workflow action.')

    def _post_generate(self, path, query, p):
        cmd, label = generate_cmd(p)
        return self.send_json(
            new_job('forge', 'generate', 'Generate ' + label, cmd, session=p.get('session', ''))
        )

    def _post_forge(self, path, query, p):
        slug = path[11:]
        plan = os.path.join(campaign.active().map_folder(slug), 'plan.txt')
        if not SLUG.match(slug) or (
            not os.path.exists(plan)
            and not (
                p.get('key_only')
                and os.path.isfile(
                    os.path.join(campaign.active().map_folder(slug), slug + '.foundry.json')
                )
            )
        ):
            raise Invalid('no such map')
        if map_busy(slug):
            raise Conflict('This map already has a job running.')
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

    def _post_claude(self, path, query, p):
        rid = str(p.get('request', ''))
        if not request_workflow.REQUEST_ID.fullmatch(rid):
            raise Invalid('bad request id')
        return self.send_json(start_request(rid))
