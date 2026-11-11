/* Passive startup census. This is NOT a full-system boot-ready authorization.
 * Version2 permits starting the original checked preflight after process
 * stability; it never bypasses preflight, stopped-reader or byte guards.
 * Uses only /proc/self/task, read/close/clock/nanosleep. Observation mode keeps
 * the full control delay; gate mode times out without starting preflight.
 * Original publication guards remain mandatory. No signal, ptrace, driver,
 * native App call or feature activation is performed by this component.
 * Context [2048,4032) is private diagnostic space beyond the unchanged
 * resident/carrier/receipt [0,1632); no persistent storage is touched. */
#define PO_MAGIC 0x3153424f31414c46ULL
#define PO_RECORDS 60u
struct po_sample {u64 hash,cpu;u32 count,running,blocked,critical;};
static u64 po_hash(u64 n){n^=n>>30;n*=0xbf58476d1ce4e5b9ULL;n^=n>>27;n*=0x94d049bb133111ebULL;return n^(n>>31);}
static int po_number(const u8 **p,const u8 *end,u64 *out){
 u64 n=0;u32 digits=0;while(*p<end&&**p>='0'&&**p<='9'){
  u32 d=*(*p)++-'0';if(n>(~0ULL-d)/10)return 0;n=n*10+d;digits++;
 }*out=n;return digits!=0;
}
static u32 po_decimal(char *p,u64 n){char b[20];u32 k=0;do{b[k++]='0'+n%10;n/=10;}while(n);u32 size=k;while(k)*p++=b[--k];return size;}
static int po_equal(const u8 *p,u32 n,const char *s){u32 i=0;while(s[i]){if(i>=n||p[i]!=(u8)s[i])return 0;i++;}return i==n;}
static long po_clock(u64 *out){
 struct {long sec,nsec;} t={0,0};long rc=a1_sc(113,1,(long)&t,0,0,0,0);
 if(rc)return rc;if(t.sec<0||t.nsec<0||t.nsec>=1000000000L||(u64)t.sec>(~0ULL-(u64)t.nsec)/1000000000ULL)return -74;
 *out=(u64)t.sec*1000000000ULL+t.nsec;return 0;
}
static long po_stat(u64 tid,struct po_sample *s){
 char path[80]="/proc/self/task/";u32 len=16;len+=po_decimal(path+len,tid);
 const char suffix[]="/stat";for(u32 i=0;i<sizeof(suffix);i++)path[len+i]=suffix[i];
 long fd=a1_sc(56,-100,(long)path,0x80000,0,0,0);if(fd==-2){s->critical|=0x8000;return 0;}if(fd<0)return fd;
 u8 buf[768];long n=a1_sc(63,fd,(long)buf,sizeof(buf),0,0,0),closed=a1_sc(57,fd,0,0,0,0,0);
 if(closed)return closed;if(n<=0)return n<0?n:-74;if(n>=sizeof(buf))return -7;
 const u8 *p=buf,*end=buf+n;u64 id=0;
 if(!po_number(&p,end,&id)||id!=tid||end-p<6||p[0]!=' '||p[1]!='(')return -74;
 const u8 *name=p+2,*right=0;for(const u8 *q=name;q<end;q++)if(*q==')')right=q;
 if(!right||end-right<5||right[1]!=' '||right[3]!=' ')return -74;
 u8 state=right[2];if(state!='R'&&state!='S'&&state!='D')return -16;
 u64 utime=0,stime=0,start=0;p=right+4;
 for(u32 field=4;field<=22;field++){
  while(p<end&&*p==' ')p++;u32 negative=p<end&&*p=='-';if(negative)p++;
  u64 v=0;if(!po_number(&p,end,&v)||p<end&&*p!=' '&&*p!='\n')return -74;
  if((field==14||field==15||field==22)&&negative)return -74;
  if(field==14)utime=v;if(field==15)stime=v;if(field==22)start=v;
 }
 if(!start)return -74;s->count++;s->running+=state=='R';s->blocked+=state=='D';s->hash^=po_hash(tid)^po_hash(start+tid);
 const char *names[]={"BackupProxy","InfraOnDmdLdr","OPTION_INSTALLE","WlanResource_Ma"};
 for(u32 i=0;i<4;i++)if(po_equal(name,(u32)(right-name),names[i])){
  s->critical|=1u<<i;if(state!='S')s->critical|=16u<<i;
  if(utime>~0ULL-stime||s->cpu>~0ULL-utime-stime)return -74;s->cpu+=utime+stime;
 }
 return 0;
}
static long po_census(u64 own,struct po_sample *s){
 /* Reuse fl23_thread_census.c's verified ARM64 UAPI, not x86 O_DIRECTORY.
  * 040000 (directory) | 02000000 (close-on-exec) = 0x84000. */
 long fd=a1_sc(56,-100,(long)"/proc/self/task",0x84000,0,0,0);if(fd<0)return fd;
 u8 buf[8192];u64 ids[512];u32 count=0;long rc=-7;
 for(u32 chunk=0;chunk<64;chunk++){
  long n=a1_sc(61,fd,(long)buf,sizeof(buf),0,0,0);if(n<0){rc=n;break;}if(!n){rc=0;break;}if(n>sizeof(buf)){rc=-74;break;}
  u32 at=0;while(at<(u32)n){
   if((u32)n-at<20){rc=-74;goto done;}u32 length=buf[at+16]|(u32)buf[at+17]<<8;
   if(length<20||length>(u32)n-at||length&7){rc=-74;goto done;}
   const u8 *p=buf+at+19,*end=buf+at+length,*zero=p;while(zero<end&&*zero)zero++;
   if(zero==end){rc=-74;goto done;}
   if(!(zero-p==1&&p[0]=='.')&&!(zero-p==2&&p[0]=='.'&&p[1]=='.')){
    u64 id=0;if(!po_number(&p,zero,&id)||p!=zero||!id||id>=0x80000000ULL){rc=-74;goto done;}
    for(u32 i=0;i<count;i++)if(ids[i]==id){rc=-74;goto done;}
    if(count==512){rc=-7;goto done;}ids[count++]=id;
   }at+=length;
  }
 }
done:
 {long closed=a1_sc(57,fd,0,0,0,0,0);if(closed)return closed;}
 if(rc)return rc;if(!count)return -74;u32 own_seen=0;
 for(u32 i=0;i<count;i++){
  if(ids[i]==own){own_seen++;continue;}rc=po_stat(ids[i],s);if(rc)return rc;
 }
 if(own_seen!=1||!s->count)return -74;s->hash^=po_hash(s->count);return 0;
}
static long s2_process_observe(u8 *page,u64 pid,u64 delay,u32 gate){
 if(!page||gate>1||delay<250000000ULL||delay>300000000000ULL||gate&&delay!=60000000000ULL||a1_sc(172,0,0,0,0,0,0)!=(long)pid)return -22;
 long tid=a1_sc(178,0,0,0,0,0,0);if(tid<=0||tid>=0x80000000L)return -22;
 u64 *h=(void*)(page+2048);for(u32 i=0;i<248;i++)h[i]=0;h[0]=PO_MAGIC;h[1]=gate?3:1;h[3]=1;
 u64 now=0;long rc=po_clock(&now);if(rc)goto done;h[4]=now;
 if(now>~0ULL-delay){rc=-22;goto done;}u64 deadline=now+delay,interval=(delay+PO_RECORDS-1)/PO_RECORDS;
 struct po_sample previous={0};u32 quiet=0;
 for(u32 i=0;i<PO_RECORDS;i++){
  rc=po_clock(&now);if(rc)goto done;if(now>=deadline)break;
  struct po_sample s={0};rc=po_census((u64)tid,&s);if(rc)goto done;
  u64 *r=h+8+i*4;r[0]=now-h[4];r[1]=s.hash;r[2]=s.count|(u64)s.running<<16|(u64)s.blocked<<32|(u64)s.critical<<48;r[3]=s.cpu;h[2]=i+1;
  /* Kernel thread identity/start-tick cohort, all four actual backend names,
   * no D-state task and sleeping critical workers. CPU ticks may increase
   * during normal polling (observed every ~3s); they are not an idle latch.
   * This qualifies an ATTEMPT of the original preflight only. Later task
   * creation remains possible; the real publisher still snapshots afresh. */
  int stable=gate?
   i&&(s.critical&~16u)==15&&(previous.critical&~16u)==15&&!s.blocked&&!previous.blocked&&s.hash==previous.hash&&s.count==previous.count:
   i&&s.critical&1&&!(s.critical&0x80f0)&&!s.blocked&&s.hash==previous.hash&&s.cpu==previous.cpu&&s.critical==previous.critical;
  if(stable)quiet++;
  else quiet=0;
  if(quiet>=(gate?10u:5u)&&(!gate||s.critical==15)&&!h[6])h[6]=now;h[7]=quiet;previous=s;
  if(gate&&quiet>=10&&s.critical==15){rc=po_clock(&now);if(!rc&&now>=deadline)rc=-110;goto done;}
  rc=po_clock(&now);if(rc)goto done;u64 next=h[4]+(i+1)*interval;if(next>deadline)next=deadline;
  if(now<next&&!s2_sleep((long)(next-now))){rc=-4;goto done;}
 }
 /* Even if slow proc reads reduce the sample count, never shorten the
  * original control delay. Time only controls this observer variant. */
 rc=po_clock(&now);if(rc)goto done;if(now<deadline&&!s2_sleep((long)(deadline-now))){rc=-4;goto done;}
 rc=po_clock(&now);if(!rc&&now<deadline)rc=-4;
 if(!rc&&gate)rc=-110; /* A deadline never authorizes an attempt. */
done:
 h[5]=now;h[3]=(u64)rc;return rc;
}
