/* Fixed native model API transitions. Compiled only into the setting payload.
 * No Backup write, firmware write, caller-supplied address or arbitrary value.
 * A returned call is only an acknowledgement; host independently rereads all
 * settings, persistence and FTP resources before permitting firmware edits.
 */
#include "close-setting-profile.h"
static u32 close_reset_check(long fd,u64 bias){
 for(u32 i=0;i<CLOSE_SETTING_GUARDS;i++){
  const struct CloseGuard *g=&close_setting_guards[i];
  if(!close_read(fd,bias+g->address,close_guard,g->size))return 4;
  SHA256 s;sha_init(&s);sha_add(&s,close_guard,g->size);sha_end(&s,close_digest);
  if(!same(close_digest,g->digest,32))return 5;
 }
 return 0;
}
static int close_reset_scalar(long fd,u64 bias,u32 id,u32 wanted){
 for(u32 i=0;i<CLOSE_FIELDS;i++)if(close_fields[i].id==id){
  const struct CloseField *f=&close_fields[i];u8 value[8];
  if(!close_read(fd,bias+f->address,value,f->width*2))return 0;
  if(f->width==1)return value[0]==wanted&&value[1]==wanted;
  return get32(value)==wanted&&get32(value+4)==wanted;
 }
 return 0;
}
static u32 close_reset_apply(u64 bias){
 u32 action=get32(request+28),before=get32(request+32);
 static const u32 ids[]={0x5a5,0x69a,0x6b2,0x497};
 static const u32 targets[]={1,0,1,0};
 static const u64 entries[]={0x2e41c50,0x2d39a38,0x2d44690,0x2db2e44};
 /* Only active -> safe. Existing stock sound On/Off are never rewritten. */
 if((action==1&&before!=0)||(action==2&&before!=1&&before!=2)||
    (action==3&&before!=0)||(action==4&&before!=1))return 8;
 u64 lock[4]={0},object[4]={0};u32 status=8;
 void (*lock_enter)(void*)=(void*)(bias+0x3137990);
 void (*lock_leave)(void*)=(void*)(bias+0x313759c);
 lock_enter(lock);
 long fd=sc(56,-100,(long)"/proc/self/mem",0x80000,0,0,0);
 if(fd<0)goto leave;
 int ready=close_reset_scalar(fd,bias,0x17,0)&&close_reset_scalar(fd,bias,0x18,0)&&
           close_reset_scalar(fd,bias,ids[action-1],before);
 /* All reset actions refuse active recall, even if exposure was not selected. */
 ready=ready&&close_reset_scalar(fd,bias,0x66,0)&&close_reset_scalar(fd,bias,0x376,0)&&
       close_reset_scalar(fd,bias,0x377,0)&&close_reset_scalar(fd,bias,0x1171,0);
 long closed=sc(57,fd,0,0,0,0,0);
 if(!ready||closed)goto leave;
 ((void(*)(void*))(bias+0x2d1b0bc))(object);
 if(object[0]!=bias+0x8d22f58)goto destroy;
 words[20]=1;
 ((void(*)(void*,u32))(bias+entries[action-1]))(object,targets[action-1]);
 words[21]=1;status=0;
destroy:
 ((void(*)(void*))(bias+0x2d1b0cc))(object);
leave:
 lock_leave(lock);return status;
}
