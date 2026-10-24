"""Guard volatile overlay: ext2 inode time fields inside guard sectors.

The camera refreshes atime/ctime/mtime of its /usr inode table on its own,
so guard sectors overlapping the inode table must compare with those fields
zeroed. Everything else in the sector stays pinned byte-for-byte.
"""
import hashlib
import unittest
from unittest.mock import patch

from test_operations import sparse_fixture
from a6700_unlock import device_profile
from a6700_unlock.catalog import load_catalog
from a6700_unlock.device_profile import (guard_volatile_overlay, inspect_sectors,
                                         load_device_profile)


def overlay_for(profile, index, normalized):
    return dict(schema=1, kind="guard-volatile-overlay",
                profile_sha256=profile.digest,
                inode_size=256, volatile_fields=[[8, 20]],
                records={str(index): dict(device=profile.entries[index]["device"],
                                          device_offset=profile.entries[index]["device_offset"],
                                          normalized_sha256=normalized)})


class GuardVolatileTests(unittest.TestCase):
    def setUp(self):
        self.profile, self.sectors = sparse_fixture()
        # sparse_fixture pins guard index 3 to an all-zero sector.
        self.guard_index = 3
        self.normalized = hashlib.sha256(bytes(512)).hexdigest()

    def inspect(self, sectors):
        return inspect_sectors(self.profile, sectors, "inspect")[0]

    def test_camera_refreshed_inode_times_no_longer_block(self):
        drifted = bytearray(self.sectors[self.guard_index])
        drifted[8:20] = b"\x11" * 12
        drifted[264:276] = b"\x22" * 12
        sectors = list(self.sectors)
        sectors[self.guard_index] = bytes(drifted)
        without = self.inspect(sectors)
        self.assertFalse(without["compatible"])
        self.assertEqual(without["guard_volatile"], [])
        with patch.object(device_profile, "_guard_volatile_data",
                          overlay_for(self.profile, self.guard_index, self.normalized)):
            with_overlay = self.inspect(sectors)
        self.assertTrue(with_overlay["compatible"])
        self.assertEqual(with_overlay["guard_volatile"], [self.guard_index])

    def test_overlay_never_leaks_into_write_targets(self):
        """Normalization must stay hash-local.

        A parallel Windows port shipped a variant (0.3.1) where the zeroed
        sectors leaked into the write plan and every unlock/close failed the
        camera-side preimage check; pin that this implementation compares on
        a copy and keeps raw bytes in both the plan and the write targets.
        """
        drifted = bytearray(self.sectors[self.guard_index])
        drifted[8:20] = b"\x11" * 12
        sectors = list(self.sectors)
        sectors[self.guard_index] = bytes(drifted)
        with patch.object(device_profile, "_guard_volatile_data",
                          overlay_for(self.profile, self.guard_index, self.normalized)):
            plan, after = inspect_sectors(self.profile, sectors, "unlock")
        self.assertTrue(plan["compatible"])
        self.assertEqual(after[self.guard_index], sectors[self.guard_index])
        self.assertNotIn(self.guard_index, plan["changed_indices"])
        for index in plan["changed_indices"]:
            self.assertEqual(self.profile.entries[index]["role"], "target")

    def test_other_bytes_stay_pinned_under_overlay(self):
        drifted = bytearray(self.sectors[self.guard_index])
        drifted[100] = 7
        sectors = list(self.sectors)
        sectors[self.guard_index] = bytes(drifted)
        with patch.object(device_profile, "_guard_volatile_data",
                          overlay_for(self.profile, self.guard_index, self.normalized)):
            result = self.inspect(sectors)
        self.assertFalse(result["compatible"])

    def test_overlay_binds_to_exact_profile_digest(self):
        drifted = bytearray(self.sectors[self.guard_index])
        drifted[8:20] = b"\x11" * 12
        sectors = list(self.sectors)
        sectors[self.guard_index] = bytes(drifted)
        data = overlay_for(self.profile, self.guard_index, self.normalized)
        data["profile_sha256"] = "0" * 64
        with patch.object(device_profile, "_guard_volatile_data", data):
            result = self.inspect(sectors)
        self.assertFalse(result["compatible"])
        self.assertEqual(result["guard_volatile"], [])

    def test_record_mismatch_against_profile_is_rejected(self):
        data = overlay_for(self.profile, self.guard_index, self.normalized)
        data["records"]["3"]["device_offset"] = 4242
        with patch.object(device_profile, "_guard_volatile_data", data):
            with self.assertRaises(device_profile.CatalogError):
                guard_volatile_overlay(self.profile)

    def test_shipped_overlay_binds_to_shipped_profile(self):
        """The shipped data file must match the shipped profile exactly."""
        import json
        from importlib.resources import files
        raw = json.loads(files("a6700_unlock").joinpath("data/guard-volatile.json")
                         .read_text(encoding="utf-8"))
        profile = load_device_profile(load_catalog())
        self.assertEqual(raw["profile_sha256"], profile.digest)
        records = guard_volatile_overlay(profile)
        self.assertEqual(sorted(records), [9, 14, 15, 17, 18, 29, 39, 40, 50])
        for key, record in raw["records"].items():
            entry = profile.entries[int(key)]
            self.assertEqual(entry["role"], "guard")
            self.assertEqual((entry["device"], entry["device_offset"]),
                             (record["device"], record["device_offset"]))
            self.assertEqual(records[int(key)]["normalized_sha256"],
                             record["normalized_sha256"])


if __name__ == "__main__":
    unittest.main()
