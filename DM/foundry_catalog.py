"""Bounded retrieval and parsing of official Foundry releases and package metadata."""

import html
import http.client
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from foundry_compat import RELEASES_URL, core, core_bound, relationships

PACKAGE_URL = 'https://foundryvtt.com/packages/'
MAX_PAGE = 3 * 1024 * 1024
MAX_MANIFEST = 512 * 1024
MAX_DEPENDENCIES = 150  # Packages read only because a release requires them.
MAX_RELEASES = 200
MAX_MANIFEST_READS = 600
MAX_PAUSE = 20  # Seconds a report waits once for a rate-limited manifest host.
# A directory build: 12, 12.343, or a pre-v9 version such as 0.6.5.
DIRECTORY_BUILD = r'(\d+(?:\.\d+){0,2})'


def _validate_url(url, official=False):
    parsed = urllib.parse.urlsplit(str(url))
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Package metadata URL must use public HTTPS.')
    if official and parsed.hostname != 'foundryvtt.com':
        raise ValueError('Expected a Foundry directory URL.')
    return parsed


def _public_address(host, port):
    """Resolve once and return an address only if every answer is public."""
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
        raise ValueError('Package metadata URL resolved to a private address.')
    return addresses[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to the address the public-address check approved, not a second DNS answer.

    The hostname still drives SNI and certificate verification. Redirects open new connections, so
    every hop is checked and pinned the same way.
    """

    def connect(self):
        if self._tunnel_host:
            raise ValueError('Package metadata downloads do not support HTTPS proxies.')
        family, kind, proto, _name, address = _public_address(self.host, self.port)
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(self.timeout)
            sock.connect(address)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(_PinnedHTTPSConnection, request, context=self._context)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch(url, limit=MAX_PAGE, official=False):
    _validate_url(url, official=official)
    # A proxy may resolve the target a second time. Connect directly to the approved address.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _PinnedHTTPSHandler, _SafeRedirect
    )
    request = urllib.request.Request(url, headers={'User-Agent': 'CampaignStudio/upgrade-report'})
    with opener.open(request, timeout=12) as response:
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError('Package metadata exceeds the size limit.')
        return body.decode('utf-8')


def _plain(fragment):
    return html.unescape(re.sub(r'<[^>]+>', ' ', fragment)).strip()


def stable_builds(page):
    builds = []
    for row in re.findall(r'<li class="article release flexrow">(.*?)</li>', page, re.S):
        match = re.search(r'href="/releases/(\d+\.\d+)"', row)
        if match and re.search(r'class="release-tag stable"', row):
            builds.append(match[1])
    if not builds:
        raise ValueError('Foundry stable release listing could not be read.')
    return sorted(set(builds), key=lambda value: core(value), reverse=True)


def _bounds_parse(compatibility):
    try:
        for value in compatibility.values():
            if value:
                core_bound(value)
    except ValueError:
        return False
    return True


def package_releases(page, package_id):
    rows = re.findall(r'<li class="package-version flexrow">(.*?)</li>', page, re.S)
    releases = []
    for row in rows:
        version_match = re.search(r'<h4 class="package-title">\s*Version\s+([^<]+)</h4>', row)
        manifest_match = re.search(r'<a href="([^"]+)" title="Manifest Installation URL"', row)
        range_match = re.search(
            rf'Foundry Version\s+{DIRECTORY_BUILD}(\+|\s*-\s*{DIRECTORY_BUILD})', _plain(row)
        )
        verified_match = re.search(rf'\(Verified\s+{DIRECTORY_BUILD}\)', _plain(row))
        if not (version_match and manifest_match and range_match):
            continue
        compatibility = {
            'minimum': range_match[1],
            'maximum': range_match[3] or '',
            'verified': verified_match[1] if verified_match else '',
        }
        if not _bounds_parse(compatibility):
            continue
        releases.append(
            {
                'id': package_id,
                'version': html.unescape(version_match[1]).strip(),
                'manifest': html.unescape(manifest_match[1]),
                'compatibility': compatibility,
            }
        )
    if rows and not releases:
        raise ValueError(f'No usable directory releases for {package_id}.')
    return releases


def _pause(error):
    """Seconds an HTTP 429 answer asks the client to wait."""
    try:
        return max(int((error.headers or {}).get('Retry-After') or 5), 1)
    except ValueError:
        return 5


def _same_version(manifest_version, directory_version):
    """Publishers often tag a manifest "v4.6.12" for the release the directory lists as "4.6.12"."""

    def plain(value):
        return value[1:] if value.startswith(('v', 'V')) else value

    return plain(manifest_version.strip()) == plain(directory_version.strip())


def _directory(package_id, fetcher):
    url = PACKAGE_URL + urllib.parse.quote(package_id, safe='') + '/'
    try:
        page = fetcher(url, official=True)
    except urllib.error.HTTPError as error:
        error.close()
        if error.code == 404:
            return {'status': 'unlisted', 'url': url, 'releases': []}
        return {'status': 'unknown', 'url': url, 'releases': [], 'error': str(error)}
    except (OSError, ValueError, UnicodeError) as error:
        return {'status': 'unknown', 'url': url, 'releases': [], 'error': str(error)}
    try:
        releases = package_releases(page, package_id)
    except ValueError as error:
        return {'status': 'unknown', 'url': url, 'releases': [], 'error': str(error)}
    if len(releases) > MAX_RELEASES:
        error = 'Directory release history exceeds the review limit.'
        return {'status': 'unknown', 'url': url, 'releases': [], 'error': error}
    return {'status': 'listed', 'url': url, 'releases': releases}


class Catalog:
    """Official directory evidence for one report, read only as far as the solver needs it.

    `packages` maps each package ID to its directory entry and is the report's evidence. With a
    fetcher, a package page is read when the package is first needed and a release manifest when the
    solver first considers that release, newest first, so a report reads a handful of releases per
    package instead of every historical one. Without a fetcher the data is complete saved evidence
    and nothing more is read.
    """

    def __init__(self, data, fetcher=None):
        self.builds = data['builds']
        self.packages = data['packages']
        self.source = data.get('source', RELEASES_URL)
        self._fetcher = fetcher
        self._dependencies = 0
        self._manifests = {}  # URL -> body; several releases may name one "latest" manifest URL.
        self.manifest_reads = 0

    def entry(self, package_id):
        """The package's directory entry, or None when saved evidence does not include it."""
        if package_id not in self.packages and self._fetcher:
            if self._dependencies >= MAX_DEPENDENCIES:
                raise ValueError('Dependency catalogue exceeds the package limit.')
            self._dependencies += 1
            self.packages[package_id] = _directory(package_id, self._fetcher)
        return self.packages.get(package_id)

    def review(self, package_id, release):
        """Add the release manifest's relationships and bounds the first time a release is used."""
        if not self._fetcher or 'requires' in release or 'error' in release:
            return release
        if self.manifest_reads >= MAX_MANIFEST_READS:
            release['error'] = 'Manifest review limit reached; compatibility remains unknown.'
            return release
        self.manifest_reads += 1
        try:
            manifest = json.loads(self._read(release['manifest']))
            if not isinstance(manifest, dict) or manifest.get('id') != package_id:
                raise ValueError('Manifest package ID differs from the directory.')
            release['requires'], release['systems'] = relationships(manifest)
            manifest_compat = manifest.get('compatibility') or {}
            if not isinstance(manifest_compat, dict):
                raise ValueError('Invalid manifest compatibility.')
            # Apply both directory and manifest hard bounds. Directory data can
            # sidegrade, so retain each source for review.
            release['manifest_compatibility'] = manifest_compat
            version = manifest.get('version')
            release['manifest_version'] = version if isinstance(version, str) else ''
            if not release['manifest_version'] or not _same_version(version, release['version']):
                raise ValueError('Manifest release version differs from the directory.')
            # The clone installs the manifest, so later checks compare its spelling ("v4.6.12").
            release['directory_version'], release['version'] = release['version'], version
        except urllib.error.HTTPError as error:
            error.close()
            release['error'] = str(error)
            if error.code == 429:
                minutes = -(-_pause(error) // 60)
                release['error'] += f'; the host asks to wait about {minutes} min'
            release['unread'] = error.code == 429 or error.code >= 500
        except (OSError, http.client.HTTPException) as error:  # A timeout or dropped connection.
            release['error'] = str(error)
            release['unread'] = True
        except (ValueError, UnicodeError, json.JSONDecodeError) as error:
            release['error'] = str(error)
        return release

    def _read(self, url):
        """Fetch a manifest once per report, pausing once if the host asks (HTTP 429)."""
        if url not in self._manifests:
            try:
                self._manifests[url] = self._fetcher(url, MAX_MANIFEST)
            except urllib.error.HTTPError as error:
                if error.code != 429 or _pause(error) > MAX_PAUSE:
                    raise
                error.close()
                time.sleep(_pause(error))
                self._manifests[url] = self._fetcher(url, MAX_MANIFEST)
        return self._manifests[url]


def collect_catalog(inventory, fetcher=None):
    """Read the official builds and the inventory's directory pages; manifests are read on use."""
    fetcher = fetcher or fetch
    builds = stable_builds(fetcher(RELEASES_URL, official=True))
    original = core(inventory['world']['coreVersion'])
    builds = [value for value in builds if core(value) >= original]
    if not builds:
        raise ValueError('No stable Foundry build is available at or above this v12 build.')
    packages = {
        package_id: _directory(package_id, fetcher)
        for package_id in [
            inventory['system']['id'],
            *(item['id'] for item in inventory['modules']),
        ]
    }
    return Catalog({'builds': builds, 'packages': packages, 'source': RELEASES_URL}, fetcher)
