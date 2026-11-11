/* Memory-only A6700 pthread lifecycle diagnostic. Never publishes FL23.
 * Caller retains this independently loaded module until join is confirmed.
 * AP fixed Backup reads; CP fixed native Senser reads and owned stream maps.
 * No application, firmware, parameter, SAVE or WBI writes.
 */
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;
#include "thread-profile.h"

struct a1_thread_report {
 u64 version,state,error,pid,creator_tid,worker_tid,handle,self;
 u64 creator_tls,worker_tls,creator_sp,worker_sp,stack_lo,stack_hi;
 u64 create_rc,join_rc,joined,library,worker_done,create_called;
 u64 worker_pid;u8 nonce[16];u64 reserved[9];
};
_Static_assert(sizeof(struct a1_thread_report)==256,"Thread report ABI");
static struct a1_thread_report report;
static u8 map_text[131072];
static u32 attempt,polling;
static struct {u64 version;long returns[2][40];u8 data[2][BACKUP_STRIDE];} backup;
_Static_assert(sizeof(backup)==648+2*BACKUP_STRIDE,"Backup report ABI");

static long t_sc(long n,long a,long b,long c,long d,long e,long f){
 register long x0 __asm__("x0")=a,x1 __asm__("x1")=b,x2 __asm__("x2")=c;
 register long x3 __asm__("x3")=d,x4 __asm__("x4")=e,x5 __asm__("x5")=f,x8 __asm__("x8")=n;
 __asm__ volatile("svc #0":"+r"(x0):"r"(x1),"r"(x2),"r"(x3),"r"(x4),"r"(x5),"r"(x8):"memory","cc");return x0;
}
static u64 t_tls(void){u64 value;__asm__ volatile("mrs %0,tpidr_el0":"=r"(value));return value;}
static u64 t_sp(void){u64 value;__asm__ volatile("mov %0,sp":"=r"(value));return value;}
static int t_equal(const u8 *a,const u8 *b,u64 n){for(u64 i=0;i<n;i++)if(a[i]!=b[i])return 0;return 1;}
#include "a1-cp-read.h"
static u32 t_u32(const u8 *p){u32 v=0;for(u32 i=0;i<4;i++)v|=(u32)p[i]<<(i*8);return v;}
static int t_hex(const u8 **p,const u8 *end,u64 *out){
 u64 v=0;u32 digits=0;
 while(*p<end){u8 c=**p;u32 n=c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:16;
  if(n==16)break;if(digits++==16)return 0;v=(v<<4)|n;(*p)++;
 }*out=v;return digits!=0;
}
static int t_maps(u64 *length){
 long fd=t_sc(56,-100,(long)"/proc/self/maps",0x80000,0,0,0);if(fd<0)return 0;
 u64 at=0;int ok=0;
 for(u32 i=0;i<1024&&at<sizeof(map_text);i++){
  long n=t_sc(63,fd,(long)(map_text+at),sizeof(map_text)-at,0,0,0);
  if(n==-4)continue;if(n<0)break;if(!n){ok=1;break;}
  if((u64)n>sizeof(map_text)-at)break;at+=n;
 }long closed=t_sc(57,fd,0,0,0,0,0);*length=at;return ok&&!closed;
}
static int t_line(const u8 *line,const u8 *end,u64 *lo,u64 *hi,u64 *offset,const u8 **permission){
 const u8 *p=line;
 if(!t_hex(&p,end,lo)||p==end||*p++!='-'||!t_hex(&p,end,hi)||*hi<=*lo||p+6>=end||*p++!=' ')return 0;
 *permission=p;p+=5;return t_hex(&p,end,offset)&&p<end&&*p==' ';
}
static u64 t_library(const char *a,u64 al,const char *b,u64 bl,const u64 *offsets,const u8 guards[][16],u32 count){
 u64 n=0;if(!t_maps(&n))return 0;
 const u8 *p=map_text,*end=p+n;u64 base=0,low=0,high=0;u32 found=0;
 while(p<end){const u8 *line=p;while(p<end&&*p!='\n')p++;
  u64 lo,hi,offset;const u8 *perm;
  if(!t_line(line,p,&lo,&hi,&offset,&perm))return 0;
  int path=(p-line>=(long)al&&t_equal(p-al,(const u8*)a,al))||
           (p-line>=(long)bl&&t_equal(p-bl,(const u8*)b,bl));
  if(path&&t_equal(perm,(const u8*)"r-xp",4)&&lo>offset&&!((lo-offset)&4095)){base=lo-offset;low=lo;high=hi;found++;}
  if(p<end)p++;
 }
 if(found!=1)return 0;
 for(u32 i=0;i<count;i++){
  u64 at=base+offsets[i];
  if(at<low||at+16>high||!t_equal((const u8*)at,guards[i],16))return 0;
 }
 return base;
}
static int t_stack(u64 sp){
 u64 n=0;if(!t_maps(&n))return 0;
 const u8 *p=map_text,*end=p+n;u32 found=0;
 while(p<end){const u8 *line=p;while(p<end&&*p!='\n')p++;
  u64 lo,hi,offset;const u8 *perm;
  if(!t_line(line,p,&lo,&hi,&offset,&perm))return 0;
  if(lo<=sp&&sp<hi&&perm[0]=='r'&&perm[1]=='w'){
   report.stack_lo=lo;report.stack_hi=hi;found++;
  }if(p<end)p++;
 }return found==1&&!(report.stack_lo<=report.creator_sp&&report.creator_sp<report.stack_hi);
}
#include "preflight.c"
static void *t_worker(void *argument){
 if(argument!=&report)return 0;
 typedef u64 (*self_fn)(void);
 report.worker_pid=t_sc(172,0,0,0,0,0,0);report.worker_tid=t_sc(178,0,0,0,0,0,0);report.worker_tls=t_tls();report.worker_sp=t_sp();
 report.self=((self_fn)(report.library+thread_offsets[1]))();
 if(report.worker_pid!=report.pid||report.worker_tid==report.creator_tid||!report.worker_tls||report.worker_tls==report.creator_tls||
    report.self!=report.worker_tls-0x6f0||!t_stack(report.worker_sp))report.error=5;
 if(!report.error){
  typedef int (*read_fn)(u32,void*,int);
  backup.version=1;report.reserved[2]=BACKUP_STRIDE;report.reserved[3]=sizeof(backup);
  for(u32 pass=0;pass<2;pass++){
   for(u32 j=0;j<BACKUP_STRIDE;j++)backup.data[pass][j]=0xa5;
   for(u32 i=0;i<40;i++)backup.returns[pass][i]=-2147483647L;
  }
  for(u32 pass=0;pass<2&&!report.error;pass++)for(u32 i=0;i<40;i++){
   u32 at=backup_specs[i].offset,n=backup_specs[i].width;u8 *dest=backup.data[pass]+at;
   int rc=backup_specs[i].chip?
    a1_cp_read(report.reserved[4],backup_specs[i].key,n,dest):
    ((read_fn)(report.reserved[0]+backup_offsets[0]))(backup_specs[i].key,dest,n);
   backup.returns[pass][i]=rc;report.reserved[1]++;
   if(backup_specs[i].chip)report.reserved[5]=a1cp_last.unresolved;
   if(rc!=(int)n)report.error=10;
   for(u32 j=0;j<16;j++)if(dest[-16+(int)j]!=0xa5||dest[n+j]!=0xa5)report.error=8;
   if(report.error)break;
  }
  if(!report.error)for(u32 i=0;i<40;i++){
   u32 at=backup_specs[i].offset,n=backup_specs[i].width;
   if(!t_equal(backup.data[0]+at,backup.data[1]+at,n))report.error=9;
  }
 }
 if(!report.error){a1_preflight_run();if(preflight.error)report.error=11;report.reserved[5]|=preflight.unresolved;}
 __atomic_store_n(&report.worker_done,1,__ATOMIC_RELEASE);
 return (void*)0x46323354ULL;
}
long fl23_a1_thread_start(const u8 *config,u64 bytes){
 if(!config||bytes!=40||!t_equal(config,(const u8*)"A67P",4)||t_u32(config+4)!=1||
    t_u32(config+24)!=48||t_u32(config+28)||t_u32(config+32)||t_u32(config+36)!=1032192)return -22;
 u32 expected=0;
 if(!__atomic_compare_exchange_n(&attempt,&expected,1,0,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE))return -114;
 report.version=1;report.state=1;report.pid=t_sc(172,0,0,0,0,0,0);
 report.creator_tid=t_sc(178,0,0,0,0,0,0);report.creator_tls=t_tls();report.creator_sp=t_sp();
 for(u32 i=0;i<16;i++)report.nonce[i]=config[8+i];
 report.library=t_library("/lib/libpthread-2.25.so",23,"/lib/libpthread.so.0",20,thread_offsets,thread_guards,3);
 report.reserved[0]=t_library("/usr/lib/libbackup.so",21,"/usr/lib/libbackup.so",21,backup_offsets,backup_guards,1);
 report.reserved[4]=t_library("/usr/lib/libGetLog.so",21,"/usr/lib/libGetLog.so",21,getlog_offsets,getlog_guards,2);
 if(!report.library||!report.reserved[0]||!report.reserved[4]){report.error=3;report.state=5;return -3;}
 typedef int (*create_fn)(u64*,const void*,void*(*)(void*),void*);
 report.create_called=1;
 int rc=((create_fn)(report.library+thread_offsets[0]))(&report.handle,0,t_worker,&report);
 report.create_rc=(u32)rc;
 if(rc){report.error=4;report.state=5;return -4;}
 report.state=2;return 0;
}
long fl23_a1_thread_poll(void){
 if(!attempt)return -22;
 u32 expected=0;
 if(!__atomic_compare_exchange_n(&polling,&expected,1,0,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE))return -16;
 long result=0;
 if(report.state==2){
  typedef int (*join_fn)(u64,void**);void *value=0;
  int rc=((join_fn)(report.library+thread_offsets[2]))(report.handle,&value);report.join_rc=(u32)rc;
  if(rc==16)result=1;
  else if(rc){report.error=6;result=-6;}
  else{
   report.joined=1;
   if(!__atomic_load_n(&report.worker_done,__ATOMIC_ACQUIRE)||report.handle!=report.self||value!=(void*)0x46323354ULL)report.error=7;
   report.state=report.error?5:4;result=report.error?-(long)report.error:0;
  }
 }else if(report.error)result=-(long)report.error;
 __atomic_store_n(&polling,0,__ATOMIC_RELEASE);return result;
}
const struct a1_thread_report *fl23_a1_thread_report(void){return attempt?&report:(const void*)0;}
const void *fl23_a1_thread_backup(void){return &backup;}
