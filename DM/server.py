"""Start the local Campaign Studio HTTP server."""

import os
import threading
from http.server import ThreadingHTTPServer

from campaign_core import (
    DATA,
    JOBS,
    JOBS_SERVICE,
    LANES,
    PORT,
    worker,
)
from http_routes import Handler


def main():
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(JOBS, exist_ok=True)
    JOBS_SERVICE.recover_unfinished()
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
