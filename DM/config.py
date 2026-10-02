"""Local settings shared by the web server, map forge and AI workers."""

import json
import os
from copy import deepcopy

HOME = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HOME, 'data', 'settings.json')
DEFAULTS = {
    'campaign_name': 'Campaign Studio',
    'world_path': '',
    'ai': {'provider': 'claude', 'model': ''},
    'images': {'endpoint': '', 'model': '', 'key_env': 'IMAGE_API_KEY', 'size': '1024x1024'},
}


def settings():
    result = deepcopy(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding='utf-8') as f:
            saved = json.load(f)
        for k, v in saved.items():
            if isinstance(v, dict) and isinstance(result.get(k), dict):
                result[k].update(v)
            else:
                result[k] = v
    return result


def world_info(path):
    path = os.path.realpath(os.path.expanduser(str(path or '')))
    if os.path.basename(path).lower() == 'world.json':
        path = os.path.dirname(path)
    manifest = os.path.join(path, 'world.json')
    if not os.path.isfile(manifest):
        raise ValueError('Choose the Foundry world folder containing world.json.')
    with open(manifest, encoding='utf-8') as f:
        world = json.load(f)
    # Standard Foundry user data layout: Data/worlds/<world-id>/world.json.
    if os.path.basename(os.path.dirname(path)).lower() != 'worlds':
        raise ValueError("The world folder must be inside Foundry's worlds directory.")
    return {
        'path': path,
        'data_path': os.path.dirname(os.path.dirname(path)),
        'id': world.get('id') or os.path.basename(path),
        'title': world.get('title') or os.path.basename(path),
        'system': world.get('system', ''),
        'system_version': world.get('systemVersion', ''),
        'foundry_version': world.get('coreVersion')
        or world.get('compatibility', {}).get('verified', ''),
    }


def foundry_data():
    if os.environ.get('FOUNDRY_DATA'):
        return os.path.realpath(os.environ['FOUNDRY_DATA'])
    path = settings().get('world_path')
    if path:
        return world_info(path)['data_path']
    return ''
