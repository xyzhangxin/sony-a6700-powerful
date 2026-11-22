"""Check the latest published Powerful6700 desktop release without installing it."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .. import __version__

_REPOSITORY = 'xyzhangxin/sony-a6700-powerful'
_LATEST_RELEASE = f'https://api.github.com/repos/{_REPOSITORY}/releases/latest'
_VERSION = r'(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})'
_LOCAL_VERSION = re.compile(_VERSION + r'(\.dev[0-9]+)?')
_RELEASE_TAG = re.compile('v' + _VERSION)
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
_DIGEST = re.compile(r'sha256:([0-9a-f]{64})')
_DOWNLOAD_HOSTS = {'github.com', 'objects.githubusercontent.com', 'release-assets.githubusercontent.com'}


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # The application queries exactly one public API endpoint.
        return None


def _fetch_latest():
    request = Request(_LATEST_RELEASE, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': f'Powerful6700/{__version__}',
    })
    with build_opener(_NoRedirects()).open(request, timeout=8) as response:
        body = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(body) > _MAX_RESPONSE_BYTES:
        raise ValueError('GitHub release response exceeds the size limit')
    return json.loads(body)


def _asset_platform(system, machine):
    architecture = machine.lower()
    if system == 'darwin' and architecture == 'arm64':
        return 'macOS-arm64'
    if system == 'win32' and architecture in ('amd64', 'x86_64'):
        return 'Windows-x64'
    return None


def check_app_update(current_version=__version__, *, system=None, machine=None, fetch=None):
    """Return a desktop update status and, when available, a fixed GitHub page URL."""
    local = _LOCAL_VERSION.fullmatch(current_version)
    target = _asset_platform(system or sys.platform, machine or platform.machine())
    if local is None or target is None:
        return {'status': 'unavailable'}
    try:
        release = (fetch or _fetch_latest)()
    except HTTPError as exc:
        return {'status': 'rate-limited' if exc.code in (403, 429) else 'unavailable'}
    except (URLError, OSError):
        return {'status': 'offline'}
    except ValueError:
        return {'status': 'unavailable'}
    if not isinstance(release, dict) or release.get('draft') is not False or release.get('prerelease') is not False:
        return {'status': 'unavailable'}
    tag = release.get('tag_name')
    latest = _RELEASE_TAG.fullmatch(tag) if isinstance(tag, str) else None
    if latest is None:
        return {'status': 'unavailable'}
    current_tuple = tuple(int(part) for part in local.groups()[:3])
    latest_tuple = tuple(int(part) for part in latest.groups())
    if latest_tuple < current_tuple or (latest_tuple == current_tuple and local.group(4) is None):
        return {'status': 'up-to-date'}
    version = tag[1:]
    assets = release.get('assets')
    filename = f'Powerful6700-{version}-{target}.zip'
    if not isinstance(assets, list):
        return {'status': 'unavailable'}
    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get('name') == filename]
    if len(matches) != 1 or matches[0].get('state') != 'uploaded':
        return {'status': 'unavailable'}
    asset = matches[0]
    size = asset.get('size')
    digest = asset.get('digest')
    sha = _DIGEST.fullmatch(digest) if isinstance(digest, str) else None
    if type(size) is not int or not 0 < size <= _MAX_DOWNLOAD_BYTES or sha is None:
        return {'status': 'unavailable'}
    offer = {'status': 'available', 'version': version, 'filename': filename,
             'size': size, 'sha256': sha[1]}
    if target == 'macOS-arm64':
        signatures = [item for item in assets if isinstance(item, dict)
                      and item.get('name') == filename + '.ed25519'
                      and item.get('state') == 'uploaded' and item.get('size') == 64]
        if len(signatures) == 1:
            offer['installable'] = True
    return offer


class _ReleaseAssetRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        destination = urlsplit(newurl)
        if (destination.scheme != 'https' or destination.hostname not in _DOWNLOAD_HOSTS
                or destination.username or destination.password or destination.port not in (None, 443)):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_asset(url):
    request = Request(url, headers={'Accept': 'application/octet-stream',
                                    'User-Agent': f'Powerful6700/{__version__}'})
    return build_opener(_ReleaseAssetRedirects()).open(request, timeout=20)


def download_app_update(offer, directory: Path, progress, *, open_asset=None):
    """Stage a verified release ZIP without following an arbitrary URL or replacing an app."""
    tag = f"v{offer['version']}"
    name = offer['filename']
    if _RELEASE_TAG.fullmatch(tag) is None or name not in (
        f"Powerful6700-{offer['version']}-macOS-arm64.zip",
        f"Powerful6700-{offer['version']}-Windows-x64.zip",
    ):
        raise ValueError('Invalid application update metadata')
    expected = offer['size']
    if type(expected) is not int or not 0 < expected <= _MAX_DOWNLOAD_BYTES or _DIGEST.fullmatch('sha256:' + offer['sha256']) is None:
        raise ValueError('Missing application update size or digest')
    url = f'https://github.com/{_REPOSITORY}/releases/download/{tag}/{name}'
    directory.mkdir(parents=True, exist_ok=True)
    partial = None
    try:
        total = 0
        digest = hashlib.sha256()
        previous = -1
        with (open_asset or _open_asset)(url) as response, tempfile.NamedTemporaryFile(
            mode='wb', dir=directory, prefix='.download-', suffix='.part', delete=False,
        ) as output:
            partial = Path(output.name)
            while chunk := response.read(512 * 1024):
                total += len(chunk)
                if total > expected:
                    raise ValueError('Application update is larger than the published size')
                digest.update(chunk)
                output.write(chunk)
                percent = min(99, (100 * total) // expected)
                if percent != previous:
                    progress(percent)
                    previous = percent
        if total != expected or digest.hexdigest() != offer['sha256']:
            raise ValueError('Application update size or digest does not match')
        target = directory / name
        os.replace(partial, target)
        return target
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)


def download_install_signature(offer, archive: Path, *, open_asset=None):
    """Fetch the detached signature. The native installer authenticates it."""
    tag = f"v{offer['version']}"
    if (offer.get('installable') is not True or _RELEASE_TAG.fullmatch(tag) is None
            or archive.name != f"Powerful6700-{offer['version']}-macOS-arm64.zip"):
        raise ValueError('Invalid Mac update signature asset')
    name = archive.name + '.ed25519'
    url = f'https://github.com/{_REPOSITORY}/releases/download/{tag}/{name}'
    with (open_asset or _open_asset)(url) as response:
        signature = response.read(65)
    if len(signature) != 64:
        raise ValueError('Invalid Ed25519 signature length')
    with tempfile.NamedTemporaryFile(dir=archive.parent, prefix='.signature-', delete=False) as stream:
        path = Path(stream.name)
        try:
            stream.write(signature)
            stream.close()
            os.replace(path, archive.with_name(name))
        finally:
            path.unlink(missing_ok=True)
