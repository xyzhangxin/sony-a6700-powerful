# Fixed parser frozen from fl23_cp_exception.py
import secrets, struct, hashlib
REQUEST=struct.Struct('<4sI16sIQI')
RESPONSE=struct.Struct('<4sI16sIIQI')
sha=lambda raw:hashlib.sha256(raw).hexdigest()
SIZE=1024

CELL=0x19e9fec

EXPECTED=[bytes.fromhex(x) for x in ('0210a001','dff800f0a52ec100','dff800f0652ec100','49f20001')]

def request(nonce=None):
    return REQUEST.pack(b'A67P',1,secrets.token_bytes(16) if nonce is None else nonce,30,0,SIZE)

def parse(raw,config):
    if len(config)!=40 or config!=request(config[8:24]) or len(raw)!=44+SIZE:
        raise ValueError('CP exception framing differs')
    envelope=RESPONSE.unpack(raw[:44]);status=envelope[3]
    if envelope[:3]!=(b'A67B',1,config[8:24]) or envelope[4:]!=(30,0,SIZE) or status not in (0,3,4,5):
        raise ValueError('CP exception response stale or invalid')
    data=raw[44:];w=struct.unpack_from('<32Q',data)
    if w[0]!=1 or not 0<w[1]<1<<31 or not 0<w[2]<1<<31 or w[4]>4 or w[6] not in (0,1,2) or w[7]>7 or w[8]>7 or w[9]!=0x315058454350 or w[10] not in (0,256) or any(w[11:]) or any(data[328:384]) or any(data[640:]):
        raise ValueError('CP exception completion extent invalid')
    pointer=w[5];before,after=struct.unpack_from('<2I',data,320)
    if pointer!=before:raise ValueError('CP exception pointer field differs')
    for i in range(w[4]):
        if data[256+i*16:256+i*16+len(EXPECTED[i])]!=EXPECTED[i]:raise ValueError('CP fingerprint confirmation differs')
    for i,e in enumerate(EXPECTED):
        if any(data[256+i*16+len(e):256+(i+1)*16]):raise ValueError('CP fingerprint padding differs')
    record=None
    if status==0:
        if w[4]!=4 or w[8]:raise ValueError('Unverified CP code or failed read')
        if w[6]==0:
            if pointer or w[7]!=5 or w[10] or after or any(data[384:640]):raise ValueError('Empty exception record malformed')
        elif w[6]==2:
            if not pointer or not(pointer&3 or pointer<0x1400000 or pointer>0x2000000-256) or w[7]!=5 or w[10] or after or any(data[384:640]):raise ValueError('Rejected exception pointer malformed')
        else:
            if pointer&3 or not 0x1400000<=pointer<=0x2000000-256 or after!=pointer or w[7]!=7 or w[10]!=256:
                raise ValueError('Incoherent or unbounded exception frame')
            regs=list(struct.unpack_from('<17I',data,384))
            record=dict(registers={**{'r'+str(i):hex(v) for i,v in enumerate(regs[:16])},'cpsr':hex(regs[16])},
                pc=hex(regs[15]),sp=hex(regs[13]),lr=hex(regs[14]),cpsr=hex(regs[16]),
                arm_mode=regs[16]&31,thumb=bool(regs[16]&32),raw_sha256=sha(data[384:640]),
                epoch_unknown=True,record_is_recent_fault_unproven=True)
    return dict(native_status=status,process_id=w[1],service_tid=w[2],fingerprints_verified=w[4],
        pointer_cell=hex(CELL),pointer=hex(pointer),pointer_state=('empty','bounded','rejected')[w[6]],
        phase=w[7],dma_error=w[8],record=record,cp_memory_written=False,controller_created=False)
