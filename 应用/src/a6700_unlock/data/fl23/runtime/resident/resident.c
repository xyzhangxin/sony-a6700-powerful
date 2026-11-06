/* Resident stage 0. General container loading/business lifecycle are external.
 * Dispatch and carrier sources are reused directly; no ELF parser resides
 * here. The only accepted stage 1 is immutable, relocation-free PIC code.
 */
#include "dispatch.c"
#define A1_CARRIER_SCHEMA 2
#include "carrier.c"
#include "sha256.h"
#include "bootstrap-profile.h"
extern __attribute__((visibility("hidden"))) const u8 fl23_boot_start[],fl23_boot_end[];
__attribute__((visibility("hidden"))) void *memcpy(void *d,const void *s,unsigned long n){
 volatile u8 *to=d;const volatile u8 *from=s;for(unsigned long i=0;i<n;i++)to[i]=from[i];return d;
}
__attribute__((visibility("hidden"))) void *memset(void *d,int v,unsigned long n){
 volatile u8 *to=d;for(unsigned long i=0;i<n;i++)to[i]=(u8)v;return d;
}
static void s0_hash(const u8 *p,u32 n,u8 out[32]){SHA256 s;sha_init(&s);sha_add(&s,p,n);sha_end(&s,out);}
/* Same CTR_EL0-derived sequence as the existing container loader. Only the
 * already RX mapping is synchronized; no writable/executable combination. */
static void s0_cache(u64 at,u64 n){
 u64 ctr;__asm__ volatile("mrs %0,ctr_el0":"=r"(ctr));u64 ds=4ULL<<((ctr>>16)&15),is=4ULL<<(ctr&15),end=at+n;
 for(u64 p=at&~(ds-1);p<end;p+=ds)__asm__ volatile("dc cvau,%0"::"r"(p):"memory");
 __asm__ volatile("dsb ish":::"memory");
 for(u64 p=at&~(is-1);p<end;p+=is)__asm__ volatile("ic ivau,%0"::"r"(p):"memory");
 __asm__ volatile("dsb ish;isb":::"memory");
}
void *fl23_a1_boot_worker(void *arg){
 struct a1_boot_context *c=arg;if(!c||c->version!=1)return 0;
 c->worker_state=1;
 struct a1_carrier_profile profile=s0_profile;
 s0_hash(fl23_boot_start,(u32)(fl23_boot_end-fl23_boot_start),profile.bootstrap);
 struct a1_carrier_result *r=(void*)((u8*)c+1024);
 long rc=fl23_a1_carrier_read(&profile,r);c->worker_error=rc;
 c->reserved[0]=r->mapping;c->reserved[1]=r->mapped_bytes;c->reserved[2]=r->package_bytes;
 if(rc){c->worker_state=2;return 0;}
 u8 digest[32];s0_hash((const u8*)r->mapping,(u32)r->package_bytes,digest);
 if(!c_eq(digest,r->expected_sha,32)){rc=-74;c->worker_state=3;goto release;}
 rc=c_sc(226,r->mapping,r->mapped_bytes,5,0,0,0);
 if(rc){c->worker_state=4;goto release;}
 s0_cache(r->mapping,r->package_bytes);
 c->reserved[3]=c_u64(r->receipt+240);c->worker_state=5;
 typedef long (*entry_fn)(struct a1_boot_context*);
 rc=((entry_fn)(r->mapping+c->reserved[3]))(c);
 c->reserved[4]=rc;c->worker_state=6;
release:
 c->worker_error=rc;c->reserved[5]=c_sc(215,r->mapping,r->mapped_bytes,0,0,0,0);
 if(c->reserved[5])c->worker_state=8;
 else c->reserved[0]=c->reserved[1]=0;
 return 0;
}
