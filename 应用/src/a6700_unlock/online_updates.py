"""Read-only online update checks over the signed local cache.

Production URLs are HTTPS and redirects are host-reviewed. Tests inject a
transport; no update check authenticates or writes to a camera.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import urllib.error
import urllib.request

from . import __version__
from .package_updates import (
    CHANNELS, MAX_ADAPTER_BYTES, MAX_ENVELOPE, LocalUpdateCache,
    UpdateError, verify_index_envelope)

API_BASE = "https://powerful6700-api.susukele.com"
_DOWNLOAD_HOSTS = {"powerful6700-api.susukele.com", "github.com", "objects.githubusercontent.com",
                   "release-assets.githubusercontent.com"}


class UpdateNetworkError(OSError):
    pass


@dataclass
class HttpResponse:
    status: int
    headers: dict
    body: bytes


def _redirect_url(url):
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in _DOWNLOAD_HOSTS
            or parsed.username or parsed.password or not parsed.path):
        raise UpdateError("更新重定向目标未经审查")
    return url


class ReviewedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _redirect_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibTransport:
    def __init__(self, api_base=API_BASE):
        from urllib.parse import urlsplit
        parsed = urlsplit(api_base)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("更新服务地址必须使用无凭据的 HTTPS URL")
        self.api_base = api_base.rstrip("/")
        self.opener = urllib.request.build_opener(ReviewedRedirectHandler())

    def get(self, url, *, headers, max_bytes, timeout):
        request = urllib.request.Request(url, headers=headers)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise UpdateError("更新响应超过大小上限")
                return HttpResponse(response.status, dict(response.headers.items()), body)
        except urllib.error.HTTPError as exc:
            return HttpResponse(exc.code, dict(exc.headers.items()), exc.read(4096))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise UpdateNetworkError("更新连接失败") from exc


class UpdateClient:
    def __init__(self, cache, trusted_keys, *, transport=None, api_base=API_BASE):
        if not isinstance(cache, LocalUpdateCache):
            raise TypeError("cache must be LocalUpdateCache")
        self.cache = cache
        self.trusted_keys = trusted_keys
        self.transport = transport or UrllibTransport(api_base)
        self.api_base = api_base.rstrip("/")

    def _headers(self, channel):
        headers = {"User-Agent": "Powerful6700/" + __version__,
                   "Accept": "application/json"}
        etag = self.cache.cached_etag(channel)
        if etag:
            headers["If-None-Match"] = etag
        return headers

    def _cached(self, channel, now):
        try:
            active = self.cache.active_adapter_set(channel, self.trusted_keys, now,
                                                   for_write=False)
            return dict(sequence=active["sequence"], cache_status=active["status"])
        except UpdateError:
            return dict(cache_status="unavailable")

    def _status(self, status, channel, now, **extra):
        return dict(status=status, **self._cached(channel, now), **extra)

    def check(self, channel, now):
        if channel not in CHANNELS:
            raise ValueError("未知更新通道")
        url = self.api_base + "/v1/updates/" + channel + "/index.json"
        try:
            response = self.transport.get(url, headers=self._headers(channel),
                                          max_bytes=MAX_ENVELOPE, timeout=15)
        except UpdateNetworkError:
            return self._status("offline", channel, now)
        except UpdateError as exc:
            return self._status("rejected", channel, now, error=str(exc))
        if response.status == 304:
            if not self.cache.cached_etag(channel):
                return self._status("rejected", channel, now, error="304_without_matching_cache")
            return self._status("not-modified", channel, now)
        if response.status in (404, 503):
            return self._status("unavailable", channel, now)
        if response.status != 200:
            return self._status("http-error", channel, now, http_status=response.status)
        try:
            index = verify_index_envelope(response.body, self.trusted_keys, channel, now)
            artifact_bytes = {}
            for artifact in index["artifacts"]:
                if artifact["kind"] != "adapter-set":
                    continue
                download = self.transport.get(artifact["url"], headers={
                    "User-Agent": "Powerful6700/" + __version__,
                    "Accept": "application/octet-stream"},
                    max_bytes=MAX_ADAPTER_BYTES, timeout=20)
                if download.status != 200:
                    return self._status("artifact-unavailable", channel, now,
                                        http_status=download.status)
                artifact_bytes[artifact["id"]] = download.body
            result = self.cache.activate_index(index, self.trusted_keys, now, artifact_bytes)
        except UpdateNetworkError:
            return self._status("offline", channel, now)
        except UpdateError as exc:
            return self._status("rejected", channel, now, error=str(exc))
        return self._status(result["status"], channel, now)


def envelope_etag(raw):
    """ETag used by the Worker: quoted SHA-256 of exact envelope bytes."""
    return '"' + hashlib.sha256(raw).hexdigest() + '"'
