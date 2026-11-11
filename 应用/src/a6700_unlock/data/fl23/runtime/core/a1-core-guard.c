/* Included after the frozen planner's PEEK helpers. Exact bootstrap bytes
 * are preimages, never journal targets. Everything else in the original
 * 12KiB cave keeps its original zero guard, including unused bootstrap tail.
 */
#include "a1-core-profile.h"
static int a1_boot_contains(u64 at,u64 n){return at>=A1_BOOT_START&&at<=A1_BOOT_END&&n<=A1_BOOT_END-at;}
static u64 a1_boot_expected(u64 at){return a1_boot_contains(at,8)?a1_boot_words[(at-A1_BOOT_START)/8]:0;}
static int a1_boot_protected(u64 address,u32 n){
 return main_pub_overlap(address,n,main_capture_bias+A1_BOOT_START,A1_BOOT_END-A1_BOOT_START)||
        main_pub_overlap(address,n,main_capture_bias+A1_RECV_PAGE,4096);
}
static long a1_boot_page_guard(const struct main_state_capture_args *a){
 for(u32 i=0;i<512;i++){
  u64 value=0;long rc=main_pub_read(a->bias+A1_RECV_PAGE+i*8,&value);
  if(rc)return rc;if(value!=a1_recv_words[i])return -116;
 }return 0;
}
