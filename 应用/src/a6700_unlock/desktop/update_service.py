"""Desktop-scoped update configuration and user-triggered checks."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from pathlib import Path

from ..online_updates import API_BASE, UpdateClient
from ..operations import default_state_dir
from ..package_updates import LocalUpdateCache


def load_trust_keys():
    from importlib.resources import files
    raw = files("a6700_unlock").joinpath("data/update-trust.json").read_bytes()
    data = json.loads(raw)
    if data.get("schema_version") != 1 or not isinstance(data.get("keys"), dict):
        raise ValueError("更新信任配置格式无效")
    if len(data["keys"]) > 16:
        raise ValueError("更新信任配置包含过多密钥")
    result = {}
    for key_id, encoded in data["keys"].items():
        if not isinstance(key_id, str) or not isinstance(encoded, str):
            raise ValueError("更新信任配置包含无效密钥")
        public = base64.b64decode(encoded, validate=True)
        if len(public) != 32 or base64.b64encode(public).decode() != encoded:
            raise ValueError("更新信任配置公钥无效")
        result[key_id] = encoded
    return result


class DesktopUpdateService:
    def __init__(self, state_dir=None, *, trusted_keys=None, transport=None, api_base=API_BASE):
        root = Path(state_dir) if state_dir is not None else default_state_dir()
        self.cache = LocalUpdateCache(root / "update-cache")
        self.trusted_keys = load_trust_keys() if trusted_keys is None else trusted_keys
        self.client = UpdateClient(self.cache, self.trusted_keys,
                                   transport=transport, api_base=api_base)

    @property
    def configured(self):
        return bool(self.trusted_keys)

    def check(self, now=None):
        if not self.configured:
            return dict(status="unconfigured", cache_status="unavailable")
        return self.client.check("stable", now or datetime.now(timezone.utc))
