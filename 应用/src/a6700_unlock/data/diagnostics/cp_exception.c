/* Fixed FL23 runtime capability probe. No firmware/parameter/WBI write API.
 * The verified arena constructor writes only one owned anonymous page.
 * Existing app mappings and files are compared through checked pipe copies.
 * Service result/config files are restored by the host ScratchTransaction.
 */
typedef unsigned char u8;typedef unsigned int u32;typedef unsigned long long u64;
static long sc(long n,long a,long b,long c,long d,long e,long f) {
register long x0 __asm__("x0")=a;register long x1 __asm__("x1")=b;
register long x2 __asm__("x2")=c;register long x3 __asm__("x3")=d;
register long x4 __asm__("x4")=e;register long x5 __asm__("x5")=f;
register long x8 __asm__("x8")=n;
__asm__ volatile("svc #0":"+r"(x0):"r"(x1),"r"(x2),"r"(x3),"r"(x4),"r"(x5),"r"(x8):"memory","cc");return x0;}
void *memset(void *p,int value,unsigned long n) {
    volatile unsigned char *bytes=p;for(unsigned long i=0;i<n;i++)bytes[i]=(unsigned char)value;return p;
}
#include "sha256.h"

#define DATA_SIZE 1024
static unsigned char request[40],result[44+DATA_SIZE],maps[524288],file_page[4096];
static const char exchange[]="/tmp/jiritsu/tmmc.jit";
static const u64 pages[4]={0,0x378b000,0x378c000,0x378d000};
static u64 words[32];
static int io(long nr,long fd,u8 *data,u64 size,u64 offset) {
    u64 done=0;
    for(u32 attempt=0;done<size&&attempt<1024;attempt++) {
        long n=sc(nr,fd,(long)(data+done),size-done,offset+done,0,0);
        if(n==-4)continue;
        if(n<=0||(u64)n>size-done)return 0;
        done+=n;
    }
    return done==size;
}
static u32 get32(const u8 *p) {u32 n=0;for(u32 i=0;i<4;i++)n|=(u32)p[i]<<(i*8);return n;}
static void put32(u8 *p,u32 n) {for(u32 i=0;i<4;i++)p[i]=(u8)(n>>(i*8));}
static int same(const u8 *a,const u8 *b,u64 n) {for(u64 i=0;i<n;i++)if(a[i]!=b[i])return 0;return 1;}
static u64 hex(const u8 **p,const u8 *end) {
    u64 value=0;u32 count=0;
    while(*p<end) {
        u8 c=**p;u32 d=c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:16;
        if(d==16)break;
        if(++count>16)return 0;
        value=(value<<4)|d;(*p)++;
    }
    return count?value:0;
}
static int locate(u64 length,u64 *bias,u64 *hint) {
    const u8 *at=maps,*end=maps+length;u32 found=0;
    while(at<end) {
        const u8 *line=at;while(at<end&&*at!='\n')at++;
        if(at==end)return 0;
        const u8 *p=line;u64 start=hex(&p,at);if(p==at||*p++!='-')return 0;
        u64 stop=hex(&p,at);if(!start||stop<=start||p+6>=at||*p++!=' ')return 0;
        int rx=same(p,(const u8*)"r-xp ",5);p+=5;
        u64 offset=hex(&p,at);if(p==at||*p!=' ')return 0;
        const char suffix[]="/usr/lib/appFw.so";
        if(rx&&!offset&&at-line>=sizeof(suffix)-1&&same(at-(sizeof(suffix)-1),(const u8*)suffix,sizeof(suffix)-1)) {
            *bias=start;found++;
            if(stop-start!=0x4489000)return 0;
        }
        at++;
    }
    if(found!=1||(*bias&4095))return 0;
    u64 candidate=*bias+0x20000000;
    at=maps;
    while(at<end) {
        const u8 *line=at;while(at<end&&*at!='\n')at++;
        const u8 *p=line;u64 start=hex(&p,at);p++;u64 stop=hex(&p,at);
        if(candidate<stop&&candidate+8192>start)candidate=(stop+4095)&~4095ull;
        at++;
    }
    if(candidate>=0x8000000000ull||candidate-*bias>=0x100000000ull)return 0;
    *hint=candidate;return 1;
}
static int checked_copy(u64 address,u8 *dest,u64 n) {
    int fd[2];if(n>4096||sc(59,(long)fd,0x80000,0,0,0,0)<0)return 0;
    long written=sc(64,fd[1],address,n,0,0,0);
    int ok=written==(long)n&&io(63,fd[0],dest,n,0);
    long a=sc(57,fd[1],0,0,0,0,0),b=sc(57,fd[0],0,0,0,0,0);
    return ok&&!a&&!b;
}
/* Fixed CP-to-owned-AP DMA read of four fingerprints only.
 * No firmware/parameter/WBI write, no arbitrary CP address, no /dev/mem.
 */
#define DMA_SIZE 4096u
#define DMA_ROWS 4u
#define DMA_ROW 40u
#define DMA_DATA (256u+DMA_ROWS*DMA_ROW)
#define DMA_MAGIC 0x31414d444350ULL
static const u64 dma_targets[DMA_ROWS]={0x1084b0,0x23db30,0x23dd28,0x41f97e};
static const u32 dma_lengths[DMA_ROWS]={4,8,8,4};
static void finish(u32 status);
static u64 dma_get64(const u8 *p){u64 v=0;for(u32 i=0;i<8;i++)v|=(u64)p[i]<<(8*i);return v;}
static void dma_put64(u8 *p,u64 v){for(u32 i=0;i<8;i++)p[i]=(u8)(v>>(8*i));}
static u64 dma_maps_base(void){
    long fd=sc(56,-100,(long)"/proc/self/maps",0x80000,0,0,0);if(fd<0)return 0;
    u32 length=0;int ok=0;
    for(u32 i=0;i<1024&&length<sizeof(maps);i++){
        long n=sc(63,fd,(long)(maps+length),sizeof(maps)-length,0,0,0);
        if(n==-4)continue;if(n<0)break;if(!n){ok=1;break;}
        if((u64)n>sizeof(maps)-length)break;length+=n;
    }
    long closed=sc(57,fd,0,0,0,0,0);if(!ok||closed)return 0;
    const u8 *at=maps,*end=maps+length;u64 base=0;u32 found=0;
    while(at<end){
        const u8 *line=at;while(at<end&&*at!='\n')at++;
        const u8 *p=line;u64 start=hex(&p,at);if(!start||p==at||*p++!='-')return 0;
        u64 stop=hex(&p,at);if(stop<=start||p+6>=at||*p++!=' ')return 0;
        const u8 *perms=p;p+=5;u64 offset=hex(&p,at);if(p==at||*p!=' ')return 0;
        const char suffix[]="/usr/lib/libGetLog.so";
        const u8 *hit=0;
        for(const u8 *q=line;q+sizeof(suffix)-1<=at;q++)if(same(q,(const u8*)suffix,sizeof(suffix)-1))hit=q;
        if(hit&&same(perms,(const u8*)"r-xp ",5)&&offset<start&&!((start-offset)&4095)){base=start-offset;found++;}
        at++;
    }
    if(found!=1||(base&4095))return 0;
    if(dma_get64((const u8*)(base+0x51e8))!=0x910003fda9bb7bfdULL)return 0;
    if(dma_get64((const u8*)(base+0x50b8))!=0x7100001fa9b97bfdULL)return 0;
    if(dma_get64((const u8*)(base+0x52fc))!=0x528007a1a9bc7bfdULL)return 0;
    return base;
}
static u32 dma_read(u64 base,u64 source,u32 length,u8 *out){
    typedef long (*alloc_fn)(u32,u32*);typedef long (*dma_fn)(int,u64,u64,u32);typedef long (*free_fn)(u32);
    alloc_fn alloc=(alloc_fn)(base+0x51e8);dma_fn transfer=(dma_fn)(base+0x50b8);free_fn release=(free_fn)(base+0x52fc);
    u32 id=0;long address=alloc(length,&id);
    if(address<=0||(u64)address>=(u64)-4095)return 1;
    long fd=sc(56,-100,(long)"/dev/stream",2,0,0,0);
    if(fd<0){release(id);return 2;}
    u64 delta=(u64)address&4095;u64 span=(u64)length+delta;
    long mapping=sc(222,0,(long)span,3,1,fd,(long)((u64)address-delta));
    if(mapping<0||(u64)mapping>=(u64)-4095){sc(57,fd,0,0,0,0,0);release(id);return 3;}
    long rc=transfer(0,source,(u64)address,length);u32 status=0;
    if(rc)status=4;
    else {const u8 *data=(const u8*)((u64)mapping+delta);for(u32 i=0;i<length;i++)out[i]=data[i];}
    if(sc(215,mapping,(long)span,0,0,0,0)&&!status)status=5;
    if(sc(57,fd,0,0,0,0,0)&&!status)status=6;
    if(release(id)&&!status)status=7;
    return status;
}
/* Fixed CP exception pointer and bounded frame, using unchanged DMA helpers. */
static const u8 exception_fingerprints[4][8]={
 {0x02,0x10,0xa0,0x01},{0xdf,0xf8,0x00,0xf0,0xa5,0x2e,0xc1,0x00},
 {0xdf,0xf8,0x00,0xf0,0x65,0x2e,0xc1,0x00},{0x49,0xf2,0x00,0x01}
};
__attribute__((constructor)) static void run(void){
 for(u32 i=0;i<sizeof(result);i++)result[i]=0;
 for(u32 i=0;i<32;i++)words[i]=0;
 long fd=sc(56,-100,(long)exchange,0,0,0,0);u8 tail;if(fd<0)return;
 int ok=io(63,fd,request,40,0);long extra=sc(63,fd,(long)&tail,1,0,0,0);sc(57,fd,0,0,0,0,0);
 if(!ok||extra||!same(request,(const u8*)"A67P",4)||get32(request+4)!=1||get32(request+24)!=30||get32(request+28)||get32(request+32)||get32(request+36)!=1024){finish(2);return;}
 words[0]=1;words[1]=sc(172,0,0,0,0,0,0);words[2]=sc(178,0,0,0,0,0,0);words[9]=0x315058454350ULL;
 u64 base=dma_maps_base();words[3]=base;if(!base){finish(3);return;}
 u8 *data=result+44;
 for(u32 i=0;i<4;i++){
  words[7]=i+1;words[8]=dma_read(base,dma_targets[i],dma_lengths[i],data+256+i*16);
  if(words[8]){finish(4);return;}
  if(!same(data+256+i*16,exception_fingerprints[i],dma_lengths[i])){finish(3);return;}
  words[4]++;
 }
 words[7]=5;words[8]=dma_read(base,0x19e9fec,4,data+320);if(words[8]){finish(4);return;}
 u32 ptr=get32(data+320);words[5]=ptr;
 if(!ptr){finish(0);return;}
 if((ptr&3)||ptr<0x1400000||ptr>0x2000000-256){words[6]=2;finish(0);return;}
 words[6]=1;words[7]=6;words[8]=dma_read(base,ptr,256,data+384);if(words[8]){finish(4);return;}
 words[10]=256;words[7]=7;words[8]=dma_read(base,0x19e9fec,4,data+324);if(words[8]){finish(4);return;}
 if(get32(data+324)!=ptr){finish(5);return;}
 finish(0);
}

static void finish(u32 status) {
    result[0]='A';result[1]='6';result[2]='7';result[3]='B';put32(result+4,1);
    for(u32 i=0;i<16;i++)result[8+i]=request[8+i];
    put32(result+24,status);put32(result+28,30);
    for(u32 i=0;i<8;i++)result[32+i]=request[28+i];
    put32(result+40,DATA_SIZE);
    for(u32 i=0;i<32;i++)for(u32 j=0;j<8;j++)result[44+i*8+j]=(u8)(words[i]>>(8*j));
    long fd=sc(56,-100,(long)exchange,0x241,0600,0,0);
    if(fd<0)return;
    if(io(64,fd,result,sizeof(result),0))sc(82,fd,0,0,0,0,0);
    sc(57,fd,0,0,0,0,0);
}
