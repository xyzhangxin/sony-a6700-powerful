/* Included in the frozen pthread derivative. No application publication.
 * Consumes native Backup reads, reuses the native CP DMA helper, then loads
 * and checks the normal core's explicit ABI without invoking its publisher.
 */
#include "sha256.h"
#include "preflight-profile.h"
struct a1_loaded {u64 version,state,mapping,bytes,entry;long error,cleanup_error;u64 reserved;};
extern long fl23_a1_load(const u8*,u64,const u8*,struct a1_loaded*);
struct a1_pf_report {
 u64 version,error,phase,failed_index,cp_reads,ap_keys,parameter_hashes,core_loaded;
 u64 core_mapping,core_bytes,core_entry,core_getter;long core_load_rc,core_unmap_rc;
 u64 initial_reply,unresolved;u8 core_sha[32];u64 reserved[12];
};
struct a1_pf_row {u64 address;u32 bytes,passes;u8 digest[32];};
_Static_assert(sizeof(struct a1_pf_report)==256,"Preflight ABI");
_Static_assert(sizeof(struct a1_pf_row)==48,"CP window ABI");
static struct a1_pf_report preflight;
static struct a1_pf_row pf_rows[28];
static u8 pf_code_snapshot[2][8192];
static u8 pf_buffer[3488];
static u32 a1_dma_cleanup;
#include "preflight-dma.h"
static void pf_hash(const u8 *p,u32 n,u8 out[32]){SHA256 s;sha_init(&s);sha_add(&s,p,n);sha_end(&s,out);}
static int pf_saved(void){
 preflight.phase=1;
 for(u32 pass=0;pass<2;pass++)for(u32 i=0;i<40;i++){
  u32 n=backup_specs[i].width,at=backup_specs[i].offset;const u8 *p=backup.data[pass]+at;
  preflight.failed_index=i+1;
  if(backup.returns[pass][i]!=(long)n)return 1;
  if(!backup_specs[i].chip){
   u32 maximum=(backup_specs[i].key==0x02cf036fu||backup_specs[i].key==0x02cf0370u)?15:9;
   for(u32 j=0;j<n;j++)if(p[j]>maximum)return 2;
   if(pass==1)preflight.ap_keys++;
  }else{
   u8 digest[32];pf_hash(p,n,digest);
   if(!t_equal(digest,pf_parameter_sha[i-38],32))return 3;
   preflight.parameter_hashes++;
  }
 }
 return 0;
}
static int pf_code(u64 base){
 preflight.phase=2;
 for(u32 j=0;j<3;j++){
  if(!t_equal((const u8*)(base+pf_dma_offsets[j]),pf_dma_guards[j],16))return 4;
 }
 for(u32 pass=0;pass<2;pass++)for(u32 i=0;i<28;i++){
  u32 n=pf_code_specs[i].bytes;u8 *p=pf_buffer+16;preflight.failed_index=i+1;
  for(u32 j=0;j<sizeof(pf_buffer);j++)pf_buffer[j]=0xa5;
  u32 status=dma_read(base,pf_code_specs[i].address,n,p);preflight.cp_reads++;
  if(a1_dma_cleanup){preflight.unresolved=1;return 5;}
  if(status)return 6;
  for(u32 j=0;j<16;j++)if(p[-16+(int)j]!=0xa5||p[n+j]!=0xa5)return 7;
  struct a1_pf_row *r=pf_rows+i;r->address=pf_code_specs[i].address;r->bytes=n;
  u8 digest[32];pf_hash(p,n,digest);
  for(u32 j=0;j<n;j++)pf_code_snapshot[pass][pf_code_specs[i].offset+j]=p[j];
  if(pass&& !t_equal(digest,r->digest,32))return 9;
  for(u32 j=0;j<32;j++)r->digest[j]=digest[j];r->passes=pass+1;
  if(!t_equal(p,pf_code_bytes+pf_code_specs[i].offset,n))return 8;
 }
 return 0;
}
#include "publish.c"
static int pf_core(void){
 preflight.phase=3;
 struct a1_loaded loaded;long rc=fl23_a1_load(pf_core_package,sizeof(pf_core_package),pf_core_sha,&loaded);
 preflight.core_load_rc=rc;preflight.core_mapping=loaded.mapping;preflight.core_bytes=loaded.bytes;preflight.core_entry=loaded.entry;
 for(u32 j=0;j<32;j++)preflight.core_sha[j]=pf_core_sha[j];
 if(rc){if(loaded.mapping)preflight.unresolved=1;return 10;}
 preflight.core_loaded=1;preflight.core_getter=loaded.mapping+PF_CORE_GETTER;
 typedef const void *(*reply_fn)(void);
 preflight.initial_reply=(u64)((reply_fn)preflight.core_getter)();
 if(preflight.initial_reply){preflight.unresolved=1;return 11;}
 int publish_rc=fl23_a1_publish_run(loaded.entry,preflight.core_getter);
 if(publication.unresolved){preflight.unresolved=1;return publish_rc;}
 preflight.core_unmap_rc=t_sc(215,loaded.mapping,loaded.bytes,0,0,0,0);
 if(preflight.core_unmap_rc){preflight.unresolved=1;return 12;}
 publication.core_released=1;
 return publish_rc;
}
static void a1_preflight_run(void){
 preflight.version=1;int rc=pf_saved();
 if(!rc)rc=pf_code(report.reserved[4]);
 if(!rc)rc=pf_core();
 preflight.error=rc;
 if(!rc){preflight.phase=4;preflight.failed_index=0;}
}
const void *fl23_a1_preflight_report(void){return &preflight;}
const void *fl23_a1_preflight_rows(void){return pf_rows;}
const void *fl23_a1_preflight_code(void){return pf_code_snapshot;}
