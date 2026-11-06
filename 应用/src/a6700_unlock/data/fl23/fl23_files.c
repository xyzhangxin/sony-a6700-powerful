/* Fixed FL23 carrier file transaction. No path/address from the host.
 * Upload is append-only into O_EXCL .new, complete SHA checked before atomic
 * rename. Unknown existing content is never replaced or deleted. A lost
 * reply is not a reason for the host to repeat this constructor.
 */
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;
#include "sha256.h"
#include "file-profile.h"
#define CHUNK 65536u
#define HEADER 72u
static const char exchange[]="/tmp/jiritsu/tmmc.jit";
static const char live[]="/setting/fl23";
static const char fresh[]="/setting/fl23.new";
static const char closed[]="/setting/fl23.off";
static u8 input[HEADER+CHUNK],output[44+CHUNK],buffer[CHUNK];
static long sc(long nr,long a,long b,long c,long d){
 register long x8 __asm__("x8")=nr,x0 __asm__("x0")=a,x1 __asm__("x1")=b,x2 __asm__("x2")=c,x3 __asm__("x3")=d;
 __asm__ volatile("svc #0":"+r"(x0):"r"(x8),"r"(x1),"r"(x2),"r"(x3):"memory","cc");return x0;
}
static int same(const u8*a,const u8*b,u32 n){u32 x=0;for(u32 i=0;i<n;i++)x|=a[i]^b[i];return !x;}
static u32 get32(u32 at){return (u32)input[at]|(u32)input[at+1]<<8|(u32)input[at+2]<<16|(u32)input[at+3]<<24;}
static void put32(u32 at,u32 v){for(u32 i=0;i<4;i++)output[at+i]=v>>(8*i);}
static int io(long nr,long fd,u8*p,u32 n,u32 off){
 for(u32 tries=0;n&&tries<1024;tries++){
  long got=sc(nr,fd,(long)p,n,off);if(got==-4)continue;
  if(got<=0||(u64)got>n)return 0;p+=got;n-=got;off+=got;
 }return !n;
}
static int sync(long fd){for(u32 i=0;i<16;i++){long rc=sc(82,fd,0,0,0);if(!rc)return 1;if(rc!=-4)return 0;}return 0;}
/* Target AArch64 UAPI: O_NOFOLLOW=0100000, O_CLOEXEC=02000000. */
static long open_file(const char*p,int writing){return sc(56,-100,(long)p,writing?0x88001:0x88000,0600);}
static int verify(long fd){
 SHA256 h;sha_init(&h);
 for(u32 at=0;at<FILE_BYTES;){u32 n=FILE_BYTES-at;if(n>CHUNK)n=CHUNK;
  if(!io(67,fd,buffer,n,at))return 0;sha_add(&h,buffer,n);at+=n;}
 u8 tail,digest[32];if(sc(67,fd,(long)&tail,1,FILE_BYTES)!=0)return 0;
 sha_end(&h,digest);return same(digest,FILE_SHA,32);
}
static void finish(u32 status,u32 bytes,u32 detail){
 output[0]='A';output[1]='6';output[2]='7';output[3]='F';put32(4,1);
 for(u32 i=0;i<16;i++)output[8+i]=input[8+i];
 put32(24,status);put32(28,bytes);put32(32,detail);put32(36,0);put32(40,0);
 long fd=sc(56,-100,(long)exchange,0x241,0600);
 if(fd>=0){if(io(64,fd,output,44+bytes,0))sync(fd);sc(57,fd,0,0,0);}
}
__attribute__((constructor))static void run(void){
 u32 status=0,size=0,detail=0;long fd=sc(56,-100,(long)exchange,0,0);u8 tail;
 if(fd<0)return;
 int valid=io(63,fd,input,HEADER,0);u32 op=get32(56),off=get32(60),n=get32(64);
 if(!valid||!same(input,(const u8*)"A67F",4)||get32(4)!=1||!same(input+24,PROFILE_SHA,32)||
    get32(68)||op>4||off>FILE_BYTES||n>CHUNK||n>FILE_BYTES-off||
    (op!=0&&op!=2&&(off||n))|| (op==2&&!n))valid=0;
 if(valid&&op==2)valid=io(63,fd,input+HEADER,n,0);
 if(valid&&sc(63,fd,(long)&tail,1,0)!=0)valid=0;
 if(sc(57,fd,0,0,0))valid=0;
 if(!valid){finish(2,0,0);return;}
 fd=-1;
 if(op==0){
  fd=open_file(live,0);
  if(fd==-2){finish(0,0,0);return;}
  if(fd<0){status=3;goto done;}
  if(!verify(fd)){status=1;goto done;}
  detail=1;
  if(n&&!io(67,fd,output+44,n,off)){status=4;goto done;}size=n;
 }else if(op==1){
  long old=open_file(live,0);
  if(old>=0){int ok=verify(old);long rc=sc(57,old,0,0,0);if(!ok||rc){status=1;goto done;}}
  else if(old!=-2){status=3;goto done;}
  fd=sc(56,-100,(long)fresh,0x880c1,0600); /* nofollow|cloexec|create|excl|WRONLY */
  if(fd<0){status=3;goto done;}if(!sync(fd))status=6;
 }else if(op==2){
  fd=open_file(fresh,1);if(fd<0){status=3;goto done;}
  if(sc(62,fd,0,2,0)!=off){status=1;goto done;}
  if(!io(68,fd,input+HEADER,n,off)){status=5;goto done;}
  if(!sync(fd)){status=6;goto done;}
  if(sc(57,fd,0,0,0)){fd=-1;status=9;goto done;}fd=open_file(fresh,0);
  if(fd<0||!io(67,fd,buffer,n,off)||!same(buffer,input+HEADER,n))status=7;
 }else{
  const char *source=op==3?fresh:live,*dest=op==3?live:closed;
  fd=open_file(source,0);if(fd<0){status=3;goto done;}
  if(!verify(fd)){status=1;goto done;}
  if(sc(57,fd,0,0,0)){fd=-1;status=9;goto done;}fd=-1;
  if(op==3){
   long old=open_file(live,0);
   if(old>=0){int ok=verify(old);long rc=sc(57,old,0,0,0);if(!ok||rc){status=1;goto done;}}
   else if(old!=-2){status=3;goto done;}
  }else{
   long old=open_file(closed,0);
   if(old>=0){int ok=verify(old);long rc=sc(57,old,0,0,0);if(!ok||rc){status=1;goto done;}}
   else if(old!=-2){status=3;goto done;}
  }
  if(sc(38,-100,(long)source,-100,(long)dest)){status=5;goto done;}
  fd=sc(56,-100,(long)"/setting",0x84000,0);
  if(fd<0||!sync(fd)){status=6;goto done;}
 }
done:
 if(fd>=0&&sc(57,fd,0,0,0))status=9;
 finish(status,status?0:size,detail);
}
