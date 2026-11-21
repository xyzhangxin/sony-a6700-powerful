/* A6700 A1 bounded PIC-container loader. No entry invocation, files, USB,
 * firmware, WBI, application patching or thread creation. The caller owns
 * mapping lifetime and must bind expected_sha outside the container.
 * sha256.h is reused unchanged from the existing frozen probe inputs. */
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;
/* Clang may lower structure copies/initializers to these freestanding ABI
 * helpers even with -fno-builtin. Volatile loops cannot recurse into libc. */
__attribute__((visibility("hidden"))) void *memcpy(void *to,const void *from,unsigned long n){
 volatile u8 *d=to;const volatile u8 *s=from;for(unsigned long i=0;i<n;i++)d[i]=s[i];return to;
}
__attribute__((visibility("hidden"))) void *memset(void *to,int value,unsigned long n){
 volatile u8 *d=to;for(unsigned long i=0;i<n;i++)d[i]=(u8)value;return to;
}
#include "sha256.h"

#define A1_HEADER 128u
#define A1_MAX_PACKAGE (2u*1024u*1024u)
#define A1_MAX_MEMORY (8u*1024u*1024u)
struct a1_plan {u64 memory,entry,segments,relocations,segment_table,relocation_table;};
struct a1_loaded {u64 version,state,mapping,bytes,entry;long error,cleanup_error;u64 reserved;};
_Static_assert(sizeof(struct a1_loaded)==64,"A1 result ABI");
static u32 a1_u32(const u8 *p){u32 v=0;for(u32 i=0;i<4;i++)v|=(u32)p[i]<<(8*i);return v;}
static u64 a1_u64(const u8 *p){u64 v=0;for(u32 i=0;i<8;i++)v|=(u64)p[i]<<(8*i);return v;}
static u64 a1_align(u64 n,u64 a){return (n+a-1)&~(a-1);}
static int a1_span(u64 at,u64 n,u64 limit){return at<=limit&&n<=limit-at;}
static int a1_zero(const u8 *p,u64 n){for(u64 i=0;i<n;i++)if(p[i])return 0;return 1;}
static int a1_equal(const u8 *a,const u8 *b,u32 n){u8 v=0;for(u32 i=0;i<n;i++)v|=a[i]^b[i];return !v;}
static const u8 *a1_segment(const u8 *p,const struct a1_plan *v,u64 i){return p+v->segment_table+i*40;}
static int a1_contains(const u8 *p,const struct a1_plan *v,u64 at,u64 n,u32 flags,int file){
 for(u64 i=0;i<v->segments;i++){
  const u8 *s=a1_segment(p,v,i);u64 start=a1_u64(s+8),bytes=a1_u64(s+(file?16:24));
  if((a1_u32(s+32)&flags)==flags&&at>=start&&a1_span(at-start,n,bytes))return 1;
 }return 0;
}
long fl23_a1_validate(const u8 *p,u64 n,const u8 *expected_sha,struct a1_plan *v){
 static const u8 magic[8]={'F','L','2','3','A','1','L','1'};
 if(!p||!expected_sha||!v||n<A1_HEADER||n>A1_MAX_PACKAGE)return -22;
 SHA256 hash;u8 digest[32];sha_init(&hash);sha_add(&hash,p,(u32)n);sha_end(&hash,digest);
 if(!a1_equal(digest,expected_sha,32))return -74;
 if(!a1_equal(p,magic,8)||a1_u32(p+8)!=1||a1_u32(p+12)!=A1_HEADER||a1_u64(p+16)!=n||
    a1_u64(p+72)||!a1_zero(p+112,16))return -22;
 struct a1_plan q={a1_u64(p+24),a1_u64(p+32),a1_u32(p+40),a1_u32(p+44),a1_u64(p+48),a1_u64(p+56)};
 if(!q.memory||q.memory>A1_MAX_MEMORY||(q.memory&4095)||!q.segments||q.segments>8||q.relocations>64||
    q.segment_table!=A1_HEADER||q.relocation_table!=A1_HEADER+q.segments*40)return -22;
 u64 table_end=q.relocation_table+q.relocations*16,payload=a1_align(table_end,16);
 if(a1_u64(p+64)!=payload||!a1_span(table_end,payload-table_end,n)||!a1_zero(p+table_end,payload-table_end))return -22;
 u64 end=0;
 for(u64 i=0;i<q.segments;i++){
  if(!a1_span(q.segment_table+i*40,40,n))return -22;
  const u8 *s=a1_segment(p,&q,i);u64 offset=a1_u64(s),at=a1_u64(s+8),file=a1_u64(s+16),mem=a1_u64(s+24);
  u32 flags=a1_u32(s+32);
  if(!mem||file>mem||!a1_span(at,mem,q.memory)||!a1_span(offset,file,n)||offset!=payload||
     flags!=4&&flags!=5&&flags!=6||a1_u32(s+36)||(!i&&at)||
     i&&(at&~4095ULL)<a1_align(end,4096))return -22;
  end=at+mem;u64 next=a1_align(offset+file,16);
  if(i+1<q.segments){if(!a1_span(offset+file,next-offset-file,n)||!a1_zero(p+offset+file,next-offset-file))return -22;payload=next;}
  else if(offset+file!=n)return -22;
 }
 if(a1_align(end,4096)!=q.memory||(q.entry&3)||!a1_contains(p,&q,q.entry,4,1,1))return -22;
 u64 previous=0;
 for(u64 i=0;i<q.relocations;i++){
  if(!a1_span(q.relocation_table+i*16,16,n))return -22;
  const u8 *r=p+q.relocation_table+i*16;u64 at=a1_u64(r),add=a1_u64(r+8);
  if(at&7||i&&at<=previous||!a1_contains(p,&q,at,8,2,0)||!a1_contains(p,&q,add,1,0,0))return -22;
  previous=at;
 }
 *v=q;return 0;
}
static long a1_sc(long n,long a,long b,long c,long d,long e,long f){
 register long x0 __asm__("x0")=a,x1 __asm__("x1")=b,x2 __asm__("x2")=c;
 register long x3 __asm__("x3")=d,x4 __asm__("x4")=e,x5 __asm__("x5")=f,x8 __asm__("x8")=n;
 __asm__ volatile("svc #0":"+r"(x0):"r"(x1),"r"(x2),"r"(x3),"r"(x4),"r"(x5),"r"(x8):"memory");return x0;
}
static void a1_cache(u64 at,u64 n){
 u64 ctr;__asm__ volatile("mrs %0,ctr_el0":"=r"(ctr));u64 ds=4ULL<<((ctr>>16)&15),is=4ULL<<(ctr&15),end=at+n;
 for(u64 p=at&~(ds-1);p<end;p+=ds)__asm__ volatile("dc cvau,%0"::"r"(p):"memory");
 __asm__ volatile("dsb ish":::"memory");
 for(u64 p=at&~(is-1);p<end;p+=is)__asm__ volatile("ic ivau,%0"::"r"(p):"memory");
 __asm__ volatile("dsb ish;isb":::"memory");
}
long fl23_a1_load(const u8 *p,u64 n,const u8 *expected_sha,struct a1_loaded *out){
 if(!out)return -22;
 for(u32 i=0;i<sizeof(*out)/8;i++)((u64*)out)[i]=0;out->version=1;
 struct a1_plan v;long rc=fl23_a1_validate(p,n,expected_sha,&v);if(rc){out->error=rc;return rc;}
 long allocated=a1_sc(222,0,v.memory,3,0x22,-1,0);
 if((u64)allocated>=(u64)-4095){out->error=allocated;return allocated;}
 u64 base=allocated;out->mapping=base;out->bytes=v.memory;
 if(!base||(base&4095)||base>=0x8000000000ULL||v.memory>0x8000000000ULL-base||
    ((u64)p<base+v.memory&&base<(u64)p+n)){rc=-34;goto failed;}
 u8 *dest=(void*)base;
 for(u64 i=0;i<v.memory;i++)dest[i]=0;
 for(u64 i=0;i<v.segments;i++){
  const u8 *s=a1_segment(p,&v,i);u64 at=a1_u64(s+8),file=a1_u64(s+16),offset=a1_u64(s);
  for(u64 j=0;j<file;j++)dest[at+j]=p[offset+j];
 }
 for(u64 i=0;i<v.relocations;i++){
  const u8 *r=p+v.relocation_table+i*16;u64 at=a1_u64(r),value=base+a1_u64(r+8);
  for(u32 j=0;j<8;j++)dest[at+j]=(u8)(value>>(j*8));
 }
 rc=a1_sc(226,base,v.memory,0,0,0,0);if(rc)goto failed;
 for(u64 i=0;i<v.segments;i++){
  const u8 *s=a1_segment(p,&v,i);u64 at=a1_u64(s+8),mem=a1_u64(s+24),lo=at&~4095ULL;
  u32 flags=a1_u32(s+32),perm=1|((flags&2)?2:0)|((flags&1)?4:0);
  rc=a1_sc(226,base+lo,a1_align(at+mem,4096)-lo,perm,0,0,0);if(rc)goto failed;
  if(flags&1)a1_cache(base+at,mem);
 }
 out->entry=base+v.entry;out->state=1;return 0;
failed:
 out->error=rc;out->cleanup_error=a1_sc(215,base,v.memory,0,0,0,0);
 if(out->cleanup_error){out->state=2;return rc;}
 out->mapping=out->bytes=0;return rc;
}
