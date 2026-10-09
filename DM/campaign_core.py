"""Campaign document storage, map workflows and job result handling."""

import datetime
import functools
import json
import os
import re
import shutil
import sys
import threading
import time
import campaign
import ai_provider
import arc_options
import commits
import config
import foundry_library
import foundry_party
import map_trash
import request_workflow
import workflow
import revisions
import records
import schema
import shapes
import storage
import transcript_classifier
import transcription
import thread_ledger
import usage
from job_service import JobService

APP = os.path.join(campaign.INSTALL, 'app')
FORGE = os.path.join(campaign.INSTALL, 'forge')
if (
    FORGE not in sys.path
):  # the forge modules (forge, generate) import by name, so add the folder once
    sys.path.insert(0, FORGE)
PORT = int(os.environ.get('DM_PORT', 8766))

RECORDINGS_FOLDER = 'Session recordings'
# campaign folders the site may show files from (read-only)
FILE_ROOTS = (
    'PCs',
    'Gods',
    'Handouts',
    'Maps & Assets',
    'Tokens',
    'Dungeon Alchemist',
    RECORDINGS_FOLDER,
    'Arena Teams',
    'Icons',
    'Landing Page & Social Media stuff',
    'Website',
    'DM',
)
DOC_NAME = re.compile(r'^[a-z0-9][a-z0-9_-]*(?:/[a-z0-9][a-z0-9_-]*)?$')
SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')
# Documents a change may delete: the World Library snapshot and these folders.
DELETABLE = ('codex/', 'threads/', 'transcripts/', 'arcs/')
# Documents the application owns: only their own routes and services change them.
APP_OWNED_DOC = re.compile(
    r'^(?:settings|foundry-library|table-lore|codex|threads|(?:codex|threads|workflows|jobs|transcripts|ledger|arcs)/.+)$'
)
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
    if (
        name in records.FIELDS
        and schema.version(campaign.active().data, campaign.active().maps) == schema.CURRENT
    ):
        raise ValueError('Write codex and threads as individual records.')
    if name.startswith(('codex/', 'threads/')):
        kind = name.split('/', 1)[0]
        if not isinstance(value, dict) or records.document_name(kind, value.get('id')) != name:
            raise ValueError('Record ID does not match its storage key.')
        records.record_path(campaign.active().data, kind, value['id'])
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


def delete_record(kind, ident):
    """Remove one record, retaining its last version in that record's history."""
    return delete_record_document(records.document_name(kind, ident))


def delete_record_document(name):
    if name != 'foundry-library' and not name.startswith(DELETABLE):
        raise ValueError(
            'Only records, transcripts, arc proposals and the World Library snapshot can be deleted through a change.'
        )
    path = doc_path(name)
    if os.path.islink(os.path.dirname(path)) or os.path.islink(path):
        raise ValueError('Unsafe record path.')
    with storage.file_lock(path):
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        keep = os.path.join(campaign.active().history, *name.split('/'))
        os.makedirs(keep, exist_ok=True)
        shutil.copy2(
            path, os.path.join(keep, datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json')
        )
        for old in sorted(os.listdir(keep))[:-KEEP_VERSIONS]:
            os.remove(os.path.join(keep, old))
        storage.remove(path)


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
    if value is None:
        delete_record_document(name)
    elif name.startswith('plan/'):
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
    expanded, deletes = [], []
    for name, value in changes:
        if name in records.FIELDS:
            for target in records.changed_documents(campaign.active().data, name, value):
                (deletes if target[1] is None else expanded).append(target)
        else:
            (deletes if value is None else expanded).append((name, value))
    return JOURNAL.commit(label, expanded + deletes, after)


def import_foundry_snapshot(snapshot, folders=None):
    """Commit a World Library snapshot and its codex changes as one recoverable operation."""
    with LOCK:
        codex = records.collection(campaign.active().data, 'codex')
        report = foundry_library.import_into_codex(snapshot, codex, folders)
        commit_docs('Import Foundry world', [('foundry-library', snapshot), ('codex', codex)])
        return report


def recover_commits():
    """Finish interrupted changes. Call before any read/modify/write of campaign documents."""
    with LOCK:
        report = JOURNAL.recover()
        if not report['conflicts']:
            map_trash.reconcile(campaign.active())
        return report


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
    file_root = os.path.realpath(campaign.active().files)
    out = []
    for dirpath, dirs, files in os.walk(full):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        for f in sorted(files):
            if os.path.splitext(f)[1].lower() in IMAGE_EXT:
                p = os.path.join(dirpath, f)
                out.append(
                    dict(
                        path=os.path.relpath(p, file_root).replace('\\', '/'),
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
    for owner, settle in SETTLERS:
        if job.get(owner):
            settle(job, message)


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
    if job['kind'] in usage.DRAFT_KINDS:
        try:
            with open(job_file(job['id'], 'log'), encoding='utf-8', errors='replace') as file:
                usage.record(job, file.read())
        except OSError:
            pass  # no log, no usage: the job itself still settles
    if job['kind'] in FINISHERS:
        FINISHERS[job['kind']](job, code, tail)
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
                        entry = records.read(campaign.active().data, 'codex', target['codex'])
                        if entry:
                            entry['image'] = image_path
                            changes.append((records.document_name('codex', entry['id']), entry))
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
            draft = workflow.parse_output(raw)
            try:
                more = workflow.advance(value, draft)
            except ValueError as e:
                # One automatic retry per batch, with the validation message as the instruction.
                if not workflow.refine(value, draft, str(e)):
                    raise
                more = True
            if more:
                start_workflow(value)
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
    if name in records.FIELDS:
        return records.collection(campaign.active().data, name)
    return read_json(doc_path(name))


def fail_request(job, message):
    box = read_json(doc_path('inbox'), {'items': []})
    item = request_item(box, job['request'])
    if item and item.get('status') == 'doing' and item.get('job') == job['id']:
        item.update(status='new', error=message)
        write_doc('inbox', box)


def discard_transcription(job, message):
    """A transcription that did not finish leaves no staged result or scratch audio."""
    transcription.discard(campaign.active().jobs, job['transcript'])


def finish_transcription(job, code, tail):
    """Store a finished transcription's transcript, or fail the job with the reason."""
    result, _ = transcription.staging(campaign.active().jobs, job['transcript'])
    try:
        if code:
            lines = [x for x in tail.splitlines() if x.strip() and not x.startswith('PROGRESS ')]
            raise ValueError('\n'.join(lines[-3:])[-1000:] or 'The transcription failed.')
        with open(result, encoding='utf-8') as file:
            staged = json.load(file)
        write_doc('transcripts/' + job['transcript'], transcription.build(job, staged))
    except (ValueError, KeyError, TypeError, OSError) as error:
        job.update(status='failed', note=str(error))
    finally:
        transcription.discard(campaign.active().jobs, job['transcript'])


def fail_classification(job, message):
    """Stop the sorting of a transcript at the window that failed, unless the transcript has moved on."""
    name = 'transcripts/' + job['classify']
    document = read_json(doc_path(name))
    if document and document['classification']['job'] == job['id']:
        document['classification'].update(status='failed', job='', error=message[:1000])
        write_doc(name, document)


def finish_classification(job, code, tail):
    """Store the passages a finished window proposed, then queue the next window or finish."""
    name = 'transcripts/' + job['classify']
    document = read_json(doc_path(name))
    state = document and document['classification']
    if not state or state['job'] != job['id'] or state['cursor'] != job['first']:
        job.update(status='failed', note='The transcript changed while it was being sorted.')
        return
    try:
        if code:
            raise ValueError(tail[-1000:] or 'The AI command failed.')
        with open(job_file(job['id'], 'log'), encoding='utf-8') as file:
            raw = file.read()
        lore = read_json(doc_path('table-lore'), shapes.TABLE_LORE.new())
        found = transcript_classifier.passages_from(
            workflow.parse_output(raw), job['first'], job['stop'], {i['id'] for i in lore['items']}
        )
        transcript_classifier.place(document, found, job['first'], job['stop'])
        if job['stop'] >= len(document['segments']):
            document['classification'].update(status='done', job='')
        else:
            try:
                document['classification'].update(job=classification_job(document)['id'])
            except ValueError as error:  # this window is kept; sorting stops before the next one
                document['classification'].update(status='failed', job='', error=str(error))
        write_doc(name, document)
    except (ValueError, KeyError, TypeError, OSError) as error:
        job.update(status='failed', note=str(error))
        fail_classification(job, str(error))


def fail_ledger(job, message):
    """Stop a thread ledger at the window that failed, unless it was removed or replaced."""
    name = 'ledger/' + job['ledger']
    value = read_json(doc_path(name))
    if value and value.get('status') == 'running' and value.get('job') == job['id']:
        value.update(status='failed', job='', error=message[:1000])
        write_doc(name, value)


def finish_ledger(job, code, tail):
    """Stage one bounded AI window; continue until all confirmed play awaits GM review."""
    name = 'ledger/' + job['ledger']
    with LOCK:
        ledger = read_json(doc_path(name))
        if not ledger or ledger.get('status') != 'running' or ledger.get('job') != job['id']:
            job.update(status='failed', note='The ledger was changed while drafting.')
            return
        try:
            transcript = read_json(doc_path('transcripts/' + job['ledger']))
            if not transcript or thread_ledger.source_hash(transcript) != ledger['source']:
                raise ValueError('Confirmed play changed. Start a new ledger draft.')
            if code:
                raise ValueError(tail[-1000:] or 'The AI command failed.')
            with open(job_file(job['id'], 'log'), encoding='utf-8') as file:
                draft = workflow.parse_output(file.read())
            entries = records.all_records(campaign.active().data, 'codex')
            threads = records.all_records(campaign.active().data, 'threads')
            batch = thread_ledger.validate_batch(
                transcript,
                draft,
                job['first'],
                job['stop'],
                {row['id'] for row in threads},
                {row['id'] for row in entries},
                {row['id'] for row in entries if row.get('type') == 'pc'},
            )
            if len(ledger['events']) + len(batch) > thread_ledger.MAX_EVENTS:
                raise ValueError('This ledger has too many events. Use a shorter transcript.')
            ledger['events'].extend(batch)
            ledger.update(cursor=job['stop'], status='ready', job='', error='')
            write_doc(name, ledger)
            if ledger['cursor'] < len(thread_ledger.lines(transcript)):
                queue_ledger(transcript, ledger)
            else:
                ledger['status'] = 'review'
                write_doc(name, ledger)
        except (ValueError, KeyError, TypeError, OSError) as error:
            ledger.update(status='failed', job='', error=str(error)[:1000])
            write_doc(name, ledger)
            job.update(status='failed', note=str(error))


def fail_arc(job, message):
    """Mark a proposal that was drafting as failed, unless it was removed or replaced."""
    name = 'arcs/' + job['arc']
    arc = read_json(doc_path(name))
    if arc and arc['status'] == 'running' and arc['job'] == job['id']:
        arc.update(status='failed', job='', error=message[:1000])
        write_doc(name, arc)


def finish_arc(job, code, tail):
    """Store the options a finished draft proposed for review, or fail the proposal with the reason."""
    name = 'arcs/' + job['arc']
    with LOCK:
        arc = read_json(doc_path(name))
        if not arc or arc['status'] != 'running' or arc['job'] != job['id']:
            job.update(status='failed', note='The proposal was removed or changed while drafting.')
            return
        try:
            if code:
                raise ValueError(tail[-1000:] or 'The AI command failed.')
            with open(job_file(job['id'], 'log'), encoding='utf-8') as file:
                draft = workflow.parse_output(file.read())
            entries = records.all_records(campaign.active().data, 'codex')
            arc['options'] = arc_options.validate(
                draft,
                arc['threads'],
                {row['id'] for row in entries},
                {row['id'] for row in entries if row.get('type') == 'pc'},
            )
            arc.update(status='review', job='', error='')
        except (ValueError, KeyError, TypeError, OSError) as error:
            arc.update(status='failed', job='', error=str(error)[:1000])
            job.update(status='failed', note=str(error))
        write_doc(name, arc)


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


# What a job leaves to settle. A new draft kind adds a row here instead of a branch in finish_job or
# settle_failed_job: FINISHERS stores a finished job's result by kind; SETTLERS marks what a failed job
# was producing, by the field that names the record it belongs to.
FINISHERS = {
    'request-draft': finish_request,
    'transcribe': finish_transcription,
    'classify': finish_classification,
    'thread-ledger': finish_ledger,
    'arc-options': finish_arc,
}
SETTLERS = (
    ('request', fail_request),
    ('transcript', discard_transcription),
    ('classify', fail_classification),
    ('ledger', fail_ledger),
    ('arc', fail_arc),
)

JOBS_SERVICE = JobService(campaign.active, LOCK, finish_job, fail_job, recover_interrupted_job)
LANES = JOBS_SERVICE.lanes
RUNNING = JOBS_SERVICE.running
job_file = JOBS_SERVICE.job_file
save_job = JOBS_SERVICE.save_job
new_job = JOBS_SERVICE.new_job
worker = JOBS_SERVICE.worker
cancel_job = JOBS_SERVICE.cancel
execute_job = JOBS_SERVICE.execute_job
log_tail = JOBS_SERVICE.log_tail
job_progress = JOBS_SERVICE.progress
list_jobs = JOBS_SERVICE.list_jobs


def usage_totals():
    return usage.totals(JOBS_SERVICE.iter_jobs())


def generate_cmd(p):
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


def campaign_facts():
    """Facts every AI draft sees: campaign name, world and, once imported, the party's size and level."""
    cfg = config.settings()
    info = {'name': cfg['campaign_name']}
    if cfg.get('world_path'):
        world = config.world_info(cfg['world_path'])
        info['world'] = world
        snapshot = foundry_library.current_snapshot(read_json(doc_path('foundry-library')), world)
        party = foundry_party.party(snapshot['documents'].get('actors', [])) if snapshot else {}
        if party.get('size'):
            info['party'] = party
    return info


def party_level():
    """The imported party's average level, rounded, for new briefs; 5 when no party is known."""
    try:
        average = campaign_facts().get('party', {}).get('average_level', 0)
    except (OSError, ValueError):
        return 5
    return max(1, min(30, round(average))) if average else 5


def campaign_info():
    """Campaign facts plus the newest session logs."""
    info = campaign_facts()
    logs = request_workflow.recent_logs(request_read, list_docs('prep'))
    if logs:
        info['recent_session_logs'] = logs
    return info


def request_pack(item):
    return request_workflow.prompt_pack(
        item,
        request_read,
        campaign_facts(),
        config.settings()['context_budget_chars'],
        prep_names=list_docs('prep'),
    )


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
        cmd = ai_provider.command('request', pack['schema'])
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


def default_recordings():
    """The campaign's recordings folder when it exists, else an empty string."""
    folder = os.path.join(campaign.active().files, RECORDINGS_FOLDER)
    return folder if os.path.isdir(folder) else ''


def transcript_busy(transcript):
    """True while a job is transcribing, sorting or drafting from this transcript."""
    return any(
        transcript in (j.get('transcript'), j.get('classify'), j.get('ledger'))
        and j.get('status') in ('queued', 'running')
        for j in JOBS_SERVICE.iter_jobs()
    )


def start_transcription(rec, session=''):
    """Queue the local transcription of one recording; its transcript is stored when the job ends."""
    with LOCK:
        options = config.settings()['transcription']
        problem = transcription.selected(options).problem(options)
        if problem:
            raise ValueError(problem)
        if session and session not in list_docs('prep'):
            raise ValueError('No such session prep.')
        return new_job(
            'transcribe',
            'transcribe',
            'Transcribe: ' + rec['name'][:60],
            transcription.worker_command(),
            transcription.request(rec, options, campaign.active().jobs),
            transcript=rec['id'],
            recording=rec,
            session=session,
            engine={key: options[key] for key in ('provider', 'model', 'language')},
        )


def classification_job(document):
    """Queue the model request for the next unsorted window of a transcript."""
    first = document['classification']['cursor']
    stop = transcript_classifier.window_end(
        document['segments'],
        first,
        transcript_classifier.window_size(config.settings()['context_budget_chars']),
    )
    lore = read_json(doc_path('table-lore'), shapes.TABLE_LORE.new())
    return new_job(
        'claude',
        'classify',
        f'Sort play from banter: {document["title"][:50]} ({first + 1}-{stop})',
        ai_provider.command('classify', transcript_classifier.SCHEMA),
        transcript_classifier.prompt(document, first, stop, lore),
        classify=document['id'],
        first=first,
        stop=stop,
    )


def start_classification(ident, restart=False):
    """Queue the sorting of a transcript from where it stopped, or from the start."""
    with LOCK:
        name = 'transcripts/' + ident
        document = read_json(doc_path(name))
        if document is None:
            raise LookupError('No such transcript.')
        if restart:
            transcript_classifier.start_over(document)
        elif document['classification']['status'] == 'done':
            raise ValueError('This transcript is sorted. Sort it again to start over.')
        job = classification_job(document)
        document['classification'].update(status='running', job=job['id'], error='')
        write_doc(name, document)
        return job


def unlink_lore(removed, skip=''):
    """Changes that clear removed table-lore items from the passages of the other transcripts."""
    changes = []
    if removed:
        for other in list_docs('transcripts'):
            document = read_json(doc_path('transcripts/' + other))
            if other != skip and transcript_classifier.unlink_lore(document, removed):
                changes.append(('transcripts/' + other, document))
    return changes


def review_passages(ident, decisions):
    """Record the GM's decisions on a transcript's passages, with the table lore they save or remove."""
    with LOCK:
        name = 'transcripts/' + ident
        document = read_json(doc_path(name))
        if document is None:
            raise LookupError('No such transcript.')
        lore = read_json(doc_path('table-lore'), shapes.TABLE_LORE.new())
        before = json.dumps(lore, sort_keys=True)
        saved = {item['id'] for item in lore['items']}
        transcript_classifier.review(document, lore, decisions, int(time.time()))
        removed = saved - {item['id'] for item in lore['items']}
        changes = unlink_lore(removed, skip=ident)
        if json.dumps(lore, sort_keys=True) != before:
            changes.append(('table-lore', lore))
        commit_docs('Review transcript ' + ident, changes + [(name, document)])
        return transcript_classifier.counts(document)


def remove_lore(ident):
    """Remove a table-lore note. Transcripts keep their passages; those that matched it lose the match."""
    with LOCK:
        lore = read_json(doc_path('table-lore'), shapes.TABLE_LORE.new())
        if ident not in {item['id'] for item in lore['items']}:
            raise LookupError('No such table lore.')
        lore['items'] = [item for item in lore['items'] if item['id'] != ident]
        commit_docs('Remove table lore ' + ident, unlink_lore({ident}) + [('table-lore', lore)])


def ledger_name(ident):
    if not transcription.ID.fullmatch(ident):
        raise ValueError('Invalid transcript ID.')
    return 'ledger/' + ident


def link_transcript_session(ident, session):
    """Attach an already-transcribed recording to a prep without running speech recognition again."""
    if not isinstance(session, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', session):
        raise ValueError('Choose a session prep.')
    with LOCK:
        transcript = read_json(doc_path('transcripts/' + ident))
        if transcript is None:
            raise FileNotFoundError('No such transcript.')
        if read_json(doc_path('prep/' + session)) is None:
            raise ValueError('No such session prep.')
        if transcript_busy(ident):
            raise ValueError('Wait for the current transcript job to finish.')
        ledger = read_json(doc_path(ledger_name(ident)))
        if ledger and ledger.get('status') in ('running', 'review', 'applied'):
            raise ValueError('This transcript already has a ledger draft or applied ledger.')
        transcript['session'] = session
        write_doc('transcripts/' + ident, transcript)
        return transcription.summary(transcript)


def queue_ledger(transcript, ledger):
    """Run the next confirmed-play window on the configured structured draft provider."""
    entries = records.all_records(campaign.active().data, 'codex')
    threads = records.all_records(campaign.active().data, 'threads')
    prep = read_json(doc_path('prep/' + ledger['session']))
    if prep is None:
        raise ValueError('The linked session prep no longer exists.')
    prompt, _, first, stop = thread_ledger.prompt(
        transcript,
        ledger,
        threads,
        entries,
        prep,
        campaign_info(),
        config.settings()['context_budget_chars'],
    )
    command = ai_provider.command('thread-ledger', thread_ledger.SCHEMA)
    job = new_job(
        'claude',
        'thread-ledger',
        f'Thread ledger: {transcript["title"][:60]}',
        command,
        prompt,
        ledger=ledger['id'],
        first=first,
        stop=stop,
    )
    ledger.update(status='running', job=job['id'], error='')
    write_doc(ledger_name(ledger['id']), ledger)
    return job


def start_ledger(ident, restart=False):
    """Start, resume or explicitly replace a draft after the GM confirms play."""
    with LOCK:
        transcript = read_json(doc_path('transcripts/' + ident))
        if transcript is None:
            raise FileNotFoundError('No such transcript.')
        if transcript_busy(ident):
            raise ValueError('Wait for the current transcript job to finish.')
        if transcript.get('classification', {}).get('status') != 'done':
            raise ValueError('Finish sorting this transcript before drafting a thread ledger.')
        if transcript_classifier.counts(transcript)['pending']:
            raise ValueError('Review the unresolved transcript passages first.')
        if not thread_ledger.lines(transcript):
            raise ValueError('Confirm at least one in-game passage first.')
        session = transcript.get('session', '')
        if not session or read_json(doc_path('prep/' + session)) is None:
            raise ValueError(
                'Link this transcript to an existing session prep before drafting a ledger.'
            )
        name = ledger_name(ident)
        ledger = read_json(doc_path(name))
        source = thread_ledger.source_hash(transcript)
        if ledger:
            if ledger.get('status') in ('running', 'applied'):
                raise ValueError('This ledger is already running or was applied.')
            if ledger.get('status') == 'review' and not restart:
                raise ValueError('Review or explicitly redraft this ledger before starting again.')
            if restart or ledger['source'] != source:
                ledger = None  # never carry old evidence into a changed source or fresh draft
        if ledger is None:
            ledger = shapes.LEDGER.new(id=ident, session=session, source=source, status='ready')
            ledger['base_revs'] = {
                records.document_name(kind, row['id']): rev_of(
                    doc_path(records.document_name(kind, row['id']))
                )
                for kind in ('threads', 'codex')
                for row in records.all_records(campaign.active().data, kind)
            }
            ledger['base_revs']['prep/' + session] = rev_of(doc_path('prep/' + session))
            write_doc(name, ledger)
        return queue_ledger(transcript, ledger)


def apply_ledger(ident, selected):
    """Apply exactly the GM-selected events with stale-target checks and a recoverable commit."""
    with LOCK:
        name = ledger_name(ident)
        ledger = read_json(doc_path(name))
        if ledger is None:
            raise FileNotFoundError('No such thread ledger.')
        if ledger['status'] == 'applied':
            if selected != ledger['selected']:
                raise ValueError('This ledger was already applied with different choices.')
            return ledger  # an HTTP retry cannot append the same story twice
        if ledger['status'] != 'review':
            raise ValueError('Wait for a complete ledger draft before applying it.')
        transcript = read_json(doc_path('transcripts/' + ident))
        if not transcript or thread_ledger.source_hash(transcript) != ledger['source']:
            raise ValueError('Confirmed play changed. Draft the ledger again.')
        chosen = thread_ledger.accepted_events(ledger, selected)
        targets = (
            {'prep/' + ledger['session']} if any(e['kind'] == 'outcome' for e in chosen) else set()
        )
        for event in chosen:
            if event['kind'] == 'outcome':
                continue
            kind = 'threads' if event['kind'] == 'thread' else 'codex'
            record_id = (
                thread_ledger.thread_id(ledger['id'], event['target'])
                if kind == 'threads'
                else event['target']
            )
            targets.add(records.document_name(kind, record_id))
        for target in targets:
            if rev_of(doc_path(target)) != ledger['base_revs'].get(target, '0'):
                raise ValueError('A target changed since drafting. Review and redraft the ledger.')
        prep = read_json(doc_path('prep/' + ledger['session']))
        if prep is None:
            raise ValueError('The linked session prep no longer exists.')
        changes = thread_ledger.changes(
            ledger,
            selected,
            lambda kind, key: records.read(campaign.active().data, kind, key),
            prep,
        )
        ledger.update(
            status='applied', selected=selected, applied=datetime.datetime.now().timestamp()
        )
        ledger.pop('base_revs', None)
        commit_docs('Apply thread ledger ' + ledger['id'], changes + [(name, ledger)])
        return ledger


def loose_threads(hero=''):
    threads = records.all_records(campaign.active().data, 'threads')
    sessions = [int(name[1:]) for name in list_docs('prep') if re.fullmatch(r's\d{1,6}', name)]
    report = thread_ledger.loose(threads, max(sessions, default=0), hero)
    by_id = {row['id']: row for row in report}
    for ident in list_docs('ledger'):
        ledger = read_json(doc_path('ledger/' + ident))
        if ledger.get('status') != 'applied':
            continue
        selected = set(ledger.get('selected', []))
        for event in ledger['events']:
            if event['id'] not in selected or event['kind'] != 'thread':
                continue
            target = thread_ledger.thread_id(ledger['id'], event['target'])
            if target in by_id:
                by_id[target].setdefault('evidence', []).append(
                    {
                        'transcript': ledger['id'],
                        'session': ledger['session'],
                        'at': event['at'],
                        'quote': event['quote'],
                    }
                )
    return report


def arc_name(ident):
    if not re.fullmatch(r'arc-[0-9a-f]{8}', str(ident)):
        raise ValueError('Invalid arc proposal ID.')
    return 'arcs/' + ident


def read_arcs():
    return [read_json(doc_path('arcs/' + ident)) for ident in list_docs('arcs')]


def start_arcs(thread_ids):
    """Queue one draft of arc options for the chosen loose threads."""
    if (
        not isinstance(thread_ids, list)
        or not 0 < len(thread_ids) <= arc_options.MAX_THREADS
        or any(not isinstance(ident, str) for ident in thread_ids)
        or len(set(thread_ids)) != len(thread_ids)
    ):
        raise ValueError(f'Choose between 1 and {arc_options.MAX_THREADS} different threads.')
    with LOCK:
        data = campaign.active().data
        threads = records.all_records(data, 'threads')
        by_id = {row['id']: row for row in threads}
        if any(by_id.get(ident, {}).get('status') not in arc_options.LOOSE for ident in thread_ids):
            raise ValueError('Choose open, planned or foreshadowed threads that still exist.')
        if len(list_docs('arcs')) >= arc_options.MAX_ARCS:
            raise ValueError('Remove some old arc proposals before drafting another.')
        prompt, _ = arc_options.prompt(
            thread_ids,
            loose_threads(),
            threads,
            records.all_records(data, 'codex'),
            campaign_info(),
            config.settings()['context_budget_chars'],
        )
        ident = 'arc-' + os.urandom(4).hex()
        titles = ', '.join(by_id[x].get('title') or x for x in thread_ids)
        job = new_job(
            'claude',
            'arc-options',
            'Arc options: ' + titles[:80],
            ai_provider.command('arc-options', arc_options.SCHEMA),
            prompt,
            arc=ident,
        )
        base_revs = {
            name: rev_of(doc_path(name))
            for name in (records.document_name('threads', x) for x in thread_ids)
        }
        write_doc(
            'arcs/' + ident,
            shapes.ARC.new(
                id=ident,
                status='running',
                threads=list(thread_ids),
                job=job['id'],
                base_revs=base_revs,
                created=int(time.time()),
            ),
        )
        return job


def apply_arc(ident, choices):
    """Apply exactly the options the GM chose, one per thread, unless a thread changed since."""
    with LOCK:
        name = arc_name(ident)
        arc = read_json(doc_path(name))
        if arc is None:
            raise LookupError('No such arc proposal.')
        picked = arc_options.chosen_options(arc, choices)
        if arc['status'] == 'applied':
            if {option['id'] for option in picked} != set(arc['choices']):
                raise ValueError('This proposal was already applied with different choices.')
            return arc  # an HTTP retry cannot plan the same arc twice
        if arc['status'] != 'review':
            raise ValueError('Wait for the proposal to finish drafting.')
        for option in picked:
            target = records.document_name('threads', option['thread'])
            if rev_of(doc_path(target)) != arc['base_revs'].get(target, '0'):
                raise ValueError('A thread changed since this was proposed. Propose again.')
        changed = arc_options.changes(
            picked, lambda key: records.read(campaign.active().data, 'threads', key)
        )
        final = {option['id']: option for option in picked}
        arc['options'] = [final.get(option['id'], option) for option in arc['options']]
        arc.update(
            status='applied',
            choices=[option['id'] for option in picked],
            applied=int(time.time()),
            base_revs={},
        )
        commit_docs('Apply arc options ' + ident, changed + [(name, arc)])
        return arc


def remove_arc(ident):
    """Remove a stored proposal. Threads it already changed keep those changes."""
    with LOCK:
        name = arc_name(ident)
        arc = read_json(doc_path(name))
        if arc is None:
            raise LookupError('No such arc proposal.')
        if arc['status'] == 'running':
            raise ValueError('Cancel the drafting job before removing this proposal.')
        commit_docs('Remove arc proposal ' + ident, [(name, None)])


def arc_cards():
    """Stored proposals, newest first, without their options."""
    titles = {
        row['id']: row.get('title', '')
        for row in records.all_records(campaign.active().data, 'threads')
    }
    cards = [
        {
            'id': arc['id'],
            'status': arc['status'],
            'created': arc['created'],
            'applied': arc['applied'],
            'error': arc['error'],
            'job': arc['job'],
            'threads': [{'id': x, 'title': titles.get(x, x)} for x in arc['threads']],
            'options': len(arc['options']),
            'choices': len(arc['choices']),
        }
        for arc in read_arcs()
    ]
    return sorted(cards, key=lambda card: -card['created'])


def arc_seeds():
    """Pitch lines from applied arcs whose threads are still unresolved, newest first."""
    return arc_options.seeds(read_arcs(), records.all_records(campaign.active().data, 'threads'))


def start_workflow(value):
    with LOCK:
        value = workflow.get(value['id'])
        if value['status'] in ('running', 'applied'):
            raise ValueError('This workflow is already running or applied.')
        workflow.check_base(value)
        if workflow.needs_plan(value):
            workflow.begin(value)
        cmd = ai_provider.command(value['kind'], workflow.schema(value['kind']))
        job = new_job(
            'claude',
            'ai-workflow',
            'AI: ' + value['kind'] + ' · ' + value['brief']['name'],
            cmd,
            workflow.prompt(value, campaign_info()),
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
        j.get('slug') == slug and j.get('status') in ('queued', 'running')
        for j in JOBS_SERVICE.iter_jobs()
    )


def apply_content(wid):
    """Link a reviewed content draft to the map, codex, threads and art queue."""
    with LOCK:
        value = workflow.get(wid)
        if value['status'] != 'review':
            raise ValueError('There is no draft awaiting review.')
        workflow.check_base(value)
        revisions.checkpoint(value['map'], 'Before applying AI content', campaign.active())
        stocked = {'type': 'mark_stocked', 'slug': value['map']}
        return workflow.apply_content(value, functools.partial(commit_docs, after=[stocked]))


def restore_revision(slug, rid):
    """Restore a map checkpoint and re-render it; recovery re-renders after a crash too."""
    rerender = forge_follow_up(slug, 'Restore ' + slug)
    with LOCK:
        [job] = revisions.restore(
            slug, rid, functools.partial(commit_docs, after=[rerender]), campaign.active()
        )
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
            revisions.checkpoint(slug, 'Before AI revision', campaign.active())
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
