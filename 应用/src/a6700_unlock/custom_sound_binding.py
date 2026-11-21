"""Fixed installed-loader observation; no caller supplied payload or paths."""
import hashlib
import json
import re
import struct
from importlib.resources import files
from .device_protocol import DeviceError
from .package_installation import PackageBinding


def assets():
    root = files('a6700_unlock').joinpath('data/native/custom-sound-binding')
    raw_manifest = root.joinpath('manifest.json').read_bytes()
    if hashlib.sha256(raw_manifest).hexdigest() != 'c39f46b08d5a91f7c3613b0c48841d7a080136d15a2ae90971bc2d07acf0e74d':
        raise DeviceError('声音加载器观察清单不匹配')
    manifest = json.loads(raw_manifest)
    if (manifest.get('schema') != 'a6700.custom-sound-binding/1'
            or manifest.get('host_sha256') != '799c133ff8fdf5ff0f095fcdec92f6c0ea03c68e0112a8288836ac244d7a2d0a'
            or manifest.get('files', {}).get('probe.elf') != '3990de1a7a662bdedf4bb5fa89ecd6a3dcf64c53f34398e0601cae355e2dbe96'):
        raise DeviceError('不支持的声音加载器观察版本')
    for name, digest in manifest['files'].items():
        if '/' in name or name.startswith('.') or hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest() != digest:
            raise DeviceError('声音加载器观察资源不匹配')
    return manifest, root.joinpath('probe.elf').read_bytes()


def request(nonce):
    if not isinstance(nonce, bytes) or len(nonce) != 16:
        raise DeviceError('加载器观察 nonce 无效')
    return struct.pack('<4sI16sIQI', b'A67P', 1, nonce, 71, 0, 16384)


def parse(raw, nonce, manifest, identity, profile):
    def require(ok):
        if not ok:
            raise DeviceError('当前机身加载器、接线或运行状态不匹配')
    if not isinstance(raw, bytes) or len(raw) != 44+16384:
        raise DeviceError('声音加载器回包长度不匹配')
    magic, version, echoed, status, kind, offset, size = struct.unpack_from('<4sI16sIIQI', raw)
    if (magic, version, kind, offset, size) != (b'A67B', 1, 71, 0, 16384):
        raise DeviceError('声音加载器回包格式不匹配')
    if echoed != nonce:
        raise DeviceError('声音加载器回包与本次 USB 请求不对应')
    if status == 8:
        raise DeviceError('本次检查未检测到声音加载器的运行端点（原生状态 8）。')
    if status:
        raise DeviceError(f'机身拒绝加载器观察（原生状态 {status}）')
    words = struct.unpack_from('<32Q', raw, 44)
    epoch = raw[300:336]
    page = raw[556:4652]
    bias, base = words[6], struct.unpack_from('<Q',page,328)[0]
    require(re.fullmatch(rb'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',epoch)
            and epoch.decode() != manifest['previous_boot_id'])
    require(words[0]==1 and words[3]==1 and words[10]==1 and words[12]==1
            and words[13]==0 and words[14]==37 and not any(words[15:])
            and 0<words[1]<1<<31 and 0<words[2]<1<<31
            and words[11]>0 and words[11]%4096==0
            and words[8]<=4194304 and words[9]<=min(words[8],131072))
    size = manifest['module_bytes']
    require(4096<=bias<0x8000000000-0x15000000 and bias%4096==0
            and 4096<=base<0x8000000000-size and base%4096==0
            and max(bias,base)>=min(bias+0x15000000,base+size))
    require(page[1120:1632].hex() in set(manifest['receipts'].values())
            and struct.unpack_from('<Q',page,8)[0]==bias
            and struct.unpack_from('<Q',page,336)[0]==size
            and struct.unpack_from('<4Q',raw,44+4608)==(1,2,0,base))
    fields=[]
    for sample in range(2):
        values={}
        for index, field in enumerate(manifest['fields']):
            at=44+manifest['scalar_offset']+sample*8*len(manifest['fields'])+index*8
            data=raw[at:at+8]
            require(not any(data[field['bytes']:]))
            values[field['name']]=int.from_bytes(data,'little')
        require(values['image_base']==base and values['image_bytes']==size
                and values['host.report.startup_ready']==1
                and values['host.report.bindings_ready']==1
                and values['host.runtime.playback.unresolved']==0
                and values['dmm.uncertain']==0 and values['dmm.copy_failed']==0
                and values['capability_session.abandoned']==0)
        if 'dmm.arena.blocked' in values:
            require(values['dmm.arena.blocked']==0)
            used=values['dmm.arena.used']
            tokens=[values[f'dmm.arena.tokens[{i}]'] for i in range(11)]
            require(used<1<<11 and all(bool(token)==bool(used&(1<<i)) for i,token in enumerate(tokens))
                    and len({token for token in tokens if token})==sum(bool(token) for token in tokens))
            parent=values['dmm.arena.parent.handle']
            require(bool(parent)==bool(used))
            if parent:
                require(values['dmm.arena.parent.capacity']>=11*12288
                        and values['dmm.arena.parent.bus_address']>0
                        and values['dmm.arena.canonical_base']>0
                        and values['dmm.arena.canonical_base']%4096==0)
            else:
                require(not any(values[key] for key in ('dmm.arena.parent.capacity',
                    'dmm.arena.parent.bus_address','dmm.arena.canonical_base')))
        fields.append(values)
        at=44+manifest['spans_offset']+sample*manifest['span_bytes']
        for span in manifest['spans']:
            expected=bytearray.fromhex(span['after_hex'])
            for pointer in span['pointers']:
                offset=pointer['offset']
                value=struct.unpack_from('<Q',expected,offset)[0]
                value += {'app':bias,'module':base,'absolute':0}[pointer['after']]
                struct.pack_into('<Q',expected,offset,value)
            require(raw[at:at+len(expected)]==expected)
            at+=len(expected)
        at=44+manifest['entries_offset']+sample*manifest['entry_bytes']
        for name, offset, encoded in manifest['entries']:
            expected=bytes.fromhex(encoded)
            if name=='sound_options_eighth_entry':
                expected=struct.pack('<IIQQ',0x23d7,0,bias+0x3781000,0)
            require(raw[at:at+len(expected)]==expected)
            at+=len(expected)
    # Trace words and counters may move while idle; require ownership and
    # startup-critical state in both independently captured samples above.
    return PackageBinding('custom_shutter_sound',manifest['loader_version'],
        manifest['host_sha256'],profile,identity,'ILCE-6700','2.00')
