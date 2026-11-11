/* Fixed FL23 runtime capability probe. No firmware/parameter/WBI write API.
 * The verified arena constructor writes only one owned anonymous page.
 * Existing app mappings and files are compared through checked pipe copies.
 * Service result/config files are restored by the host ScratchTransaction.
 */
typedef unsigned char u8;typedef unsigned int u32;typedef unsigned long u64;
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

#define DATA_SIZE 928256
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

static char task_root[64];
/* Read-only task census. IDs come solely from /proc/self/task getdents64.
 * No signal, ptrace, clone, or application-memory write syscall exists here.
 */
#define MAX_TASKS 512u
#define ROW_BYTES 896u
#define STATUS_AT 256u
#define MAPS_AT (STATUS_AT+4096u)
#define ROWS_AT (MAPS_AT+131072u)
#define SECOND_AT (ROWS_AT+MAX_TASKS*ROW_BYTES)
static u8 dents[4096];static u32 first[MAX_TASKS],second[MAX_TASKS];
static void finish(u32 status);
static void census_u64(u8 *p,u64 value){for(u32 i=0;i<8;i++)p[i]=(u8)(value>>(8*i));}
static long read_text(const char *path,u8 *dest,u32 capacity){
    long fd=sc(56,-100,(long)path,0x80000,0,0,0);
    if(fd<0)return fd;
    u32 length=0;long answer=-75;
    for(u32 attempt=0;attempt<128;attempt++){
        if(length==capacity){u8 tail;long n=sc(63,fd,(long)&tail,1,0,0,0);answer=n==0?(long)length:-75;break;}
        long n=sc(63,fd,(long)(dest+length),capacity-length,0,0,0);
        if(n==-4)continue;
        if(n<0){answer=n;break;}if(!n){answer=length;break;}
        if((u64)n>capacity-length){answer=-75;break;}length+=(u32)n;
    }
    long closed=sc(57,fd,0,0,0,0,0);return closed?closed:answer;
}
static int enum_error(int code,u64 phase,u64 bytes,u64 at,u64 size){
    words[11]=phase;words[12]=bytes;words[13]=at;words[14]=size;
    for(u32 i=0;i<sizeof(dents);i++)result[44+STATUS_AT+i]=dents[i];
    return code;
}
static int enumerate(u32 *ids){
    /* ARM64 UAPI O_DIRECTORY=040000, O_CLOEXEC=02000000. */
    long fd=sc(56,-100,(long)task_root,0x84000,0,0,0);
    if(fd<0)return enum_error((int)fd,1,0,0,0);
    u32 count=0;int result=-75;
    for(u32 batch=0;batch<64;batch++){
        long n=sc(61,fd,(long)dents,sizeof(dents),0,0,0);
        if(n==-4)continue;
        if(n<0){result=enum_error((int)n,2,0,0,0);break;}
        if(!n){result=(int)count;break;}
        if((u64)n>sizeof(dents)){result=enum_error(-75,3,n,0,0);break;}
        u32 at=0;
        while(at<(u32)n){
            if((u32)n-at<20){result=enum_error(-22,4,n,at,0);goto done;}
            u32 size=dents[at+16]|((u32)dents[at+17]<<8);
            if(size<20||size%8||size>(u32)n-at){result=enum_error(-22,5,n,at,size);goto done;}
            u8 *name=dents+at+19;u32 available=size-19,tid=0,used=0;
            if(name[0]=='.'&&available>=2&&(name[1]==0||(available>=3&&name[1]=='.'&&name[2]==0))){at+=size;continue;}
            for(;used<available&&name[used];used++){
                u32 digit=name[used]-'0';if(digit>9||used>=10||tid>(2147483647u-digit)/10){result=enum_error(-22,6,n,at,size);goto done;}
                tid=tid*10+digit;
            }
            if(!used||used==available||!tid||count==MAX_TASKS){result=enum_error(-75,7,n,at,size);goto done;}
            ids[count++]=tid;at+=size;
        }
    }
done:
    if(sc(57,fd,0,0,0,0,0))return -5;
    if(result<0)return result;
    for(u32 i=1;i<count;i++){u32 value=ids[i],j=i;while(j&&ids[j-1]>value){ids[j]=ids[j-1];j--;}ids[j]=value;}
    for(u32 i=1;i<count;i++)if(ids[i-1]==ids[i])return -22;
    return result;
}
static void task_path(char *dest,u32 tid,const char *suffix){
    const char root[]="/proc/self/task/";u32 at=0;for(;at<sizeof(root)-1;at++)dest[at]=root[at];
    char reverse[10];u32 n=0;do{reverse[n++]='0'+tid%10;tid/=10;}while(tid);
    while(n)dest[at++]=reverse[--n];for(u32 i=0;suffix[i];i++)dest[at++]=suffix[i];dest[at]=0;
}

#define NAMES_KERNEL_CACHE_CONTRACT 1
