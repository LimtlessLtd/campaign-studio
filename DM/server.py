"""Start the local Campaign Studio HTTP server."""

import os
import threading
import time
from http.server import ThreadingHTTPServer

import campaign
import remote_access
import schema
from campaign_core import (
    JOBS_SERVICE,
    LANES,
    PORT,
    recover_commits,
    worker,
)
from http_routes import GATE, Handler


def main():
    started = time.time()
    bind = os.environ.get('DM_BIND', '127.0.0.1')
    try:
        remote_access.check_bind(bind, GATE.code)
    except remote_access.AccessError as error:
        raise SystemExit(str(error))
    server = ThreadingHTTPServer((bind, PORT), Handler)
    here = campaign.active()
    os.makedirs(here.data, exist_ok=True)
    os.makedirs(here.jobs, exist_ok=True)
    try:
        # Refuse newer data first; finish interrupted changes before migrating their documents.
        schema.check(here.data, here.maps)
        report = recover_commits()
        migration = schema.migrate(here.data, here.maps, here.backups)
    except (schema.SchemaError, OSError) as error:
        server.server_close()
        raise SystemExit(f'Campaign Studio could not open the campaign in {here.data}: {error}')
    for entry in report['completed']:
        print('Completed interrupted change: ' + entry['label'], flush=True)
    for entry in report['conflicts']:
        print('Interrupted change needs review on the dashboard: ' + entry['label'], flush=True)
    if migration['status'] == 'migrated' and migration['changes']:
        print(
            f'Updated {len(migration["changes"])} documents from data schema {migration["from"]} '
            f'to {migration["version"]}. Backup: {migration["backup"]}',
            flush=True,
        )
    # Jobs queued by recovered changes (such as a render) belong to this run, not the last one.
    JOBS_SERVICE.recover_unfinished(before=started)
    for lane in LANES:
        threading.Thread(target=worker, args=(lane,), daemon=True).start()
    print(f'DM site: http://127.0.0.1:{PORT}  (Ctrl+C to stop)', flush=True)
    if GATE.enabled:
        print(f'Other devices: http://{bind}:{PORT} with the access code (plain HTTP).', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
