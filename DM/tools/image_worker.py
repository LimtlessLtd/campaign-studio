"""Generate one queued image through a configured images/generations endpoint."""

import base64
import io
import json
import os
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config
import campaign_core as core
from PIL import Image


def generate(rid):
    art = core.read_json(core.doc_path('art'), {'items': []})
    item = next((i for i in art['items'] if i['id'] == rid), None)
    if not item:
        raise ValueError('Artwork request not found.')
    cfg = config.settings()['images']
    endpoint = cfg.get('endpoint')
    if not endpoint or not cfg.get('model'):
        raise ValueError('Configure an image endpoint and model in Settings first.')
    url = urllib.parse.urlparse(endpoint)
    if url.scheme not in ('http', 'https') or url.username or url.password:
        raise ValueError('Invalid image provider endpoint.')
    if url.scheme == 'http' and url.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Remote image providers require HTTPS.')
    headers = {'Content-Type': 'application/json'}
    key = os.environ.get(cfg['key_env'])
    if key:
        headers['Authorization'] = 'Bearer ' + key
    elif url.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('The configured image API key environment variable is not set.')
    body = json.dumps(
        {'model': cfg['model'], 'prompt': item['prompt'], 'size': cfg['size'], 'n': 1}
    ).encode()
    req = urllib.request.Request(endpoint, data=body, headers=headers)
    # Local providers should bypass machine-wide proxies.
    opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if url.hostname in ('127.0.0.1', 'localhost', '::1')
        else urllib.request.build_opener()
    )
    with opener.open(req, timeout=600) as response:
        result = json.loads(response.read(40 * 1024 * 1024))
    encoded = result.get('data', [{}])[0].get('b64_json')
    if not encoded:
        raise ValueError('The image endpoint must return data[0].b64_json.')
    data = base64.b64decode(encoded, validate=True)
    if len(data) > 25 * 1024 * 1024:
        raise ValueError('The generated image exceeds 25 MB.')
    image = Image.open(io.BytesIO(data))
    image.verify()
    ext = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}.get(image.format)
    if not ext:
        raise ValueError('The provider returned an unsupported image format.')
    os.makedirs(core.UPLOADS, exist_ok=True)
    name = 'generated-' + os.urandom(8).hex() + ext
    with open(os.path.join(core.UPLOADS, name), 'xb') as f:
        f.write(data)
    path = 'DM/uploads/' + name
    # The parent server applies this result under its document lock.
    print(json.dumps({'path': path}), flush=True)
    return path


if __name__ == '__main__':
    try:
        generate(sys.argv[1])
    except Exception as e:
        sys.exit('Image generation failed: ' + str(e))
