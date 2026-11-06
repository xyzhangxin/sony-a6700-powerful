/* Included only in the A1 publication worker, after the native preflight.
 * The frozen core owns all application writes. This bridge supplies its
 * explicit request, copies the reply and classifies controller lifetime.
 * An unknown controller outcome keeps both loaded modules resident.
 */
#include "publish-profile.h"
struct a1_publish_report {
 u64 version,attempted,reply_ready,child_created,reaped,no_child,unresolved,core_released;
 long entry_rc;u64 native_status,pid,tid,child,wait_return,wait_status,reply_bytes;
 u8 nonce[16];u64 reserved[14];
};
_Static_assert(sizeof(struct a1_publish_report)==256,"Publication bridge ABI");
static struct a1_publish_report publication;
static u8 publication_reply[A1_CORE_REPLY_BYTES];
static u64 pub_u64(const u8 *p){u64 v=0;for(u32 i=0;i<8;i++)v|=(u64)p[i]<<(i*8);return v;}
static void pub_put32(u8 *p,u32 v){for(u32 i=0;i<4;i++)p[i]=(u8)(v>>(i*8));}
int fl23_a1_publish_run(u64 entry,u64 getter){
 u8 config[40]={0};const u8 magic[4]={'A','6','7','P'};
 publication.version=1;publication.attempted=1;
 for(u32 i=0;i<4;i++)config[i]=magic[i];pub_put32(config+4,1);
 for(u32 i=0;i<16;i++){config[8+i]=report.nonce[i];publication.nonce[i]=report.nonce[i];}
 pub_put32(config+24,40);pub_put32(config+36,A1_CORE_DATA_SIZE);
 typedef long (*entry_fn)(const u8*,u64);typedef const u8 *(*reply_fn)(void);
 publication.entry_rc=((entry_fn)entry)(config,40);
 const u8 *raw=((reply_fn)getter)();
 if(!raw){publication.unresolved=1;return 13;}
 /* A pointer is trusted only within the separately hash-bound core ABI.
  * The full existing publisher parser still validates the copied report. */
 for(u32 i=0;i<A1_CORE_REPLY_BYTES;i++)publication_reply[i]=raw[i];
 publication.reply_ready=1;publication.reply_bytes=A1_CORE_REPLY_BYTES;
 u32 status=t_u32(raw+24);publication.native_status=status;
 if(!t_equal(raw,(const u8*)"A67B",4)||t_u32(raw+4)!=1||!t_equal(raw+8,config+8,16)||
    status>12||publication.entry_rc!=(long)status||t_u32(raw+28)!=40||
    pub_u64(raw+32)||t_u32(raw+40)!=A1_CORE_DATA_SIZE){publication.unresolved=1;return 13;}
 const u8 *body=raw+44;u64 pid=pub_u64(body+8),tid=pub_u64(body+16),child=pub_u64(body+24);
 u64 waited=pub_u64(body+32),wait_status=pub_u64(body+40);
 publication.pid=pid;publication.tid=tid;publication.child=child;publication.wait_return=waited;publication.wait_status=wait_status;
 publication.child_created=child>0&&child<(1ULL<<31);
 publication.reaped=publication.child_created&&waited==child&&(wait_status&255)!=127&&wait_status!=65535;
 if(status==2){
  for(u32 i=0;i<A1_CORE_DATA_SIZE;i++)if(body[i]){publication.unresolved=1;return 13;}
  publication.no_child=1;
 }else{
  if(pid!=report.worker_pid||tid!=report.worker_tid){publication.unresolved=1;return 13;}
  publication.no_child=!publication.child_created&&status>=3&&status<=9&&(child==0||child>=((u64)-4095));
 }
 if(publication.reaped){
  const u8 *h=raw+300;
  if(pub_u64(body+48)!=A1_CORE_CHILD_BYTES||pub_u64(h)!=0x0000000141333246ULL||
     pub_u64(h+8)!=pid||pub_u64(h+16)!=tid||pub_u64(h+24)!=child||!t_equal(raw+508,config+8,16)){
   publication.unresolved=1;return 13;
  }
 }
 if(!publication.reaped&&!publication.no_child){publication.unresolved=1;return 13;}
 return status?14:0;
}
const void *fl23_a1_publish_report(void){return &publication;}
const void *fl23_a1_publish_reply(void){return publication.reply_ready?publication_reply:(const void*)0;}
