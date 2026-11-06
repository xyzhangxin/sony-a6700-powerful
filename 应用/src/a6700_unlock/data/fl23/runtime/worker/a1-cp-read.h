/* Fixed A6700 2.00 READ_INTERCHIP through the vendor Senser OSAL route.
 * Caller supplies its validated libGetLog r-xp load bias, owns the device
 * lock, and serializes all Senser traffic until finish. No USB is used.
 * u8/u32/u64 must already be defined. See test_fl23_a1_cp_read.py.
 */
#ifndef FL23_A1_CP_READ_H
#define FL23_A1_CP_READ_H
struct a1cp_diagnostic {
 u64 version,key,requested,starts,finishes,physical,mapping,mapping_bytes;
 long start_rc,finish_rc,open_rc,map_rc,unmap_rc,close_rc;
 u64 response_state,response_status,returned,finish_state,finish_status;
 long result;u64 unresolved;
};
static struct a1cp_diagnostic a1cp_last;
static long a1cp_sc(long n,long a,long b,long c,long d,long e,long f){
 register long x0 __asm__("x0")=a,x1 __asm__("x1")=b,x2 __asm__("x2")=c;
 register long x3 __asm__("x3")=d,x4 __asm__("x4")=e,x5 __asm__("x5")=f,x8 __asm__("x8")=n;
 __asm__ volatile("svc #0":"+r"(x0):"r"(x1),"r"(x2),"r"(x3),"r"(x4),"r"(x5),"r"(x8):"memory","cc");return x0;
}
static u64 a1cp_u64(const u8 *p){u64 v=0;for(u32 i=0;i<8;i++)v|=(u64)p[i]<<(i*8);return v;}
static u32 a1cp_u32(const u8 *p){u32 v=0;for(u32 i=0;i<4;i++)v|=(u32)p[i]<<(i*8);return v;}
static void a1cp_put32(u8 *p,u32 v){for(u32 i=0;i<4;i++)p[i]=(u8)(v>>(i*8));}
static int a1cp_equal(const u8 *a,const u8 *b,u32 n){for(u32 i=0;i<n;i++)if(a[i]!=b[i])return 0;return 1;}
/* Success returns the exact unit byte count. All failures are negative:
 * -10 scope, -11 code guard, -12 start transport, -13 start metadata,
 * -14 open, -15 mmap, -16 munmap, -17 close, -18 finish transport,
 * -19 finish metadata. a1cp_last retains every cleanup result even when
 * an earlier failure determines the returned error. Never retry start here.
 */
static int a1_cp_read(u64 getlog,u32 key,u32 bytes,u8 *out){
 for(u32 i=0;i<sizeof(a1cp_last)/8;i++)((u64*)&a1cp_last)[i]=0;
 a1cp_last.version=1;a1cp_last.key=key;a1cp_last.requested=bytes;
 if(!out||!getlog||(getlog&4095)||
    !((key==0x02a40001u&&bytes==8192)||(key==0x02a4000du&&bytes==28672))){a1cp_last.result=-10;return -10;}
 static const u8 guard[16]={0xfd,0x7b,0xbb,0xa9,0xfd,0x03,0x00,0x91,0xf7,0x63,0x03,0xa9,0xf7,0x03,0x00,0xaa};
 if(!a1cp_equal((const u8*)(getlog+0x46d8),guard,16)){a1cp_last.result=-11;return -11;}
 typedef int (*query_fn)(u8*,int,const u8*);
 query_fn query=(query_fn)(getlog+0x46d8);
 u8 request[336],response[336],finish[336],closed[336];
 for(u32 i=0;i<336;i++)request[i]=response[i]=finish[i]=closed[i]=0;
 request[0]=1;request[1]=1;request[2]=1;
 a1cp_put32(request+4,12);a1cp_put32(request+8,0x40);
 request[0x24]=3;request[0x25]=6;request[0x26]=0x40;
 a1cp_put32(request+0x28,key);a1cp_put32(request+0x2c,1);
 a1cp_last.starts=1;a1cp_last.unresolved=1;a1cp_last.start_rc=query(response,336,request);
 if(a1cp_last.start_rc){a1cp_last.result=-12;return -12;}
 a1cp_last.response_state=response[0];a1cp_last.response_status=response[1];
 u64 physical=a1cp_u64(response+0x10);u32 returned=a1cp_u32(response+0x18);
 a1cp_last.physical=physical;a1cp_last.returned=returned;
 int identity=a1cp_equal(response+0x24,request+0x24,12)&&response[2]==request[2]&&response[3]==request[3];
 int framing=!response[4]&&!response[5]&&!a1cp_u32(response+8);
 /* Only a positively identified process-end grants ownership. The vendor
  * error path (0/0x81 and zero pointer/count) has already freed its state.
  * Unknown/busy/malformed replies must not free someone else's global read.
  */
 if(identity&&framing&&response[0]==0&&response[1]==0x81&&!physical&&!returned){
  a1cp_last.unresolved=0;a1cp_last.result=-13;return -13;
 }
 if(!identity||!framing||response[0]!=8||response[1]!=1||!physical||
    physical>0x7fffffffffffffffULL-bytes){a1cp_last.result=-13;return -13;}
 int result=0;long fd=-1,mapping=-1;u64 span=0;
 if(returned!=bytes)result=-13;
 else{
  fd=a1cp_sc(56,-100,(long)"/dev/stream",0x80000,0,0,0);a1cp_last.open_rc=fd;
  if(fd<0)result=-14;
  else{
   u64 delta=physical&4095;span=bytes+delta;
   mapping=a1cp_sc(222,0,span,1,1,fd,physical-delta);a1cp_last.map_rc=mapping;
   if((u64)mapping>=(u64)-4095)result=-15;
   else{
    a1cp_last.mapping=mapping;a1cp_last.mapping_bytes=span;
    if(!mapping)result=-15;
    else{const u8 *source=(const u8*)((u64)mapping+delta);for(u32 i=0;i<bytes;i++)out[i]=source[i];}
    a1cp_last.unmap_rc=a1cp_sc(215,mapping,span,0,0,0,0);
    if(a1cp_last.unmap_rc&&!result)result=-16;
   }
   a1cp_last.close_rc=a1cp_sc(57,fd,0,0,0,0,0);
   if(a1cp_last.close_rc&&!result)result=-17;
  }
 }
 /* Follow the original libGetLog +0x4888..+0x48d0 finish template. */
 for(u32 i=0;i<336;i++)finish[i]=request[i];
 finish[0]=0x10;finish[0xc]=response[4];
 for(u32 i=0;i<4;i++)finish[0x10+i]=response[8+i];
 for(u32 i=0;i<8;i++)finish[0x18+i]=response[0x10+i];
 for(u32 i=0;i<4;i++)finish[0x20+i]=response[0x18+i];
 a1cp_last.finishes=1;a1cp_last.finish_rc=query(closed,336,finish);
 a1cp_last.finish_state=closed[0];a1cp_last.finish_status=closed[1];
 if(a1cp_last.finish_rc){if(!result)result=-18;}
 else if(closed[0]!=0x20||closed[1]!=1||closed[4]||closed[5]||a1cp_u32(closed+8)||
         closed[2]!=request[2]||closed[3]!=request[3]||!a1cp_equal(closed+0x24,request+0x24,12)||
         a1cp_u64(closed+0x10)||a1cp_u32(closed+0x18)){if(!result)result=-19;}
 else if(!a1cp_last.unmap_rc&&!a1cp_last.close_rc)a1cp_last.unresolved=0;
 a1cp_last.result=result?result:(int)bytes;return (int)a1cp_last.result;
}
#endif
