#ifndef FL23_A1_BOOT_ABI_H
#define FL23_A1_BOOT_ABI_H
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;
/* One process-lifetime RW page. Parent and worker write disjoint fields.
 * Offsets through reserved[] preserve the first dispatcher prototype ABI. */
struct a1_boot_context {
 u64 version,bias,app_this,pid,creator_tid,handle,parent_state;
 long create_rc,detach_rc;
 u8 reservation[192];
 u64 worker_state,worker_error,reserved[17];
};
_Static_assert(sizeof(struct a1_boot_context)==416,"Boot context ABI");
#endif
