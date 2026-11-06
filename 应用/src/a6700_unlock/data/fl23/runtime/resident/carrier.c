/* Read-only fixed-slot carrier protocol. No slot is selected here.
 * Bootstrap supplies immutable geometry/body identity and a digest computed
 * from its own linked bytes. Package length/digest live ONLY in the external
 * receipt, avoiding bootstrap -> worker -> core -> bootstrap hash cycles.
 * CRC detects receipt corruption; selected-build authorization remains the
 * installer's separately frozen receipt/source binding, not this CRC.
 */
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;
#ifndef A1_CARRIER_SCHEMA
#define A1_CARRIER_SCHEMA 1
#endif
struct a1_carrier_profile {
 u64 slot,capacity,device_bytes;
 u8 identity[32],firmware[32],bootstrap[32];
};
struct a1_carrier_result {
 u64 version,state,mapping,mapped_bytes,package_bytes;
 long error,close_rc,unmap_rc;
 u8 expected_sha[32];
 u8 receipt[512];
};
_Static_assert(sizeof(struct a1_carrier_result)==608,"Carrier result ABI");
static u32 c_u32(const u8 *p){u32 v=0;for(u32 i=0;i<4;i++)v|=(u32)p[i]<<(i*8);return v;}
static u64 c_u64(const u8 *p){u64 v=0;for(u32 i=0;i<8;i++)v|=(u64)p[i]<<(i*8);return v;}
static int c_eq(const u8 *p,const u8 *q,u32 n){u8 v=0;for(u32 i=0;i<n;i++)v|=p[i]^q[i];return !v;}
static int c_nonzero(const u8 *p,u32 n){u8 v=0;for(u32 i=0;i<n;i++)v|=p[i];return !!v;}
u32 fl23_a1_receipt_crc(const u8 *p){
 u32 crc=~0u;for(u32 i=0;i<508;i++){crc^=p[i];for(u32 b=0;b<8;b++)crc=(crc>>1)^(0xedb88320u&-(crc&1));}return ~crc;
}
long fl23_a1_receipt_validate(const u8 *h,const struct a1_carrier_profile *p){
 if(!h||!p||!p->slot||(p->slot&511)||(p->capacity&511)||p->capacity<1024||
    p->capacity>2097664||p->slot>p->device_bytes||p->capacity>p->device_bytes-p->slot||
    !c_nonzero(p->identity,32)||!c_nonzero(p->firmware,32)||!c_nonzero(p->bootstrap,32))return -22;
#if A1_CARRIER_SCHEMA == 2
 if(!c_eq(h,(const u8*)"FL23A1R2",8)||c_u32(h+8)!=2||c_u32(h+48)!=1||c_u32(h+52)!=2||
#else
 if(!c_eq(h,(const u8*)"FL23A1R1",8)||c_u32(h+8)!=1||c_u32(h+48)!=48||c_u32(h+52)!=1||
#endif
    c_u32(h+12)!=512||
    !c_eq(h+16,(const u8*)"A1READY1",8)||c_u64(h+24)!=p->slot||c_u64(h+32)!=p->capacity||
    c_u64(h+40)<128||c_u64(h+40)>2097152||c_u64(h+40)>p->capacity-512||
    !c_u64(h+56)||
    !c_eq(h+64,p->identity,32)||!c_eq(h+96,p->firmware,32)||!c_eq(h+128,p->bootstrap,32)||
    !c_nonzero(h+160,32)||!c_nonzero(h+192,32)||!c_nonzero(h+224,16))return -22;
 /* These offsets depend on the final worker/core layout. Keeping them in
  * the external receipt (not fixed bootstrap) also avoids a layout cycle.
  * The caller must check each against verified file-backed RX segments. */
#if A1_CARRIER_SCHEMA == 2
 u64 entry=c_u64(h+240);if((entry&3)||entry>c_u64(h+40)-4)return -22;
 for(u32 i=248;i<508;i++)if(h[i])return -22;
#else
 for(u32 i=240;i<272;i+=8){u64 at=c_u64(h+i);if(at<4||(at&3)||at>8388608-4)return -22;}
 for(u32 i=272;i<508;i++)if(h[i])return -22;
#endif
 return c_u32(h+508)==fl23_a1_receipt_crc(h)?0:-74;
}
long fl23_a1_receipt_exports(const u8 *h,const u8 *package,u64 n){
 /* Called only after fl23_a1_load has independently verified SHA and the
  * complete container structure. Defend local table bounds nevertheless. */
 if(!h||!package||n<128)return -22;
 u32 count=c_u32(package+40);u64 table=c_u64(package+48);
 if(!count||count>8||table!=128||table>n||count*40>n-table)return -22;
 for(u32 i=240;i<272;i+=8){
  u64 at=c_u64(h+i);int found=0;
  if(at<4||(at&3))return -22;
  for(u32 j=0;j<count;j++){
   const u8 *s=package+table+j*40;u64 start=c_u64(s+8),bytes=c_u64(s+16);
   if(c_u32(s+32)==5&&at>=start&&bytes>=4&&at-start<=bytes-4)found=1;
  }if(!found)return -22;
 }return 0;
}
static long c_sc(long n,long a,long b,long c,long d,long e,long f){
 register long x0 __asm__("x0")=a,x1 __asm__("x1")=b,x2 __asm__("x2")=c;
 register long x3 __asm__("x3")=d,x4 __asm__("x4")=e,x5 __asm__("x5")=f,x8 __asm__("x8")=n;
 __asm__ volatile("svc #0":"+r"(x0):"r"(x1),"r"(x2),"r"(x3),"r"(x4),"r"(x5),"r"(x8):"memory","cc");return x0;
}
static long c_read(long fd,u8 *p,u64 n,u64 at){
 for(u32 tries=0;n&&tries<1024;tries++){
  long rc=c_sc(67,fd,(long)p,n,at,0,0);if(rc==-4)continue;
  if(rc<0)return rc;if(!rc||(u64)rc>n)return -5;
  p+=rc;n-=rc;at+=rc;
 }return n?-110:0;
}
long fl23_a1_carrier_read(const struct a1_carrier_profile *p,struct a1_carrier_result *r){
 if(!p||!r)return -22;
 volatile u8 *zero=(void*)r;for(u32 i=0;i<sizeof(*r);i++)zero[i]=0;r->version=1;
 /* Reject even malformed geometry before opening the device. Dummy receipt
  * validation is not used: all profile checks are explicit and independent. */
 if(!p->slot||(p->slot&511)||(p->capacity&511)||p->capacity<1024||p->capacity>2097664||
    p->slot>p->device_bytes||p->capacity>p->device_bytes-p->slot||
    !c_nonzero(p->identity,32)||!c_nonzero(p->firmware,32)||!c_nonzero(p->bootstrap,32))return r->error=-22;
 long fd=c_sc(56,-100,(long)"/setting/fl23",0x80000,0,0,0);
 if(fd<0)return r->error=fd;
 int acquired=0;long rc=c_read(fd,r->receipt,512,p->slot);
 if(!rc)rc=fl23_a1_receipt_validate(r->receipt,p);
 if(!rc){
  r->package_bytes=c_u64(r->receipt+40);r->mapped_bytes=(r->package_bytes+4095)&~4095ULL;
  long map=c_sc(222,0,r->mapped_bytes,3,0x22,-1,0);
  if((u64)map>=(u64)-4095)rc=map;
  else{
   acquired=1;r->mapping=map;
   if(!map||((u64)map&4095)||(u64)map>=0x8000000000ULL-r->mapped_bytes)rc=-34;
   else rc=c_read(fd,(u8*)map,r->package_bytes,p->slot+512);
  }
 }
 if(!rc){u8 second[512];rc=c_read(fd,second,512,p->slot);if(!rc&&!c_eq(second,r->receipt,512))rc=-116;}
 r->close_rc=c_sc(57,fd,0,0,0,0,0);
 if(!rc&&r->close_rc)rc=r->close_rc;
 if(rc){
  r->error=rc;
  if(acquired){r->unmap_rc=c_sc(215,r->mapping,r->mapped_bytes,0,0,0,0);if(!r->unmap_rc)r->mapping=r->mapped_bytes=0;}
  r->state=r->close_rc||r->unmap_rc?2:0;return rc;
 }
 for(u32 i=0;i<32;i++)r->expected_sha[i]=r->receipt[192+i];
 r->state=1;return 0;
}
