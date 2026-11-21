import unittest

from a6700_unlock.capability_packages import (
    builtin_adapter_set, package_versions, resolve_capability_selection,
)
from a6700_unlock.catalog import load_catalog
from a6700_unlock.custom_sound_capability_package import (
    CAPABILITY_PACKAGE_SCHEMA, load_custom_sound_capability_package,
    load_custom_sound_menu_ui_assets, plan_menu_ui_part,
)
from a6700_unlock.device_profile import load_device_profile


class CustomSoundCapabilityPackageTests(unittest.TestCase):
    def test_unified_package_is_strictly_bound_and_installable(self):
        manifest = load_custom_sound_capability_package()
        self.assertEqual(manifest["schema"], CAPABILITY_PACKAGE_SCHEMA)
        self.assertEqual(manifest["package_id"], "custom_shutter_sound")
        self.assertFalse(manifest["camera_io"])
        self.assertTrue(manifest["installable"])
        self.assertEqual(manifest["deployment"], "fl23-unified-carrier")
        self.assertEqual(manifest["dependencies"], ["sound"])
        self.assertEqual(manifest["carrier"]["flags"], [4, 5, 6, 7])
        self.assertEqual(set(manifest["carrier"]["variants"]), {"4", "5", "6", "7"})
        self.assertEqual(manifest["menu_ui"]["sector_count"], 23)
        self.assertEqual(manifest["menu_ui"]["slot_count"], 10)
        self.assertEqual(manifest["menu_ui"]["default_label"], "默认")
        self.assertEqual(manifest["menu_ui"]["row_address"], "0x3781000")
        self.assertEqual(
            manifest["menu_ui"]["parts"],
            [
                "part-01-append", "part-02-size", "part-03-index",
                "part-04-prepare", "part-05-lookup", "part-06-publish",
            ],
        )
        self.assertEqual(
            manifest["root_input"],
            {
                "path": "root-input/a6700-2.00.json",
                "logical_path": "release/src/a6700_unlock/data/a6700-2.00.json",
                "sha256": "d6b5eddabe1f05bd5f3069563dde6b9a4eee6800006d162fccfc8fec9951d698",
                "bytes": 151087,
                "source": "Powerful6700 0.4.2 frozen source bundle",
                "purpose": "Fixed FL23 root-transaction catalog pin for custom_shutter_sound",
            })

    def test_builtin_adapter_set_binds_the_installable_package(self):
        profile = load_device_profile(load_catalog())
        adapter = builtin_adapter_set(profile)
        package = next(row for row in adapter["packages"]
                       if row["package_id"] == "custom_shutter_sound")
        self.assertTrue(package["available"])
        self.assertEqual(package["dependencies"], ["sound"])
        self.assertEqual(package["modification"]["executor"],
                         "custom-shutter-sound-package")
        self.assertTrue(package["modification"]["installable"])
        self.assertEqual(package["modification"]["package_schema"],
                         CAPABILITY_PACKAGE_SCHEMA)
        self.assertEqual(package["modification"]["carrier"]["flags"], [4, 5, 6, 7])
        self.assertEqual(package["modification"]["menu_ui"]["sector_count"], 23)
        self.assertEqual(package["modification"]["menu_ui"]["row_address"], "0x3781000")
        self.assertTrue(package_versions(adapter)["custom_shutter_sound"]["available"])

        unlocked = resolve_capability_selection(adapter, {"custom_shutter_sound"}, "unlock")
        self.assertEqual(unlocked["resolved_features"], ["sound", "custom_shutter_sound"])
        closed = resolve_capability_selection(adapter, {"custom_shutter_sound"}, "close")
        self.assertEqual(closed["resolved_features"], ["custom_shutter_sound"])

    def test_sparse_menu_ui_assets_are_loaded_and_preserve_inode_time_drift(self):
        assets = load_custom_sound_menu_ui_assets()
        self.assertEqual(assets["manifest"]["sector_count"], 23)
        self.assertTrue(assets["manifest"]["installable"])
        self.assertEqual(
            [part["name"] for part in assets["parts"]],
            [
                "part-01-append", "part-02-size", "part-03-index",
                "part-04-prepare", "part-05-lookup", "part-06-publish",
            ],
        )
        self.assertEqual(
            [part["manifest"]["sector_count"] for part in assets["parts"]],
            [4, 4, 4, 6, 3, 2],
        )
        for part in assets["parts"]:
            self.assertNotIn("before", part)
            self.assertNotIn("after", part)
            self.assertTrue(part["reader"].startswith(b"\x7fELF"))
            self.assertTrue(part["writer"].startswith(b"\x7fELF"))

        # Simulate drifted timestamps on ext2 inode-size sectors and verify preservation.
        part2 = assets["parts"][1]
        from pathlib import Path
        research_dir = (
            Path(__file__).resolve().parents[2]
            / "output/运行/FL2-FL3/统一启动底座/unified-menu-ui"
        )
        if research_dir.is_dir():
            research = load_custom_sound_menu_ui_assets(research_dir)
            r_part2 = research["parts"][1]
            drifted_before = []
            for s in r_part2["before"]:
                b = bytearray(s)
                b[8:20] = b"\x55" * 12
                b[264:276] = b"\x66" * 12
                drifted_before.append(bytes(b))
            detail, after = plan_menu_ui_part(part2, drifted_before, "unlock")
            self.assertEqual(detail["state"], "disabled")
            self.assertGreater(detail["changed_bytes"], 0)
            for src, dst in zip(drifted_before, after):
                self.assertEqual(dst[8:20], b"\x55" * 12)
                self.assertEqual(dst[264:276], b"\x66" * 12)
                self.assertNotEqual(src, dst)
            detail_off, restored = plan_menu_ui_part(part2, after, "close")
            self.assertEqual(detail_off["state"], "enabled")
            self.assertEqual(restored, drifted_before)


if __name__ == "__main__":
    unittest.main()
