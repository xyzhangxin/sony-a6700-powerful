"""Built-in capability packages and exact adapter-set resolution.

This is declarative metadata only. Existing planners and native executors still
own byte plans and writes. No network package, dynamic code or JSON-only address
extension is accepted here.
"""
from __future__ import annotations

import hashlib
from importlib.resources import files
import json
import re

from .creative_looks import FEATURES as CREATIVE_FEATURES
from .operation_workflow import WORKFLOW_REVISION, WORKFLOW_SCHEMA

ADAPTER_SET_SCHEMA = "a6700.adapter-set/v1"
CAPABILITY_PACKAGE_SCHEMA = "a6700.capability-package/v1"
CORE_API_VERSION = 1
_EXECUTORS = {"fixed-sector-transaction", "creative-look-package"}
_REQUIRED = {"independent-readback", "fresh-preflight", "shared-device-lock"}
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class CapabilityPackageError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _load_creative_manifest():
    raw = files("a6700_unlock").joinpath("data/fl23/manifest.json").read_bytes()
    manifest = json.loads(raw)
    if (manifest.get("schema") != 1 or not _DIGEST.fullmatch(manifest.get("profile_sha256", ""))
            or type(manifest.get("carrier_revision")) is not int
            or not isinstance(manifest.get("variants"), dict)):
        raise CapabilityPackageError("创意外观适配清单不完整")
    return manifest, hashlib.sha256(raw).hexdigest()


def _base_package(feature, available):
    return dict(
        schema=CAPABILITY_PACKAGE_SCHEMA, package_id=feature["id"],
        version=feature.get("revision", 1), release_sequence=1,
        min_core_api=CORE_API_VERSION,
        required_capabilities=sorted(_REQUIRED),
        available=available,
        dependencies=list(feature.get("requires", [])),
        workflow=dict(schema=WORKFLOW_SCHEMA, workflow_revision=WORKFLOW_REVISION,
                      definition="builtin-aftercare-v1"),
        acceptance=dict(definition="builtin-aftercare-v1", required=True,
                        evidence="user_attestation"),
        shared_resources=[],
    )


def _ordinary_package(profile, feature):
    available = feature["id"] in profile.catalog.available_features
    package = _base_package(feature, available)
    if not available:
        package["unavailable_reason"] = feature.get("unavailable_reason", "当前未开放")
    package["modification"] = dict(
        executor="fixed-sector-transaction",
        profile_sha256=profile.digest,
        catalog_sha256=profile.catalog.digest,
        edit_ids=[edit.id for edit in profile.catalog.edits if edit.feature == feature["id"]],
    )
    return package


def _creative_package(feature, available, manifest, manifest_digest):
    package = _base_package(feature, available)
    if not available:
        package["unavailable_reason"] = "创意外观安装组件缺失或校验未通过"
    package.update(
        modification=dict(
            executor="creative-look-package",
            package_profile_sha256=manifest["profile_sha256"],
            manifest_sha256=manifest_digest,
            carrier_revision=manifest["carrier_revision"],
            variants=sorted({1, *(int(flags) for flags in manifest["variants"])}),
        ),
        shared_resources=[dict(id="startup-loader", retention="retain-while-any-dependent-enabled")],
    )
    return package


def _validate_edit_ownership(profile):
    owners = {}
    for edit in profile.catalog.edits:
        target = (edit.enabled, edit.disabled)
        for offset in range(edit.offset, edit.end):
            key = (edit.image, offset)
            previous = owners.setdefault(key, target)
            if previous != target:
                raise CapabilityPackageError(
                    f"同字节写入目标冲突：{edit.image}@{offset}")


def _validate_dependencies(packages):
    by_id = {package["package_id"]: package for package in packages}
    if len(by_id) != len(packages):
        raise CapabilityPackageError("能力包 ID 重复")
    visiting, visited = set(), set()

    def visit(identifier):
        if identifier in visiting:
            raise CapabilityPackageError("能力包依赖存在循环")
        if identifier in visited:
            return
        if identifier not in by_id:
            raise CapabilityPackageError("能力包依赖未知：" + identifier)
        visiting.add(identifier)
        for dependency in by_id[identifier]["dependencies"]:
            visit(dependency)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in by_id:
        visit(identifier)


def validate_adapter_set(adapter_set):
    if not isinstance(adapter_set, dict) or adapter_set.get("schema") != ADAPTER_SET_SCHEMA:
        raise CapabilityPackageError("不支持的适配集 schema")
    body = dict(adapter_set)
    digest = body.pop("digest", None)
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest) or _digest(body) != digest:
        raise CapabilityPackageError("适配集摘要不匹配")
    if body.get("core_api_version") != CORE_API_VERSION:
        raise CapabilityPackageError("适配集核心 API 不兼容")
    for key in ("profile_sha256", "catalog_sha256"):
        if not isinstance(body.get(key), str) or not _DIGEST.fullmatch(body[key]):
            raise CapabilityPackageError("适配集资源摘要无效：" + key)
    packages = body.get("packages")
    if not isinstance(packages, list) or not packages:
        raise CapabilityPackageError("适配集没有能力包")
    for package in packages:
        if (package.get("schema") != CAPABILITY_PACKAGE_SCHEMA
                or type(package.get("version")) is not int or package["version"] < 1
                or type(package.get("release_sequence")) is not int or package["release_sequence"] < 1
                or type(package.get("min_core_api")) is not int
                or package["min_core_api"] > CORE_API_VERSION
                or type(package.get("available")) is not bool
                or package["modification"]["executor"] not in _EXECUTORS
                or not set(package["required_capabilities"]) <= _REQUIRED):
            raise CapabilityPackageError("能力包契约或核心能力不兼容")
    _validate_dependencies(packages)
    resources = {}
    for package in packages:
        for resource in package["shared_resources"]:
            previous = resources.setdefault(resource["id"], resource["retention"])
            if previous != resource["retention"]:
                raise CapabilityPackageError("共享组件保留策略冲突：" + resource["id"])
    return adapter_set


def builtin_adapter_set(profile, *, fl23_available=True, names_available=True,
                        include_creative_looks=True):
    """Compile the currently bundled resources into one exact adapter set."""
    _validate_edit_ownership(profile)
    creative_ids = {feature['id'] for feature in CREATIVE_FEATURES}
    packages = [_ordinary_package(profile, feature)
                for feature in profile.catalog.data["features"]
                if feature['id'] not in creative_ids]
    creative = None
    if include_creative_looks:
        manifest, manifest_digest = _load_creative_manifest()
        creative = {feature["id"]: feature for feature in CREATIVE_FEATURES}
        packages.append(_creative_package(creative["fl23"], fl23_available,
                                          manifest, manifest_digest))
        packages.append(_creative_package(creative["creative_look_names"], names_available,
                                          manifest, manifest_digest))
    body = dict(
        schema=ADAPTER_SET_SCHEMA,
        adapter_id="a6700-2.00-builtin",
        model=profile.catalog.data["model"], firmware=profile.catalog.data["firmware"],
        profile_sha256=profile.digest, catalog_sha256=profile.catalog.digest,
        core_api_version=CORE_API_VERSION, packages=packages,
    )
    adapter_set = dict(body, digest=_digest(body))
    return validate_adapter_set(adapter_set)


def package_versions(adapter_set):
    return {package["package_id"]: dict(version=package["version"],
                                        workflow_revision=package["workflow"]["workflow_revision"],
                                        available=package["available"])
            for package in adapter_set["packages"]}


def resolve_capability_selection(adapter_set, selected, action):
    """Resolve dependencies without producing or changing any byte plan."""
    validate_adapter_set(adapter_set)
    if action not in ("unlock", "close", "restore"):
        raise CapabilityPackageError("能力包操作类型不受支持")
    if not isinstance(selected, (set, frozenset, list, tuple)) or not selected:
        raise CapabilityPackageError("至少选择一个能力包")
    identifiers = list(selected)
    if len(set(identifiers)) != len(identifiers) or any(not isinstance(item, str) for item in identifiers):
        raise CapabilityPackageError("能力包选择无效")
    packages = {package["package_id"]: package for package in adapter_set["packages"]}
    if any(identifier not in packages for identifier in identifiers):
        raise CapabilityPackageError("选择包含未知能力包")
    if any(not packages[identifier]["available"] for identifier in identifiers):
        raise CapabilityPackageError("选择包含未开放能力包")
    resolved = set()

    def include(identifier):
        if identifier in resolved:
            return
        resolved.add(identifier)
        for dependency in packages[identifier]["dependencies"]:
            if not packages[dependency]["available"]:
                raise CapabilityPackageError("依赖能力包未开放：" + dependency)
            include(dependency)

    for identifier in identifiers:
        include(identifier)
    ordered = [package["package_id"] for package in adapter_set["packages"]
               if package["package_id"] in resolved]
    return dict(schema=ADAPTER_SET_SCHEMA, action=action,
                adapter_set_sha256=adapter_set["digest"],
                selected_features=identifiers, resolved_features=ordered,
                package_versions={identifier: package_versions(adapter_set)[identifier]
                                  for identifier in ordered})
