"""Bounded retrieval and parsing of official Foundry releases and package metadata."""

import html
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from foundry_compat import RELEASES_URL, compatible, core, relationships

PACKAGE_URL = 'https://foundryvtt.com/packages/'
MAX_PAGE = 3 * 1024 * 1024
MAX_MANIFEST = 512 * 1024


def _validate_url(url, official=False):
    parsed = urllib.parse.urlsplit(str(url))
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Package metadata URL must use public HTTPS.')
    if official and parsed.hostname != 'foundryvtt.com':
        raise ValueError('Expected a Foundry directory URL.')
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
        raise ValueError('Package metadata URL resolved to a private address.')
    return parsed


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch(url, limit=MAX_PAGE, official=False):
    _validate_url(url, official=official)
    opener = urllib.request.build_opener(_SafeRedirect)
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


def package_releases(page, package_id):
    rows = re.findall(r'<li class="package-version flexrow">(.*?)</li>', page, re.S)
    releases = []
    for row in rows:
        version_match = re.search(r'<h4 class="package-title">\s*Version\s+([^<]+)</h4>', row)
        manifest_match = re.search(r'<a href="([^"]+)" title="Manifest Installation URL"', row)
        range_match = re.search(
            r'Foundry Version\s+(\d+(?:\.\d+)?)(\+|\s*-\s*(\d+(?:\.\d+)?))', _plain(row)
        )
        verified_match = re.search(r'\(Verified\s+(\d+(?:\.\d+)?)\)', _plain(row))
        if not (version_match and manifest_match and range_match):
            continue
        releases.append(
            {
                'id': package_id,
                'version': html.unescape(version_match[1]).strip(),
                'manifest': html.unescape(manifest_match[1]),
                'compatibility': {
                    'minimum': range_match[1],
                    'maximum': range_match[3] or '',
                    'verified': verified_match[1] if verified_match else '',
                },
            }
        )
    if rows and not releases:
        raise ValueError(f'No usable directory releases for {package_id}.')
    return releases


def _directory(package_id, fetcher):
    url = PACKAGE_URL + urllib.parse.quote(package_id, safe='') + '/'
    try:
        page = fetcher(url, official=True)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {'status': 'unlisted', 'url': url, 'releases': []}
        return {'status': 'unknown', 'url': url, 'releases': [], 'error': str(error)}
    except (OSError, ValueError, UnicodeError) as error:
        return {'status': 'unknown', 'url': url, 'releases': [], 'error': str(error)}
    try:
        releases = package_releases(page, package_id)
    except ValueError as error:
        return {'status': 'unknown', 'url': url, 'releases': [], 'error': str(error)}
    return {'status': 'listed', 'url': url, 'releases': releases}


def collect_catalog(inventory, fetcher=None):
    """Read the official directory and release manifests; unresolved data stays unknown."""
    fetcher = fetcher or fetch
    builds = stable_builds(fetcher(RELEASES_URL, official=True))
    original = core(inventory['world']['coreVersion'])
    builds = [value for value in builds if core(value) >= original]
    if not builds:
        raise ValueError('No stable Foundry build is available at or above this v12 build.')
    catalog = {}
    queue = [inventory['system']['id']] + [m['id'] for m in inventory['modules'] if m['enabled']]
    manifest_reads = 0
    while queue:
        package_id = queue.pop(0)
        if package_id in catalog:
            continue
        if len(catalog) >= 150:
            raise ValueError('Dependency catalogue exceeds the package limit.')
        entry = _directory(package_id, fetcher)
        catalog[package_id] = entry
        if entry['status'] != 'listed':
            continue
        if len(entry['releases']) > 200:
            entry['status'] = 'unknown'
            entry['error'] = 'Directory release history exceeds the review limit.'
            entry['releases'] = []
            continue
        for release in entry['releases']:
            if not any(compatible(release['compatibility'], core(build)) for build in builds):
                continue
            if manifest_reads >= 600:
                release['error'] = 'Manifest review limit reached; compatibility remains unknown.'
                continue
            manifest_reads += 1
            try:
                manifest = json.loads(fetcher(release['manifest'], MAX_MANIFEST))
                if not isinstance(manifest, dict) or manifest.get('id') != package_id:
                    raise ValueError('Manifest package ID differs from the directory.')
                release['requires'], release['systems'] = relationships(manifest)
                manifest_compat = manifest.get('compatibility') or {}
                if not isinstance(manifest_compat, dict):
                    raise ValueError('Invalid manifest compatibility.')
                # Apply both directory and manifest hard bounds. Directory data can
                # sidegrade, so retain each source for review.
                release['manifest_compatibility'] = manifest_compat
                release['manifest_version'] = manifest.get('version', '')
                if manifest.get('version') != release['version']:
                    raise ValueError('Manifest release version differs from the directory.')
                for dependency in release['requires']:
                    if dependency['type'] == 'module' and dependency['id'] not in catalog:
                        queue.append(dependency['id'])
            except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
                release['error'] = str(error)
    for module in inventory['modules']:
        if module['id'] not in catalog:
            entry = _directory(module['id'], fetcher)
            entry['releases'] = []  # Disabled packages do not constrain the search.
            catalog[module['id']] = entry
    return {'builds': builds, 'packages': catalog, 'source': RELEASES_URL}
