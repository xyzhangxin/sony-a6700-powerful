"""Local shutter sound market session, audio conversion, and preview playback for Windows."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import uuid
import wave

from PySide6.QtCore import QFile, QObject, QSettings, QTimer, Signal
from a6700_unlock.host_io import default_state_dir

if sys.platform == 'win32':
    import ctypes
    import ctypes.wintypes as wintypes
    import winsound


CAMERA_SAMPLE_RATE = 24000
CAMERA_CLIP_DURATION = 0.350
CAMERA_CLIP_FRAMES = 8400
MAX_CAMERA_SLOT_NAME_BYTES = 255
MAX_IMPORT_BYTES = 20 * 1024 * 1024
SUPPORTED_IMPORT_EXTS = {'wav', 'mp3', 'm4a', 'aac', 'aiff', 'aif', 'flac', 'wma'}

BUNDLED_DEFAULT_SOUNDS = (
    ('coin', '起跳吃金币', 350, '336BFC4B-BBDB-4285-ADDF-353B4D2223F9'),
    ('slap', '扇巴掌', 350, '9A2DB6E8-7A5F-4D5B-B57E-71323E52122A'),
    ('duck', '鸭叫', 350, 'D0C80002-0000-4000-8000-000000000002'),
    ('beep', '嘟嘟', 200, '1C5A4886-53D4-4B3A-BEB2-31025EC74BB0'),
)

DEMO_EXAMPLES = (
    ('清脆单音', '短促、清晰的拍摄提示', '提示音', 880.0, 0.16, False),
    ('柔和双音', '两段柔和的确认声', '提示音', 520.0, 0.32, True),
    ('轻触快门', '低沉短音与快速衰减', '机械感', 160.0, 0.12, False),
)


class AudioMarketError(ValueError):
    """Domain error with localized Chinese explanation for the audio market UI."""


@dataclass
class MarketSound:
    id: str
    title: str
    subtitle: str
    author: str
    duration_ms: int
    tags: list[str]
    source_kind: str  # 'sample' | 'personal' | 'catalog'
    file_path: Path | None = None
    package_id: str | None = None
    sound_id: str | None = None

    @property
    def personal(self) -> bool:
        return self.source_kind == 'personal'


@dataclass
class MarketSlot:
    id: int
    sound: MarketSound | None = None

    @property
    def label(self) -> str:
        return f'槽位 {self.id}'


@dataclass
class AudioTrimRequest:
    id: str
    path: Path
    duration: float
    pcm_samples: list[int] = field(default_factory=list)
    sample_rate: int = CAMERA_SAMPLE_RATE

    @property
    def title(self) -> str:
        return self.path.stem


def bundled_audio_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / '默认音频'
    return Path(__file__).resolve().parents[1] / 'macos/默认音频'


def build_wav_bytes(samples: list[int], sample_rate: int = CAMERA_SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        if samples:
            wf.writeframes(struct.pack(f'<{len(samples)}h', *samples))
        else:
            wf.writeframes(b'')
    return buf.getvalue()


def _resample_linear(mono: list[float], src_rate: int, dst_rate: int) -> list[int]:
    if not mono:
        return []
    if src_rate == dst_rate:
        return [max(-32767, min(32767, int(round(v * 32767.0)))) for v in mono]
    out_len = max(1, int(round(len(mono) * dst_rate / src_rate)))
    ratio = src_rate / dst_rate
    last_idx = len(mono) - 1
    out: list[int] = []
    for i in range(out_len):
        pos = i * ratio
        idx = int(pos)
        if idx >= last_idx:
            val = mono[last_idx]
        else:
            frac = pos - idx
            val = mono[idx] * (1.0 - frac) + mono[idx + 1] * frac
        out.append(max(-32767, min(32767, int(round(val * 32767.0)))))
    return out


def _decode_wav_bytes(raw: bytes) -> tuple[list[int], float]:
    """Decode uncompressed PCM or IEEE-float RIFF/WAV into 24kHz mono 16-bit samples."""
    if len(raw) < 12 or raw[:4] != b'RIFF' or raw[8:12] != b'WAVE':
        raise AudioMarketError('无法读取或解码该 WAV 文件。')
    pos = 12
    fmt_tag = None
    channels = 0
    rate = 0
    bits = 0
    data_bytes = None
    while pos + 8 <= len(raw):
        chunk_id = raw[pos:pos + 4]
        chunk_size = struct.unpack_from('<I', raw, pos + 4)[0]
        pos += 8
        chunk = raw[pos:pos + chunk_size]
        pos += chunk_size + (chunk_size & 1)
        if chunk_id == b'fmt ' and len(chunk) >= 16:
            fmt_tag, channels, rate, _, _, bits = struct.unpack_from('<HHIIHH', chunk, 0)
            if fmt_tag == 0xFFFE and len(chunk) >= 40:
                sub_tag = struct.unpack_from('<H', chunk, 24)[0]
                fmt_tag = sub_tag
        elif chunk_id == b'data':
            data_bytes = chunk
    if fmt_tag not in (1, 3) or channels <= 0 or rate <= 0 or not data_bytes:
        raise AudioMarketError('不支持该 WAV 音频编码或采样参数。')
    frame_bytes = channels * (bits // 8)
    if frame_bytes <= 0:
        raise AudioMarketError('音频文件位深无效，无法导入。')
    total_frames = len(data_bytes) // frame_bytes
    if total_frames <= 0:
        raise AudioMarketError('音频文件为空，无法导入。')
    duration = total_frames / rate
    # Trim candidate decode ceiling before converting 20MB+ long tracks in pure Python.
    mono: list[float] = []
    if fmt_tag == 1:
        if bits == 8:
            for off in range(0, total_frames * frame_bytes, frame_bytes):
                mono.append(sum((data_bytes[off + c] - 128) / 128.0 for c in range(channels)) / channels)
        elif bits == 16:
            vals = struct.unpack_from(f'<{total_frames * channels}h', data_bytes, 0)
            for i in range(0, len(vals), channels):
                mono.append(sum(vals[i:i + channels]) / (channels * 32768.0))
        elif bits == 24:
            for off in range(0, total_frames * frame_bytes, frame_bytes):
                s = 0.0
                for c in range(channels):
                    b = data_bytes[off + c * 3:off + c * 3 + 3]
                    v = int.from_bytes(b, 'little', signed=True)
                    s += v / 8388608.0
                mono.append(s / channels)
        elif bits == 32:
            vals = struct.unpack_from(f'<{total_frames * channels}i', data_bytes, 0)
            for i in range(0, len(vals), channels):
                mono.append(sum(vals[i:i + channels]) / (channels * 2147483648.0))
        else:
            raise AudioMarketError('不支持该 WAV 位深，请选择常见音频文件。')
    else:
        if bits != 32:
            raise AudioMarketError('不支持 64 位浮点 WAV，请选择常见音频文件。')
        vals = struct.unpack_from(f'<{total_frames * channels}f', data_bytes, 0)
        for i in range(0, len(vals), channels):
            mono.append(max(-1.0, min(1.0, sum(vals[i:i + channels]) / channels)))
    return _resample_linear(mono, rate, CAMERA_SAMPLE_RATE), duration


def _decode_media_foundation(path: Path) -> tuple[list[int], float]:
    """Decode WAV/MP3/M4A/AAC/FLAC/WMA via Windows Media Foundation to 24kHz mono 16-bit PCM."""
    if sys.platform != 'win32':
        raise AudioMarketError('当前系统无法解码该音频格式，请转换为 WAV 后重试。')

    class GUID(ctypes.Structure):
        _fields_ = [
            ('Data1', wintypes.DWORD),
            ('Data2', wintypes.WORD),
            ('Data3', wintypes.WORD),
            ('Data4', ctypes.c_ubyte * 8),
        ]

    def guid(text: str) -> GUID:
        return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)

    def vcall(ptr, idx: int, restype, *argtypes):
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[idx])

    ole32 = ctypes.windll.ole32
    mfplat = ctypes.windll.mfplat
    mfreadwrite = ctypes.windll.mfreadwrite

    MF_API_VERSION = (0x0002 << 16) | 0x0070
    MF_FIRST_AUDIO_STREAM = 0xFFFFFFFD
    MF_AUDIO = guid('73647561-0000-0010-8000-00AA00389B71')
    MF_PCM = guid('00000001-0000-0010-8000-00AA00389B71')
    MF_MAJOR = guid('48eba18e-f8c9-4687-bf11-0a74c9f96a8f')
    MF_SUBTYPE = guid('f7e34c9a-42e8-4714-b74b-cb29d72c35e5')
    MF_CHANNELS = guid('37e48bf5-645e-4c5b-89de-ada9e29b696a')
    MF_RATE = guid('5faeeae7-0290-4c31-9e8a-c534f68d9dba')
    MF_BITS = guid('f2deb57f-40fa-4764-aa33-ed4f2d1ff669')

    co_hr = ole32.CoInitializeEx(None, 2)
    co_owned = co_hr in (0, 1)
    if mfplat.MFStartup(MF_API_VERSION, 0) != 0:
        if co_owned:
            ole32.CoUninitialize()
        raise AudioMarketError('Windows 音频解码服务初始化失败。')

    reader = ctypes.c_void_p()
    mt = ctypes.c_void_p()
    pcm_raw = bytearray()
    try:
        if mfreadwrite.MFCreateSourceReaderFromURL(ctypes.c_wchar_p(str(path)), None, ctypes.byref(reader)) != 0 or not reader:
            raise AudioMarketError('无法读取或解码该音频文件，请确认文件未损坏且为标准音频。')
        if mfplat.MFCreateMediaType(ctypes.byref(mt)) != 0 or not mt:
            raise AudioMarketError('无法准备相机音频转换。')
        vcall(mt, 24, ctypes.c_long, ctypes.POINTER(GUID), ctypes.POINTER(GUID))(mt, ctypes.byref(MF_MAJOR), ctypes.byref(MF_AUDIO))
        vcall(mt, 24, ctypes.c_long, ctypes.POINTER(GUID), ctypes.POINTER(GUID))(mt, ctypes.byref(MF_SUBTYPE), ctypes.byref(MF_PCM))
        vcall(mt, 21, ctypes.c_long, ctypes.POINTER(GUID), ctypes.c_uint32)(mt, ctypes.byref(MF_CHANNELS), 1)
        vcall(mt, 21, ctypes.c_long, ctypes.POINTER(GUID), ctypes.c_uint32)(mt, ctypes.byref(MF_RATE), CAMERA_SAMPLE_RATE)
        vcall(mt, 21, ctypes.c_long, ctypes.POINTER(GUID), ctypes.c_uint32)(mt, ctypes.byref(MF_BITS), 16)
        if vcall(reader, 7, ctypes.c_long, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p)(
            reader, MF_FIRST_AUDIO_STREAM, None, mt
        ) != 0:
            raise AudioMarketError('无法转换该音频文件的编码格式。')

        # Cap decoded buffer at ~30 minutes of 24kHz mono audio to avoid OOM on malformed files.
        max_bytes = 30 * 60 * CAMERA_SAMPLE_RATE * 2
        while len(pcm_raw) < max_bytes:
            actual = ctypes.c_uint32()
            flags = ctypes.c_uint32()
            ts = ctypes.c_int64()
            sample = ctypes.c_void_p()
            hr = vcall(
                reader, 9, ctypes.c_long,
                ctypes.c_uint32, ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_int64), ctypes.POINTER(ctypes.c_void_p),
            )(reader, MF_FIRST_AUDIO_STREAM, 0, ctypes.byref(actual), ctypes.byref(flags), ctypes.byref(ts), ctypes.byref(sample))
            if hr != 0:
                raise AudioMarketError('音频解码过程中发生错误。')
            if not sample:
                break
            try:
                buf = ctypes.c_void_p()
                if vcall(sample, 41, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))(sample, ctypes.byref(buf)) == 0 and buf:
                    try:
                        pdata = ctypes.c_void_p()
                        maxlen = ctypes.c_uint32()
                        curlen = ctypes.c_uint32()
                        if vcall(
                            buf, 3, ctypes.c_long,
                            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)
                        )(buf, ctypes.byref(pdata), ctypes.byref(maxlen), ctypes.byref(curlen)) == 0 and curlen.value:
                            pcm_raw.extend(ctypes.string_at(pdata.value, curlen.value))
                            vcall(buf, 4, ctypes.c_long)(buf)
                    finally:
                        vcall(buf, 2, ctypes.c_ulong)(buf)
            finally:
                vcall(sample, 2, ctypes.c_ulong)(sample)
    finally:
        if mt:
            vcall(mt, 2, ctypes.c_ulong)(mt)
        if reader:
            vcall(reader, 2, ctypes.c_ulong)(reader)
        mfplat.MFShutdown()
        if co_owned:
            ole32.CoUninitialize()

    if len(pcm_raw) < 2:
        raise AudioMarketError('音频文件为空或无音频轨道，无法导入。')
    frame_count = len(pcm_raw) // 2
    samples = list(struct.unpack_from(f'<{frame_count}h', pcm_raw, 0))
    return samples, frame_count / CAMERA_SAMPLE_RATE


def decode_audio_file(path: Path) -> tuple[list[int], float]:
    """Validate and decode an audio file into 24kHz mono 16-bit samples."""
    ext = path.suffix.lstrip('.').lower()
    if ext not in SUPPORTED_IMPORT_EXTS:
        shown = ext or '未知'
        raise AudioMarketError(f'不支持的音频格式「.{shown}」；请选择 MP3、M4A、AAC、WAV、AIFF、FLAC 或 WMA。')
    if not path.is_file():
        raise AudioMarketError('请选择有效的音频文件。')
    size = path.stat().st_size
    if size <= 0 or size > MAX_IMPORT_BYTES:
        raise AudioMarketError('请选择有效的音频文件（大小不超过 20 MB）。')
    if ext == 'wav':
        try:
            return _decode_wav_bytes(path.read_bytes())
        except AudioMarketError:
            if sys.platform != 'win32':
                raise
    return _decode_media_foundation(path)


def trim_waveform(samples: list[int], bins: int = 64) -> list[float]:
    if not samples or bins <= 0:
        return [0.0] * max(1, bins)
    step = max(1, len(samples) // bins)
    peaks: list[float] = []
    for i in range(bins):
        start = min(len(samples), i * step)
        end = len(samples) if i + 1 == bins else min(len(samples), start + step)
        if end <= start:
            peaks.append(0.0)
        else:
            peak = max(abs(samples[j]) for j in range(start, end)) / 32768.0
            peaks.append(min(1.0, peak))
    top = max(peaks) if peaks else 0.0
    return [p / top if top > 0 else 0.0 for p in peaks]


def is_valid_camera_slot_scalar(ch: str) -> bool:
    value = ord(ch)
    if value < 0x80:
        return (
            (48 <= value <= 57)
            or (65 <= value <= 90)
            or (97 <= value <= 122)
            or value in (46, 95, 45, 32)
        )
    return not (
        (0x0080 <= value <= 0x009F)
        or (0x200B <= value <= 0x200F)
        or (0x202A <= value <= 0x202E)
        or (0x2066 <= value <= 0x2069)
        or (0xD800 <= value <= 0xDFFF)
        or value == 0xFEFF
        or (0xFFFE <= value <= 0xFFFF)
    )


def camera_slot_name_error(raw: str) -> str | None:
    if not raw:
        return '请输入写入相机的槽位名称。'
    if raw.startswith('.') or raw.startswith(' ') or '..' in raw:
        return '名称不能以 . 或空格开头，也不能包含 ..。'
    for ch in raw:
        if not is_valid_camera_slot_scalar(ch):
            return '名称包含不支持的符号（支持中英文、数字、空格及 ._-）。'
    if len(raw.encode('utf-8')) > MAX_CAMERA_SLOT_NAME_BYTES:
        return '名称 UTF-8 长度超过 255 字节，请缩短名称。'
    return None


def sanitize_camera_slot_name(raw: str) -> str:
    text = raw.strip()
    if text.lower().endswith('.wav'):
        text = text[:-4].strip()
    cleaned = ''.join(ch for ch in text if is_valid_camera_slot_scalar(ch))
    while '..' in cleaned:
        cleaned = cleaned.replace('..', '.')
    while cleaned and cleaned[0] in ('.', ' '):
        cleaned = cleaned[1:]
    cleaned = cleaned.strip()
    kept = []
    byte_count = 0
    for ch in cleaned:
        nb = len(ch.encode('utf-8'))
        if byte_count + nb > MAX_CAMERA_SLOT_NAME_BYTES:
            break
        byte_count += nb
        kept.append(ch)
    clipped = ''.join(kept).strip()
    return clipped or '自定义快门音'


def default_camera_target_name(sound: MarketSound) -> str:
    return sanitize_camera_slot_name(sound.title)


def validated_camera_target_name(raw: str) -> str:
    trimmed = raw.strip()
    err = camera_slot_name_error(trimmed)
    if err:
        raise AudioMarketError(err)
    return trimmed


class AudioMarketSession(QObject):
    """Manage bundled, local personal, and catalog sounds with preview playback."""

    changed = Signal()

    def __init__(
        self,
        library: Path | None = None,
        favorites_store: QSettings | dict | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.library = Path(library) if library is not None else default_state_dir() / 'audio-library'
        self._favorites_store = favorites_store if favorites_store is not None else QSettings('Powerful6700', 'AudioMarket')
        self.personal_sounds: list[MarketSound] = []
        self.demo_sounds: list[MarketSound] = []
        self.catalog_sounds: list[MarketSound] = []
        self.favorites: set[str] = self._load_favorites()
        self.slots: list[MarketSlot] = [MarketSlot(id=i) for i in range(1, 11)]
        self.playing_id: str | None = None
        self.preparing_id: str | None = None
        self.loading_catalog = False
        self.query = ''
        self.category = '全部声音'
        self.selected_sound_id: str | None = None
        self.message = ''
        self.error = ''
        self._playing_buffer: bytes | None = None
        self._play_timer = QTimer(self)
        self._play_timer.setSingleShot(True)
        self._play_timer.timeout.connect(self._on_playback_timeout)
        self._samples_dir = Path(tempfile.mkdtemp(prefix='powerful-audio-market-'))
        self._prepare_samples()
        self.load_imports()

    def cleanup(self) -> None:
        self.stop_preview()
        shutil.rmtree(self._samples_dir, ignore_errors=True)

    def _load_favorites(self) -> set[str]:
        if isinstance(self._favorites_store, dict):
            return set(self._favorites_store.get('audioMarketFavorites', []))
        val = self._favorites_store.value('audioMarketFavorites', [])
        if isinstance(val, str):
            return {val} if val else set()
        return set(val or [])

    def _save_favorites(self) -> None:
        items = sorted(self.favorites)
        if isinstance(self._favorites_store, dict):
            self._favorites_store['audioMarketFavorites'] = items
        else:
            self._favorites_store.setValue('audioMarketFavorites', items)

    def _prepare_samples(self) -> None:
        try:
            audio_dir = bundled_audio_dir()
            for filename, title, duration_ms, ident in BUNDLED_DEFAULT_SOUNDS:
                wav_path = audio_dir / f'{filename}.wav'
                if not wav_path.is_file():
                    continue
                self.demo_sounds.append(
                    MarketSound(
                        id=ident,
                        title=title,
                        subtitle='24000 Hz · 16 位单声道',
                        author='Powerful6700',
                        duration_ms=duration_ms,
                        tags=['内置'],
                        source_kind='sample',
                        file_path=wav_path,
                    )
                )
            for idx, (title, subtitle, tag, freq, duration, double_tone) in enumerate(DEMO_EXAMPLES):
                url = self._samples_dir / f'demo-{idx}.wav'
                self._write_demo_sample(url, freq, duration, double_tone)
                self.demo_sounds.append(
                    MarketSound(
                        id=f'demo-{idx}',
                        title=title,
                        subtitle=subtitle,
                        author='Powerful6700 示例音频',
                        duration_ms=int(round(duration * 1000)),
                        tags=[tag],
                        source_kind='sample',
                        file_path=url,
                    )
                )
        except OSError:
            self.error = '示例音频准备失败，请尝试导入本地 WAV。'

    def _write_demo_sample(self, path: Path, freq: float, duration: float, double_tone: bool) -> None:
        frames = int(round(duration * CAMERA_SAMPLE_RATE))
        samples: list[int] = []
        for i in range(CAMERA_CLIP_FRAMES):
            if i >= frames:
                samples.append(0)
                continue
            t = i / CAMERA_SAMPLE_RATE
            progress = i / max(1, frames)
            envelope = math.sin(min(1.0, t / 0.008) * math.pi / 2.0) * math.exp(-progress * 3.8)
            if double_tone:
                pulse = 0.0 if (0.40 <= progress <= 0.54) else 1.0
                tone = math.sin(2.0 * math.pi * (freq if progress < 0.45 else freq * 1.25) * t)
                val = tone * envelope * pulse
            else:
                tone = math.sin(2.0 * math.pi * freq * t) + 0.25 * math.sin(2.0 * math.pi * freq * 2.0 * t)
                val = tone * 0.8 * envelope
            samples.append(max(-32767, min(32767, int(round(val * 24000.0)))))
        path.write_bytes(build_wav_bytes(samples))

    def load_imports(self) -> None:
        self.personal_sounds.clear()
        if not self.library.is_dir():
            return
        entries: list[tuple[float, MarketSound]] = []
        for folder in self.library.iterdir():
            if not folder.is_dir():
                continue
            try:
                uid = str(uuid.UUID(folder.name)).upper()
            except ValueError:
                continue
            meta_file = folder / 'audio.json'
            if not meta_file.is_file():
                continue
            try:
                record = json.loads(meta_file.read_text(encoding='utf-8'))
                wav_name = record.get('wavFilename') or 'audio.wav'
                wav_path = folder / wav_name
                if not wav_path.is_file():
                    continue
                sound = MarketSound(
                    id=uid,
                    title=str(record['title']),
                    subtitle=str(record['subtitle']),
                    author='我的导入',
                    duration_ms=int(record['durationMS']),
                    tags=['本地导入'],
                    source_kind='personal',
                    file_path=wav_path,
                )
                stat = folder.stat()
                created = getattr(stat, 'st_birthtime', stat.st_mtime)
                entries.append((created, sound))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        entries.sort(key=lambda item: item[0], reverse=True)
        self.personal_sounds = [item[1] for item in entries]

    def visible_sounds(self) -> list[MarketSound]:
        personal_ids = {s.id for s in self.personal_sounds}
        return self.personal_sounds + self.catalog_sounds + [s for s in self.demo_sounds if s.id not in personal_ids]

    def filtered_sounds(self) -> list[MarketSound]:
        text = self.query.strip().lower()
        items: list[MarketSound] = []
        for sound in self.visible_sounds():
            if self.category == '收藏' and sound.id not in self.favorites:
                continue
            if self.category == '我的音频' and not sound.personal:
                continue
            if text:
                hay = ' '.join([sound.title, sound.subtitle, sound.author, *sound.tags]).lower()
                if text not in hay:
                    continue
            items.append(sound)
        return items

    def set_catalog_from_worker(self, packages: list[dict]) -> None:
        loaded: list[MarketSound] = []
        for pkg in packages:
            pkg_id = pkg.get('package_id', '')
            pkg_title = pkg.get('title', pkg_id)
            for item in pkg.get('sounds', []):
                sid = item.get('id', '')
                loaded.append(
                    MarketSound(
                        id=f'{pkg_id}:{sid}',
                        title=item.get('title', sid),
                        subtitle=item.get('description', ''),
                        author=pkg_title,
                        duration_ms=int(item.get('duration_ms', 350)),
                        tags=list(item.get('tags', [])),
                        source_kind='catalog',
                        package_id=pkg_id,
                        sound_id=sid,
                    )
                )
        self.catalog_sounds = loaded
        self.loading_catalog = False
        self.changed.emit()

    def installed_slots(self, sound: MarketSound) -> list[int]:
        return [slot.id for slot in self.slots if slot.sound and slot.sound.id == sound.id]

    def toggle_favorite(self, sound: MarketSound) -> None:
        if sound.id in self.favorites:
            self.favorites.remove(sound.id)
        else:
            self.favorites.add(sound.id)
        self._save_favorites()
        self.changed.emit()

    @property
    def suggested_slot(self) -> int | None:
        for slot in self.slots:
            if slot.sound is None:
                return slot.id
        return None

    def install_demo(self, sound: MarketSound, slot: int, replacing: str | None = None) -> bool:
        if not 1 <= slot <= 10:
            self.error = '请选择 1 到 10 号槽位。'
            self.changed.emit()
            return False
        current = self.slots[slot - 1].sound
        current_id = current.id if current else None
        if current_id != replacing:
            self.error = f'槽位 {slot} 状态已变化，请重新确认。'
            self.changed.emit()
            return False
        self.slots[slot - 1].sound = sound
        self.selected_sound_id = sound.id
        self.error = ''
        self.message = f'已演示安装「{sound.title}」到槽位 {slot}（仅演示）。'
        self.changed.emit()
        return True

    def import_local(self, path: Path) -> AudioTrimRequest | MarketSound:
        """Validate/decode local audio. Returns MarketSound if saved directly, or AudioTrimRequest if >350ms."""
        samples, duration = decode_audio_file(path)
        self.stop_preview()
        self.error = ''
        self.message = ''
        req = AudioTrimRequest(id=str(uuid.uuid4()).upper(), path=path, duration=duration, pcm_samples=samples)
        if duration > CAMERA_CLIP_DURATION:
            return req
        return self.save_trim(req, 0.0, duration)

    def _slice_clip(self, req: AudioTrimRequest, start: float, end: float, *, pad: bool) -> bytes:
        lo = max(0, min(len(req.pcm_samples), int(round(start * req.sample_rate))))
        hi = max(lo, min(len(req.pcm_samples), int(round(end * req.sample_rate))))
        if hi <= lo:
            raise AudioMarketError('请先选择一段音频。')
        clipped = req.pcm_samples[lo:hi][:CAMERA_CLIP_FRAMES]
        if not any(s != 0 for s in clipped):
            raise AudioMarketError('所选片段没有声音，请调整起点后重试。')
        if pad and len(clipped) < CAMERA_CLIP_FRAMES:
            clipped = clipped + [0] * (CAMERA_CLIP_FRAMES - len(clipped))
        return build_wav_bytes(clipped)

    def save_trim(self, req: AudioTrimRequest, start: float, end: float) -> MarketSound:
        wav_data = self._slice_clip(req, start, end, pad=True)
        uid = str(uuid.uuid4()).upper()
        title = req.title
        folder = self.library / uid
        folder.mkdir(parents=True, exist_ok=True)
        try:
            (folder / 'audio.wav').write_bytes(wav_data)
            meta = {
                'title': title,
                'subtitle': '24000 Hz · 16 位单声道',
                'durationMS': 350,
                'wavFilename': 'audio.wav',
            }
            (folder / 'audio.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
        except OSError:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        sound = MarketSound(
            id=uid,
            title=title,
            subtitle='24000 Hz · 16 位单声道',
            author='我的导入',
            duration_ms=350,
            tags=['本地导入'],
            source_kind='personal',
            file_path=folder / 'audio.wav',
        )
        self.personal_sounds.insert(0, sound)
        self.selected_sound_id = uid
        self.message = f'已导入「{title}」（已裁剪为 350 毫秒）' if req.duration > CAMERA_CLIP_DURATION else f'已导入「{title}」'
        self.error = ''
        self.changed.emit()
        return sound

    def rename_personal_sound(self, sound: MarketSound, new_title: str) -> MarketSound:
        trimmed = new_title.strip()
        err = camera_slot_name_error(trimmed)
        if err:
            raise AudioMarketError(err)
        if not sound.personal:
            raise AudioMarketError('仅支持重命名「我的音频」中的本地音频。')
        try:
            uid = str(uuid.UUID(sound.id)).upper()
        except ValueError:
            raise AudioMarketError('仅支持重命名「我的音频」中的本地音频。')
        folder = self.library / uid
        meta_path = folder / 'audio.json'
        old_record = json.loads(meta_path.read_text(encoding='utf-8'))
        target_wav = folder / 'audio.wav'
        if not target_wav.is_file() and sound.file_path and sound.file_path.is_file():
            target_wav.write_bytes(sound.file_path.read_bytes())
        old_name = old_record.get('wavFilename')
        if old_name and old_name != 'audio.wav':
            (folder / old_name).unlink(missing_ok=True)
        new_record = {
            'title': trimmed,
            'subtitle': old_record['subtitle'],
            'durationMS': old_record['durationMS'],
            'wavFilename': 'audio.wav',
        }
        meta_path.write_text(json.dumps(new_record, ensure_ascii=False), encoding='utf-8')
        updated = MarketSound(
            id=sound.id,
            title=trimmed,
            subtitle=new_record['subtitle'],
            author=sound.author,
            duration_ms=int(new_record['durationMS']),
            tags=list(sound.tags),
            source_kind='personal',
            file_path=target_wav,
        )
        for idx, item in enumerate(self.personal_sounds):
            if item.id == sound.id:
                self.personal_sounds[idx] = updated
        for slot in self.slots:
            if slot.sound and slot.sound.id == sound.id:
                slot.sound = updated
        self.message = f'已将「{sound.title}」重命名为「{trimmed}」'
        self.error = ''
        self.changed.emit()
        return updated

    def trash_personal_sound(self, sound: MarketSound) -> None:
        if not sound.personal or not any(s.id == sound.id for s in self.personal_sounds):
            raise AudioMarketError('仅可删除「我的音频」中的本地音频。')
        try:
            uid = str(uuid.UUID(sound.id)).upper()
        except ValueError:
            raise AudioMarketError('仅可删除「我的音频」中的本地音频。')
        folder = self.library / uid
        if folder.exists():
            if not QFile.moveToTrash(str(folder)):
                shutil.rmtree(folder, ignore_errors=True)
        if self.playing_id == sound.id:
            self.stop_preview()
        self.personal_sounds = [s for s in self.personal_sounds if s.id != sound.id]
        if self.selected_sound_id == sound.id:
            self.selected_sound_id = None
        if sound.id in self.favorites:
            self.favorites.remove(sound.id)
            self._save_favorites()
        for slot in self.slots:
            if slot.sound and slot.sound.id == sound.id:
                slot.sound = None
        self.message = f'已移除「{sound.title}」'
        self.error = ''
        self.changed.emit()

    def preview(self, sound: MarketSound, worker_preview_cb=None) -> None:
        was_active = self.playing_id == sound.id or self.preparing_id == sound.id
        self.stop_preview()
        if was_active:
            return
        self.error = ''
        if sound.source_kind in ('sample', 'personal') and sound.file_path is not None:
            if not sound.file_path.is_file():
                self.error = '本地音频文件不存在，请重新导入。'
                self.changed.emit()
                return
            self._play_wav_bytes(sound.file_path.read_bytes(), sound.id, sound.duration_ms)
            return
        if sound.source_kind == 'catalog' and sound.package_id and sound.sound_id and worker_preview_cb is not None:
            self.preparing_id = sound.id
            self.changed.emit()
            worker_preview_cb(sound.package_id, sound.sound_id, sound.id)
            return
        self.error = '试听音频不可用。'
        self.changed.emit()

    def play_catalog_wav_b64(self, b64: str, sound_id: str, duration_ms: int = 350) -> None:
        try:
            raw = base64.b64decode(b64)
        except ValueError:
            self.preparing_id = None
            self.error = '无法解码云端音频数据。'
            self.changed.emit()
            return
        self.preparing_id = None
        self._play_wav_bytes(raw, sound_id, duration_ms)

    def preview_trim_range(self, req: AudioTrimRequest, start: float, end: float) -> None:
        if self.playing_id == req.id:
            self.stop_preview()
            return
        self.stop_preview()
        wav_bytes = self._slice_clip(req, start, end, pad=False)
        ms = max(50, int(round((end - start) * 1000)))
        self._play_wav_bytes(wav_bytes, req.id, ms)

    def _play_wav_bytes(self, raw: bytes, sound_id: str, duration_ms: int) -> None:
        self.preparing_id = None
        if not raw or not raw.startswith(b'RIFF'):
            self.error = '试听音频格式无效。'
            self.changed.emit()
            return
        self._playing_buffer = raw
        if sys.platform == 'win32':
            try:
                winsound.PlaySound(raw, winsound.SND_MEMORY | winsound.SND_ASYNC)
            except RuntimeError:
                self._playing_buffer = None
                self.error = '无法播放该声音，请检查系统音频输出设备。'
                self.changed.emit()
                return
        self.playing_id = sound_id
        self._play_timer.start(max(120, duration_ms + 80))
        self.changed.emit()

    def _on_playback_timeout(self) -> None:
        self.playing_id = None
        self._playing_buffer = None
        self.changed.emit()

    def stop_preview(self) -> None:
        self._play_timer.stop()
        if sys.platform == 'win32':
            try:
                winsound.PlaySound(None, 0)
            except RuntimeError:
                pass
        was_playing = self.playing_id is not None or self.preparing_id is not None
        self.playing_id = None
        self.preparing_id = None
        self._playing_buffer = None
        if was_playing:
            self.changed.emit()
