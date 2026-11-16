/* SPDX-License-Identifier: GPL-3.0-only
 * Fixed A6700 2.00 PP1 loading experiment. Nine separately built actions.
 * No arbitrary address/path, imaging output setter or firmware-file write.
 * Trace code/state stay in an owned anonymous mapping across service dlclose.
 * STOP restores callsites but deliberately keeps inert mapping until reboot,
 * allowing any already-fetched branch/in-flight callback to return safely.
 */
#include "photo_lut_pp1_trace.h"
#include "photo_lut_pp1_profile.h"
#ifndef PLUT_ACTION
#error Build one fixed module per action
#endif

static long sc(long nr,long a,long b,long c,long d,long e,long f) {
    register long x0 __asm__("x0")=a,x1 __asm__("x1")=b;
    register long x2 __asm__("x2")=c,x3 __asm__("x3")=d;
    register long x4 __asm__("x4")=e,x5 __asm__("x5")=f;
    register long x8 __asm__("x8")=nr;
    __asm__ volatile("svc #0":"+r"(x0):"r"(x1),"r"(x2),"r"(x3),"r"(x4),"r"(x5),"r"(x8):"memory","cc");
    return x0;
}
void *memset(void *p,int value,unsigned long n) {
    volatile u8 *q=p;for(unsigned long i=0;i<n;i++)q[i]=(u8)value;return p;
}
void *memcpy(void *to,const void *from,unsigned long n) {
    u8 *a=to;const u8 *b=from;for(unsigned long i=0;i<n;i++)a[i]=b[i];return to;
}
static int same(const void *a,const void *b,u32 n) {
    const u8 *p=a,*q=b;for(u32 i=0;i<n;i++)if(p[i]!=q[i])return 0;return 1;
}
static u32 countof_guards(void){return sizeof(guards)/sizeof(guards[0]);}
static u8 maps[262144],scratch[PLUT_CODE_BYTES];
static struct plut_request request;
static struct plut_response response;
static const char exchange[]="/tmp/jiritsu/tmmc.jit";
static u64 bias,resident,trace_data;
static long memfd=-1;
static u32 maps_bytes;
/* Used only when no resident trace exists. No target data or paths are copied. */
struct allocation_debug {
    u8 magic[4];u32 version;
    u32 map_count,free_gaps,hints_checked,mmap_attempts,rejected_mappings,unmap_failures;
    long last_mmap_error,last_protection_error,last_state_mmap_error;
    u64 reachable_low,reachable_high,largest_gap_start,largest_gap_bytes;
    u64 required_near_bytes,required_data_bytes;
};
static struct allocation_debug allocation;

/* SHA-256 implementation for bounded fixed code windows. */
static u32 ror(u32 x,u32 n){return(x>>n)|(x<<(32-n));}
static void digest(const u8 *data,u32 length,u8 out[32]) {
    static const u32 k[64]={
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
    u32 h[8]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    u8 padded[128]={0};u32 blocks=(length+9+63)/64;
    if(length>64){memset(out,0,32);return;}
    memcpy(padded,data,length);padded[length]=0x80;
    u64 bits=(u64)length*8;for(u32 i=0;i<8;i++)padded[blocks*64-1-i]=(u8)(bits>>(8*i));
    for(u32 block=0;block<blocks;block++){
        u32 w[64];const u8 *p=padded+64*block;
        for(u32 i=0;i<16;i++)w[i]=((u32)p[4*i]<<24)|((u32)p[4*i+1]<<16)|((u32)p[4*i+2]<<8)|p[4*i+3];
        for(u32 i=16;i<64;i++)w[i]=w[i-16]+(ror(w[i-15],7)^ror(w[i-15],18)^(w[i-15]>>3))+w[i-7]+(ror(w[i-2],17)^ror(w[i-2],19)^(w[i-2]>>10));
        u32 a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],v=h[7];
        for(u32 i=0;i<64;i++){
            u32 t=v+(ror(e,6)^ror(e,11)^ror(e,25))+((e&f)^((~e)&g))+k[i]+w[i];
            u32 s=(ror(a,2)^ror(a,13)^ror(a,22))+((a&b)^(a&c)^(b&c));
            v=g;g=f;f=e;e=d+t;d=c;c=b;b=a;a=t+s;
        }
        h[0]+=a;h[1]+=b;h[2]+=c;h[3]+=d;h[4]+=e;h[5]+=f;h[6]+=g;h[7]+=v;
    }
    for(u32 i=0;i<8;i++)for(u32 j=0;j<4;j++)out[4*i+j]=(u8)(h[i]>>(24-8*j));
}

static int exact(long nr,long fd,u8 *bytes,u32 size,u64 offset) {
    u32 done=0;
    for(u32 tries=0;tries<512&&done<size;tries++){
        long n=sc(nr,fd,(long)(bytes+done),size-done,offset+done,0,0);
        if(n==-4)continue;if(n<=0||(u64)n>size-done)return 0;done+=(u32)n;
    }
    return done==size;
}
static int readmem(u64 address,void *to,u32 size) {
    return address&&address<0x8000000000ULL-size&&exact(67,memfd,to,size,address);
}
static int load_maps(void) {
    long fd=sc(56,-100,(long)"/proc/self/maps",0x80000,0,0,0);if(fd<0)return 0;
    u32 used=0;int good=0;
    for(u32 tries=0;tries<512;tries++){
        if(used==sizeof(maps))break;
        long n=sc(63,fd,(long)(maps+used),sizeof(maps)-used,0,0,0);
        if(n==-4)continue;if(n<0||(u64)n>sizeof(maps)-used)break;
        if(!n){good=1;break;}used+=(u32)n;
    }
    if(sc(57,fd,0,0,0,0,0))good=0;maps_bytes=used;return good;
}
static int hex(const u8 **p,const u8 *end,u64 *value) {
    u32 n=0;u64 v=0;
    while(*p<end){u8 c=**p;u32 d=c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:16;
        if(d==16)break;if(++n>16)return 0;v=(v<<4)|d;(*p)++;}
    *value=v;return n!=0;
}
struct mapping {u64 start,end,offset;const u8 *perm,*suffix;u32 suffix_bytes;};
static int mapline(const u8 *line,const u8 *end,struct mapping *m) {
    const u8 *p=line;
    if(!hex(&p,end,&m->start)||p==end||*p++!='-'||!hex(&p,end,&m->end)||p+6>end||*p++!=' ')return 0;
    m->perm=p;p+=5;if(!hex(&p,end,&m->offset))return 0;
    m->suffix=p;m->suffix_bytes=(u32)(end-p);
    return m->start&&m->end>m->start&&m->end<0x8000000000ULL;
}
static int find_bias(void) {
    const u8 *p=maps,*end=p+maps_bytes;u32 found=0;
    const char name[]="/usr/lib/appFw.so";
    while(p<end){const u8 *line=p;while(p<end&&*p!='\n')p++;if(p==end)return 0;
        struct mapping m;if(!mapline(line,p,&m))return 0;p++;
        if(m.suffix_bytes>=sizeof(name)-1&&same(m.suffix+m.suffix_bytes-sizeof(name)+1,name,sizeof(name)-1)&&
           m.offset==0&&same(m.perm,"r-xp",4)){
            bias=m.start;found++;
        }
    }
    return found==1&&!(bias&4095)&&bias<0x8000000000ULL-0x15000000;
}
static int in_mapping(u64 address,u32 size,int executable) {
    const u8 *p=maps,*end=p+maps_bytes;u32 found=0;
    while(p<end){const u8 *line=p;while(p<end&&*p!='\n')p++;struct mapping m;
        if(!mapline(line,p,&m))return 0;if(p<end)p++;
        if(address>=m.start&&address+size<=m.end&&m.perm[0]=='r'&&
           (executable<0?m.perm[1]=='w':!executable||m.perm[2]=='x'))found++;
    }
    return found==1;
}
static int branch(u64 site,u64 target,u32 *word) {
    long delta=(long)target-(long)site;
    if((delta&3)||delta<-(1L<<27)||delta>=1L<<27)return 0;
    *word=0x94000000u|((u32)(delta>>2)&0x3ffffffu);return 1;
}
static struct plut_trace *state(void){return(struct plut_trace *)(uintptr_t)trace_data;}
static struct bridge_config *config(void){return(struct bridge_config *)(uintptr_t)(resident+bridge_config_offset);}
static struct pp1_runtime *runtime(void){return(struct pp1_runtime *)(uintptr_t)(resident+pp1_runtime_offset);}
static int disable_adapter(void) {
    __atomic_fetch_and(&runtime()->gate,~PP1_OPEN,__ATOMIC_ACQ_REL);
    __atomic_store_n(&config()->enabled,0,__ATOMIC_RELEASE);
    return !(__atomic_load_n(&runtime()->gate,__ATOMIC_ACQUIRE)&~PP1_OPEN)&&
           !__atomic_load_n(&runtime()->pending_pairs,__ATOMIC_ACQUIRE);
}
static int configuration_ok(void) {
    struct bridge_config c;struct pp1_runtime r;
    if(!readmem(resident+bridge_config_offset,&c,sizeof(c))||
       !readmem(resident+pp1_runtime_offset,&r,sizeof(r)))return 0;
    if(c.magic!=PP1_CONFIG_MAGIC||c.abi!=1||c.bytes!=sizeof(c)||c.bias!=bias||c.enabled>1||c.reserved||
       c.padding[0]||c.padding[1]||c.padding[2]||c.padding[3]||r.magic!=PP1_RUNTIME_MAGIC||r.reserved)return 0;
    return 1;
}
static int getter_snapshot(void) {
    typedef const u32 *(*getter_fn)(void);
    struct pp1_diagnostic *d=&response.experiment;
    u64 general=(u64)(uintptr_t)((getter_fn)(uintptr_t)(bias+model_getters[0].getter))();
    u64 context=(u64)(uintptr_t)((getter_fn)(uintptr_t)(bias+model_getters[1].getter))();
    d->general_pointer=general;d->context_pointer=context;
    if(general!=bias+model_getters[0].va||context!=bias+model_getters[1].va||
       !in_mapping(general,4,0)||!in_mapping(context,4,0))return 0;
    d->flags|=3;
    if(!readmem(general,&d->general_first,4)||!readmem(context,&d->context_first,4)||
       !readmem(general,&d->general_second,4)||!readmem(context,&d->context_second,4))return 0;
    if(d->general_first!=d->general_second||d->context_first!=d->context_second)return 0;
    d->flags|=4;return 1;
}
static void veneer_image(u8 image[PLUT_VENEER_BYTES]) {
    const u32 ldr=0x58000050u,br=0xd61f0200u;
    for(u32 i=0;i<PLUT_HELPERS;i++){
        u64 target=resident+trace_offsets[i];
        memcpy(image+16*i,&ldr,4);memcpy(image+16*i+4,&br,4);memcpy(image+16*i+8,&target,8);
    }
}
static int veneers_ok(void) {
    u8 expected[PLUT_VENEER_BYTES],actual[PLUT_VENEER_BYTES];veneer_image(expected);
    return in_mapping(bias+veneer_base,PLUT_VENEER_BYTES,1)&&readmem(bias+veneer_base,actual,PLUT_VENEER_BYTES)&&same(actual,expected,PLUT_VENEER_BYTES);
}
static int locate_trace(void) {
    const u8 *p=maps,*end=p+maps_bytes;u32 found=0;
    while(p<end){const u8 *line=p;while(p<end&&*p!='\n')p++;struct mapping m;
        if(!mapline(line,p,&m))return -1;if(p<end)p++;
        if(m.end-m.start!=PLUT_CODE_BYTES||m.offset||!same(m.perm,"r-xp",4))continue;
        /* Reuse static reply storage: the enlarged trace must not occupy the
         * camera loader thread's stack. No events are published by this read. */
        struct plut_locator locator;
        if(!in_mapping(m.start+PLUT_CODE_BYTES,PLUT_NEAR_BYTES-PLUT_CODE_BYTES,-1)||
           !readmem(m.start+PLUT_CODE_BYTES,&locator,sizeof(locator))||locator.magic!=PLUT_LOCATOR_MAGIC)continue;
        if(locator.abi!=PLUT_ABI||locator.bytes!=sizeof(locator)||locator.reserved||
           locator.near_bytes!=PLUT_NEAR_BYTES||locator.data_bytes!=PLUT_DATA_BYTES||
           locator.state_bytes!=PLUT_DATA_BYTES||locator.pid!=response.pid||locator.bias!=bias||
           !locator.state_address||locator.state_address>=0x8000000000ULL-PLUT_DATA_BYTES||
           (locator.state_address<m.start+PLUT_NEAR_BYTES&&locator.state_address+PLUT_DATA_BYTES>m.start)||
           !in_mapping(locator.state_address,PLUT_DATA_BYTES,-1))return -1;
        struct plut_trace *header=&response.trace;
        if(!readmem(locator.state_address,header,PLUT_HEADER_BYTES)||header->magic!=PLUT_MAGIC||
           !same(header->token,locator.token,16))return -1;
        if(header->abi!=PLUT_ABI||header->bytes!=sizeof(*header)||header->bias!=bias||header->pid!=response.pid)return -1;
        if(header->active>1||header->phase<PLUT_READY||header->phase>PLUT_UNCERTAIN)return -1;
        if(header->capacity!=PLUT_EVENTS||header->event_bytes!=sizeof(struct plut_event))return -1;
        for(u32 i=0;i<PLUT_FIELDS;i++)if(header->model_addresses[i]!=bias+fields[i].va)return -1;
        for(u32 i=0;i<PLUT_HELPERS;i++)if(header->helpers[i]!=bias+helpers[i])return -1;
        for(u32 i=0;i<PLUT_SITES;i++){
            u32 expected;
            if(header->original[i]!=sites[i].word||
               !branch(bias+sites[i].va,bias+veneer_base+16*sites[i].kind,&expected)||
               header->patched[i]!=expected)return -1;
        }
        if(!readmem(m.start,scratch,sizeof(trace_code))||!same(scratch,trace_code,sizeof(trace_code)))return -1;
        resident=m.start;trace_data=locator.state_address;
        if(!veneers_ok()||!configuration_ok())return -1;found++;
    }
    return found>1?-1:(int)found;
}
static int guards_ok(int installed) {
    u8 d[32];
    for(u32 j=0;j<countof_guards();j++){
        const struct Guard *g=&guards[j];
        if(!in_mapping(bias+g->va,g->size,1)||!readmem(bias+g->va,scratch,g->size))return 0;
        if(installed&&g->va>=veneer_base&&g->va+g->size<=veneer_base+sizeof(veneer_original)){
            if(!veneers_ok())return 0;memcpy(scratch,veneer_original+g->va-veneer_base,g->size);
        }
        for(u32 i=0;installed&&i<PLUT_SITES;i++){
            u64 at=sites[i].va;
            if(at>=g->va&&at+4<=g->va+g->size){
                u32 actual;memcpy(&actual,scratch+at-g->va,4);
                u32 expected=state()->patched[i];
                if(actual!=expected&&actual!=sites[i].word)return 0;
                memcpy(scratch+at-g->va,&sites[i].word,4);
            }
        }
        digest(scratch,g->size,d);if(!same(d,g->digest,32))return 0;
    }
    return 1;
}
static int model_snapshot(void) {
    struct plut_model second[PLUT_FIELDS]={0};
    for(u32 pass=0;pass<2;pass++)for(u32 i=0;i<PLUT_FIELDS;i++){
        struct plut_model *m=pass?&second[i]:&response.models[i];m->id=fields[i].id;
        if(!in_mapping(bias+fields[i].va,8,0)||!readmem(bias+fields[i].va,&m->first,8))return 0;
    }
    return same(second,response.models,sizeof(second));
}
static void sync_code(u64 address,u64 size) {
    u64 ctr;__asm__ volatile("mrs %0,ctr_el0":"=r"(ctr));
    u64 dl=4ull<<((ctr>>16)&15),il=4ull<<(ctr&15);
    for(u64 p=address&~(dl-1);p<address+size;p+=dl)__asm__ volatile("dc cvau,%0"::"r"(p):"memory");
    __asm__ volatile("dsb ish":::"memory");
    for(u64 p=address&~(il-1);p<address+size;p+=il)__asm__ volatile("ic ivau,%0"::"r"(p):"memory");
    __asm__ volatile("dsb ish\nisb":::"memory");
}
static int change_word(u32 i,u32 before,u32 after) {
    u64 address=bias+sites[i].va,page=address&~4095ULL;u32 actual;
    if(!readmem(address,&actual,4)||actual!=before)return 0;
    if(sc(226,page,4096,7,0,0,0))return 0;
    __atomic_store_n((u32 *)(uintptr_t)address,after,__ATOMIC_RELEASE);sync_code(address,4);
    int restored=sc(226,page,4096,5,0,0,0)==0;
    if(!readmem(address,&actual,4)||actual!=after)restored=0;
    return restored;
}
static int restore_sites(void) {
    struct plut_trace *s=state();int good=1;
    if(!disable_adapter())return 0;
    __atomic_fetch_and(&s->capture_control,~PLUT_CAPTURE,__ATOMIC_RELEASE);
    __atomic_store_n(&s->active,0,__ATOMIC_RELEASE);
    for(u32 i=0;i<PLUT_SITES;i++){
        u32 actual=0;if(!readmem(bias+sites[i].va,&actual,4)){good=0;continue;}
        if(actual==sites[i].word){
            if(sc(226,(bias+sites[i].va)&~4095ULL,4096,5,0,0,0))good=0;
            continue;
        }
        if(actual!=s->patched[i]||!change_word(i,actual,sites[i].word))good=0;
    }
    s->phase=good?PLUT_STOPPED:PLUT_UNCERTAIN;if(!good)s->faults|=1;
    return good;
}
static int free_interval(u64 address) {
    const u8 *p=maps,*end=p+maps_bytes;
    while(p<end){const u8 *line=p;while(p<end&&*p!='\n')p++;struct mapping m;
        if(!mapline(line,p,&m))return 0;if(p<end)p++;
        if(address<m.end&&address+PLUT_NEAR_BYTES>m.start)return 0;
    }
    return 1;
}
static int reachable(u64 address) {
    u32 word;
    for(u32 i=0;i<PLUT_SITES;i++)
        if(!branch(bias+sites[i].va,address+trace_offsets[sites[i].kind],&word))return 0;
    return address&&address<0x8000000000ULL-PLUT_NEAR_BYTES;
}
static int try_mapping(u64 candidate) {
    allocation.hints_checked++;
    if(allocation.mmap_attempts>=128||!reachable(candidate)||!free_interval(candidate))return 0;
    /* A hint only: never MAP_FIXED or replacement of a live mapping. */
    allocation.mmap_attempts++;
    long mapped=sc(222,candidate,PLUT_NEAR_BYTES,3,0x22,-1,0);
    if(mapped<0){allocation.last_mmap_error=mapped;return 0;}
    if(!reachable((u64)mapped)){
        allocation.rejected_mappings++;
        if(sc(215,mapped,PLUT_NEAR_BYTES,0,0,0,0)){allocation.unmap_failures++;return -1;}
        return 0;
    }
    resident=(u64)mapped;return 1;
}
static int gap(u64 start,u64 end,int attempt) {
    if(end<=start)return 0;
    u64 candidate=(start+0xffffULL)&~0xffffULL;
    if(candidate>=allocation.reachable_high||candidate>end||end-candidate<PLUT_NEAR_BYTES)return 0;
    if(!attempt){
        allocation.free_gaps++;
        if(end-candidate>allocation.largest_gap_bytes){
            allocation.largest_gap_start=candidate;allocation.largest_gap_bytes=end-candidate;
        }
        return 0;
    }
    return try_mapping(candidate);
}
static int scan_gaps(int attempt) {
    const u8 *p=maps,*end=p+maps_bytes;
    u64 cursor=allocation.reachable_low,previous_end=0;
    u64 limit=allocation.reachable_high+PLUT_NEAR_BYTES-1;
    while(p<end){const u8 *line=p;while(p<end&&*p!='\n')p++;struct mapping m;
        if(!mapline(line,p,&m)||m.start<previous_end)return -1;if(p<end)p++;
        previous_end=m.end;if(!attempt)allocation.map_count++;
        if(m.end<=cursor)continue;
        if(m.start>cursor){
            int found=gap(cursor,m.start<limit?m.start:limit,attempt);if(found)return found;
        }
        if(m.end>cursor)cursor=m.end;if(cursor>=limit)return 0;
    }
    return gap(cursor,limit,attempt);
}
static int plan_allocation(void) {
    memset(&allocation,0,sizeof(allocation));memcpy(allocation.magic,"A6MA",4);allocation.version=2;
    allocation.required_near_bytes=0;allocation.required_data_bytes=PLUT_DATA_BYTES;
    u64 low=0x10000,high=0x8000000000ULL-PLUT_NEAR_BYTES;
    for(u32 i=0;i<PLUT_SITES;i++){
        u64 site=bias+sites[i].va,offset=trace_offsets[sites[i].kind];
        u64 first=site>(1ULL<<27)+offset?site-(1ULL<<27)-offset:0x10000;
        u64 last=site+(1ULL<<27)-offset;
        if(first>low)low=first;if(last<high)high=last;
    }
    allocation.reachable_low=(low+0xffffULL)&~0xffffULL;
    allocation.reachable_high=high;
    return low<high&&scan_gaps(0)==0;
}
static void report_allocation(void) {
    memcpy(&response.trace,&allocation,sizeof(allocation));response.flags|=8;
}
static void discard_unpublished(void) {
    if(trace_data&&sc(215,trace_data,PLUT_DATA_BYTES,0,0,0,0))allocation.unmap_failures++;
    if(resident&&sc(215,resident,PLUT_NEAR_BYTES,0,0,0,0))allocation.unmap_failures++;
    trace_data=0;resident=0;
}
static int prepare_veneers(void) {
    u8 image[PLUT_VENEER_BYTES],actual[PLUT_VENEER_BYTES];u64 address=bias+veneer_base,page=address&~4095ULL;
    if(!readmem(address,actual,PLUT_VENEER_BYTES)||!same(actual,veneer_original,PLUT_VENEER_BYTES))return 0;
    if(sc(226,page,4096,7,0,0,0))return 0;
    veneer_image(image);memcpy((void *)(uintptr_t)address,image,PLUT_VENEER_BYTES);sync_code(address,PLUT_VENEER_BYTES);
    if(!sc(226,page,4096,5,0,0,0)&&readmem(address,actual,PLUT_VENEER_BYTES)&&same(actual,image,PLUT_VENEER_BYTES))return 1;
    /* No callsite has been published. Roll back only our fixed padding bytes.
     * Keep allocated code/state on uncertainty rather than leaving dangling
     * veneer targets or claiming a failed permission restoration succeeded. */
    if(!sc(226,page,4096,7,0,0,0)){
        memcpy((void *)(uintptr_t)address,veneer_original,PLUT_VENEER_BYTES);sync_code(address,PLUT_VENEER_BYTES);
        if(!sc(226,page,4096,5,0,0,0)&&readmem(address,actual,PLUT_VENEER_BYTES)&&same(actual,veneer_original,PLUT_VENEER_BYTES))return 0;
    }
    return -1;
}
static u32 allocate_trace(void) {
    if(!plan_allocation())return PLUT_MAPS;allocation.required_near_bytes=0;
    /* Fixed guarded text-tail veneers retain the original LR. Code and event
     * data use ordinary anonymous mappings with no address hint or replacement. */
    allocation.mmap_attempts=1;
    long code=sc(222,0,PLUT_NEAR_BYTES,3,0x22,-1,0);
    if(code<0){allocation.last_mmap_error=code;return PLUT_ALLOCATION;}
    resident=(u64)code;
    if(!resident||resident>=0x8000000000ULL-PLUT_NEAR_BYTES){discard_unpublished();return PLUT_ALLOCATION;}
    {
        long data=sc(222,0,PLUT_DATA_BYTES,3,0x22,-1,0);
        if(data<0){allocation.last_state_mmap_error=data;discard_unpublished();return PLUT_ALLOCATION;}
        trace_data=(u64)data;
        if(!trace_data||trace_data>=0x8000000000ULL-PLUT_DATA_BYTES||
           (trace_data<resident+PLUT_NEAR_BYTES&&trace_data+PLUT_DATA_BYTES>resident)){
            discard_unpublished();return PLUT_ALLOCATION;
        }
        memcpy((void *)(uintptr_t)resident,trace_code,sizeof(trace_code));
        struct plut_trace *s=state();memset(s,0,sizeof(*s));
        struct plut_locator *locator=(struct plut_locator *)(uintptr_t)(resident+PLUT_CODE_BYTES);
        memset(locator,0,sizeof(*locator));locator->magic=PLUT_LOCATOR_MAGIC;locator->abi=PLUT_ABI;
        locator->bytes=sizeof(*locator);locator->state_address=trace_data;locator->state_bytes=PLUT_DATA_BYTES;
        locator->pid=response.pid;locator->bias=bias;memcpy(locator->token,request.token,16);
        locator->near_bytes=PLUT_NEAR_BYTES;locator->data_bytes=PLUT_DATA_BYTES;
        s->magic=PLUT_MAGIC;s->abi=PLUT_ABI;s->bytes=sizeof(*s);
        memcpy(s->token,request.token,16);s->pid=response.pid;s->bias=bias;
        s->capacity=PLUT_EVENTS;s->event_bytes=sizeof(struct plut_event);
        for(u32 j=0;j<PLUT_FIELDS;j++)s->model_addresses[j]=bias+fields[j].va;
        for(u32 j=0;j<PLUT_HELPERS;j++)s->helpers[j]=bias+helpers[j];
        for(u32 j=0;j<PLUT_SITES;j++){
            s->original[j]=sites[j].word;
            if(!branch(bias+sites[j].va,bias+veneer_base+16*sites[j].kind,&s->patched[j])){
                discard_unpublished();return PLUT_NO_SPACE;
            }
        }
        struct bridge_config *c=config();c->magic=PP1_CONFIG_MAGIC;c->abi=1;c->bytes=sizeof(*c);c->bias=bias;
        runtime()->magic=PP1_RUNTIME_MAGIC;
        s->phase=PLUT_READY;sync_code(resident,PLUT_CODE_BYTES);
        long protect=sc(226,resident,PLUT_CODE_BYTES,5,0,0,0);
        if(protect){
            allocation.last_protection_error=protect;
            discard_unpublished();return PLUT_PROTECTION;
        }
        return PLUT_OK;
    }
}
static int trace_snapshot(void) {
    struct plut_trace *s=state(),*to=&response.trace;
    if(!readmem((u64)(uintptr_t)s,to,PLUT_HEADER_BYTES))return 0;
    for(u32 i=0;i<PLUT_EVENTS;i++){
        struct plut_event *event=&s->events[i];
        if(!__atomic_load_n(&event->committed,__ATOMIC_ACQUIRE))continue;
        memcpy(&to->events[i],event,sizeof(*event));
    }
    struct pp1_diagnostic *d=&response.experiment;struct pp1_runtime *r=runtime();
    d->enabled=__atomic_load_n(&config()->enabled,__ATOMIC_ACQUIRE);
    d->gate=__atomic_load_n(&r->gate,__ATOMIC_ACQUIRE);
    d->pending_pairs=__atomic_load_n(&r->pending_pairs,__ATOMIC_ACQUIRE);
    d->admitted=__atomic_load_n(&r->admitted,__ATOMIC_RELAXED);
    d->resolved=__atomic_load_n(&r->resolved,__ATOMIC_RELAXED);
    d->valid_frames=__atomic_load_n(&r->valid_frames,__ATOMIC_RELAXED);
    d->bad_frames=__atomic_load_n(&r->bad_frames,__ATOMIC_RELAXED);
    d->config_address=resident+bridge_config_offset;d->runtime_address=resident+pp1_runtime_offset;
    d->flags|=8;response.flags|=4;return 1;
}
static u32 execute(void) {
    u8 zero[16]={0};
    if(!same(request.magic,"A6PQ",4)||request.abi!=PLUT_ABI||request.action!=PLUT_ACTION)return PLUT_BAD_REQUEST;
    if((PLUT_ACTION==PLUT_RESUME||PLUT_ACTION==PLUT_MARK)?!request.marker:request.marker!=0)return PLUT_BAD_REQUEST;
    if((PLUT_ACTION==PLUT_SNAPSHOT)!=same(request.token,zero,16))return PLUT_BAD_REQUEST;
    response.pid=(u64)sc(172,0,0,0,0,0,0);
    if(!load_maps()||!find_bias())return PLUT_MAPS;response.bias=bias;
    memfd=sc(56,-100,(long)"/proc/self/mem",0x80000,0,0,0);if(memfd<0)return PLUT_MEMORY;
    int found=locate_trace();if(found<0)return PLUT_IDENTITY;
    response.resident=resident;
    if(!guards_ok(found==1))return PLUT_IDENTITY;response.flags|=1;
    int getters_good=getter_snapshot();
    if(!getters_good&&(PLUT_ACTION==PLUT_SNAPSHOT||PLUT_ACTION==PLUT_INSTALL))return PLUT_READINESS;
    if(model_snapshot())response.flags|=2;
    else if(PLUT_ACTION==PLUT_SNAPSHOT||PLUT_ACTION==PLUT_INSTALL)return PLUT_CHANGED;
    /* Unstable settings must not prevent reading, pausing or restoring this
     * owned observer. Their unconfirmed samples remain explicitly unflagged. */
    if(PLUT_ACTION==PLUT_SNAPSHOT){
        if(!found){if(!plan_allocation())return PLUT_MAPS;report_allocation();return PLUT_OK;}
        return trace_snapshot()?PLUT_OK:PLUT_MEMORY;
    }
    if(PLUT_ACTION==PLUT_INSTALL){
        if(found)return PLUT_OCCUPIED;
        u32 allocated=allocate_trace();
        if(allocated){report_allocation();return allocated;}response.resident=resident;
        if(!guards_ok(0)){
            response.flags&=~1u;discard_unpublished();response.resident=0;return PLUT_IDENTITY;
        }
        struct plut_trace *s=state();int good=1;
        int prepared=prepare_veneers();
        if(prepared<=0){
            if(!prepared){discard_unpublished();response.resident=0;report_allocation();return PLUT_PROTECTION;}
            s->phase=PLUT_UNCERTAIN;s->faults|=1;trace_snapshot();return PLUT_TRANSACTION;
        }
        for(u32 i=0;i<PLUT_SITES;i++)if(!change_word(i,sites[i].word,s->patched[i])){good=0;break;}
        if(!good){restore_sites();trace_snapshot();return PLUT_TRANSACTION;}
        __atomic_store_n(&s->active,1,__ATOMIC_RELEASE);s->phase=PLUT_RUNNING;
        trace_snapshot();return PLUT_OK;
    }
    if(!found)return PLUT_NO_TRACE;
    if(!same(state()->token,request.token,16))return PLUT_OWNER;
    if(PLUT_ACTION==PLUT_READ)return trace_snapshot()?PLUT_OK:PLUT_MEMORY;
    if(PLUT_ACTION==PLUT_STOP){
        if(!disable_adapter()){trace_snapshot();return PLUT_BUSY;}
        int good=restore_sites();trace_snapshot();return good?PLUT_OK:PLUT_TRANSACTION;
    }
    if(PLUT_ACTION==PLUT_ENABLE||PLUT_ACTION==PLUT_DISABLE){
        if(state()->phase!=PLUT_RUNNING||!__atomic_load_n(&state()->active,__ATOMIC_ACQUIRE))return PLUT_TERMINAL;
        if(PLUT_ACTION==PLUT_DISABLE){disable_adapter();return trace_snapshot()?PLUT_OK:PLUT_MEMORY;}
        if(!getters_good||!(response.flags&2)||!runtime()->valid_frames||runtime()->bad_frames||
           !(__atomic_load_n(&state()->capture_control,__ATOMIC_ACQUIRE)&PLUT_CAPTURE)||
           __atomic_load_n(&state()->reserved_slots,__ATOMIC_RELAXED)>=PLUT_EVENTS-16){trace_snapshot();return PLUT_READINESS;}
        if(!disable_adapter()){trace_snapshot();return PLUT_BUSY;}
        __atomic_store_n(&config()->enabled,1,__ATOMIC_RELEASE);
        __atomic_store_n(&runtime()->gate,PP1_OPEN,__ATOMIC_RELEASE);
        return trace_snapshot()?PLUT_OK:PLUT_MEMORY;
    }
    if(PLUT_ACTION==PLUT_PAUSE||PLUT_ACTION==PLUT_RESUME||PLUT_ACTION==PLUT_MARK){
        if(state()->phase!=PLUT_RUNNING||!__atomic_load_n(&state()->active,__ATOMIC_ACQUIRE))return PLUT_TERMINAL;
        if(PLUT_ACTION==PLUT_PAUSE)disable_adapter();
        ((void (*)(u32,u32))(uintptr_t)(resident+trace_control_offset))(PLUT_ACTION,request.marker);
        return trace_snapshot()?PLUT_OK:PLUT_MEMORY;
    }
    return PLUT_BAD_REQUEST;
}
__attribute__((constructor)) static void plut_service_entry(void) {
    memset(&response,0,sizeof(response));long fd=sc(56,-100,(long)exchange,0x80000,0,0,0);
    if(fd<0)return;
    u8 tail;int valid=exact(63,fd,(u8 *)&request,sizeof(request),0);
    long extra=sc(63,fd,(long)&tail,1,0,0,0),closed=sc(57,fd,0,0,0,0,0);
    memcpy(response.magic,"A6PR",4);response.abi=PLUT_ABI;
    memcpy(response.experiment.magic,"A6PX",4);response.experiment.bytes=sizeof(response.experiment);
    memcpy(response.nonce,request.nonce,16);response.action=PLUT_ACTION;response.bytes=sizeof(response);
    response.status=(!valid||extra||closed)?PLUT_BAD_REQUEST:execute();
    if(memfd>=0&&sc(57,memfd,0,0,0,0,0))response.status=PLUT_IO;
    fd=sc(56,-100,(long)exchange,0x80241,0600,0,0);if(fd<0)return;
    if(exact(64,fd,(u8 *)&response,sizeof(response),0))sc(82,fd,0,0,0,0,0);
    sc(57,fd,0,0,0,0,0);
}
