/* Complete original-function readers in the SAME stopped publication cohort.
 * No attach/detach, POKE, allocation, application calls or device entry.
 * Embedded caller includes the commit/planner first and defines EMBEDDED.
 */
#ifndef FL23_MAIN_READERS_EMBEDDED
#include "fl23_main_publication.c"
#endif
#include "main-readers-profile.h"

struct main_readers_report {
 u64 version,status,threads,function_ranges,registers,frames;
 u64 code_matches,old_pointer_matches,first_tid,first_address;
 u64 complete_functions,publication_sites,cohort_start,cohort,elapsed_ns,reserved;
};
_Static_assert(sizeof(struct main_readers_report)==128,"reader report ABI");
static struct main_readers_report fl23_main_readers_last;

static int main_readers_active(u64 address,int returning,const struct main_state_capture_args *a){
 if(!address)return 0;
 for(u32 delta=0;delta<=(returning?4u:0u);delta+=4){
  if(address<delta)continue;u64 at=address-delta;
  if(a->owned<=at&&at<a->owned+0x200000ULL)return 1;
  for(u32 i=0;i<sizeof(main_readers_ranges)/sizeof(main_readers_ranges[0]);i++)
   if(a->bias+main_readers_ranges[i].start<=at&&at<a->bias+main_readers_ranges[i].end)return 1;
 }
 return 0;
}
static int main_readers_old_pointer(u64 at,const struct main_state_capture_args *a){
 /* Also refuse one-past pointers which can be decremented after resume. */
 return (a->bias+MAIN_OLD_COUNTER<=at&&at<=a->bias+MAIN_OLD_COUNTER+MAIN_OLD_COUNTER_COUNT*4ULL)||
        (a->bias+MAIN_OLD_INPUT<=at&&at<=a->bias+MAIN_OLD_INPUT+MAIN_OLD_GROUP_COUNT*24ULL);
}
static int main_readers_wait_pc(u64 pc,const struct main_state_capture_args *a){
 const u64 *h=(const void*)a->cohort;const u8 *maps=(const void*)(a->cohort+256);
 u32 n=(u32)h[5],at=0;static const char suffix[]="/lib/libpthread-2.25.so";
 while(at<n){
  u64 lo=0,hi=0;
  if(!main_pub_hex(maps,n,&at,&lo)||at>=n||maps[at++]!='-'||!main_pub_hex(maps,n,&at,&hi)||
     hi<=lo||at+5>n||maps[at++]!=' ')return 0;
  const u8 *perm=maps+at;at+=4;u32 end=at;while(end<n&&maps[end]!='\n')end++;
  if(lo<=pc&&pc<hi&&perm[0]=='r'&&perm[1]=='-'&&perm[2]=='x'&&perm[3]=='p'&&
     end>=sizeof(suffix)-1){
   u32 start=end-(sizeof(suffix)-1),same=1;
   for(u32 i=0;i<sizeof(suffix)-1;i++)if(maps[start+i]!=(u8)suffix[i])same=0;
   if(same)return 1;
  }
  at=end+(end<n);
 }
 return 0;
}
static int main_readers_empty_park(const unsigned char *row,const struct main_state_capture_args *a){
 u64 pc=main_capture_u64(row+592+32*8),head=0,tail=0;
 if(!main_readers_wait_pc(pc,a)||main_capture_expired())return 0;
 /* All parent threads are stopped. The exact wait return precedes the
  * re-read of the mutex-protected queue, not an extracted record or write.
  * No transaction/control request other than these two fixed PEEKs exists. */
 if(main_capture_sc(117,2,main_capture_tid,a->bias+MAIN_READERS_QUEUE_HEAD,(long)&head)||
    main_capture_sc(117,2,main_capture_tid,a->bias+MAIN_READERS_QUEUE_TAIL,(long)&tail)||
    !head||(head&7)||head>=0x8000000000ULL||head!=tail)return 0;
 return 1;
}
long fl23_main_readers_stopped(const struct main_state_capture_args *a,const struct main_pub_result *plan){
 struct main_readers_report *r=&fl23_main_readers_last;
 for(u32 i=0;i<sizeof(*r)/8;i++)((u64*)r)[i]=0;
 r->version=1;r->status=1;r->complete_functions=MAIN_READERS_FUNCTIONS;r->publication_sites=MAIN_READERS_SITES;
 r->function_ranges=sizeof(main_readers_ranges)/sizeof(main_readers_ranges[0]);
 if(!a||((u64)a&7)||(u64)a>=0x8000000000ULL-sizeof(*a)||!plan||((u64)plan&7)||
    (u64)plan>=0x8000000000ULL-sizeof(*plan)||!main_state_addresses(a->bias,a->owned)||
    a->bytes!=MAIN_COHORT_SIZE||!main_pub_span(a->cohort,a->bytes)||
    main_pub_overlap((u64)a,sizeof(*a),a->bias,0x15000000)||
    main_pub_overlap((u64)a,sizeof(*a),a->owned,0x200000)||
    plan->status||!plan->ready||plan->edits!=96||plan->words!=328||plan->safe_to_publish)return -22;
 r->cohort=a->cohort;r->cohort_start=main_capture_start;
 if(main_capture_parent!=a->pid||main_capture_bias!=a->bias||main_capture_owner!=a->owned||
    !main_capture_cohort(a)||((const u64*)a->cohort)[15]!=main_capture_start){r->status=2;return -116;}
 const u64 *h=(const void*)a->cohort;const unsigned char *data=(const void*)a->cohort;
 for(u32 i=0;i<h[4];i++){
  if(main_capture_expired()){r->status=5;return -110;}
  const unsigned char *row=data+MAIN_COHORT_ROWS+i*MAIN_COHORT_ROW;
  u64 tid=main_capture_u32(row),pc=main_capture_u64(row+592+32*8),lr=main_capture_u64(row+592+30*8);
  r->threads++;
  if(main_readers_active(pc,0,a)||main_readers_active(lr,1,a)){
   r->status=3;r->code_matches++;r->first_tid=tid;r->first_address=main_readers_active(pc,0,a)?pc:lr;return -16;
  }
  for(u32 j=0;j<main_capture_u32(row+12);j++){
   u64 address=main_capture_u64(row+872+j*16);r->frames++;
   if(main_readers_active(address,1,a)){
    if(address==a->bias+MAIN_READERS_WAIT_RETURN&&main_readers_empty_park(row,a)){
     r->reserved++;continue;
    }
    r->status=3;r->code_matches++;r->first_tid=tid;r->first_address=address;return -16;
   }
  }
  for(u32 j=0;j<31;j++){
   u64 address=main_capture_u64(row+592+j*8);r->registers++;
   if(main_readers_old_pointer(address,a)){
    r->status=4;r->old_pointer_matches++;r->first_tid=tid;r->first_address=address;return -16;
   }
  }
 }
 if(!main_capture_cohort(a)||((const u64*)a->cohort)[15]!=r->cohort_start){r->status=2;return -116;}
 if(main_capture_expired()){r->status=5;return -110;}
 r->status=0;return 0;
}
