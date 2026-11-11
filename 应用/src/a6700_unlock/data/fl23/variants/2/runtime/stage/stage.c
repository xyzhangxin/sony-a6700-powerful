#define fl23_names_stage_entry fl23_a1_stage_entry
/* Immutable external stage for the existing resident-B pthread.
 * Only the separately loaded names bootstrap has mutable state. No second
 * pthread, CP preflight, service exchange or persistent write occurs here.
 */
#include "boot-abi.h"
#include "loader.c"
#include "names-stage-profile.h"

#define NS_DIAG 0x3153474e31414c46ULL
static u64 ns_get(const u8 *p){u64 v=0;for(u32 i=0;i<8;i++)v|=(u64)p[i]<<(8*i);return v;}
static int s2_sleep(long n){
 struct {long sec,nsec;} t={n/1000000000L,n%1000000000L};
 return n>0&&a1_sc(101,(long)&t,0,0,0,0,0)==0;
}
#include "startup-observer.h"

static void ns_evidence(struct a1_boot_context *c,const u8 *r,long rc,long released,u64 retained){
 u64 *d=(void*)((u8*)c+416);
 for(u32 i=0;i<76;i++)d[i]=0;
 d[0]=NS_DIAG;d[1]=1;d[2]=NS_ACTION;d[3]=rc;d[4]=released;d[5]=retained;
 if(r){
  for(u32 i=0;i<16;i++)d[6+i]=ns_get(r+i*8);
  for(u32 i=0;i<16;i++)d[22+i]=ns_get(r+192+i*8);
  for(u32 i=0;i<12;i++)d[38+i]=ns_get(r+320+i*8);
  for(u32 i=0;i<12;i++)d[50+i]=ns_get(r+416+i*8);
  for(u32 i=0;i<14;i++)d[62+i]=ns_get(r+848+i*8);
 }
 c->reserved[15]=NS_DIAG;c->reserved[16]=1;
}

__attribute__((section(".text.stage_entry")))
long fl23_names_stage_entry(struct a1_boot_context *c){
 if(!c||c->version!=1||c->worker_state!=5||!c->bias||(c->bias&4095)||
    c->pid!=(u64)a1_sc(172,0,0,0,0,0,0))return -22;
 /* Same receipt bytes used by the installed stage; never invent a nonce or
  * keep a callback into this immutable stage in a nested module. */
 const u8 *nonce=(const u8*)c+1120+224;u32 nonzero=0;
 for(u32 i=0;i<16;i++)nonzero|=nonce[i];if(!nonzero)return -22;
 long rc=s2_process_observe((u8*)c,c->pid,60000000000ULL,1);
 if(rc){ns_evidence(c,0,rc,0,0);return rc;}
 struct a1_loaded m={0};rc=fl23_a1_load(ns_package,sizeof(ns_package),ns_digest,&m);
 c->reserved[6]=m.mapping;c->reserved[7]=m.bytes;c->reserved[8]=rc;
 if(rc){ns_evidence(c,0,rc,m.cleanup_error,m.state==2);return rc;}
 /* Exact 1104B ABI is separately proven and hash-bound by the builder. */
 u64 report[138]={0};
 typedef long(*entry_fn)(u64,const u8*,void*);
 rc=((entry_fn)m.entry)(NS_ACTION,nonce,report);c->reserved[9]=rc;c->reserved[11]=rc;
 const u64 *controller=report+24,*transaction=report+40;
 int valid=report[0]==1&&report[2]==c->pid&&report[8]==NS_ACTION&&
           (!report[13]||controller[0]==1);
 /* Keep nested code and its report while a child/trace/shared report may
  * still need it. Retaining the separate 8KB name owner alone does NOT mean
  * retaining this temporary 1.5MB bootstrap after it has safely returned. */
 int retain=!valid||controller[14]||transaction[7]||
            (controller[7]&&!controller[9]);
 if(!rc&&(!valid||report[1]||!report[5]||report[3]!=c->bias||
          report[6]!=(NS_ACTION==0?report[7]:NS_ACTION==1)||report[7]>1||
          !report[13]||controller[1]||retain))rc=-116;
 c->reserved[12]=report[5];c->reserved[13]=report[6];
 long freed=0;
 if(!retain){freed=a1_sc(215,m.mapping,m.bytes,0,0,0,0);c->reserved[14]=freed;
  if(freed)retain=1;else c->reserved[6]=c->reserved[7]=0;}
 ns_evidence(c,(const u8*)report,rc,freed,retain);
 if(retain)return freed?freed:-115;
 return rc;
}
