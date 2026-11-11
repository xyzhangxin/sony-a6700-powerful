"""Versioned command/event contract shared by the desktop bridges.

Transport is newline-delimited JSON over a local worker pipe. The contract
adds correlation metadata without exposing paths, backends or raw USB requests.
"""
from __future__ import annotations

import json
import re

from ..operation_workflow import EVIDENCE_SCHEMA, WORKFLOW_SCHEMA

API_VERSION = 1
MAX_REQUEST_BYTES = 8192

_COMMAND_FIELDS = {
    "environment": set(), "reset": {"scenario"}, "acknowledge": {"value"},
    "prepare": set(), "confirm": {"id", "value"}, "confirm_preparation": {"value"},
    "restart_preparation": set(), "select_feature": {"id", "value"}, "select": {"mode"},
    "acknowledge_check": set(), "inspect": set(), "unlock": set(), "close": set(),
    "backup": set(), "restore": {"backup_id"}, "update_fl23": set(), "update_ftp": set(),
    "takeover": {"confirmation", "confirmed"}, "cancel": set(), "disconnect": set(),
    "reconnect": set(), "home": set(), "shutdown": set(),
    "diagnostic": {"camera"}, "guided_diagnostic": {"action"},
    "upload_feedback": {"description", "images"},
    "check_updates": set(),
}
CAPABILITIES = dict(
    commands=sorted(_COMMAND_FIELDS), workflow_schema=WORKFLOW_SCHEMA,
    evidence_schema=EVIDENCE_SCHEMA, signed_packages=False,
    execution="local-core", arbitrary_device_requests=False,
)
_SIMULATION_ONLY = {"reset", "disconnect", "reconnect"}
_PHYSICAL_ONLY = {"takeover", "diagnostic", "guided_diagnostic", "upload_feedback", "check_updates"}


def capabilities(mode):
    if mode not in ("simulation", "physical"):
        raise ValueError("未知桌面模式。")
    unsupported = _PHYSICAL_ONLY if mode == "simulation" else _SIMULATION_ONLY
    return dict(CAPABILITIES, commands=sorted(set(_COMMAND_FIELDS) - unsupported))


_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("请求包含重复字段。")
        result[key] = value
    return result


def decode_request(raw):
    """Parse one request, rejecting duplicate/unknown fields and bad versions."""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise ValueError("请求过长或格式无效。")
    request = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(request, dict):
        raise ValueError("无效请求。")
    version = request.get("api_version", API_VERSION)
    if type(version) is not int or version != API_VERSION:
        raise ValueError("不支持的桌面协议版本。")
    command = request.get("command")
    if not isinstance(command, str) or command not in _COMMAND_FIELDS:
        raise ValueError("未知桌面命令。")
    allowed = _COMMAND_FIELDS[command] | {"command", "request_id", "api_version"}
    if set(request) - allowed:
        raise ValueError("请求包含该命令不支持的字段。")
    request_id = request.get("request_id")
    if request_id is not None and (not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id)):
        raise ValueError("请求关联 ID 无效。")
    return request


def event(kind, *, request_id=None, operation_id=None, **values):
    if not isinstance(kind, str) or not kind:
        raise ValueError("事件类型无效。")
    state = values.get("state")
    if operation_id is None and isinstance(state, dict):
        operation_id = state.get("operation_id")
    payload = dict(type=kind, api_version=API_VERSION)
    if request_id is not None:
        payload["request_id"] = request_id
    if operation_id is not None:
        payload["operation_id"] = operation_id
    payload.update(values)
    return payload
