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
