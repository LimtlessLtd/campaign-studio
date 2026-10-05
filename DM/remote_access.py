"""Optional access code for connections that do not come from this computer.

By default the site answers only on 127.0.0.1 and needs no code. When the owner starts it with `DM_BIND` set
to another address (for a phone on the same network or a private network) and `DM_ACCESS_CODE`, anyone
else must enter the code once; the browser then keeps a session cookie until the server restarts.
"""

import hashlib
import hmac
import html
import ipaddress
import secrets
import threading
import time

COOKIE = 'dm_session'
MIN_CODE = 8
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60
LOCAL_HOSTS = ('127.0.0.1', 'localhost')


class AccessError(ValueError):
    pass


def is_loopback(address):
    try:
        return ipaddress.ip_address(address.split('%')[0]).is_loopback
    except ValueError:
        return False


def host_name(header):
    """The host in a Host header, without its port (IPv6 literals keep their brackets)."""
    host = (header or '').strip()
    if host.startswith('['):
        return host.split(']')[0] + ']'
    return host.rsplit(':', 1)[0] if ':' in host else host


def check_bind(bind, code):
    """Refuse to listen beyond this computer without a usable access code."""
    if is_loopback(bind) or bind == 'localhost':
        return
    if len(code) < MIN_CODE:
        raise AccessError(
            f'DM_BIND={bind} makes the site reachable from other devices, so DM_ACCESS_CODE must be set '
            f'to at least {MIN_CODE} characters.'
        )


class AccessGate:
    def __init__(self, code='', clock=time.monotonic):
        self.code = code or ''
        self.clock = clock
        self._key = secrets.token_bytes(32)  # sessions end when the server restarts
        self._failures = {}  # client address -> (count, locked until)
        self._lock = threading.Lock()

    @property
    def enabled(self):
        return len(self.code) >= MIN_CODE

    def local_request(self, peer, host_header):
        """A request from this computer to a local name; no code needed."""
        return is_loopback(peer) and host_name(host_header) in LOCAL_HOSTS

    def _token(self):
        return hmac.new(self._key, self.code.encode('utf-8'), hashlib.sha256).hexdigest()

    def session_cookie(self, secure):
        return f'{COOKIE}={self._token()}; Path=/; HttpOnly; SameSite=Strict' + (
            '; Secure' if secure else ''
        )

    def has_session(self, cookie_header):
        if not self.enabled:
            return False
        for part in (cookie_header or '').split(';'):
            name, _, value = part.strip().partition('=')
            if name == COOKIE and hmac.compare_digest(value, self._token()):
                return True
        return False

    def locked_for(self, peer):
        with self._lock:
            count, until = self._failures.get(peer, (0, 0))
            return max(0, int(until - self.clock())) if count >= MAX_FAILURES else 0

    def try_code(self, peer, attempt):
        """True when the code is right. Repeated misses from one address lock it out for a minute."""
        if not self.enabled or self.locked_for(peer):
            return False
        ok = hmac.compare_digest(attempt.encode('utf-8'), self.code.encode('utf-8'))
        with self._lock:
            if ok:
                self._failures.pop(peer, None)
            else:
                count, until = self._failures.get(peer, (0, 0))
                if until and until <= self.clock():
                    count = 0
                count += 1
                self._failures[peer] = (count, self.clock() + LOCKOUT_SECONDS)
        return ok


def login_page(message=''):
    note = f'<p role="alert">{html.escape(message)}</p>' if message else ''
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Campaign Studio sign in</title>'
        '<style>body{font:16px system-ui,sans-serif;margin:0;min-height:100vh;display:grid;'
        'place-items:center;background:#16161d;color:#f1efe8}'
        'form{width:min(92vw,22rem);display:grid;gap:.9rem}'
        'input,button{font:inherit;padding:.7rem;border-radius:.4rem;border:1px solid #6b6b78}'
        'button{background:#d9b45f;color:#16161d;font-weight:600;border-color:#d9b45f}'
        'p{margin:0;color:#ffb4a8}</style></head><body>'
        '<form method="post" action="/login"><h1>Campaign Studio</h1>'
        f'{note}<label>Access code<br>'
        '<input name="code" type="password" autocomplete="current-password" required autofocus '
        'style="width:100%;box-sizing:border-box"></label>'
        '<button type="submit">Sign in</button></form></body></html>'
    ).encode('utf-8')
