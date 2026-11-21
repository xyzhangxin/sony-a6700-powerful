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

#define DATA_SIZE 393216
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
/* Fixed nonblocking boot diagnostics. Sources are read-only; no clearing,
 * syslog control, device writes, ioctl, DMA, signals, or application calls. */
struct log_spec {const char *path;u32 capacity,directory;};
static const struct log_spec log_specs[]={
 {"/proc/klog",262144,0},{"/log/logdata.dat",8192,0},
 {"/proc/uptime",128,0},{"/proc/sys/kernel/random/boot_id",64,0},
 {"/proc/cmdline",4096,0},{"/sys/power/image_mode",32,0},
 {"/sys/power/image_dev",64,0},{"/log",8192,1},
 {"/log/logdata_cp.dat",8192,0},
 {"/log/logdata_core.dat",8192,0},
 {"/proc/net/dev",8192,0},{"/proc/net/route",8192,0},
 {"/proc/net/wireless",8192,0},{"/proc/net/fib_trie",8192,0}
};
static u8 log_chunk[16384];
static void finish(u32 status);
static void log64(u8 *p,u64 v){for(u32 i=0;i<8;i++)p[i]=(u8)(v>>(8*i));}
__attribute__((constructor)) static void run(void){
 for(u32 i=0;i<sizeof(result);i++)result[i]=0;
 for(u32 i=0;i<32;i++)words[i]=0;
 long fd=sc(56,-100,(long)exchange,0,0,0,0);u8 tail;if(fd<0)return;
 int ok=io(63,fd,request,40,0);long extra=sc(63,fd,(long)&tail,1,0,0,0);sc(57,fd,0,0,0,0,0);
 if(!ok||extra||!same(request,(const u8*)"A67P",4)||get32(request+4)!=1||get32(request+24)!=28||get32(request+28)||get32(request+32)||get32(request+36)!=DATA_SIZE){finish(2);return;}
 words[0]=1;words[1]=sc(172,0,0,0,0,0,0);words[2]=sc(178,0,0,0,0,0,0);
 words[3]=sizeof(log_specs)/sizeof(log_specs[0]);u32 cursor=1280;
 for(u32 i=0;i<sizeof(log_specs)/sizeof(log_specs[0]);i++){
  const struct log_spec *s=log_specs+i;u8 *r=result+44+256+i*64,*out=result+44+cursor;
  u32 used=0,flags=0;u64 total=0;long error=0;
  put32(r+16,cursor);put32(r+24,s->capacity);cursor+=s->capacity;
  /* O_RDONLY|O_NONBLOCK|O_NOFOLLOW|O_CLOEXEC (+ O_DIRECTORY). */
  fd=sc(56,-100,(long)s->path,0xa0800|(s->directory?0x4000:0),0,0,0);
  if(fd<0)error=fd;
  else {
   for(u32 attempt=0;attempt<128;attempt++){
    long n=sc(s->directory?61:63,fd,(long)log_chunk,s->directory?8192:sizeof(log_chunk),0,0,0);
    if(n==-4){if(attempt==127){flags|=2;error=-4;}continue;}
    if(n<0){error=n;break;}
    if(!n){flags|=1;break;}
    if(n>(long)sizeof(log_chunk)){error=-75;break;}
    u64 previous=total;total+=(u64)n;
    if(s->directory){
     /* Keep one complete getdents batch; never turn its names into paths. */
     if(used){flags|=2;break;}
     if((u64)n>s->capacity){error=-75;break;}
     for(u32 j=0;j<(u32)n;j++)out[j]=log_chunk[j];used=(u32)n;
    }else {
     /* Every fixed capacity is a power of two. Retain a ring while reading,
      * then rotate once, avoiding repeated 128KiB shifts in the live probe. */
     for(u32 j=0;j<(u32)n;j++)out[(previous+j)&(s->capacity-1)]=log_chunk[j];
     used=total>s->capacity?s->capacity:(u32)total;
    }
    if(attempt==127)flags|=2;
   }
   long closed=sc(57,fd,0,0,0,0,0);if(closed&&!error)error=closed;
  }
  if(total>used){
   flags|=4;
   if(!s->directory){
    u32 split=total&(s->capacity-1);
    u32 starts[3]={0,split,0},ends[3]={split,s->capacity,s->capacity};
    for(u32 k=0;k<3;k++)for(u32 a=starts[k],b=ends[k];a<b&&a<--b;a++){
     u8 t=out[a];out[a]=out[b];out[b]=t;
    }
   }
  }
  log64(r,(u64)error);log64(r+8,total);put32(r+20,used);put32(r+28,flags);
 }
 words[4]=cursor;finish(0);
}

static void finish(u32 status) {
    result[0]='A';result[1]='6';result[2]='7';result[3]='B';put32(result+4,1);
    for(u32 i=0;i<16;i++)result[8+i]=request[8+i];
    put32(result+24,status);put32(result+28,28);
    for(u32 i=0;i<8;i++)result[32+i]=request[28+i];
    put32(result+40,DATA_SIZE);
    for(u32 i=0;i<32;i++)for(u32 j=0;j<8;j++)result[44+i*8+j]=(u8)(words[i]>>(8*j));
    long fd=sc(56,-100,(long)exchange,0x241,0600,0,0);
    if(fd<0)return;
    if(io(64,fd,result,sizeof(result),0))sc(82,fd,0,0,0,0,0);
    sc(57,fd,0,0,0,0,0);
}
