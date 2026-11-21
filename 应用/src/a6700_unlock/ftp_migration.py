"""One-time, fixed FTP migration using the shared locked native transaction.

This command is packaged separately. Normal application operations do not call
it. Unknown bytes stop the migration; no arbitrary profile or address is an
end-user input.
"""
from dataclasses import dataclass
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import sys

from .catalog import Catalog
from .device_profile import DeviceProfile, inspect_sectors
from .device_protocol import DeviceError
from . import operations

BUNDLE_SHA256 = "c56725d3fc77393f741a5e225b172df682a7af536f15860c492100b965cf2138"
SELECTED = ("ftp", "ftp-custom-key-matrix", "ftp-third-party-repair")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class MigrationBundle:
    profile: DeviceProfile
    generic: DeviceProfile
    assets: dict
    digest: str


def load_bundle():
    root = files("a6700_unlock").joinpath("data/ftp-migration")
    raw = root.joinpath("manifest.json").read_bytes()
    if sha(raw) != BUNDLE_SHA256:
        raise DeviceError("迁移适配集校验失败")
    manifest = json.loads(raw)
    if manifest.get("schema") != 1 or manifest.get("kind") != "ftp-migration-to-public-042":
        raise DeviceError("迁移适配集类型不匹配")
    for name, digest in manifest["files"].items():
        if ".." in Path(name).parts or Path(name).is_absolute() or sha(root.joinpath(name).read_bytes()) != digest:
            raise DeviceError("迁移资源校验失败：" + name)
    catalog = Catalog.from_dict(json.loads(root.joinpath("catalog.json").read_bytes()))
    profile = DeviceProfile.from_dict(json.loads(root.joinpath("profile.json").read_bytes()), catalog)
    generic_catalog = Catalog.from_dict(json.loads(root.joinpath("generic-catalog.json").read_bytes()))
    generic = DeviceProfile.from_dict(json.loads(root.joinpath("generic-profile.json").read_bytes()), generic_catalog)
    if (profile.digest != manifest["profile_sha256"] or catalog.digest != manifest["catalog_sha256"]
            or generic.digest != manifest["generic_profile_sha256"]
            or manifest["selected_features"] != list(SELECTED)):
        raise DeviceError("迁移清单与固定适配集不匹配")
    native = json.loads(root.joinpath("native/manifest.json").read_bytes())
    if native["profile_sha256"] != profile.digest or native["source_sha256"] != sha(
            files("a6700_unlock").joinpath("native/transaction.c").read_bytes()):
        raise DeviceError("迁移载荷与源码不匹配")
    assets = {name: root.joinpath("native/" + name + ".elf").read_bytes() for name in ("reader", "writer")}
    if any(sha(assets[name]) != native["payloads"][name]["sha256"] for name in assets):
        raise DeviceError("迁移原生载荷校验失败")
    if files("a6700_unlock").joinpath("data/guard-volatile.json").read_bytes() != root.joinpath(
            "generic-guard-volatile.json").read_bytes():
        raise DeviceError("通用版 inode 守卫规则不匹配")
    return MigrationBundle(profile, generic, assets, sha(raw))


def project_generic(bundle, sectors):
    if len(sectors) != len(bundle.profile.entries) or any(len(s) != 512 for s in sectors):
        raise DeviceError("迁移窗口读取不完整")
    by_key = {(r["device"], r["device_offset"]): sector
              for r, sector in zip(bundle.profile.entries, sectors)}
    return [by_key[(r["device"], r["device_offset"])] for r in bundle.generic.entries]


def generic_inspection(bundle, sectors):
    # Explicitly exclude the dev1 third-party acceptance overlay. Passing this
    # check means the complete public window accepts the repaired bytes.
    return inspect_sectors(bundle.generic, project_generic(bundle, sectors),
                           allow_guard_compatibility=False)[0]


def migration_plan(bundle, before):
    plan, after = inspect_sectors(bundle.profile, before, "unlock", features=SELECTED,
                                 allow_guard_compatibility=False)
    if not plan["compatible"]:
        raise DeviceError("存在未知字段或结构差异，迁移已停止：" + json.dumps(plan["issues"], ensure_ascii=False))
    generic = generic_inspection(bundle, after)
    if not generic["compatible"]:
        raise DeviceError("迁移目标不能通过通用版原始校验，拒绝写入")
    original = generic_inspection(bundle, before)
    for feature in original["features"]:
        if feature["id"] == "ftp":
            continue
        final = next(f for f in generic["features"] if f["id"] == feature["id"])
        if final["state"] != feature["state"]:
            raise DeviceError("迁移改变了非 FTP 功能状态")
    plan["migration"] = dict(bundle_sha256=bundle.digest,
        generic_profile_sha256=bundle.generic.digest, target_generic_compatible=True,
        restores_third_party_guards=True, custom_key_matrix_bytes=134,
        camera_menu_validation="pending-after-restart")
    return plan, after


class MigrationBackend:
    """Recheck the exact migration plan before delegating to the USB backend."""
    def __init__(self, bundle, backend):
        self.bundle, self.backend = bundle, backend
        self.observed = None

    def __getattr__(self, name):
        return getattr(self.backend, name)

    def __enter__(self):
        self.backend.__enter__()
        return self

    def __exit__(self, *args):
        return self.backend.__exit__(*args)

    def read(self, nonce):
        first = self.observed is None
        self.observed = self.backend.read(nonce)
        if first:
            migration_plan(self.bundle, self.observed)
        return self.observed

    def write(self, before, after, nonce):
        _, expected = migration_plan(self.bundle, before)
        if after != expected:
            raise DeviceError("执行目标与固定迁移计划不一致")
        return self.backend.write(before, after, nonce)


def pending_write(directory):
    for intent in (directory / "runs").glob("*/write-intent.json"):
        result_path = intent.with_name("result.json")
        if not result_path.is_file():
            return str(intent.parent)
        result = json.loads(result_path.read_bytes())
        native = result.get("native_result") or {}
        safe = (result.get("verified") and result.get("status") in ("completed", "no-op")
                or result.get("status") == "rolled-back" and native.get("rolled_back")
                or result.get("status") == "rejected" and native.get("touched") == 0)
        if not safe:
            return str(intent.parent)
    return None


def execute_migration(bundle, backend, *, apply=False, state_dir=None, progress=print):
    directory = Path(state_dir) if state_dir else operations.default_state_dir()
    wrapped = MigrationBackend(bundle, backend)
    with operations.device_lock(directory, observe_returned_settings=True):
        previous = pending_write(directory)
        if previous:
            raise DeviceError("前序写入结果需要先核对，不自动重发：" + previous)
        # The transaction takes a fresh read after acquiring the shared lock.
        result = operations._execute_session(bundle.profile, wrapped, "unlock", directory,
            progress, dry_run=not apply, features=SELECTED)
        if wrapped.observed is not None:
            generic = generic_inspection(bundle, wrapped.observed)
            operations.save_json(Path(result["run_directory"]) / "public-042-inspection.json", generic)
            result.update(generic_compatible=generic["compatible"],
                          generic_profile_sha256=bundle.generic.digest)
            if result.get("verified") and not generic["compatible"]:
                result.update(verified=False, status="verification-failed",
                              error="迁移回读未通过通用版原始校验")
        result.update(migration_bundle_sha256=bundle.digest, camera_menu_validation="pending-after-restart")
        # Keep the shared core's original transaction result intact.
        operations.save_json(Path(result["run_directory"]) / "migration-result.json", result)
        return result


def main():
    if "--self-check" in sys.argv:
        bundle = load_bundle()
        from .usb.backend import payloads
        from .usb.legacy import check_libusb_library
        payloads(bundle.generic)
        check_libusb_library()
        print(json.dumps(dict(ok=True, device_access=False, write_attempted=False,
                              libusb_loadable=True, bundle_sha256=bundle.digest,
                              profile_sha256=bundle.profile.digest)))
        return 0
    print("Powerful6700 FTP 一次性迁移修复（ILCE-6700 固件 2.00）")
    print("本工具撤回已知第三方守卫差异，补齐两个 FTP 自定义键选项，保留其他功能。")
    print("先退出其他相机工具，USB 使用海量存储器模式；请勿在修复中断电或拔线。")
    bundle = load_bundle()
    from .usb.backend import UsbBackend
    def backend():
        # Initialize the unchanged platform adapter, then select only the fixed,
        # independently verified migration reader/writer and matching profile.
        adapter = UsbBackend(bundle.generic, progress=print)
        adapter.profile, adapter.assets = bundle.profile, bundle.assets
        return adapter
    print("\n第一次检查只读取，不发送固件写入器。")
    preview = execute_migration(bundle, backend())
    print("检查记录：" + preview["run_directory"])
    if preview.get("status") != "dry-run":
        print("检查未通过：" + preview.get("error", preview["status"]))
        return 1
    plan = preview["plan"]
    print(f"预计修改 {plan['changed_bytes']} 字节／{plan['changed_sectors']} 个扇区。")
    if input("确认执行请输入 FIX，其他输入退出：").strip() != "FIX":
        print("已退出，没有发送固件写入器。")
        return 0
    result = execute_migration(bundle, backend(), apply=True)
    print("结果记录：" + result["run_directory"])
    if result.get("verified") and result.get("generic_compatible"):
        print("修复及独立回读通过，通用 0.4.2 原始校验通过。")
        if result.get("cleanup_warning"):
            print("USB 服务退出提示：" + result["cleanup_warning"])
        print("请正常关机，拔掉 USB 及外接电源，取下电池至少 10 秒后重装开机。")
        print("使用原通用版连接并检查；")
        print("再检查回放自定义键的“FTP传输”和“FTP传输(这个影像)”两个选项。")
        print("菜单与实际 FTP 上传需在相机重启后另行验收。")
        return 0
    print("修复未确认成功，请保留全部记录，不要盲目重试：" + result.get("error", result["status"]))
    return 1


def entrypoint():
    try:
        return main()
    except (DeviceError, OSError, ValueError) as exc:
        print("已停止：" + str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(entrypoint())
