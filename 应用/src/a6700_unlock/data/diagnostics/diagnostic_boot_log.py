# Fixed parser frozen from fl23_boot_log.py
import secrets, struct, hashlib
REQUEST=struct.Struct('<4sI16sIQI')
RESPONSE=struct.Struct('<4sI16sIIQI')
sha=lambda raw:hashlib.sha256(raw).hexdigest()
SIZE=393216

LEGACY_SPECS=[('/proc/klog',262144),('/log/logdata.dat',8192),('/proc/uptime',128),
       ('/proc/sys/kernel/random/boot_id',64),('/proc/cmdline',4096),
       ('/sys/power/image_mode',32),('/sys/power/image_dev',64),('/log',8192),
       ('/log/logdata_cp.dat',8192),('/log/logdata_core.dat',8192)]

SPECS=LEGACY_SPECS+[('/proc/net/dev',8192),('/proc/net/route',8192),
                    ('/proc/net/wireless',8192),('/proc/net/fib_trie',8192)]

DATA_AT=1280

def request(nonce=None):
    return REQUEST.pack(b'A67P',1,secrets.token_bytes(16) if nonce is None else nonce,28,0,SIZE)

def parse(raw,config):
    if len(config)!=40 or config!=request(config[8:24]) or len(raw)!=44+SIZE:
        raise ValueError('Boot log framing extent differs')
    if RESPONSE.unpack(raw[:44])!=(b'A67B',1,config[8:24],0,28,0,SIZE):
        raise ValueError('Boot log response failed or stale')
    data=raw[44:];w=struct.unpack_from('<32Q',data)
    specs=SPECS;data_at=DATA_AT
    if w[3]==10:
        # Two exact earlier layouts remain parseable for original evidence.
        first_capacity=struct.unpack_from('<I',data,280)[0]
        if first_capacity==262144:specs=LEGACY_SPECS
        elif first_capacity==131072:
            old_paths={1:'/proc/last_kmsg',7:'/sys/fs/pstore',8:'/sys/fs/pstore/dmesg-ramoops-0',9:'/sys/fs/pstore/console-ramoops-0'}
            specs=[(old_paths.get(i,p),131072 if i==0 else 65536 if i in (1,8,9) else c) for i,(p,c) in enumerate(LEGACY_SPECS)]
        else:raise ValueError('Unreviewed legacy boot log layout')
        data_at=1024
    if w[0]!=1 or not 0<w[1]<1<<31 or not 0<w[2]<1<<31 or w[3]!=len(specs) or any(w[5:]) or any(data[256+64*len(specs):data_at]):
        raise ValueError('Boot log completion header differs')
    records=[];cursor=data_at
    for i,(path,capacity) in enumerate(specs):
        error,total,at,length,cap,flags=struct.unpack_from('<qQ4I',data,256+i*64)
        if not -4095<=error<=0 or at!=cursor or cap!=capacity or length>capacity or total>2097152 or flags&~7 or any(data[288+i*64:320+i*64]):
            raise ValueError('Boot log record extent differs')
        if total<length or bool(flags&4)!=(total>length) or any(data[at+length:at+capacity]):
            raise ValueError('Boot log tail/padding differs')
        payload=data[at:at+length];cursor+=capacity
        records.append(dict(index=i,path=path,errno=-error,total_read=total,bytes=length,
            eof=bool(flags&1),read_budget_exhausted=bool(flags&2),prefix_discarded=bool(flags&4),
            sha256=sha(payload)))
    if cursor!=w[4] or any(data[cursor:]):raise ValueError('Boot log output coverage differs')
    return dict(process_id=w[1],service_tid=w[2],records=records,source_writes=False,
                log_cleared=False,reset_cause_proven=False)
