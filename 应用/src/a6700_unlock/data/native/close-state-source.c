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
#include "close-state-sha256.h"

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
/* Fixed model observation only. No setter, ptrace, signal or device write.
 * All source addresses and code fingerprints are compiled into the profile.
 * The only write is the existing temporary service exchange result. */
#include "close-state-profile.h"
#ifndef CLOSE_KIND
#define CLOSE_KIND 75
#endif
#ifdef CLOSE_RESET
static u32 close_reset_check(long fd,u64 bias);
static u32 close_reset_apply(u64 bias);
#endif
static void finish(u32 status);
static u8 close_second[1024],close_guard[64],close_digest[32];
static u64 close_u64(const u8 *p){u64 v=0;for(u32 i=0;i<8;i++)v|=(u64)p[i]<<(8*i);return v;}
static int close_library(u64 length,u64 *base){
 const u8 *p=maps,*end=p+length;u32 count=0;
 while(p<end){const u8 *line=p;while(p<end&&*p!='\n')p++;const u8 *e=p;if(p<end)p++;
  const u8 *q=line;u64 lo=hex(&q,e);if(q==e||*q++!='-')return 0;u64 hi=hex(&q,e);
  if(!lo||hi<=lo||q+6>=e||*q++!=' ')return 0;const u8 *perm=q;q+=5;u64 offset=hex(&q,e);
  const char suffix[]="/usr/lib/libInfraFtpClient.so";
  if(e-line>=sizeof(suffix)-1&&same(e-sizeof(suffix)+1,(const u8*)suffix,sizeof(suffix)-1)&&
     same(perm,(const u8*)"r-xp",4)&&!offset){*base=lo;count++;}
 }
 return count==1&&*base&& !(*base&4095)&&*base<0x8000000000ULL-0x50000;
}
static long close_file(const char *path,u8 *to,u32 capacity){
 long fd=sc(56,-100,(long)path,0x80000,0,0,0);if(fd<0)return fd;
 u32 used=0;long rc=-7;
 for(u32 i=0;i<1024;i++){
  if(used==capacity)break;
  long got=sc(63,fd,(long)(to+used),capacity-used,0,0,0);
  if(got==-4)continue;if(got<0){rc=got;break;}
  if(!got){rc=used;break;}
  if((u64)got>capacity-used){rc=-5;break;}used+=got;
 }
 long done=sc(57,fd,0,0,0,0,0);return done?done:rc;
}
static int close_read(long fd,u64 address,u8 *to,u32 size){
 return size<=64&&io(67,fd,to,size,address);
}
static int close_pointer(u64 p,u32 bytes){return p&&!(p&7)&&p<0x8000000000ULL-bytes;}
__attribute__((constructor)) static void run(void){
 long fd=sc(56,-100,(long)exchange,0,0,0,0);u8 tail;if(fd<0)return;
 int valid=io(63,fd,request,40,0);long extra=sc(63,fd,(long)&tail,1,0,0,0);
 long closed=sc(57,fd,0,0,0,0,0);
 if(!valid||extra||closed||!same(request,(const u8*)"A67P",4)||get32(request+4)!=1||
    get32(request+24)!=CLOSE_KIND||get32(request+36)!=DATA_SIZE){finish(2);return;}
#ifdef CLOSE_RESET
 if(get32(request+28)<1||get32(request+28)>4||get32(request+32)>3){finish(2);return;}
#else
 if(get32(request+28)||get32(request+32)){finish(2);return;}
#endif
 words[0]=1;words[1]=sc(172,0,0,0,0,0,0);words[2]=sc(178,0,0,0,0,0,0);
 long n=close_file("/proc/self/maps",maps,sizeof(maps));u64 bias=0,hint=0;
 if(n<=0||!locate(n,&bias,&hint)||bias>=0x8000000000ULL-0x15000000ULL){finish(3);return;}
 words[3]=bias;u64 library=0;
 if(!close_library(n,&library)){finish(3);return;}
 words[8]=library;
 fd=sc(56,-100,(long)"/proc/self/mem",0x80000,0,0,0);if(fd<0){finish(4);return;}
 u32 status=0;
 for(u32 pass=0;pass<2;pass++){
  if(!close_read(fd,library+0x241a8,close_guard,60)){status=4;goto done;}
  SHA256 lib_state;sha_init(&lib_state);sha_add(&lib_state,close_guard,60);sha_end(&lib_state,close_digest);
  if(!same(close_digest,close_library_guard,32)){status=5;goto done;}
  for(u32 j=0;j<CLOSE_RESOURCE_GUARDS;j++){
   if(!close_read(fd,library+close_resource_guards[j].address,close_guard,close_resource_guards[j].size)){status=4;goto done;}
   SHA256 s;sha_init(&s);sha_add(&s,close_guard,close_resource_guards[j].size);sha_end(&s,close_digest);
   if(!same(close_digest,close_resource_guards[j].digest,32)){status=5;goto done;}
  }
  if(!close_read(fd,library+0x46ea8,close_guard,8)||close_u64(close_guard)!=library+0x483f8){status=5;goto done;}
  if(!close_read(fd,library+0x46e78,close_guard,8)||close_u64(close_guard)!=library+0x48400){status=5;goto done;}
  if(!close_read(fd,library+0x483f8,close_guard,12)){status=4;goto done;}
  u64 table=close_u64(close_guard);u32 threads=get32(close_guard+8);
  if(pass&&(table!=words[9]||threads!=words[10])){status=6;goto done;}
  words[9]=table;words[10]=threads;
  if(!close_read(fd,library+0x483b8,close_guard,8)){status=4;goto done;}
  u64 client=close_u64(close_guard);
  if(pass&&client!=words[11]){status=6;goto done;}words[11]=client;
  if(!close_read(fd,library+0x48408,close_guard,8)){status=4;goto done;}
  u64 media=close_u64(close_guard);
  if(pass&&media!=words[12]){status=6;goto done;}words[12]=media;
  if(!close_read(fd,library+0x48528,close_guard,16)){status=4;goto done;}
  u64 channels=close_u64(close_guard);u32 channel_count=get32(close_guard+12);
  if(channel_count>2||(channel_count&&!close_pointer(channels,channel_count*0x1a8))){status=4;goto done;}
  if(pass&&(channels!=words[14]||channel_count!=words[13])){status=6;goto done;}
  words[13]=channel_count;words[14]=channels;
  for(u32 j=0;j<channel_count;j++){
   u8 *row=(pass?close_second:result+44)+768+j*32;
   if(!close_read(fd,channels+j*0x1a8,close_guard,24)){status=4;goto done;}
   put32(row,j);put32(row+4,get32(close_guard));
   for(u32 k=0;k<8;k++)row[8+k]=close_guard[16+k];
  }
  if(client){
   if(!close_pointer(client,0xac4)||!close_read(fd,client+0xac0,close_guard,4)){status=4;goto done;}
   u32 state=get32(close_guard);if(pass&&state!=words[15]){status=6;goto done;}words[15]=state;
  }
  if(media){
   if(!close_pointer(media,0x80)||!close_read(fd,media+0x40,close_guard,32)){status=4;goto done;}
   u64 first=close_u64(close_guard),first_node=close_u64(close_guard+24);
   if(!close_read(fd,media+0x60,close_guard,32)){status=4;goto done;}
   u64 last=close_u64(close_guard),last_node=close_u64(close_guard+24);
   if(pass&&(words[16]!=first||words[17]!=last||words[18]!=first_node||words[19]!=last_node)){status=6;goto done;}
   words[16]=first;words[17]=last;words[18]=first_node;words[19]=last_node;
  }
  for(u32 i=0;i<CLOSE_GUARDS;i++){
   if(!close_read(fd,bias+close_guards[i].address,close_guard,close_guards[i].size)){status=4;goto done;}
   SHA256 state;sha_init(&state);sha_add(&state,close_guard,close_guards[i].size);sha_end(&state,close_digest);
   if(!same(close_digest,close_guards[i].digest,32)){status=5;goto done;}
  }
  for(u32 i=0;i<CLOSE_FIELDS;i++){
   u8 *row=(pass?close_second:result+44+256)+i*16;
   put32(row,close_fields[i].id);put32(row+4,close_fields[i].width);
   if(!close_read(fd,bias+close_fields[i].address,row+8,close_fields[i].width*2)){status=4;goto done;}
  }
  words[4]++;
 }
 if(!same(close_second,result+44+256,CLOSE_FIELDS*16)){status=6;goto done;}
 if(!same(close_second+768,result+44+768,64)){status=6;goto done;}
 words[5]=CLOSE_FIELDS;words[6]=CLOSE_GUARDS;
#ifdef CLOSE_RESET
 status=close_reset_check(fd,bias);
#endif
done:
 if(sc(57,fd,0,0,0,0,0)){words[7]=1;status=7;}
#ifdef CLOSE_RESET
 if(!status)status=close_reset_apply(bias);
#endif
 finish(status);
}
#ifdef CLOSE_RESET
#include "close-setting.c"
#endif
static void finish(u32 status) {
    result[0]='A';result[1]='6';result[2]='7';result[3]='B';put32(result+4,1);
    for(u32 i=0;i<16;i++)result[8+i]=request[8+i];
    put32(result+24,status);put32(result+28,75);
    for(u32 i=0;i<8;i++)result[32+i]=request[28+i];
    put32(result+40,DATA_SIZE);
    for(u32 i=0;i<32;i++)for(u32 j=0;j<8;j++)result[44+i*8+j]=(u8)(words[i]>>(8*j));
    long fd=sc(56,-100,(long)exchange,0x241,0600,0,0);
    if(fd<0)return;
    if(io(64,fd,result,sizeof(result),0))sc(82,fd,0,0,0,0,0);
    sc(57,fd,0,0,0,0,0);
}
