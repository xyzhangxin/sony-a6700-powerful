"""Versioned post-operation workflow and evidence, with no device access.

The existing transaction remains the writer and verifier. This module compiles
its verified result into declarative restart/acceptance steps; timers and user
attestations never upgrade a firmware result into a feature acceptance.
"""
from __future__ import annotations

WORKFLOW_SCHEMA = "a6700.workflow/v1"
EVIDENCE_SCHEMA = "a6700.evidence/v1"
WORKFLOW_REVISION = 1


def _acceptance_steps(action, check):
    if check["id"] == "fl23" and action == "unlock":
        return [
            dict(id="fl23_menu_acceptance", feature_id="fl23", path=check["path"],
                 expected="主列表有 FL2 与 FL3，图标、预览和说明正常"),
            dict(id="fl23_capture_playback_acceptance", feature_id="fl23",
                 path="拍摄、保存与回放", expected="使用 FL2 与 FL3 拍摄、保存及回放正常"),
        ]
    return [dict(id=check["id"] + "_acceptance", feature_id=check["id"],
                 path=check["path"], expected=check["expected"])]


def build_workflow(action, restart_steps, checks, changed_ids, restart_required=True):
    steps = []
    for step in restart_steps if restart_required else ():
        item = dict(id="restart_" + step["id"], type="manual_action", phase="restart",
                    title=step["title"], detail=step["detail"],
                    evidence="user_attestation", required=True)
        if step["id"] == "wait":
            item["timing"] = dict(kind="minimum", seconds=10)
        if step["id"] == "on":
            item["emits"] = "camera_power_on_confirmed"
        steps.append(item)
    if "fl23" in changed_ids and action == "unlock":
        steps.append(dict(
            id="fl23_ready", type="wait_for_ready", phase="ready",
            anchor="camera_power_on_confirmed", evidence="user_attestation",
            timing=dict(kind="estimate", seconds=40, completion="prompt_checks_only"),
            required=True,
        ))
    for check in checks:
        for acceptance in _acceptance_steps(action, check):
            steps.append(dict(acceptance, type="acceptance", phase="acceptance",
                              method="manual", evidence="user_attestation", required=True))
    return dict(schema=WORKFLOW_SCHEMA, workflow_revision=WORKFLOW_REVISION,
                action=action, steps=steps)


def build_evidence(action, report, checks, restart_required):
    status = report.get("status")
    preparation = report.get("close_preparation") or {}
    settings_changed = bool(report.get("settings_write_attempted")
                            or preparation.get("settings_write_attempted")
                            or preparation.get("completed"))
    if settings_changed:
        setting_status = ("verified" if preparation.get("settings_verified") and preparation.get("ready")
                          else "unknown")
    else:
        setting_status = "unchanged"
    transaction_status = "no_op" if status in ("no-op", "settings-prepared") else "verified"

    cleanup_status = report.get("cleanup_status")
    if cleanup_status == "msc-observed":
        exit_status, exit_source = "confirmed", "device"
    elif cleanup_status == "simulation-only":
        exit_status, exit_source = "confirmed", "simulation"
    elif cleanup_status == "unconfirmed" or report.get("cleanup_warning"):
        exit_status, exit_source = "unconfirmed", "core"
    else:
        exit_status, exit_source = "unknown", "core"

    return dict(
        schema=EVIDENCE_SCHEMA,
        stages=dict(
            setting_preparation=dict(status=setting_status, source="core"),
            transaction=dict(status=transaction_status, source="independent_readback"),
            service_exit=dict(status=exit_status, source=exit_source),
            restart_ready=dict(status="awaiting_user" if restart_required else "not_run",
                               source="user_attestation" if restart_required else "core"),
        ),
        acceptance=[dict(step_id=acceptance["id"], feature_id=acceptance["feature_id"],
                         status="pending", source=None, required=True, method="manual")
                    for check in checks for acceptance in _acceptance_steps(action, check)],
    )
