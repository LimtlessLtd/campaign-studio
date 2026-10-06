import http.client
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'DM'))

import campaign
import http_routes
import remote_access

CODE = 'correct horse'


class GateTests(unittest.TestCase):
    def test_bind_beyond_this_computer_needs_a_code(self):
        remote_access.check_bind('127.0.0.1', '')
        remote_access.check_bind('::1', '')
        remote_access.check_bind('0.0.0.0', CODE)
        for bind, code in (('0.0.0.0', ''), ('192.168.1.5', 'short')):
            with self.assertRaises(remote_access.AccessError):
                remote_access.check_bind(bind, code)

    def test_wrong_codes_lock_the_address_then_release(self):
        now = [0.0]
        gate = remote_access.AccessGate(CODE, clock=lambda: now[0])
        for _ in range(remote_access.MAX_FAILURES):
            self.assertFalse(gate.try_code('phone', 'nope'))
        self.assertGreater(gate.locked_for('phone'), 0)
        self.assertFalse(gate.try_code('phone', CODE))  # locked even for the right code
        self.assertTrue(gate.try_code('laptop', CODE))  # other addresses are unaffected
        now[0] += remote_access.LOCKOUT_SECONDS + 1
        self.assertTrue(gate.try_code('phone', CODE))

    def test_expired_lockouts_are_forgotten_once_the_table_is_full(self):
        now = [0]
        gate = remote_access.AccessGate(CODE, clock=lambda: now[0])
        for n in range(remote_access.MAX_TRACKED):
            gate.try_code(f'10.0.{n // 250}.{n % 250}', 'wrong')
        now[0] = remote_access.LOCKOUT_SECONDS + 1
        gate.try_code('192.168.0.9', 'wrong')
        self.assertEqual(list(gate._failures), ['192.168.0.9'])

    def test_full_table_blocks_new_addresses_without_losing_active_lockouts(self):
        now = [0.0]
        gate = remote_access.AccessGate(CODE, clock=lambda: now[0])
        for n in range(remote_access.MAX_TRACKED):
            gate.try_code(f'10.0.{n // 250}.{n % 250}', 'wrong')
        self.assertEqual(gate.locked_for('192.168.0.9'), remote_access.LOCKOUT_SECONDS)
        self.assertFalse(gate.try_code('192.168.0.9', CODE))
        self.assertEqual(len(gate._failures), remote_access.MAX_TRACKED)
        self.assertTrue(gate.try_code('10.0.0.0', CODE))
        now[0] = remote_access.LOCKOUT_SECONDS + 1
        self.assertTrue(gate.try_code('192.168.0.9', CODE))

    def test_a_short_code_never_enables_the_gate(self):
        self.assertFalse(remote_access.AccessGate('abc').enabled)
        self.assertFalse(remote_access.AccessGate('abc').has_session('dm_session=x'))


class RemoteRouteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        previous = campaign.activate(campaign.Campaign(Path(temporary.name) / 'Studio'))
        self.addCleanup(campaign.activate, previous)
        saved = http_routes.GATE
        http_routes.GATE = remote_access.AccessGate(CODE)
        self.addCleanup(setattr, http_routes, 'GATE', saved)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), http_routes.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def send(self, path, method='GET', host='studio.example', headers=None, body=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        conn.putrequest(method, path, skip_host=True)
        conn.putheader('Host', host)
        for key, value in (headers or {}).items():
            conn.putheader(key, value)
        if body is not None:
            conn.putheader('Content-Length', str(len(body)))
        conn.endheaders(body)
        response = conn.getresponse()
        data = response.read()
        self.addCleanup(conn.close)
        return response.status, data, response

    def login(self, code=CODE):
        body = urllib.parse.urlencode({'code': code}).encode()
        return self.send(
            '/login',
            'POST',
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            body=body,
        )

    def test_local_requests_need_no_code(self):
        status, _, _ = self.send('/api/state', host='127.0.0.1:8766')
        self.assertEqual(status, 200)

    def test_other_hosts_see_the_sign_in_page_and_no_data(self):
        status, body, _ = self.send('/')
        self.assertEqual(status, 401)
        self.assertIn(b'Access code', body)
        status, body, _ = self.send('/api/state')
        self.assertEqual(status, 401)
        self.assertNotIn(b'campaign', body.lower().replace(b'access code', b''))
        status, _, _ = self.send('/api/doc/codex', 'PUT', headers={'X-DM-Site': '1'}, body=b'{}')
        self.assertEqual(status, 401)

    def test_wrong_code_is_refused_and_right_code_opens_a_session(self):
        status, _, response = self.login('wrong code')
        self.assertEqual(status, 401)
        self.assertIsNone(response.getheader('Set-Cookie'))
        status, _, response = self.login()
        self.assertEqual(status, 303)
        cookie = response.getheader('Set-Cookie')
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        session = cookie.split(';')[0]
        status, _, _ = self.send('/api/state', headers={'Cookie': session})
        self.assertEqual(status, 200)
        status, _, _ = self.send('/api/state', headers={'Cookie': 'dm_session=forged'})
        self.assertEqual(status, 401)

    def test_https_proxy_login_sets_a_secure_cookie_for_the_phone_host(self):
        body = urllib.parse.urlencode({'code': CODE}).encode()
        status, _, response = self.send(
            '/login',
            'POST',
            host='192.168.1.50',
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-Forwarded-Proto': 'https',
            },
            body=body,
        )
        self.assertEqual(status, 303)
        cookie = response.getheader('Set-Cookie')
        self.assertIn('; Secure', cookie)
        status, _, _ = self.send(
            '/api/state', host='192.168.1.50', headers={'Cookie': cookie.split(';')[0]}
        )
        self.assertEqual(status, 200)

    def test_an_oversized_login_body_gets_a_page_not_a_dropped_connection(self):
        status, body, _ = self.send(
            '/login',
            'POST',
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            body=b'code=' + b'x' * 5000,
        )
        self.assertEqual(status, 400)
        self.assertIn(b'Access code', body)

    def test_a_local_page_cannot_be_reached_through_a_rebound_name(self):
        status, _, _ = self.send('/api/state', host='evil.example')
        self.assertEqual(status, 401)


class LocalOnlyTests(unittest.TestCase):
    def test_without_a_code_other_hosts_are_forbidden_and_login_is_absent(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        previous = campaign.activate(campaign.Campaign(Path(temporary.name) / 'Studio'))
        self.addCleanup(campaign.activate, previous)
        server = ThreadingHTTPServer(('127.0.0.1', 0), http_routes.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        self.addCleanup(conn.close)
        conn.putrequest('GET', '/api/state', skip_host=True)
        conn.putheader('Host', 'studio.example')
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 403)
