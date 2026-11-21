import unittest
import hashlib
import io
import zipfile

from a6700_unlock.audio_packages import (
    AUDIO_INDEX_SCHEMA, AUDIO_PACKAGE_SCHEMA, CAMERA_CLIP_FRAMES,
    CAMERA_SAMPLE_RATE, AudioPackageError, _digest, build_audio_bundle,
    build_audio_package, inspect_pcm_wav, normalize_camera_clip, select_sound,
    validate_audio_bundle, validate_audio_index, validate_audio_package)


def pcm_wav():
    payload = b"\x00\x00" * 2
    return (b"RIFF" + (len(payload) + 36).to_bytes(4, "little") + b"WAVE" +
            b"fmt " + (16).to_bytes(4, "little") +
            b"\x01\x00\x01\x00\x80\xbb\x00\x00\x00\x77\x01\x00\x02\x00\x10\x00" +
            b"data" + len(payload).to_bytes(4, "little") + payload)


def sound(**overrides):
    value = dict(
        sound_id="camera-shutter", title="Camera Shutter",
        description="A short mechanical shutter replacement.", filename="camera-shutter.wav",
        bytes=524288, sha256="a" * 64, duration_ms=2000, sample_rate=48000,
        channels=1, bit_depth=16, codec="pcm-wav", tags=["shutter", "short"])
    value.update(overrides)
    return value


def package(**overrides):
    body = dict(
        schema=AUDIO_PACKAGE_SCHEMA, package_id="community-shutters", version="1.0.0",
        release_sequence=1, min_core_api=1, kind="audio-asset",
        title="Community shutters", description="Community shutter sounds.",
        author="Morris", license="CC-BY-4.0", sounds=[sound()])
    value = dict(body)
    value.update(overrides)
    value = dict(value, digest=_digest(value))
    return value


def index(**overrides):
    body = dict(schema=AUDIO_INDEX_SCHEMA, index_id="community-audio", version="1.0.0",
                release_sequence=1, min_core_api=1, title="Community audio",
                packages=[package()])
    value = dict(body, digest=_digest(body))
    value.update(overrides)
    return value


def resign_package(value):
    body = dict(value)
    body.pop("digest")
    return dict(body, digest=_digest(body))


def wav_manifest():
    import hashlib
    data = pcm_wav()
    return package(sounds=[sound(
        bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
        duration_ms=1, sample_rate=48000, channels=1, bit_depth=16)])


def audio_bundle(manifest=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("package.json", __import__("json").dumps(
            manifest or wav_manifest(), sort_keys=True, separators=(",", ":")))
        archive.writestr("camera-shutter.wav", pcm_wav())
    return output.getvalue()


class AudioPackageTests(unittest.TestCase):
    def test_valid_data_only_package_and_index(self):
        result = validate_audio_package(package())
        self.assertEqual(result["kind"], "audio-asset")
        self.assertEqual(validate_audio_index(index())["schema"], AUDIO_INDEX_SCHEMA)

    def test_select_sound_returns_immutable_metadata(self):
        selected = select_sound(index(), "community-shutters", "camera-shutter")
        self.assertEqual(selected["package_version"], "1.0.0")
        self.assertEqual(selected["sha256"], "a" * 64)
        with self.assertRaises(AudioPackageError):
            select_sound(index(), "community-shutters", "missing")

    def test_digest_and_tampering_are_rejected(self):
        tampered = package()
        tampered["title"] = "Changed"
        with self.assertRaisesRegex(AudioPackageError, "摘要不匹配"):
            validate_audio_package(tampered)
        tampered_index = index(title="Changed")
        with self.assertRaisesRegex(AudioPackageError, "摘要不匹配"):
            validate_audio_index(tampered_index)

    def test_code_and_unknown_fields_are_rejected(self):
        executable = package()
        executable["executor"] = "shell"
        with self.assertRaisesRegex(AudioPackageError, "字段不匹配"):
            validate_audio_package(executable)
        camera_payload = sound(payload="shell")
        bad = package(sounds=[camera_payload])
        with self.assertRaisesRegex(AudioPackageError, "字段不匹配"):
            validate_audio_package(bad)

    def test_identifiers_filenames_and_duplicate_entries_are_rejected(self):
        with self.assertRaises(AudioPackageError):
            validate_audio_package(package(package_id="../escape"))
        with self.assertRaises(AudioPackageError):
            validate_audio_package(package(sounds=[sound(filename="../escape.wav")]))
        duplicate = package(sounds=[sound(), sound(title="Duplicate")])
        with self.assertRaisesRegex(AudioPackageError, "音频 ID 重复"):
            validate_audio_package(duplicate)
        duplicate_name = package(sounds=[sound(), sound(sound_id="another")])
        with self.assertRaisesRegex(AudioPackageError, "音频文件名重复"):
            validate_audio_package(duplicate_name)

    def test_audio_constraints_and_license_are_enforced(self):
        cases = [
            sound(bytes=524289),
            sound(duration_ms=2001),
            sound(sample_rate=7999),
            sound(channels=3),
            sound(bit_depth=12),
            sound(codec="mp3"),
            sound(tags=["duplicate", "duplicate"]),
        ]
        for case in cases:
            with self.assertRaises(AudioPackageError):
                validate_audio_package(package(sounds=[case]))
        with self.assertRaisesRegex(AudioPackageError, "许可证"):
            validate_audio_package(package(license="All rights reserved"))

    def test_index_rejects_duplicate_packages_and_bad_digests(self):
        duplicate = index()
        duplicate["packages"] = [package(), resign_package(
            package(title="Second"))]
        body = dict(duplicate)
        body.pop("digest")
        duplicate = dict(body, digest=_digest(body))
        with self.assertRaisesRegex(AudioPackageError, "音频包 ID 重复"):
            validate_audio_index(duplicate)

    def test_valid_audio_bundle_contains_only_manifest_and_matching_wav(self):
        bundle = audio_bundle()
        result = validate_audio_bundle(bundle)
        self.assertEqual(result["manifest"]["package_id"], "community-shutters")
        self.assertEqual(result["entries"]["camera-shutter.wav"]["bytes"], 48)

    def test_bundle_rejects_extra_files_and_manifest_drift(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("package.json", __import__("json").dumps(wav_manifest()))
            archive.writestr("camera-shutter.wav", pcm_wav())
            archive.writestr("extra.wav", b"extra")
        with self.assertRaisesRegex(AudioPackageError, "文件与清单不匹配"):
            validate_audio_bundle(output.getvalue())

        changed = wav_manifest()
        changed["title"] = "Changed"
        with self.assertRaisesRegex(AudioPackageError, "摘要不匹配"):
            validate_audio_bundle(audio_bundle(changed))

    def test_bundle_rejects_wav_hash_and_format_drift(self):
        manifest = wav_manifest()
        manifest["sounds"][0]["sha256"] = "b" * 64
        body = dict(manifest)
        body.pop("digest")
        manifest = dict(body, digest=_digest(body))
        with self.assertRaisesRegex(AudioPackageError, "哈希"):
            validate_audio_bundle(audio_bundle(manifest))

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            bad_wav = b"N" * 44
            bad_manifest = package(sounds=[sound(
                bytes=len(bad_wav),
                sha256=hashlib.sha256(bad_wav).hexdigest(),
                duration_ms=1, sample_rate=48000, channels=1, bit_depth=16)])
            archive.writestr("package.json", __import__("json").dumps(
                bad_manifest, sort_keys=True, separators=(",", ":")))
            archive.writestr("camera-shutter.wav", bad_wav)
        with self.assertRaisesRegex(AudioPackageError, "RIFF"):
            validate_audio_bundle(output.getvalue())

        bad_rate = wav_manifest()
        bad_rate["sounds"][0]["sample_rate"] = 44100
        body = dict(bad_rate)
        body.pop("digest")
        bad_rate = dict(body, digest=_digest(body))
        with self.assertRaisesRegex(AudioPackageError, "音频参数"):
            validate_audio_bundle(audio_bundle(bad_rate))

    def test_build_package_and_bundle_from_local_wav(self):
        package = build_audio_package(
            [dict(filename="camera-shutter.wav", data=pcm_wav(),
                  title="Camera Shutter", description="Local export",
                  tags=["shutter"])],
            package_id="local-shutters", version="1.0.0",
            title="Local shutters", description="Local only.",
            author="Morris", license="CC-BY-4.0")
        self.assertEqual(package["schema"], AUDIO_PACKAGE_SCHEMA)
        self.assertEqual(package["sounds"][0]["sound_id"], "camera-shutter")
        self.assertEqual(package["sounds"][0]["bytes"], len(pcm_wav()))

        bundle = build_audio_bundle(
            package, [dict(filename="camera-shutter.wav", data=pcm_wav())])
        result = validate_audio_bundle(bundle)
        self.assertEqual(result["manifest"], package)
        self.assertEqual(result["entries"]["camera-shutter.wav"]["bytes"], len(pcm_wav()))

    def test_build_package_default_id_and_deterministic_bundle(self):
        data = pcm_wav()
        package = build_audio_package(
            [dict(filename="a.wav", data=data)],
            package_id="single-sound", version="1.0.0",
            title="Single sound", description="Single sound package.",
            author="Morris", license="CC0-1.0")
        self.assertEqual(package["sounds"][0]["sound_id"], "sound-a")
        first = build_audio_bundle(package, [dict(filename="a.wav", data=data)])
        second = build_audio_bundle(package, [dict(filename="a.wav", data=data)])
        self.assertEqual(first, second)

    def test_build_package_rejects_bad_metadata_and_duplicate_wavs(self):
        with self.assertRaisesRegex(AudioPackageError, "许可证"):
            build_audio_package(
                [dict(filename="camera-shutter.wav", data=pcm_wav())],
                package_id="local-shutters", version="1.0.0",
                title="Local shutters", description="Local only.",
                author="Morris", license="All rights reserved")
        with self.assertRaises(AudioPackageError):
            build_audio_package(
                [dict(filename="bad.wav", data=b"not a wav")],
                package_id="local-shutters", version="1.0.0",
                title="Local shutters", description="Local only.",
                author="Morris", license="CC-BY-4.0")
        package = build_audio_package(
            [dict(filename="camera-shutter.wav", data=pcm_wav())],
            package_id="local-shutters", version="1.0.0",
            title="Local shutters", description="Local only.",
            author="Morris", license="CC-BY-4.0")
        with self.assertRaisesRegex(AudioPackageError, "WAV 与清单不匹配"):
            build_audio_bundle(package, [dict(filename="missing.wav", data=pcm_wav())])

    def test_normalize_camera_clip_pads_short_wavs_and_matches_bundled_defaults(self):
        from pathlib import Path
        normalized = normalize_camera_clip(pcm_wav())
        parsed = inspect_pcm_wav(normalized, "short.wav")
        self.assertEqual((parsed["sample_rate"], parsed["channels"],
                          parsed["bit_depth"], parsed["frame_count"],
                          parsed["duration_ms"], parsed["bytes"]),
                         (CAMERA_SAMPLE_RATE, 1, 16, CAMERA_CLIP_FRAMES, 350,
                          44 + CAMERA_CLIP_FRAMES * 2))
        self.assertEqual(normalize_camera_clip(normalized), normalized)

        wav_dir = Path(__file__).resolve().parents[1] / "desktop/macos/默认音频"
        for name in ("coin.wav", "slap.wav", "duck.wav", "beep.wav"):
            raw = (wav_dir / name).read_bytes()
            info = inspect_pcm_wav(raw, name)
            self.assertEqual((info["sample_rate"], info["channels"],
                              info["bit_depth"], info["frame_count"],
                              info["duration_ms"]),
                             (CAMERA_SAMPLE_RATE, 1, 16, CAMERA_CLIP_FRAMES, 350))
            self.assertEqual(normalize_camera_clip(raw), raw)


if __name__ == "__main__":
    unittest.main()
