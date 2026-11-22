"""Verify a separate CP VEF command path to the native user-MLUT APIs offline.

The class attribution uses its vtable and RTTI, not a nearby logging string.
It does not identify the command's callers as full-resolution still/JPEG jobs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from capstone import Cs,CS_ARCH_ARM,CS_MODE_THUMB,CS_MODE_LITTLE_ENDIAN
from emulate_lut_parameter_helpers import CP_BASE,CP_SHA256,check_hash

RANGES=((0x6e6896,0x1a2),(0x6e15de,0x62),(0x6e1640,0x76),(0x6e0188,0x2c))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('cp',type=Path);p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    if args.out.exists():p.error('Output exists')
    raw=args.cp.read_bytes();check_hash(raw,CP_SHA256)
    def read(a,n):
        if not CP_BASE<=a<=CP_BASE+len(raw)-n:raise ValueError('Read outside CP file')
        return raw[a-CP_BASE:a-CP_BASE+n]
    def word(a):return struct.unpack('<I',read(a,4))[0]
    def string(a):return read(a,100).split(b'\0',1)[0].decode('ascii')
    if word(0xd23d5c)!=0x6e6897 or word(0xd23d44)!=0xd23d60:
        raise AssertionError('Unexpected VEF vtable binding')
    name=string(word(0xd23d64))
    if name!='N3VEF21VefPathCmdSetLookNameE':raise AssertionError('Unexpected VEF RTTI')
    dis=Cs(CS_ARCH_ARM,CS_MODE_THUMB|CS_MODE_LITTLE_ENDIAN)
    windows=[];instructions={}
    for start,length in RANGES:
        code=list(dis.disasm(read(start,length),start))
        if sum(i.size for i in code)!=length:raise AssertionError('Incomplete Thumb decoding')
        instructions.update({i.address:i for i in code})
        windows.append(dict(va=hex(start),bytes=length,sha256=hashlib.sha256(read(start,length)).hexdigest(),
            instructions=[f'{i.address:#x}: {i.mnemonic} {i.op_str}' for i in code]))
    edges=[]
    for site,target in ((0x6e696e,0x6e1640),(0x6e69fc,0x6e15de),
                        (0x6e161a,0x542108),(0x6e1690,0x542344)):
        ins=instructions[site]
        if ins.mnemonic!='bl' or ins.op_str!=f'#{target:#x}':raise AssertionError('Unexpected VEF edge')
        edges.append(dict(site=hex(site),target=hex(target),instruction=f'{ins.mnemonic} {ins.op_str}'))
    report=dict(schema='a6700-lab/cp-vef-user-mlut-static-path/1',result='verified_static_edges',
        cp_sha256=CP_SHA256,base=hex(CP_BASE),class_rtti=name,
        vtable=dict(function_pointer_va='0xd23d5c',function_thumb_pointer='0x6e6897',
                    typeinfo_pointer_va='0xd23d44',typeinfo='0xd23d60',name_pointer_va='0xd23d64'),
        edges=edges,windows=windows,tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        camera_access=False,ap_binding_to_this_command_verified=False,
        full_resolution_still_job_verified=False,jpeg_buffer_connection_verified=False,
        observations=['VEF::VefPathCmdSetLookName reaches the same native User3D/User3DArtGen APIs as the existing VFX path',
            'This route uses its own callback 0x6e01b5; it is not the AP transfer callback or VFX callback 0x4dca4d',
            'Function 0x6e0188 converts driver return codes; it is not a completion wait or a readiness proof'],
        limitations=['Static code/vtable/RTTI edges only, no execution of VEF virtual interfaces or drivers',
            'VEF naming and shared MLUT APIs do not prove still-photo or full-size JPEG use',
            'No complete indirect caller graph, input/output-pin provenance or image-buffer lifetime is established'])
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(f'{name}: 4 native edges verified; still/JPEG route remains unverified.')

if __name__=='__main__':main()
