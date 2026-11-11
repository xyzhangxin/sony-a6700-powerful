/* Fast App-thread half of A1 bootstrap. No carrier IO or publisher here.
 * The once-per-process reservation and one RW page deliberately live until
 * process exit. The detached worker owns its own separate result fields.
 * Standalone offline build supplies a worker boundary, not a boot installer.
 */
#include "boot-guard.c"
#include "boot-dispatch-profile.h"
#include "boot-abi.h"
_Static_assert(sizeof(struct a1_boot_context)<=4096,"Bootstrap context page");
extern void *fl23_a1_boot_worker(void *);
static int b_equal(const u8 *p,const u8 *q,u32 n){u8 v=0;for(u32 i=0;i<n;i++)v|=p[i]^q[i];return !v;}
static u64 b_tls(void){u64 v;__asm__ volatile("mrs %0,tpidr_el0":"=r"(v));return v;}
/* Return values describe dispatch only; they never claim business success.
 * 0=already reserved, 1=created+detached, negative=stop until process exit. */
__attribute__((section(".text.entry")))
long fl23_a1_trigger_dispatch(u64 bias,u64 app_this){
 struct a1_guard_result owner;
 long rc=fl23_a1_guard_claim(&owner,0);if(rc!=1)return rc;
 /* Bind the native import route before deriving the create/detach entries.
  * pthread_self is already an original App import. A first PLT call permits
  * lazy resolution; exact PLT bytes, TLS result and all 3 function prefixes
  * must match the fixed body. Never infer a library base from a return value. */
 if(bias&4095||bias>=0x8000000000ULL-A1_APP_END||!app_this||
    !b_equal((const u8*)(bias+A1_SELF_PLT),self_plt_guard,16))return -3;
 typedef u64 (*self_fn)(void);
 u64 self=((self_fn)(bias+A1_SELF_PLT))(),tls=b_tls();
 if(!tls||self!=tls-0x6f0)return -4;
 u64 target=*(const volatile u64*)(bias+A1_SELF_GOT);
 if(target<A1_SELF_OFFSET||target>=0x8000000000ULL)return -5;
 u64 library=target-A1_SELF_OFFSET;
 if(library&4095)return -5;
 if(!b_equal((const u8*)target,self_guard,16)||
    !b_equal((const u8*)(library+A1_CREATE_OFFSET),create_guard,16)||
    !b_equal((const u8*)(library+A1_DETACH_OFFSET),detach_guard,16))return -6;
 long page=g_sc(222,0,4096,3,0x22,-1,0);
 if((u64)page>=(u64)-4095)return page;
 /* mmap(0,...) should never return zero on this kernel; do not dereference
  * an unexpected result, and keep the claim so failures cannot busy-loop. */
 if(!page||((u64)page&4095)||(u64)page>=0x8000000000ULL)return -7;
 struct a1_boot_context *c=(void*)page;
 volatile u8 *zero=(void*)c;for(u32 i=0;i<4096;i++)zero[i]=0;
 c->version=1;c->bias=bias;c->app_this=app_this;c->pid=owner.pid;
 c->creator_tid=g_sc(178,0,0,0,0,0,0);c->parent_state=1;
 c->create_rc=c->detach_rc=-999;
 for(u32 i=0;i<sizeof(owner);i++)((u8*)&c->reservation)[i]=((const u8*)&owner)[i];
 typedef int (*create_fn)(u64*,const void*,void*(*)(void*),void*);
 int made=((create_fn)(library+A1_CREATE_OFFSET))(&c->handle,0,fl23_a1_boot_worker,c);
 c->create_rc=made;
 if(made){c->parent_state=4;return -8;}
 /* The context cannot be freed here even if the worker already returned.
  * A failed detach retains the joinable thread, page and reservation. No
  * retry, second create, join from App, or unloading of live worker code. */
 typedef int (*detach_fn)(u64);
 int detached=((detach_fn)(library+A1_DETACH_OFFSET))(c->handle);
 c->detach_rc=detached;c->parent_state=detached?5:2;
 return detached?-9:1;
}
