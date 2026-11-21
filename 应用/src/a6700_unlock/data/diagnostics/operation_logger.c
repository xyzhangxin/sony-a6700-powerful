/* Background operation logger. Never changes settings or FL23 behavior.
 * Reads the current creative-look selection and appends each change to the
 * persistent Jiritsu history file, fsyncing every record. */
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
static u32 attempt,polling,stop_requested;
static const char history_path[]="/tmp/jiritsu/history.txt";
static u32 history_bytes;
#define HISTORY_LIMIT (120u*1024u)
#define SELECTION_KEY 0x02cf036fU
#define SELECTION_BYTES 1u
#define MR_KEY 0x02cf0370U
#define MR_BYTES 13u
#define MAIN_PARAMETER 0x530u
#define RECORD_GET_OFFSET 0x2fac304u
#define LIVE_GET_OFFSET 0x2fac320u

static long t_sc(long n,long a,long b,long c,long d,long e,long f){
 register long x0 __asm__("x0")=a,x1 __asm__("x1")=b,x2 __asm__("x2")=c;
 register long x3 __asm__("x3")=d,x4 __asm__("x4")=e,x5 __asm__("x5")=f,x8 __asm__("x8")=n;
 __asm__ volatile("svc #0":"+r"(x0):"r"(x1),"r"(x2),"r"(x3),"r"(x4),"r"(x5),"r"(x8):"memory","cc");return x0;
}
static u64 t_tls(void){u64 value;__asm__ volatile("mrs %0,tpidr_el0":"=r"(value));return value;}
static u64 t_sp(void){u64 value;__asm__ volatile("mov %0,sp":"=r"(value));return value;}
static int t_equal(const u8 *a,const u8 *b,u64 n){for(u64 i=0;i<n;i++)if(a[i]!=b[i])return 0;return 1;}
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
static u32 t_dec(u8 *out,u32 value){
 u32 n=0,i=1000000000;
 if(!value){out[0]='0';return 1;}
 while(i>value)i/=10;
 for(;;){u32 digit=value/i;out[n++]=(u8)('0'+digit);value%=i;i/=10;if(!i)break;}
 return n;
}
static u32 t_signed(u8 *out,long value){
 if(value<0){out[0]='-';return 1+t_dec(out+1,(u32)(-value));}
 return t_dec(out,(u32)value);
}
static u32 t_hex_bytes(u8 *out,const u8 *data,u32 bytes){
 static const char digits[]="0123456789abcdef";
 u32 at=0;
 for(u32 i=0;i<bytes;i++){out[at++]=(u8)digits[data[i]>>4];out[at++]=(u8)digits[data[i]&15];}
 return at;
}
static u64 t_base_for_path(const char *path,u32 length){
 u64 maps_bytes=0;if(!t_maps(&maps_bytes))return 0;
 const u8 *p=map_text,*end=p+maps_bytes;u64 found=0,base=0;
 while(p<end){
  const u8 *line=p;while(p<end&&*p!='\n')p++;
  u64 lo,hi,offset;const u8 *permission;
  if(!t_line(line,p,&lo,&hi,&offset,&permission))return 0;
  if((u64)(p-line)>=length&&t_equal(p-length,(const u8*)path,length)
     &&t_equal(permission,(const u8*)"r-xp",4)&&offset==0){base=lo-offset;found++;}
  if(p<end)p++;
 }
 return found==1?base:0;
}
static int log_event(u32 selector,u32 marker){
 u8 line[80];u32 at=0;
 if(marker==1||marker==2){
  const char *text=marker==1?"START ":"STOP ";for(u32 i=0;text[i];i++)line[at++]=text[i];
  const char *digits="0123456789abcdef";
  for(u32 i=0;i<16;i++){line[at++]=digits[report.nonce[i]>>4];line[at++]=digits[report.nonce[i]&15];}
  line[at++]='\n';
 }
 else{
  u64 ts[2]={0,0};if(t_sc(113,1,(long)ts,0,0,0,0))return -1;
  const char *prefix="T";line[at++]=prefix[0];at+=t_dec(line+at,(u32)ts[0]);
  const char *sel=marker==3?" READY SEL=":marker==4?" ERR=":" SEL=";
  for(u32 i=0;sel[i];i++)line[at++]=sel[i];
  at+=t_signed(line+at,marker==4?(long)(int)selector:(long)selector);line[at++]='\n';
 }
 if(history_bytes+at>HISTORY_LIMIT)return -6;
 long fd=t_sc(56,-100,(long)history_path,0xa0441,0644,0,0);if(fd<0)return -2;
 long written=t_sc(64,fd,(long)line,at,0,0,0);
 if(written!=at){t_sc(57,fd,0,0,0,0,0);return -3;}
 long sync=t_sc(82,fd,0,0,0,0,0);if(sync){t_sc(57,fd,0,0,0,0,0);return -4;}
 long closed=t_sc(57,fd,0,0,0,0,0);
 if(!closed)history_bytes+=at;
 return closed?-5:0;
}
static int log_state(u32 record,u32 live,u32 saved,const u8 *mr,u32 marker){
 u8 line[128];u32 at=0;
 u64 ts[2]={0,0};if(t_sc(113,1,(long)ts,0,0,0,0))return -1;
 line[at++]='T';at+=t_dec(line+at,(u32)ts[0]);
 const char *prefix=marker==3?" READY ":marker==4?" ERR ":" STATE ";
 for(u32 i=0;prefix[i];i++)line[at++]=(u8)prefix[i];
 const char *rec="REC=";for(u32 i=0;rec[i];i++)line[at++]=(u8)rec[i];at+=t_dec(line+at,record);line[at++]=' ';
 const char *live_text="LIVE=";for(u32 i=0;live_text[i];i++)line[at++]=(u8)live_text[i];at+=t_dec(line+at,live);line[at++]=' ';
 const char *sel="SEL=";for(u32 i=0;sel[i];i++)line[at++]=(u8)sel[i];at+=t_dec(line+at,saved);line[at++]=' ';
 const char *mr_text="MR=";for(u32 i=0;mr_text[i];i++)line[at++]=(u8)mr_text[i];at+=t_hex_bytes(line+at,mr,MR_BYTES);line[at++]='\n';
 if(history_bytes+at>HISTORY_LIMIT)return -6;
 long fd=t_sc(56,-100,(long)history_path,0xa0441,0644,0,0);if(fd<0)return -2;
 long written=t_sc(64,fd,(long)line,at,0,0,0);
 if(written!=(long)at){t_sc(57,fd,0,0,0,0,0);return -3;}
 long sync=t_sc(82,fd,0,0,0,0,0);if(sync){t_sc(57,fd,0,0,0,0,0);return -4;}
 long closed=t_sc(57,fd,0,0,0,0,0);
 if(!closed)history_bytes+=at;
 return closed?-5:0;
}
static void *t_worker(void *argument){
 if(argument!=&report)return 0;
 typedef u64 (*self_fn)(void);
 report.worker_pid=t_sc(172,0,0,0,0,0,0);report.worker_tid=t_sc(178,0,0,0,0,0,0);report.worker_tls=t_tls();report.worker_sp=t_sp();
 report.self=((self_fn)(report.library+thread_offsets[1]))();
 if(report.worker_pid!=report.pid||report.worker_tid==report.creator_tid||!report.worker_tls||report.worker_tls==report.creator_tls||
    report.self!=report.worker_tls-0x6f0||!t_stack(report.worker_sp))report.error=5;
 if(!report.error){
  typedef int (*read_fn)(u32,void*,int);
  typedef u32 (*param_get_fn)(u32);
  u64 app_base=t_base_for_path("/usr/lib/appFw.so",17);
  param_get_fn record_get=(param_get_fn)(app_base+RECORD_GET_OFFSET);
  param_get_fn live_get=(param_get_fn)(app_base+LIVE_GET_OFFSET);
  u8 previous_mr[MR_BYTES];u32 previous_record=0xffffffff,previous_live=0xffffffff,previous_saved=0xffffffff;
  for(u32 i=0;i<MR_BYTES;i++)previous_mr[i]=0xff;
  int rc=0;
  if(log_event(0,1))report.error=11;
  if(!report.error){
   u8 saved=0;u8 mr[MR_BYTES];
   rc=((read_fn)(report.reserved[0]+backup_offsets[0]))(SELECTION_KEY,&saved,SELECTION_BYTES);
   int mr_rc=((read_fn)(report.reserved[0]+backup_offsets[0]))(MR_KEY,mr,MR_BYTES);
   u32 record=app_base?record_get(MAIN_PARAMETER):0;
   u32 live=app_base?live_get(MAIN_PARAMETER):0;
   if(rc!=SELECTION_BYTES||mr_rc!=MR_BYTES){
    report.reserved[2]=(u32)(rc!=SELECTION_BYTES?rc:mr_rc);report.reserved[1]=2;
    if(log_event((u32)(signed char)(rc!=SELECTION_BYTES?rc:mr_rc),4))report.error=11;
   }else if(log_state(record,live,saved,mr,3)){
    report.error=11;(void)log_event((u32)-11,4);
   }else{
    /* READY is probe telemetry, not a gate for the generic log service. */
    report.reserved[1]=1;previous_saved=saved;previous_record=record;previous_live=live;
    for(u32 i=0;i<MR_BYTES;i++)previous_mr[i]=mr[i];
   }
  }
  /* Record until explicitly stopped or until the camera powers off. A time
   * limit would silently turn a real reproduction into an incomplete log. */
  while(report.reserved[1]==1&&!report.error&&!__atomic_load_n(&stop_requested,__ATOMIC_ACQUIRE)){
   u8 saved=0;u8 mr[MR_BYTES];
   rc=((read_fn)(report.reserved[0]+backup_offsets[0]))(SELECTION_KEY,&saved,SELECTION_BYTES);
   int mr_rc=((read_fn)(report.reserved[0]+backup_offsets[0]))(MR_KEY,mr,MR_BYTES);
   u32 record=app_base?record_get(MAIN_PARAMETER):0;
   u32 live=app_base?live_get(MAIN_PARAMETER):0;
   if(rc!=SELECTION_BYTES||mr_rc!=MR_BYTES){report.reserved[2]=(u32)(rc!=SELECTION_BYTES?rc:mr_rc);report.reserved[1]=2;if(log_event((u32)(signed char)(rc!=SELECTION_BYTES?rc:mr_rc),4))report.error=11;break;}
   if(record!=previous_record||live!=previous_live||saved!=previous_saved||!t_equal(mr,previous_mr,MR_BYTES)){
    if(log_state(record,live,saved,mr,0)){report.error=11;break;}
    previous_record=record;previous_live=live;previous_saved=saved;
    for(u32 i=0;i<MR_BYTES;i++)previous_mr[i]=mr[i];
   }
   u64 delay[2]={0,100000000};t_sc(101,(long)delay,0,0,0,0,0);
  }
  /* A degraded optional probe never kills the logging service. Keep the
     worker alive for other/future probes until the user explicitly stops. */
  while(report.reserved[1]==2&&!report.error&&!__atomic_load_n(&stop_requested,__ATOMIC_ACQUIRE)){
   u64 delay[2]={0,100000000};t_sc(101,(long)delay,0,0,0,0,0);
  }
  if(log_event(0,2))report.error=11;
  if(!report.reserved[1])report.reserved[1]=2;
 }
 __atomic_store_n(&report.worker_done,1,__ATOMIC_RELEASE);
 return (void*)0x46323354ULL;
}
long fl23_a1_thread_poll(void);
long fl23_a1_thread_start(const u8 *config,u64 bytes){
 if(!config||bytes!=40||!t_equal(config,(const u8*)"A67P",4)||t_u32(config+4)!=1||
    t_u32(config+24)!=46||t_u32(config+28)||t_u32(config+32)||t_u32(config+36)!=131072)return -22;
 u32 expected=0;
 if(!__atomic_compare_exchange_n(&attempt,&expected,1,0,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE))return -114;
 report.version=1;report.state=1;report.pid=t_sc(172,0,0,0,0,0,0);
 report.creator_tid=t_sc(178,0,0,0,0,0,0);report.creator_tls=t_tls();report.creator_sp=t_sp();
 for(u32 i=0;i<16;i++)report.nonce[i]=config[8+i];
 report.library=t_library("/lib/libpthread-2.25.so",23,"/lib/libpthread.so.0",20,thread_offsets,thread_guards,3);
 report.reserved[0]=t_library("/usr/lib/libbackup.so",21,"/usr/lib/libbackup.so",21,backup_offsets,backup_guards,1);
 if(!report.library||!report.reserved[0]){report.error=3;report.state=5;return -3;}
 typedef int (*create_fn)(u64*,const void*,void*(*)(void*),void*);
 report.create_called=1;
 int rc=((create_fn)(report.library+thread_offsets[0]))(&report.handle,0,t_worker,&report);
 report.create_rc=(u32)rc;
 if(rc){report.error=4;report.state=5;return -4;}
 report.state=2;
 if(report.error)return -(long)report.error;
 return 0;
}
long fl23_a1_thread_poll(void){
 if(!attempt)return -22;
 __atomic_store_n(&stop_requested,1,__ATOMIC_RELEASE);
 u32 expected=0;
 if(!__atomic_compare_exchange_n(&polling,&expected,1,0,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE))return -16;
 long result=0;
 if(report.state==2){
  /* thread_offsets[2] is the guarded blocking pthread_join in this module. */
  typedef int (*join_fn)(u64,void**);void *value=0;
  int rc=((join_fn)(report.library+thread_offsets[2]))(report.handle,&value);report.join_rc=(u32)rc;
  if(rc){report.error=6;result=-6;}
  else{
   report.joined=1;
   if(!__atomic_load_n(&report.worker_done,__ATOMIC_ACQUIRE)||report.handle!=report.self||value!=(void*)0x46323354ULL)report.error=7;
   report.state=report.error?5:4;result=report.error?-(long)report.error:0;
  }
 }else if(report.error)result=-(long)report.error;
 __atomic_store_n(&polling,0,__ATOMIC_RELEASE);return result;
}
const struct a1_thread_report *fl23_a1_thread_report(void){return attempt?&report:(const void*)0;}
const void *fl23_a1_thread_backup(void){return (const void*)0;}
