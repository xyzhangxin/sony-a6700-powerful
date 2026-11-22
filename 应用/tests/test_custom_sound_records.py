import hashlib
import struct
import unittest

from a6700_unlock.audio_packages import inspect_pcm_wav
from a6700_unlock.custom_sound_slots import slot_inventory
from a6700_unlock.custom_sound_records import (
    HEADER_BYTES, CustomSoundRecordError, build_selection_record,
    build_slot_record, decode_selection_record, decode_slot_record)


def pcm_wav():
    payload = b"\x01\x00" * 2
    return (b"RIFF" + (len(payload) + 36).to_bytes(4, "little") + b"WAVE" +
            b"fmt " + (16).to_bytes(4, "little") +
            b"\x01\x00\x01\x00\x80\xbb\x00\x00\x00\x77\x01\x00\x02\x00\x10\x00" +
            b"data" + len(payload).to_bytes(4, "little") + payload)


class CustomSoundRecordTests(unittest.TestCase):
    def setUp(self):
        self.wav = pcm_wav()
        self.candidate = inspect_pcm_wav(self.wav, "camera-shutter.wav")
        self.generation = 0x10000007

    def test_slot_record_roundtrip_and_binary_layout(self):
        record = build_slot_record(self.candidate, self.wav, 1, self.generation)
        decoded = decode_slot_record(record, expected_slot=1,
                                     expected_generation=self.generation)
        self.assertEqual(decoded["filename"], "camera-shutter.wav")
        self.assertEqual(decoded["wav"], self.wav)
        self.assertEqual(decoded["wav_sha256"], hashlib.sha256(self.wav).hexdigest())
        self.assertEqual(len(record), HEADER_BYTES + len("camera-shutter.wav") + len(self.wav))
        self.assertEqual(struct.unpack_from("<I", record, 28)[0], 2)
        self.assertEqual(struct.unpack_from("<I", record, 24)[0], len(record))

    def test_slot_ten_record_is_supported(self):
        generation = 0xA0000001
        record = build_slot_record(self.candidate, self.wav, 10, generation)
        decoded = decode_slot_record(record, expected_slot=10,
                                     expected_generation=generation)
        self.assertEqual(decoded["slot"], 10)
        self.assertEqual(decoded["generation"], generation)

    def test_inventory_preserves_numbers_and_distinguishes_unread_from_empty(self):
        record = build_slot_record(self.candidate, self.wav, 10, 0xA0000001)
        rows = slot_inventory({1: None, 10: record})
        self.assertEqual([r["slot"] for r in rows], list(range(1, 11)))
        self.assertEqual(rows[0]["label"], "1 [空]")
        self.assertEqual(rows[1]["label"], "2 [未读取]")
        self.assertEqual(rows[9]["label"], "10 camera-shutter.wav")
        self.assertTrue(rows[9]["playable"])
        self.assertFalse(rows[0]["playable"])
        self.assertEqual(slot_inventory({9: record})[8]["status"], "invalid")
        for slot in (0, 11, True):
            with self.assertRaises(ValueError):
                slot_inventory({slot: None})

    def test_default_cannot_be_written_as_an_audio_slot(self):
        with self.assertRaises(CustomSoundRecordError):
            build_slot_record(self.candidate, self.wav, 0, self.generation)

    def test_selection_record_is_header_only_and_valid(self):
        for selection in (0, 1, 2):
            record = build_selection_record(selection)
            self.assertEqual(len(record), HEADER_BYTES)
            self.assertEqual(decode_selection_record(record), selection)
        self.assertEqual(struct.unpack_from("<I", record, 28)[0], 1)

    def test_slot_identity_and_candidate_hash_are_guarded(self):
        with self.assertRaises(CustomSoundRecordError):
            build_slot_record(self.candidate, self.wav, 3, self.generation)
        with self.assertRaises(CustomSoundRecordError):
            build_slot_record(self.candidate, self.wav, 1, 0x20000007)
        with self.assertRaises(CustomSoundRecordError):
            build_slot_record(self.candidate, b"not-the-wav", 1, self.generation)
        with self.assertRaises(CustomSoundRecordError):
            build_slot_record(dict(self.candidate, filename="../bad.wav"),
                              self.wav, 1, self.generation)

    def test_slot_record_tampering_is_rejected(self):
        record = bytearray(build_slot_record(self.candidate, self.wav, 1, self.generation))
        record[HEADER_BYTES + len("camera-shutter.wav")] ^= 1
        # Repair CRC so this specifically proves the WAV SHA-256 guard.
        from a6700_unlock.custom_sound_records import _record_crc
        struct.pack_into("<I", record, 64, _record_crc(record))
        with self.assertRaises(CustomSoundRecordError):
            decode_slot_record(bytes(record))

        record = bytearray(build_slot_record(self.candidate, self.wav, 1, self.generation))
        record[12] ^= 1
        with self.assertRaises(CustomSoundRecordError):
            decode_slot_record(bytes(record))

    def test_cross_slot_record_is_rejected(self):
        record = build_slot_record(self.candidate, self.wav, 1, self.generation)
        with self.assertRaises(CustomSoundRecordError):
            decode_slot_record(record, expected_slot=2)
        with self.assertRaises(CustomSoundRecordError):
            decode_slot_record(record, expected_generation=0x10000008)

    def test_utf8_chinese_and_extensionless_slot_names_roundtrip(self):
        for name in ("清脆快门", "我的快门声 5", "qing-cui-dan-yin"):
            candidate = inspect_pcm_wav(self.wav, name)
            record = build_slot_record(candidate, self.wav, 5, 0x50000001)
            decoded = decode_slot_record(record, expected_slot=5,
                                         expected_generation=0x50000001)
            self.assertEqual(decoded["filename"], name)
            self.assertEqual(
                len(record),
                HEADER_BYTES + len(name.encode("utf-8")) + len(self.wav))
        for bad in ("", ".leading", " leading", "a..b", "bad/name", "a\x00b",
                    "\ud800", "\u0080", "\u202e", "中" * 86):
            with self.subTest(bad=repr(bad)), self.assertRaises(CustomSoundRecordError):
                build_slot_record(dict(self.candidate, filename=bad),
                                  self.wav, 1, self.generation)


if __name__ == "__main__":
    unittest.main()
