static long main_capture_bulk_read(u64,u64,u64,u64);
/* Internal stopped-cohort state preparation, not a USB/installation entry.
 * The controller supplies a fresh exact reader and deadline check. This unit
 * writes only the reserved owner, its caller-owned report and private
 * controller state;
 * it does not stop/resume threads or grant permission to publish hooks.
 */
typedef unsigned int u32;typedef unsigned long long u64;
#include "main-state-profile.h"
#include "fl23_stack_bounds.h"
typedef long (*main_state_reader)(u64 pid,u64 address,u64 destination,u64 bytes);
typedef int (*main_state_deadline)(void);
struct main_state_result {
 u64 version,status,ready,read_calls,read_bytes,inherited_counters,added_inputs;
 long error;
};
static int main_state_addresses(u64 bias,u64 owned){
 return bias&&owned&&!(bias&4095)&&!(owned&4095)&&
  bias<0x8000000000ULL-0x15000000ULL&&owned<0x8000000000ULL-0x200000ULL&&
  !(owned<bias+0x15000000ULL&&bias<owned+0x200000ULL)&&
  (owned>=bias?owned-bias:bias-owned)<0x100000000ULL;
}
static int main_state_result_valid(u64 bias,u64 owned,struct main_state_result *r){
 return r&&!((u64)r&7)&&(u64)r<0x8000000000ULL-sizeof(*r)&&main_state_addresses(bias,owned)&&
  !((u64)r<owned+0x200000ULL&&owned<(u64)r+sizeof(*r))&&
  !((u64)r<bias+0x15000000ULL&&bias<(u64)r+sizeof(*r));
}
int fl23_main_state_prepare(u64 pid,u64 bias,u64 owned,main_state_reader read,
 main_state_deadline expired,struct main_state_result *r){
 if(!main_state_result_valid(bias,owned,r))return 1;
 for(u32 i=0;i<sizeof(*r)/8;i++)((u64*)r)[i]=0;r->version=1;
 if(!pid||pid>=0x80000000ULL||!read||!expired){r->status=1;return 1;}
 u32 *counters=(u32*)(owned+MAIN_STATE_COUNTER),*inputs=(u32*)(owned+MAIN_STATE_INPUT);
 const u64 sources[2]={bias+MAIN_OLD_COUNTER, bias+MAIN_OLD_INPUT};
 u64 targets[2]={(u64)counters,(u64)inputs};
 const u64 lengths[2]={MAIN_OLD_COUNTER_COUNT*4ULL,MAIN_OLD_GROUP_COUNT*24ULL};
 for(u32 i=0;i<2;i++){
  if(expired()){r->status=2;r->error=-110;return 2;}
  r->read_calls++;long n=read(pid,sources[i],targets[i],lengths[i]);
  if(n!=(long)lengths[i]){r->status=3;r->error=n<0?n:-5;return 3;}
  r->read_bytes+=n;
 }
 if(expired()){r->status=2;r->error=-110;return 2;}
 for(u32 i=MAIN_OLD_COUNTER_COUNT;i<MAIN_NEW_COUNTER_COUNT;i++)counters[i]=0;
 for(u32 i=0;i<sizeof(main_counter_sources)/sizeof(main_counter_sources[0]);i++){
  counters[main_counter_sources[i].target]=counters[main_counter_sources[i].source];r->inherited_counters++;
 }
 for(u32 i=0;i<sizeof(main_added_groups)/sizeof(main_added_groups[0]);i++){
  u32 *row=inputs+(MAIN_OLD_GROUP_COUNT+i)*6;
  row[0]=main_added_groups[i];row[1]=row[2]=row[3]=0x80000000U;row[4]=row[5]=0;r->added_inputs++;
 }
 if(expired()){r->status=2;r->error=-110;return 2;}
 r->ready=1;return 0;
}
/* Caller is the still-running audit controller, before its cleanup label.
 * This gate cannot attach/stop/detach a thread or authorize publication.
 * Only two fixed prefixes are read with the kernel ABI already used by the
 * audit. process_vm_readv is NOT implemented in the pinned camera kernel.
 */
struct main_state_capture_args {u64 pid,bias,owned,cohort,bytes;unsigned char nonce[16];};
static u64 main_capture_start,main_capture_parent,main_capture_tid,main_capture_bias,main_capture_owner;
static long main_capture_sc(long n,long a,long b,long c,long d){
 register long x0 __asm__("x0")=a;register long x1 __asm__("x1")=b;
 register long x2 __asm__("x2")=c;register long x3 __asm__("x3")=d;
 register long x8 __asm__("x8")=n;
 __asm__ volatile("svc 0":"+r"(x0):"r"(x1),"r"(x2),"r"(x3),"r"(x8):"memory","cc");return x0;
}
static u64 main_capture_u64(const unsigned char *p){u64 v=0;for(u32 i=0;i<8;i++)v|=(u64)p[i]<<(8*i);return v;}
static u32 main_capture_u32(const unsigned char *p){u32 v=0;for(u32 i=0;i<4;i++)v|=(u32)p[i]<<(8*i);return v;}
static int main_capture_expired(void){
 u64 ts[2]={0,0};if(main_capture_sc(113,1,(long)ts,0,0)||ts[1]>=1000000000ULL||ts[0]>0xffffffffULL)return 1;
 u64 n=ts[0]*1000000000ULL+ts[1];return !n||n<main_capture_start||n-main_capture_start>=MAIN_COHORT_BUDGET;
}
static int main_capture_active(u64 address,int returning,const struct main_state_capture_args *a){
 if(!address)return 0;
 for(u32 delta=0;delta<=(returning?4u:0u);delta+=4){
  if(address<delta)continue;u64 p=address-delta;
  if(a->owned<=p&&p<a->owned+0x200000ULL)return 1;
  for(u32 i=0;i<sizeof(main_capture_ranges)/sizeof(main_capture_ranges[0]);i++)
   if(a->bias+main_capture_ranges[i].offset<=p&&p<a->bias+main_capture_ranges[i].offset+main_capture_ranges[i].size)return 1;
 }
 return 0;
}
static int main_capture_cohort_busy;
static int main_capture_cohort(const struct main_state_capture_args *a){
 main_capture_cohort_busy=0;u32 busy=0;
 const u64 *h=(const void*)a->cohort;
 if(h[0]!=0x0000000141333246ULL||h[1]!=a->pid||!h[2]||h[2]>=0x80000000ULL||
    !h[3]||h[3]>=0x80000000ULL||h[3]!=main_capture_sc(172,0,0,0,0)||h[3]!=main_capture_sc(178,0,0,0,0)||h[3]==h[1]||
    !h[4]||h[4]>MAIN_COHORT_MAX||!h[5]||h[5]>131072||h[6]!=h[4]||h[7]!=h[4]||
    h[8]!=h[4]||h[9]!=h[4]||h[10]!=h[4]||h[11]||h[12]!=1||h[13]!=1||h[14]||
    !h[15]||h[16]<h[15]||h[17]<h[16]||h[18]||h[19]||h[20]||
    h[25]!=MAIN_COHORT_BUDGET||h[28]||h[29]||h[30]!=h[4]||h[31]!=h[4])return 0;
 const unsigned char *data=(const void*)a->cohort;u32 have_pid=0,have_tid=0,previous=0;
 for(u32 i=0;i<16;i++)if(data[208+i]!=a->nonce[i])return 0;
 fl23_sb_word stack_sp[MAIN_COHORT_MAX],stack_lo[MAIN_COHORT_MAX],stack_hi[MAIN_COHORT_MAX];
 for(u32 i=0;i<h[4];i++)stack_sp[i]=main_capture_u64(data+MAIN_COHORT_ROWS+i*MAIN_COHORT_ROW+592+31*8);
 if(!fl23_stack_bounds_many(data+256,(u32)h[5],stack_sp,(u32)h[4],stack_lo,stack_hi))return 0;
 for(u32 i=0;i<h[4];i++){
  const unsigned char *row=data+MAIN_COHORT_ROWS+i*MAIN_COHORT_ROW;
  u32 tid=main_capture_u32(row),frames=main_capture_u32(row+12);
  if(!tid||tid>=0x80000000U||tid<=previous||tid==h[3]||main_capture_u32(row+4)!=15||
     main_capture_u32(row+8)!=0x80057fU||frames>32||main_capture_u64(row+16)||main_capture_u64(row+24)||
     main_capture_u64(row+32)!=tid||main_capture_u64(row+40)||main_capture_u64(row+48)!=(u64)-999||
     main_capture_u64(row+56)!=272||main_capture_u32(row+64)||main_capture_u64(row+1376))return 0;
  previous=tid;have_pid|=tid==h[1];have_tid|=tid==h[2];
  if(main_capture_u32(data+MAIN_COHORT_IDS+i*4)!=tid||
     main_capture_u32(data+MAIN_COHORT_SECOND+i*4)!=tid||main_capture_u32(data+MAIN_COHORT_THIRD+i*4)!=tid)return 0;
  u64 fp=main_capture_u64(row+592+29*8),sp=main_capture_u64(row+592+31*8),pc=main_capture_u64(row+592+32*8);
  if(!sp||(sp&15)||sp>=0x8000000000ULL||!pc||(pc&3)||pc>=0x8000000000ULL||main_capture_u64(row+592+33*8)&15)return 0;
  u64 lr=main_capture_u64(row+592+30*8);
  if((lr&3)||lr>=0x8000000000ULL)return 0;
  busy|=main_capture_active(pc,0,a)||main_capture_active(lr,1,a);
  for(u32 j=0;j<frames;j++){
   if(!fl23_stack_frame(sp,fp,stack_lo[i],stack_hi[i]))return 0;
   u64 next=main_capture_u64(row+864+j*16),lr=main_capture_u64(row+872+j*16);
   if((next&&next<=fp)||(lr&3)||lr>=0x8000000000ULL)return 0;busy|=main_capture_active(lr,1,a);fp=next;
  }
  if(fp)return 0;
 }
 if(!have_pid||!have_tid)return 0;main_capture_cohort_busy=busy;return !busy;
}
static long main_capture_read(u64 pid,u64 source,u64 dest,u64 bytes){
 if(pid!=main_capture_parent||!((source==main_capture_bias+MAIN_OLD_COUNTER&&dest==main_capture_owner+MAIN_STATE_COUNTER&&bytes==MAIN_OLD_COUNTER_COUNT*4ULL)||
   (source==main_capture_bias+MAIN_OLD_INPUT&&dest==main_capture_owner+MAIN_STATE_INPUT&&bytes==MAIN_OLD_GROUP_COUNT*24ULL)))return -22;
 for(u64 i=0;i<bytes;i+=8){
  if(!(i&1023)&&main_capture_expired())return -110;
  long rc=main_capture_sc(117,2,main_capture_tid,source+i,dest+i);if(rc)return rc;
 }
 return bytes;
}
int fl23_main_state_capture_stopped(const struct main_state_capture_args *a,struct main_state_result *r){
 if(!a||((u64)a&7)||(u64)a>=0x8000000000ULL-sizeof(*a)||!a->pid||a->pid>=0x80000000ULL||!main_state_result_valid(a->bias,a->owned,r)||
    a->bytes!=MAIN_COHORT_SIZE||!a->cohort||(a->cohort&4095)||a->cohort>=0x8000000000ULL-a->bytes||
    ((u64)r<a->cohort+a->bytes&&a->cohort<(u64)r+sizeof(*r))||
    ((u64)r<(u64)a+sizeof(*a)&&(u64)a<(u64)r+sizeof(*r))||
    ((u64)a<a->owned+0x200000ULL&&a->owned<(u64)a+sizeof(*a))||
    ((u64)a<a->bias+0x15000000ULL&&a->bias<(u64)a+sizeof(*a))||
    ((u64)a<a->cohort+a->bytes&&a->cohort<(u64)a+sizeof(*a))||
    (a->cohort<a->owned+0x200000ULL&&a->owned<a->cohort+a->bytes)||
    (a->cohort<a->bias+0x15000000ULL&&a->bias<a->cohort+a->bytes))return 1;
 for(u32 i=0;i<sizeof(*r)/8;i++)((u64*)r)[i]=0;r->version=1;
 if(!main_capture_cohort(a)){r->status=4;r->error=main_capture_cohort_busy?-16:-116;return 4;}
 const u64 *h=(const void*)a->cohort;
 main_capture_start=h[15];main_capture_parent=a->pid;main_capture_tid=h[2];main_capture_bias=a->bias;main_capture_owner=a->owned;
 int rc=fl23_main_state_prepare(a->pid,a->bias,a->owned,main_capture_bulk_read,main_capture_expired,r);
 if(!rc&&(!main_capture_cohort(a)||h[15]!=main_capture_start||h[2]!=main_capture_tid)){r->ready=0;r->status=4;r->error=(main_capture_cohort_busy&&h[15]==main_capture_start&&h[2]==main_capture_tid)?-16:-116;return 4;}
 if(!rc&&main_capture_expired()){r->ready=0;r->status=2;r->error=-110;return 2;}
 return rc;
}
/* Instruction generation only. The caller still must read the complete
 * aligned native word, preserve neighbours and check every preimage. */
int fl23_main_state_code(u64 bias,u64 owned,u32 index,u64 *address,u32 *before,u32 *after){
 if(!main_state_addresses(bias,owned)||index>=sizeof(main_state_code)/sizeof(main_state_code[0])||!address||!before||!after)return 1;
 const struct main_state_instruction *r=main_state_code+index;
 u64 pc=bias+r->offset;u32 word=r->after;
 if(r->kind){
  long delta=(long)((owned+r->target)&~4095ULL)-(long)(pc&~4095ULL);
  if(delta%4096||delta/4096<-(1L<<20)||delta/4096>=(1L<<20))return 2;
  u64 imm=(u64)(delta/4096)&0x1fffffULL;
  word=0x90000000U|((u32)(imm&3)<<29)|((u32)(imm>>2)<<5)|(r->before&31);
 }
 *address=pc;*before=r->before;*after=word;return 0;
}
int fl23_main_state_pointer(u64 bias,u64 owned,u32 index,u64 *address,u64 *before,u64 *after){
 if(!main_state_addresses(bias,owned)||index>=sizeof(main_state_pointers)/sizeof(main_state_pointers[0])||!address||!before||!after)return 1;
 const struct main_state_pointer *p=main_state_pointers+index;
 *address=bias+p->offset;*before=bias+p->before;*after=owned+p->after;return 0;
}
