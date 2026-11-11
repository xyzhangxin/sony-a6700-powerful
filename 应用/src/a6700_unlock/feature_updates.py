"""Recognize additive feature revisions and plan updates without device access.

Revision definitions live in the catalog. This module contains no firmware
offsets, USB access, file writes or alternate transaction implementation.
"""
from __future__ import annotations


def validate_updates(features, edits):
    by_id = {edit.id: edit for edit in edits}
    for feature in features:
        if "updates" not in feature and "revision" not in feature:
            continue
        updates = feature.get("updates")
        revision = feature.get("revision")
        if (type(revision) is not int or revision < 2
                or not isinstance(updates, list) or len(updates) != revision - 1):
            raise ValueError("Feature revisions require a contiguous update history from revision 2")
        owned = set()
        update_ids = set()
        for expected, update in enumerate(updates, 2):
            if not isinstance(update, dict) or type(update.get("revision")) is not int or update["revision"] != expected:
                raise ValueError("Invalid feature update revision")
            ident = update.get("id")
            if not isinstance(ident, str) or not ident or ident in update_ids:
                raise ValueError("Invalid or duplicate feature update id")
            update_ids.add(ident)
            ids = update.get("edit_ids")
            if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids):
                raise ValueError("Feature update requires edit ids")
            for ident in ids:
                edit = by_id.get(ident)
                if (ident in owned or edit is None or edit.feature != feature["id"]
                        or edit.disabled == edit.enabled):
                    raise ValueError("Feature update has repeated, foreign or inactive edits")
                owned.add(ident)
        if not any(e.feature == feature["id"] and e.id not in owned and e.disabled != e.enabled for e in edits):
            raise ValueError("Feature update requires an existing base feature")


def revision_status(feature, edits, states):
    """Keep base capability state separate from additive update completeness."""
    if "updates" not in feature:
        return {}
    from .planner import _combined_state

    owned = [e for e in edits if e.feature == feature["id"]]
    delta_ids = {ident for update in feature["updates"] for ident in update["edit_ids"]}
    base = _combined_state([states[e.id] for e in owned if e.id not in delta_ids])
    raw = _combined_state([states[e.id] for e in owned])
    delta = [states[ident] for ident in delta_ids]
    result = dict(state=raw, base_state=base, installed_revision=None,
                  target_revision=feature["revision"], update_state="unrecognized",
                  update_available=False, update_edit_ids=[])
    if raw == "unknown" or any(s not in ("enabled", "disabled") for s in delta):
        result["update_state"] = "unknown"
    elif base == "disabled" and all(s == "disabled" for s in delta):
        result.update(state="disabled", installed_revision=0, update_state="uninstalled")
    elif base == "enabled":
        installed = 1
        for update in feature["updates"]:
            if not all(states[ident] == "enabled" for ident in update["edit_ids"]):
                break
            installed = update["revision"]
        current = all(s == "enabled" for s in delta)
        clean_pending = all(states[ident] == "disabled"
                            for update in feature["updates"] if update["revision"] > installed
                            for ident in update["edit_ids"])
        pending = [e.id for e in owned if e.id in delta_ids and states[e.id] != "enabled"]
        result.update(state="enabled", installed_revision=installed,
                      update_state="current" if current else "available" if clean_pending else "incomplete",
                      update_available=not current, update_edit_ids=pending)
    return result


def plan_update(profile, sectors, feature_id):
    """Use the ordinary unlock planner, but require an intact enabled base.

    The wire operation remains unlock; requested_action records the narrower
    update intent. CLI/desktop update controls are not enabled here.
    """
    from .device_profile import inspect_sectors

    feature = next((f for f in profile.catalog.data["features"] if f["id"] == feature_id), None)
    if feature is None or "updates" not in feature:
        raise ValueError("Feature has no update definition")
    plan, after = inspect_sectors(profile, sectors, "unlock", features=[feature_id])
    state = next(f for f in plan["features"] if f["id"] == feature_id)
    plan.update(requested_action="update", update_feature=feature_id,
                from_revision=state["installed_revision"], target_revision=state["target_revision"])
    if state["update_state"] not in ("available", "incomplete", "current"):
        plan["issues"].append(dict(kind="update-requires-enabled-base", feature=feature_id,
                                   base_state=state["base_state"], update_state=state["update_state"]))
        plan.update(compatible=False, status="blocked", changed_indices=[], changed_sectors=0, changed_bytes=0)
        after = list(sectors)
    if plan["compatible"]:
        allowed = {(e.image, at) for e in profile.catalog.edits if e.id in state["update_edit_ids"]
                   for at in range(e.offset, e.end)}
        for row, before, target in zip(profile.entries, sectors, after):
            for at, (a, b) in enumerate(zip(before, target)):
                if a != b and (row.get("image"), row.get("file_offset", 0) + at) not in allowed:
                    raise ValueError("Update plan changed a byte outside the pending revision")
    return plan, after
