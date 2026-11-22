"""Execute the packaged custom-sound AArch64 payload against synthetic files."""
from __future__ import annotations

import io
import struct
import unittest

from a6700_unlock.audio_import import build_audio_import_plan
from a6700_unlock.audio_packages import inspect_pcm_wav
from a6700_unlock.custom_sound_native import (
    custom_sound_clear_pending_request, custom_sound_request,
    custom_sound_response, custom_sound_wipe_request,
    load_custom_sound_payload)
from a6700_unlock.custom_sound_records import build_slot_record

try:
    from elftools.elf.elffile import ELFFile
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_INTR
    from unicorn.arm64_const import (
        UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_X0, UC_ARM64_REG_X1,
        UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X30, UC_ARM64_REG_X8)
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


def pcm_wav():
    payload = b"\x01\x00" * 2
    return (b"RIFF" + (len(payload) + 36).to_bytes(4, "little") + b"WAVE" +
            b"fmt " + (16).to_bytes(4, "little") +
            b"\x01\x00\x01\x00\x80\xbb\x00\x00\x00\x77\x01\x00\x02\x00\x10\x00" +
            b"data" + len(payload).to_bytes(4, "little") + payload)


class Machine:
    def __init__(self, binary, request, files=None, failure=None, dirs=None, unknown_dir_types=False):
        self.request = bytearray(request)
        self.files = dict(files or {})
        self.dirs = {"/setting"} | set(dirs or ())
        for path in self.files:
            parent = path.rsplit("/", 1)[0]
            while parent and parent != "/":
                if parent.startswith("/setting/"):
                    self.dirs.add(parent)
                if "/" not in parent[1:]:
                    break
                parent = parent.rsplit("/", 1)[0]
        self.failure = failure
        self.unknown_dir_types = unknown_dir_types
        self.failed = False
        self.fds = {}
        self.next_fd = 3
        self.calls = []
        self.write_count = 0
        self.renames = []

        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        elf = ELFFile(io.BytesIO(binary))
        self.base = 0x100000
        segments = [segment for segment in elf.iter_segments()
                    if segment["p_type"] == "PT_LOAD"]
        end = max(segment["p_vaddr"] + segment["p_memsz"] for segment in segments)
        self.uc.mem_map(self.base, (end + 4095) // 4096 * 4096)
        for segment in segments:
            self.uc.mem_write(self.base + segment["p_vaddr"], segment.data())
        relocations = elf.get_section_by_name(".rela.dyn")
        for relocation in (relocations.iter_relocations() if relocations else []):
            if relocation["r_info_type"] != 1027:
                raise RuntimeError("Unexpected custom-sound relocation")
            self.uc.mem_write(
                self.base + relocation["r_offset"],
                struct.pack("<Q", self.base + relocation["r_addend"]))
        init = elf.get_section_by_name(".init_array")
        self.entry = int.from_bytes(
            self.uc.mem_read(self.base + init["sh_addr"], 8), "little")
        self.stop = 0x8000000
        self.uc.mem_map(self.stop, 4096)
        self.uc.mem_map(0x3000000, 0x200000)
        self.uc.reg_write(UC_ARM64_REG_SP, 0x31fff90)
        self.uc.reg_write(UC_ARM64_REG_X30, self.stop)
        self.uc.hook_add(UC_HOOK_INTR, self.syscall)

    def cstring(self, pointer):
        data = bytearray()
        for index in range(512):
            value = self.uc.mem_read(pointer + index, 1)
            if value == b"\0":
                return data.decode("ascii")
            data += value
        raise RuntimeError("Unbounded custom-sound path")

    def ret(self, value):
        self.uc.reg_write(UC_ARM64_REG_X0, value & ((1 << 64) - 1))

    def syscall(self, uc, intno, data):
        number = uc.reg_read(UC_ARM64_REG_X8)
        a, b, c, d = [uc.reg_read(register) for register in (
            UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3)]
        self.calls.append(number)
        if number == 34:
            path = self.cstring(b)
            if path in self.dirs:
                self.ret(-17)
            elif path.rsplit("/", 1)[0] not in self.dirs:
                self.ret(-2)
            else:
                self.dirs.add(path)
                self.ret(0)
        elif number == 35:
            raw_path = self.cstring(b)
            if a in self.fds and self.fds[a][0] == "directory":
                path = self.fds[a][1].rstrip("/") + "/" + raw_path
            else:
                path = raw_path
            if c & 0x200:
                if (self.failure == "rmdir" and path == "/setting/csnd"
                        and not self.failed):
                    self.failed = True
                    self.ret(-5)
                elif path not in self.dirs:
                    self.ret(-2)
                elif any(p.startswith(path + "/") for p in self.files) or any(
                        p.startswith(path + "/") for p in self.dirs):
                    self.ret(-39)
                else:
                    self.dirs.remove(path)
                    self.ret(0)
            elif (self.failure == "cleanup" and path.endswith(".bak")
                    and path in self.files and not self.failed):
                self.failed = True
                self.ret(-5)
            elif (self.failure == "unlink-pending" and ".shutter-autoload-" in path
                    and path in self.files and not self.failed):
                self.failed = True
                self.ret(-5)
            elif path in self.dirs:
                self.ret(-21)
            elif path not in self.files:
                self.ret(-2)
            else:
                del self.files[path]
                self.ret(0)
        elif number == 38:
            old_path = self.cstring(b)
            new_path = self.cstring(d)
            self.renames.append((old_path, new_path))
            if old_path not in self.files:
                self.ret(-2)
            elif (self.failure == "rename" and old_path.endswith(".tmp")
                  and new_path.endswith(".bin") and not self.failed):
                self.failed = True
                self.ret(-5)
            elif (self.failure in ("rollback", "readback-rollback")
                  and old_path.endswith(".bak")
                  and new_path.endswith(".bin")):
                self.failed = True
                self.ret(-5)
            else:
                self.files.pop(new_path, None)
                self.files[new_path] = self.files.pop(old_path)
                self.ret(0)
        elif number == 56:
            path = self.cstring(b)
            flags = c
            if path == "/tmp/jiritsu/tmmc.jit":
                if flags & 0o1000:
                    self.request = bytearray()
                item = ["result", 0]
            elif c & 0x8c000:
                if path not in self.dirs:
                    self.ret(-2)
                    return
                item = ["directory", path, 0]
            elif path in self.dirs:
                item = ["directory", path, 0]
            else:
                if path not in self.files:
                    if not (flags & 0o100) or path.rsplit("/", 1)[0] not in self.dirs:
                        self.ret(-2)
                        return
                    self.files[path] = bytearray()
                if flags & 0o1000:
                    self.files[path] = bytearray()
                item = ["file", path, 0]
            fd = self.next_fd
            self.next_fd += 1
            self.fds[fd] = item
            self.ret(fd)
        elif number == 57:
            del self.fds[a]
            self.ret(0)
        elif number == 61:
            item = self.fds[a]
            assert item[0] == "directory"
            dir_path = item[1]
            prefix = dir_path.rstrip("/") + "/"
            children = [("..", 4), (".", 4)]
            seen_names = set()
            for candidate in sorted(self.dirs):
                if candidate.startswith(prefix):
                    rest = candidate[len(prefix):]
                    if "/" not in rest and rest not in seen_names:
                        seen_names.add(rest)
                        children.append((rest, 0 if self.unknown_dir_types else 4))
            for candidate in sorted(self.files):
                if candidate.startswith(prefix):
                    rest = candidate[len(prefix):]
                    if "/" not in rest and rest not in seen_names:
                        seen_names.add(rest)
                        children.append((rest, 0 if self.unknown_dir_types else 8))
            idx = item[2]
            out = bytearray()
            ino = 1
            while idx < len(children):
                name, dtype = children[idx]
                encoded = name.encode("ascii") + b"\x00"
                reclen = (19 + len(encoded) + 7) & ~7
                if len(out) + reclen > c:
                    break
                out += struct.pack("<QqHB", ino, idx + 1, reclen, dtype)
                out += encoded + bytes(reclen - 19 - len(encoded))
                idx += 1
                ino += 1
            if not out and idx < len(children):
                self.ret(-22)
                return
            item[2] = idx
            uc.mem_write(b, bytes(out))
            self.ret(len(out))
        elif number in (63, 64):
            item = self.fds[a]
            if item[0] == "result":
                source, target, at = self.request, self.request, item[1]
            else:
                path = item[1]
                source = target = self.files[path]
                at = item[2]
            if number == 63:
                value = bytes(source[at:at + c])
                if (self.failure in ("readback", "readback-rollback")
                        and item[0] == "file"
                        and path.endswith(".bin") and not self.failed):
                    self.failed = True
                    value = (bytes([value[0] ^ 1]) + value[1:]) if value else b"BAD"
                uc.mem_write(b, value)
                item[-1] += len(value)
                self.ret(len(value))
            else:
                value = bytes(uc.mem_read(b, c))
                if (self.failure == "write" and item[0] == "file"
                        and path.endswith(".tmp") and not self.failed):
                    self.failed = True
                    self.ret(-5)
                    return
                if len(target) < at + c:
                    target.extend(bytes(at + c - len(target)))
                target[at:at + c] = value
                item[-1] += c
                if item[0] != "result":
                    self.write_count += 1
                self.ret(c)
        elif number == 82:
            item = self.fds[a]
            path = item[1] if item[0] != "result" else "/tmp/jiritsu/tmmc.jit"
            if (self.failure == "fsync" and item[0] == "file"
                    and path.endswith(".tmp") and not self.failed):
                self.failed = True
                self.ret(-5)
            else:
                self.ret(0)
        else:
            raise RuntimeError(f"Unexpected custom-sound syscall {number}")

    def run(self):
        self.uc.emu_start(self.entry, self.stop, count=10_000_000)
        if self.uc.reg_read(UC_ARM64_REG_PC) != self.stop:
            raise RuntimeError("Custom-sound payload did not finish")
        return bytes(self.request)


@unittest.skipUnless(AVAILABLE, "custom-sound native tests require unicorn and pyelftools")
class CustomSoundNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wav = pcm_wav()
        cls.candidate = inspect_pcm_wav(cls.wav, "camera-shutter.wav")
        cls.plan = build_audio_import_plan(
            cls.candidate, dict(type="local"), target_slot=1,
            capability_available=True, implementation_ready=True)
        cls.record = build_slot_record(cls.candidate, cls.wav, 1, 0x10000007)
        cls.nonce = bytes(range(16))
        cls.request = custom_sound_request(cls.plan, cls.record, cls.nonce)
        cls.payload = load_custom_sound_payload()

    def machine(self, *, files=None, request=None, failure=None, dirs=None, unknown_dir_types=False):
        machine = Machine(self.payload, request or self.request, files=files,
                          failure=failure, dirs=dirs, unknown_dir_types=unknown_dir_types)
        result = custom_sound_response(machine.run(), self.nonce)
        return result, machine

    def test_payload_publishes_new_slot_and_independently_reads_back(self):
        result, machine = self.machine()
        self.assertEqual(result["status"], 0)
        self.assertFalse(result["backup_cleanup_failed"])
        self.assertEqual(machine.files["/setting/csnd/slot1.bin"], self.record)
        self.assertNotIn("/setting/csnd/slot1.tmp", machine.files)
        self.assertNotIn("/setting/csnd/slot1.bak", machine.files)
        self.assertIn("/setting/csnd", machine.dirs)
        self.assertIn(34, machine.calls)
        self.assertNotIn(35, machine.calls)
        self.assertIn(38, machine.calls)
        self.assertIn(82, machine.calls)

    def test_payload_rotates_existing_primary_to_backup(self):
        old = build_slot_record(self.candidate, self.wav, 1, 0x10000006)
        result, machine = self.machine(files={
            "/setting/csnd/slot1.bin": old,
        })
        self.assertEqual(result["status"], 0)
        self.assertEqual(machine.files["/setting/csnd/slot1.bin"], self.record)
        self.assertNotIn("/setting/csnd/slot1.bak", machine.files)
        self.assertNotIn("/setting/csnd/slot1.tmp", machine.files)

    def test_guarded_replace_and_restore_require_exact_preimage(self):
        path = '/setting/csnd/slot1.bin'
        old = build_slot_record(self.candidate, self.wav, 1, 0x10000006)
        request = custom_sound_request(self.plan, self.record, self.nonce, expected=old)
        result, machine = self.machine(files={path: old}, request=request)
        self.assertEqual(result['status'], 0)
        self.assertEqual(machine.files[path], self.record)
        request = custom_sound_request(self.plan, old, self.nonce, expected=self.record)
        result, machine = self.machine(files={path: self.record}, request=request)
        self.assertEqual(result['status'], 0)
        self.assertEqual(machine.files[path], old)
        result, machine = self.machine(files={path: b'changed'}, request=request)
        self.assertEqual(result['status'], 10)
        self.assertEqual(machine.files[path], b'changed')
        self.assertEqual(machine.write_count, 0)
        self.assertFalse(machine.renames)

    def test_guarded_empty_import_and_restore_never_touch_other_slots(self):
        path = '/setting/csnd/slot1.bin'
        request = custom_sound_request(self.plan, self.record, self.nonce, expected=None)
        result, machine = self.machine(files={'/setting/csnd/slot10.bin': b'neighbor'}, request=request)
        self.assertEqual(result['status'], 0)
        files = dict(machine.files)
        request = custom_sound_request(self.plan, self.record, self.nonce, remove=True)
        result, machine = self.machine(files=files, request=request)
        self.assertEqual(result['status'], 0)
        self.assertNotIn(path, machine.files)
        self.assertEqual(machine.files['/setting/csnd/slot10.bin'], b'neighbor')
        result, machine = self.machine(files={path: b'changed'}, request=request)
        self.assertEqual(result['status'], 10)
        self.assertEqual(machine.files[path], b'changed')

    def test_interrupted_import_files_are_preserved_and_block_new_write(self):
        for suffix in ('tmp', 'bak'):
            files = {'/setting/csnd/slot1.bin': b'current',
                     '/setting/csnd/slot1.' + suffix: b'recovery-data'}
            result, machine = self.machine(files=files)
            self.assertEqual(result['status'], 9)
            self.assertEqual(machine.files, files)
            self.assertEqual(machine.write_count, 0)
            self.assertFalse(machine.renames)
            self.assertNotIn(35, machine.calls)

    def test_successful_readback_reports_backup_cleanup_failure(self):
        old = build_slot_record(self.candidate, self.wav, 1, 0x10000006)
        result, machine = self.machine(files={
            "/setting/csnd/slot1.bin": old,
        }, failure="cleanup")
        self.assertEqual(result["status"], 0)
        self.assertTrue(result["backup_cleanup_failed"])
        self.assertEqual(machine.files["/setting/csnd/slot1.bin"], self.record)
        self.assertEqual(machine.files["/setting/csnd/slot1.bak"], old)

    def test_payload_publishes_slot_ten_path(self):
        plan = build_audio_import_plan(
            self.candidate, dict(type="local"), target_slot=10,
            capability_available=True, implementation_ready=True)
        record = build_slot_record(self.candidate, self.wav, 10, 0xA0000001)
        request = custom_sound_request(plan, record, self.nonce)
        result, machine = self.machine(request=request)
        self.assertEqual(result["status"], 0)
        self.assertEqual(machine.files["/setting/csnd/slot10.bin"], record)
        self.assertNotIn("/setting/csnd/slot10.tmp", machine.files)

    def test_forged_record_identity_is_rejected_without_file_changes(self):
        request = bytearray(self.request)
        request[80] = 2
        result, machine = self.machine(request=bytes(request))
        self.assertEqual(result["status"], 2)
        self.assertNotIn("/setting/csnd/slot1.bin", machine.files)
        self.assertEqual(machine.write_count, 0)

    def test_write_and_fsync_failures_preserve_old_primary(self):
        old = build_slot_record(self.candidate, self.wav, 1, 0x10000006)
        for failure in ("write", "fsync"):
            with self.subTest(failure=failure):
                result, machine = self.machine(
                    files={"/setting/csnd/slot1.bin": old}, failure=failure)
                self.assertEqual(result["status"], 4)
                self.assertEqual(machine.files["/setting/csnd/slot1.bin"], old)
                self.assertNotIn("/setting/csnd/slot1.tmp", machine.files)

    def test_rename_failure_rolls_back_old_primary(self):
        old = build_slot_record(self.candidate, self.wav, 1, 0x10000006)
        result, machine = self.machine(
            files={"/setting/csnd/slot1.bin": old}, failure="rename")
        self.assertEqual(result["status"], 5)
        self.assertEqual(machine.files["/setting/csnd/slot1.bin"], old)
        self.assertNotIn("/setting/csnd/slot1.tmp", machine.files)

    def test_readback_mismatch_rolls_back_old_primary(self):
        old = build_slot_record(self.candidate, self.wav, 1, 0x10000006)
        result, machine = self.machine(
            files={"/setting/csnd/slot1.bin": old}, failure="readback")
        self.assertEqual(result["status"], 6)
        self.assertEqual(machine.files["/setting/csnd/slot1.bin"], old)
        self.assertNotIn("/setting/csnd/slot1.tmp", machine.files)

    def test_incomplete_rollback_is_explicit(self):
        old = build_slot_record(self.candidate, self.wav, 1, 0x10000006)
        result, machine = self.machine(
            files={"/setting/csnd/slot1.bin": old}, failure="readback-rollback")
        self.assertEqual(result["status"], 8)
        self.assertFalse(result["rolled_back"])
        self.assertEqual(machine.files["/setting/csnd/slot1.bak"], old)

    def test_wipe_removes_all_csnd_files_subdirs_and_directory(self):
        request = custom_sound_wipe_request(self.nonce)
        files = {
            "/setting/csnd/slot1.bin": self.record,
            "/setting/csnd/slot10.tmp": b"stale",
            "/setting/csnd/.shutter-autoload-pending": b"",
            "/setting/csnd/state": b"state-data",
            "/setting/csnd/nested/extra.bin": b"nested",
            "/setting/other/keep.bin": b"untouched",
        }
        result, machine = self.machine(
            files=files, request=request, dirs={"/setting/csnd/empty_sub"})
        self.assertEqual(result["status"], 0)
        self.assertEqual(machine.files, {"/setting/other/keep.bin": b"untouched"})
        self.assertNotIn("/setting/csnd", machine.dirs)
        self.assertNotIn("/setting/csnd/nested", machine.dirs)
        self.assertNotIn("/setting/csnd/empty_sub", machine.dirs)

    def test_wipe_handles_dt_unknown_subdirs_and_is_idempotent_when_absent(self):
        request = custom_sound_wipe_request(self.nonce)
        result, machine = self.machine(
            files={"/setting/csnd/slot1.bin": self.record, "/setting/csnd/sub/f": b"x"},
            request=request, unknown_dir_types=True)
        self.assertEqual(result["status"], 0)
        self.assertEqual(machine.files, {})
        self.assertNotIn("/setting/csnd", machine.dirs)

        result2, machine2 = self.machine(files={}, request=request)
        self.assertEqual(result2["status"], 0)
        self.assertNotIn("/setting/csnd", machine2.dirs)

    def test_wipe_reports_failure_and_rejects_malformed_requests(self):
        request = custom_sound_wipe_request(self.nonce)
        result, machine = self.machine(
            files={"/setting/csnd/slot1.bin": self.record}, request=request, failure="rmdir")
        self.assertEqual(result["status"], 8)
        self.assertIn("/setting/csnd", machine.dirs)

        bad = bytearray(request)
        struct.pack_into("<I", bad, 56, 1)
        result_bad, _ = self.machine(files={"/setting/csnd/slot1.bin": self.record}, request=bytes(bad))
        self.assertEqual(result_bad["status"], 2)

    def test_clear_pending_removes_lock_markers_only_and_preserves_slots(self):
        request = custom_sound_clear_pending_request(self.nonce)
        files = {
            "/setting/csnd/slot1.bin": self.record,
            "/setting/csnd/slot3.bin": self.record,
            "/setting/csnd/state": b"state-data",
            "/setting/csnd/.shutter-autoload-pending": b"",
            "/setting/csnd/.shutter-autoload-quarantine": b"",
            "/setting/other/keep.bin": b"untouched",
        }
        result, machine = self.machine(files=files, request=request)
        self.assertEqual(result["status"], 0)
        self.assertTrue(result["markers_cleared"])
        self.assertEqual(machine.files, {
            "/setting/csnd/slot1.bin": self.record,
            "/setting/csnd/slot3.bin": self.record,
            "/setting/csnd/state": b"state-data",
            "/setting/other/keep.bin": b"untouched",
        })
        self.assertIn("/setting/csnd", machine.dirs)

        # Idempotent when markers or /setting/csnd are already absent.
        result2, machine2 = self.machine(files={"/setting/csnd/slot1.bin": self.record}, request=request)
        self.assertEqual(result2["status"], 0)
        self.assertFalse(result2["markers_cleared"])
        self.assertEqual(machine2.files, {"/setting/csnd/slot1.bin": self.record})

        result3, _ = self.machine(files={}, request=request)
        self.assertEqual(result3["status"], 0)
        self.assertFalse(result3["markers_cleared"])

        failed, failed_machine = self.machine(
            files={"/setting/csnd/slot1.bin": self.record, "/setting/csnd/.shutter-autoload-pending": b""},
            request=request, failure="unlink-pending")
        self.assertEqual(failed["status"], 8)
        self.assertEqual(failed_machine.files["/setting/csnd/slot1.bin"], self.record)

        bad = bytearray(request)
        struct.pack_into("<I", bad, 56, 1)
        result_bad, _ = self.machine(files=files, request=bytes(bad))
        self.assertEqual(result_bad["status"], 2)


if __name__ == "__main__":
    unittest.main()
