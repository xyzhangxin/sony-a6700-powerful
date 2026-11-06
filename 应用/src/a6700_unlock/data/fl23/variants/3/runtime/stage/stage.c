/* External-stage-only revision. Resident B, core, guards and CP are unchanged.
 * Bounded retry is admitted only by the original, completed no-write report.
 * Compact evidence occupies the unused context gap [416,1024); immutable
 * receipt/carrier data begins at 1024 and must never be overwritten. */
#include "boot-abi.h"
#include "loader.c"
#include "stage-profile.h"
#define S2_DIAG 0x3147443231414c46ULL
static void s2_put(u8 *p,u32 v){for(u32 i=0;i<4;i++)p[i]=(u8)(v>>(8*i));}
static u64 s2_q(const u8 *p){u64 v=0;for(u32 i=0;i<8;i++)v|=(u64)p[i]<<(8*i);return v;}
static u32 s2_d(const u8 *p){u32 v=0;for(u32 i=0;i<4;i++)v|=(u32)p[i]<<(8*i);return v;}
static int s2_same(const u8 *a,const u8 *b,u32 n){for(u32 i=0;i<n;i++)if(a[i]!=b[i])return 0;return 1;}
static int s2_zero(const u8 *p,u32 n){for(u32 i=0;i<n;i++)if(p[i])return 0;return 1;}
static int s2_pointer(u64 p,u64 n,const struct a1_loaded *m){return n<=m->bytes&&p>=m->mapping&&p-m->mapping<=m->bytes-n;}
static int s2_sleep(long nanos){
 if(nanos<=0)return 0;
 struct {long sec,nsec;} delay={nanos/1000000000L,nanos%1000000000L};
 return a1_sc(101,(long)&delay,0,0,0,0,0)==0;
}
#if S2_PROCESS_OBSERVE
#include "startup-observer.h"
#endif

/* 1 means an explicitly transient refusal BEFORE any POKE. Never retry a
 * rollback, published/partial outcome, stale bytes or uncertain controller. */
static int s2_clean_refusal(const u8 *raw,const u8 *nonce,const u64 *pub){
 if(!raw||pub[9]!=11||!pub[4]||pub[6]||pub[8]!=11||!s2_same(raw,(const u8*)"A67B",4)||
    s2_d(raw+4)!=1||!s2_same(raw+8,nonce,16)||s2_d(raw+24)!=11||s2_d(raw+28)!=40||s2_q(raw+32)||s2_d(raw+40)!=S2_CORE_BYTES-44)return 0;
 const u8 *body=raw+44,*h=raw+300,*e=h+S2_SNAPSHOT_BYTES;
 u64 pid=s2_q(body+8),tid=s2_q(body+16),child=s2_q(body+24),waited=s2_q(body+32),status=s2_q(body+40);
 if(!child||child>=0x80000000ULL||waited!=child||!status||(status&255)||s2_q(body+48)!=S2_SNAPSHOT_BYTES+12288||s2_q(body+64)||
    s2_q(body+72)!=pid||s2_q(body+80)!=tid||pid!=pub[10]||tid!=pub[11]||child!=pub[12]||waited!=pub[13]||status!=pub[14]||
    s2_q(h)!=0x0000000141333246ULL||s2_q(h+8)!=pid||s2_q(h+16)!=tid||s2_q(h+24)!=child||
    !s2_same(h+208,nonce,16)||s2_q(h+7*8)!=s2_q(h+11*8)||s2_q(e)!=0x315550333246ULL||s2_q(e+8)!=11||s2_q(e+40))return 0;
 /* The unpublished owner and all native containers were fully destroyed. */
 if(s2_q(e+64+9*8)!=7||s2_q(e+64+14*8)!=7||s2_q(e+64+15*8))return 0;
 return 1;
}
static int s2_no_write(const u8 *raw,const u8 *nonce,const u64 *pub){
 if(!s2_clean_refusal(raw,nonce,pub))return 0;
 const u8 *h=raw+300,*e=h+S2_SNAPSHOT_BYTES,*c=e+640;
 return s2_q(h+14*8)==15&&s2_q(e+56)==1&&s2_q(c)==0x31544d4333324646ULL&&
        s2_q(c+8)==1&&s2_q(c+16)==1&&!s2_q(c+24)&&!s2_q(c+32)&&s2_zero(c+304,96);
}
static int s2_retry(const u8 *raw,const u8 *nonce,const u64 *pub){
 if(!s2_clean_refusal(raw,nonce,pub))return 0;
 const u8 *h=raw+300,*e=h+S2_SNAPSHOT_BYTES,*c=e+640;
 u64 audit_status=s2_q(h+14*8),life=s2_q(e+56);
 if((audit_status==7||audit_status==11||audit_status==13)&&!life&&s2_zero(c,S2_COMMIT_BYTES))return 1;
 if(!s2_no_write(raw,nonce,pub))return 0;
 long rc=(long)s2_q(c+112),error=(long)s2_q(c+128+18*8);
 if(rc==-16||rc==-110||(rc==2&&error==-110))return 1;
 /* state_capture returns its status (2), and plan.failed stores that
  * status in plan.error. The underlying errno remains state.error. Only
  * this exact deadline report is the same transient refusal, never an
  * arbitrary plan status2 or stale-byte failure. */
 const u8 *p=c+128;
 /* A complete structural cohort may still contain an active patch caller.
  * The core reports only that case as state4/-EBUSY; malformed/incomplete
  * cohorts keep -ESTALE and must never enter this retry path. */
 if(rc==2&&error==4&&!s2_q(c+40)&&s2_q(p)==1&&s2_q(p+8)==4&&!s2_q(p+16)&&
    (long)s2_q(p+56)==-16&&s2_q(p+64)==1&&s2_q(p+72)==2&&
    !s2_q(p+80)&&!s2_q(p+88)&&!s2_q(p+96)&&!s2_q(p+104))return 1;
 return rc==2&&error==2&&!s2_q(c+40)&&s2_q(p)==1&&s2_q(p+8)==2&&!s2_q(p+16)&&
        (long)s2_q(p+56)==-110&&s2_q(p+64)==1&&s2_q(p+72)==2&&
        !s2_q(p+80)&&!s2_q(p+88)&&!s2_q(p+96)&&!s2_q(p+104);
}

static void s2_evidence(struct a1_boot_context *c,u32 attempt,long rc,const u64 *pf,const u64 *pub,const u8 *raw,u32 retry){
 u64 *d=(void*)((u8*)c+416),first_status=0,first_error=0,first_plan=0,first_state=0;
 if(d[0]==S2_DIAG&&d[1]==3){first_status=d[72];first_error=d[73];first_plan=d[74];first_state=d[75];}
 for(u32 i=0;i<76;i++)d[i]=0;
 d[0]=S2_DIAG;d[1]=3;d[2]=attempt;d[3]=pub?pub[9]:0;d[4]=retry;d[5]=pub?pub[4]:0;d[6]=pf?pf[1]:0;d[7]=rc;
 if(raw){
  const u8 *h=raw+300,*e=h+S2_SNAPSHOT_BYTES,*commit=e+640,*plan=commit+128;
  const u32 fields[8]={4,7,9,11,14,15,17,28};
  for(u32 i=0;i<8;i++)d[8+i]=s2_q(raw+44+i*8);
  for(u32 i=0;i<8;i++)d[16+i]=s2_q(h+fields[i]*8);
  for(u32 i=0;i<16;i++)d[24+i]=s2_q(commit+i*8);
  for(u32 i=0;i<22;i++)d[40+i]=s2_q(plan+i*8);
  d[62]=s2_q(e+64+9*8);d[63]=s2_q(e+64+14*8);d[64]=s2_q(e+64+15*8);d[65]=s2_q(raw+44+8*8);
  d[66]=s2_q(e+40);d[67]=pub?pub[6]:0;d[68]=pf?pf[15]:0;d[69]=s2_q(h+25*8);
  d[70]=s2_q(e+9000+8*8);d[71]=s2_q(e+9000+9*8);
  if(!first_status&&s2_q(h+14*8)){first_status=s2_q(h+14*8);first_error=s2_q(h+28*8);first_plan=s2_q(plan+18*8);first_state=s2_q(plan+7*8);}
 }
 d[72]=first_status;d[73]=first_error;d[74]=first_plan;d[75]=first_state;
 c->reserved[15]=attempt;c->reserved[16]=S2_DIAG;
}

__attribute__((section(".text.stage_entry")))
long fl23_a1_stage_entry(struct a1_boot_context *c){
 if(!c||c->version!=1)return -22;
 /* App has returned from dispatch. Waiting/census uses only the detached
  * worker; a process gate starts the unchanged preflight, never a bypass of
  * its IO, fresh all-thread snapshot, object or byte guards. */
#if S2_PROCESS_OBSERVE
 long observed=s2_process_observe((u8*)c,c->pid,S2_INITIAL_DELAY_NS,S2_PROCESS_GATE);
 if(observed)return observed;
#else
 if(!s2_sleep(S2_INITIAL_DELAY_NS))return -4;
#endif
 for(u32 attempt=1;attempt<=S2_MAX_ATTEMPTS;attempt++){
  struct a1_loaded m;long rc=fl23_a1_load(s1_package,sizeof(s1_package),s1_digest,&m);
  c->reserved[6]=m.mapping;c->reserved[7]=m.bytes;c->reserved[8]=rc;
  if(rc)return rc;
  u8 config[40]={0};config[0]='A';config[1]='6';config[2]='7';config[3]='P';s2_put(config+4,1);
  const u8 *receipt=(const u8*)c+1120;
  for(u32 i=0;i<16;i++)config[8+i]=receipt[224+i];
  s2_put(config+24,48);s2_put(config+36,S1_REQUEST_BYTES);
  typedef long(*start_fn)(const u8*,u64);typedef long(*poll_fn)(void);typedef const u64 *(*get_fn)(void);
  rc=((start_fn)m.entry)(config,40);c->reserved[9]=rc;
  if(!rc)for(u32 i=0;i<500;i++){
   rc=((poll_fn)(m.mapping+S1_POLL))();c->reserved[10]=i+1;
   if(rc!=1)break;if(!s2_sleep(10000000)){rc=-4;break;}
  }
  c->reserved[11]=rc;
  const u64 *t=((get_fn)(m.mapping+S1_REPORT))();
  if(!t||!s2_pointer((u64)t,256,&m)||t[0]!=1||!(t[16]||!t[19]||t[14])||t[28]){s2_evidence(c,attempt,-115,0,0,0,0);return -115;}
  const u64 *pf=((get_fn)(m.mapping+S1_PREFLIGHT))(),*pub=((get_fn)(m.mapping+S1_PUBLICATION))();
  if(!pf||!pub||!s2_pointer((u64)pf,256,&m)||!s2_pointer((u64)pub,256,&m)||pf[15]||pub[6]){s2_evidence(c,attempt,-115,0,0,0,0);return -115;}
  c->reserved[12]=pf[1];c->reserved[13]=pub[9];
  const u8 *raw=(const u8*)((get_fn)(m.mapping+S2_PUBLICATION_REPLY))();
  if(raw&&!s2_pointer((u64)raw,S2_CORE_BYTES,&m)){s2_evidence(c,attempt,-115,pf,pub,0,0);return -115;}
  if((raw!=0)!=(pub[2]!=0)||raw&&(!s2_same(raw,(const u8*)"A67B",4)||s2_d(raw+4)!=1||!s2_same(raw+8,config+8,16)||
     s2_d(raw+24)!=pub[9]||s2_d(raw+28)!=40||s2_q(raw+32)||s2_d(raw+40)!=S2_CORE_BYTES-44||pub[15]!=S2_CORE_BYTES)){
   s2_evidence(c,attempt,-115,pf,pub,0,0);return -115;
  }
  u32 retry=rc&&attempt<S2_MAX_ATTEMPTS&&s2_retry(raw,config+8,pub);
  s2_evidence(c,attempt,rc,pf,pub,raw,retry);
  /* Preserve the existing worker's report allocation only after its thread
   * joined, core was released, child reaped and an intact NO_WRITE report
   * proved native-owner destruction. This is an explicitly marked diagnostic
   * mapping, never an authorization to retry or resume an unknown controller.
   * A fixed USB collector copies the full report before retiring this mapping. */
  if(rc&&!retry&&pub[7]==1&&m.bytes==S2_WORKER_BYTES&&
     (u64)raw==m.mapping+S2_REPLY_OFFSET&&s2_no_write(raw,config+8,pub)){
   u64 *d=(void*)((u8*)c+416);d[14]=S2_HOLD;d[15]=S2_REPLY_OFFSET;return rc;
  }
  long freed=a1_sc(215,m.mapping,m.bytes,0,0,0,0);c->reserved[14]=freed;
  if(freed)return freed;
  c->reserved[6]=c->reserved[7]=0;
  if(!retry)return rc;
  if(!s2_sleep(500000000))return -4;
 }
 return -11;
}
