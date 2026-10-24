from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from a6700_unlock.catalog import Catalog, CatalogError, load_catalog
from a6700_unlock.cli import main
from a6700_unlock.planner import InputError, LocalImage, analyze


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fixture():
    # All bytes are synthetic. Two features share a sector, one spans a sector
    # boundary, and a canonical-hidden field accepts a known legacy open value.
    images = {"app": bytes(1536), "wbi": bytes(1024)}
    data = {
        "schema_version": 1, "catalog_id": "synthetic", "bundle_version": "1",
        "model": "synthetic", "firmware": "1", "sector_size": 512,
        "scope": "offline-known-snapshot-only", "device_write_supported": False,
        "features": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
        "images": {key: {"size": len(value), "normalized_sha256": digest(value), "view": "synthetic"}
                   for key, value in images.items()},
        "edits": [
            dict(id="a-app", image="app", offset=10, disabled="0000", enabled="0100",
                 known_values=["0000", "0100"], feature="a", purpose="A menu"),
            dict(id="b-app", image="app", offset=15, disabled="00", enabled="02",
                 known_values=["00", "02"], feature="b", purpose="B menu"),
            dict(id="cross", image="app", offset=511, disabled="000000", enabled="010203",
                 known_values=["000000", "010203"], feature="b", purpose="boundary"),
            dict(id="a-wbi", image="wbi", offset=12, disabled="00", enabled="01",
                 known_values=["00", "01"], feature="a", purpose="A snapshot"),
            dict(id="neutral", image="wbi", offset=30, disabled="00", enabled="00",
                 known_values=["00", "01"], feature="a", purpose="hide duplicate"),
        ],
    }
    return data, images


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.data, self.originals = fixture()
        self.catalog = Catalog.from_dict(self.data)
        self.paths = {key: self.directory / (key + ".img") for key in self.originals}
        self.put(self.originals)

    def put(self, images):
        for key, value in images.items():
            self.paths[key].write_bytes(value)

    def apply_to_copy(self, report):
        # Test helper only; production package deliberately has no apply backend.
        result = {key: bytearray(path.read_bytes()) for key, path in self.paths.items()}
        for edit in report["changes"]:
            image, at = edit["image"], edit["offset"]
            self.assertEqual(result[image][at:at + edit["length"]].hex(), edit["before"])
            result[image][at:at + edit["length"]] = bytes.fromhex(edit["after"])
        for image, value in result.items():
            self.assertEqual(digest(value), report["images"][image]["projected_sha256"])
        return result

    def test_unlock_then_close_roundtrip_and_idempotence(self):
        original = {k: p.read_bytes() for k, p in self.paths.items()}
        first = analyze(self.catalog, self.paths, "unlock")
        self.assertEqual(first["status"], "ready")
        self.assertEqual(first["bundle_state"], "disabled")
        self.assertEqual(first["changed_bytes"], 6)
        self.assertFalse(first["device_access"])
        self.assertFalse(first["executable_on_device"])
        self.assertEqual(original, {k: p.read_bytes() for k, p in self.paths.items()})
        installed = self.apply_to_copy(first)
        self.put(installed)
        repeated = analyze(self.catalog, self.paths, "unlock")
        self.assertEqual((repeated["status"], repeated["bundle_state"]), ("no-op", "enabled"))
        closing = analyze(self.catalog, self.paths, "close")
        restored = self.apply_to_copy(closing)
        self.assertEqual(restored, self.originals)
        self.put(restored)
        self.assertEqual(analyze(self.catalog, self.paths, "close")["status"], "no-op")

    def test_offline_plan_preserves_disabled_release_fields(self):
        self.data['features'][1].update(available=False, unavailable_reason='not ready')
        catalog = Catalog.from_dict(self.data)
        for action in ('unlock', 'close'):
            plan = analyze(catalog, self.paths, action)
            self.assertTrue(plan['compatible'])
            self.assertFalse(any(c['feature'] == 'b' for c in plan['changes']))

    def test_partial_feature_only_completes_missing_fields(self):
        app = bytearray(self.originals["app"])
        app[10] = 1
        self.paths["app"].write_bytes(app)
        result = analyze(self.catalog, self.paths, "unlock")
        self.assertEqual(result["bundle_state"], "partial")
        self.assertNotIn("a-app", [c["edit_id"] for c in result["changes"]])
        self.assertIn("a-wbi", [c["edit_id"] for c in result["changes"]])

    def test_known_legacy_duplicate_is_closed_for_both_actions(self):
        wbi = bytearray(self.originals["wbi"])
        wbi[30] = 1
        self.paths["wbi"].write_bytes(wbi)
        for action in ("unlock", "close"):
            result = analyze(self.catalog, self.paths, action)
            change = next(c for c in result["changes"] if c["edit_id"] == "neutral")
            self.assertEqual((change["state_before"], change["after"]), ("legacy", "00"))

    def test_sector_merge_and_boundary(self):
        result = analyze(self.catalog, self.paths, "unlock")
        app_sectors = [s for s in result["sectors"] if s["image"] == "app"]
        self.assertEqual(len(app_sectors), 2)
        self.assertEqual(set(app_sectors[0]["edit_ids"]), {"a-app", "b-app", "cross"})
        self.assertEqual(app_sectors[1]["edit_ids"], ["cross"])
        outputs = self.apply_to_copy(result)
        for sector in result["sectors"]:
            at, image = sector["file_offset"], sector["image"]
            self.assertEqual(sector["after_sha256"], digest(outputs[image][at:at + sector["length"]]))
        changed = {(c["image"], c["offset"] + i) for c in result["changes"]
                   for i, (a, b) in enumerate(zip(bytes.fromhex(c["before"]), bytes.fromhex(c["after"]))) if a != b}
        for key, original in self.originals.items():
            self.assertEqual({(key, i) for i, (a, b) in enumerate(zip(original, outputs[key])) if a != b},
                             {v for v in changed if v[0] == key})

    def test_unknown_managed_value_blocks_all_changes(self):
        app = bytearray(self.originals["app"])
        app[10] = 3
        self.paths["app"].write_bytes(app)
        result = analyze(self.catalog, self.paths, "unlock")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["changes"], [])
        self.assertEqual(result["sectors"], [])
        self.assertEqual(result["bundle_state"], "unknown")
        self.assertIn("unknown-value", [x["kind"] for x in result["issues"]])

    def test_unknown_unmanaged_content_blocks_otherwise_known_fields(self):
        for key in self.paths:
            with self.subTest(image=key):
                self.put(self.originals)
                data = bytearray(self.originals[key])
                data[100] = 7
                self.paths[key].write_bytes(data)
                result = analyze(self.catalog, self.paths, "close")
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["changes"], [])
                self.assertEqual(result["issues"][0]["kind"], "identity-mismatch")

    def test_partial_counter_value_is_not_a_known_field_state(self):
        app = bytearray(self.originals["app"])
        app[511] = 1  # one byte of a three-byte field is insufficient
        self.paths["app"].write_bytes(app)
        self.assertEqual(analyze(self.catalog, self.paths, "unlock")["status"], "blocked")

    def test_truncation_missing_image_and_unsupported_action(self):
        self.paths["app"].write_bytes(b"short")
        with self.assertRaises(InputError):
            analyze(self.catalog, self.paths)
        with self.assertRaises(InputError):
            analyze(self.catalog, {"app": self.paths["app"]})
        with self.assertRaises(InputError):
            analyze(self.catalog, self.paths, "flash")

    def test_symlink_and_fifo_refused_without_opening(self):
        alias = self.directory / "alias"
        try:
            alias.symlink_to(self.paths["app"])
        except OSError:
            self.skipTest("creating symlinks needs Windows developer mode")
        with self.assertRaises(InputError):
            with LocalImage(alias, 1536):
                self.fail("symlink accepted")
        if hasattr(os, "mkfifo"):
            fifo = self.directory / "fifo"
            os.mkfifo(fifo)
            with self.assertRaises(InputError):
                with LocalImage(fifo, 1536):
                    self.fail("FIFO accepted")

    def test_input_mutation_and_path_replacement_detected(self):
        with LocalImage(self.paths["app"], 1536) as image:
            # Cross one NTFS timestamp tick: mutation inside the same tick as
            # the open would legitimately keep identical mtime values.
            time.sleep(0.02)
            with self.paths["app"].open("r+b") as f:
                f.seek(100)
                f.write(b"x")
            with self.assertRaises(InputError):
                image.assert_unchanged()
        self.put(self.originals)
        with LocalImage(self.paths["app"], 1536) as image:
            replacement = self.directory / "replacement"
            replacement.write_bytes(self.originals["app"])
            try:
                replacement.replace(self.paths["app"])
            except PermissionError:
                # Windows refuses to replace a file we hold open with a
                # mapping: the swap cannot happen, which enforces the same
                # no-replacement guarantee the identity guard checks below.
                return
            with self.assertRaises(InputError):
                image.assert_unchanged()

    def test_v2_extension_skips_v1_and_adds_only_new_feature(self):
        installed = self.apply_to_copy(analyze(self.catalog, self.paths, "unlock"))
        self.put(installed)
        v2 = deepcopy(self.data)
        v2["bundle_version"] = "2"
        v2["features"].append({"id": "c", "name": "C"})
        v2["edits"].append(dict(id="c-app", image="app", offset=900, disabled="00", enabled="01",
                                known_values=["00", "01"], feature="c", purpose="v2 addition"))
        result = analyze(Catalog.from_dict(v2), self.paths, "unlock")
        self.assertEqual([c["edit_id"] for c in result["changes"]], ["c-app"])
        self.assertEqual(result["changed_bytes"], 1)
        self.assertEqual(analyze(self.catalog, self.paths, "unlock")["status"], "no-op")
        self.put(self.apply_to_copy(result))
        self.assertEqual(analyze(self.catalog, self.paths, "unlock")["status"], "blocked")
        self.assertEqual(analyze(Catalog.from_dict(v2), self.paths, "unlock")["status"], "no-op")

    def test_known_v1_value_migrates_to_revised_v2_implementation(self):
        self.put(self.apply_to_copy(analyze(self.catalog, self.paths, "unlock")))
        v2 = deepcopy(self.data)
        v2["bundle_version"] = "2"
        v2["edits"][0].update(enabled="0200", known_values=["0000", "0100", "0200"])
        result = analyze(Catalog.from_dict(v2), self.paths, "unlock")
        self.assertEqual(len(result["changes"]), 1)
        self.assertEqual((result["changes"][0]["state_before"], result["changes"][0]["after"]),
                         ("legacy", "0200"))
        self.put(self.apply_to_copy(result))
        self.assertEqual(analyze(Catalog.from_dict(v2), self.paths, "unlock")["status"], "no-op")

    def test_cli_output_is_non_overwriting_and_blocked_exit_is_nonzero(self):
        output = self.directory / "report.json"
        args = ["plan", "--app", str(self.paths["app"]), "--wbi", str(self.paths["wbi"]),
                "--action", "unlock", "--output", str(output)]
        with patch("a6700_unlock.cli.load_catalog", return_value=self.catalog):
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(main(args), 0)
            self.assertEqual(json.loads(output.read_text())["status"], "ready")
            first = output.read_bytes()
            with patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(main(args), 2)
            self.assertEqual(output.read_bytes(), first)
            with patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(main(args[:-1] + [str(self.paths["app"])]), 2)
            app = bytearray(self.originals["app"])
            app[10] = 99
            self.paths["app"].write_bytes(app)
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(main(args[:-2]), 2)

    def test_catalog_digest_is_deterministic(self):
        self.assertEqual(self.catalog.digest, Catalog.from_dict(deepcopy(self.data)).digest)
        different = deepcopy(self.data)
        different["edits"][0]["purpose"] = "changed"
        self.assertNotEqual(self.catalog.digest, Catalog.from_dict(different).digest)

    def test_invalid_catalogs_rejected(self):
        mutations = [
            lambda d: d.update(schema_version=2),
            lambda d: d.update(device_write_supported=True),
            lambda d: d["edits"][1].update(offset=11),
            lambda d: d["edits"][1].update(offset=-1),
            lambda d: d["edits"][1].update(offset=99999),
            lambda d: d["edits"][1].update(enabled="0102"),
            lambda d: d["edits"][1].update(known_values=["00"]),
            lambda d: d["edits"][1].update(known_values=["00", "00", "02"]),
            lambda d: d["edits"][1].update(id="a-app"),
            lambda d: d["edits"][1].update(feature="missing"),
            lambda d: d["features"].append(d["features"][0]),
            lambda d: d["features"].append({"id": "empty", "name": "Empty"}),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                data = deepcopy(self.data)
                mutation(data)
                with self.assertRaises(CatalogError):
                    Catalog.from_dict(data)

    def test_real_catalog_loads_with_unified_features_and_complete_definitions(self):
        catalog = load_catalog()
        self.assertEqual({f["id"] for f in catalog.data["features"]},
                         {"ftp", "shutter", "exposure", "sound", "pixel_shift", "dynamic_active"})
        self.assertEqual(len(catalog.edits), 355)
        self.assertEqual(catalog.data["historical_app_only_parent_permission_bytes"], 12)
        self.assertEqual(catalog.summary()["images"]["app"]["changed_bytes"], 122)
        self.assertEqual(catalog.summary()["images"]["wbi"]["changed_bytes"], 228)
        self.assertEqual(catalog.summary()["images"]["cpapp"]["changed_bytes"], 1)
        sound = next(f for f in catalog.data["features"] if f["id"] == "sound")
        self.assertNotIn("requires", sound)


if __name__ == "__main__":
    unittest.main()
