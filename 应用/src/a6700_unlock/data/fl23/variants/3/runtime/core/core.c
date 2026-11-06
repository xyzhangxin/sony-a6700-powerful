/* Fixed FL23 runtime capability probe. No firmware/parameter/WBI write API.
 * The verified arena constructor writes only one owned anonymous page.
 * Existing app mappings and files are compared through checked pipe copies.
 * Service result/config files are restored by the host ScratchTransaction.
 */
typedef unsigned char u8;typedef unsigned int u32;typedef unsigned long long u64;
static long sc(long n,long a,long b,long c,long d,long e,long f) {
register long x0 __asm__("x0")=a;register long x1 __asm__("x1")=b;
register long x2 __asm__("x2")=c;register long x3 __asm__("x3")=d;
register long x4 __asm__("x4")=e;register long x5 __asm__("x5")=f;
register long x8 __asm__("x8")=n;
__asm__ volatile("svc #0":"+r"(x0):"r"(x1),"r"(x2),"r"(x3),"r"(x4),"r"(x5),"r"(x8):"memory","cc");return x0;}
void *memset(void *p,int value,unsigned long n) {
    volatile unsigned char *bytes=p;for(unsigned long i=0;i<n;i++)bytes[i]=(unsigned char)value;return p;
}
#include "sha256.h"

#define DATA_SIZE 936448
static unsigned char request[40],result[44+DATA_SIZE],maps[524288],file_page[4096];
static const char exchange[]="/tmp/jiritsu/tmmc.jit";
static const u64 pages[4]={0,0x378b000,0x378c000,0x378d000};
static u64 words[32];
static int io(long nr,long fd,u8 *data,u64 size,u64 offset) {
    u64 done=0;
    for(u32 attempt=0;done<size&&attempt<1024;attempt++) {
        long n=sc(nr,fd,(long)(data+done),size-done,offset+done,0,0);
        if(n==-4)continue;
        if(n<=0||(u64)n>size-done)return 0;
        done+=n;
    }
    return done==size;
}
static u32 get32(const u8 *p) {u32 n=0;for(u32 i=0;i<4;i++)n|=(u32)p[i]<<(i*8);return n;}
static void put32(u8 *p,u32 n) {for(u32 i=0;i<4;i++)p[i]=(u8)(n>>(i*8));}
static int same(const u8 *a,const u8 *b,u64 n) {for(u64 i=0;i<n;i++)if(a[i]!=b[i])return 0;return 1;}
static u64 hex(const u8 **p,const u8 *end) {
    u64 value=0;u32 count=0;
    while(*p<end) {
        u8 c=**p;u32 d=c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:16;
        if(d==16)break;
        if(++count>16)return 0;
        value=(value<<4)|d;(*p)++;
    }
    return count?value:0;
}
static int locate(u64 length,u64 *bias,u64 *hint) {
    const u8 *at=maps,*end=maps+length;u32 found=0;
    while(at<end) {
        const u8 *line=at;while(at<end&&*at!='\n')at++;
        if(at==end)return 0;
        const u8 *p=line;u64 start=hex(&p,at);if(p==at||*p++!='-')return 0;
        u64 stop=hex(&p,at);if(!start||stop<=start||p+6>=at||*p++!=' ')return 0;
        int rx=same(p,(const u8*)"r-xp ",5);p+=5;
        u64 offset=hex(&p,at);if(p==at||*p!=' ')return 0;
        const char suffix[]="/usr/lib/appFw.so";
        if(rx&&!offset&&at-line>=sizeof(suffix)-1&&same(at-(sizeof(suffix)-1),(const u8*)suffix,sizeof(suffix)-1)) {
            *bias=start;found++;
            if(stop-start!=0x4489000)return 0;
        }
        at++;
    }
    if(found!=1||(*bias&4095))return 0;
    u64 candidate=*bias+0x20000000;
    at=maps;
    while(at<end) {
        const u8 *line=at;while(at<end&&*at!='\n')at++;
        const u8 *p=line;u64 start=hex(&p,at);p++;u64 stop=hex(&p,at);
        if(candidate<stop&&candidate+8192>start)candidate=(stop+4095)&~4095ull;
        at++;
    }
    if(candidate>=0x8000000000ull||candidate-*bias>=0x100000000ull)return 0;
    *hint=candidate;return 1;
}
static int checked_copy(u64 address,u8 *dest,u64 n) {
    int fd[2];if(n>4096||sc(59,(long)fd,0x80000,0,0,0,0)<0)return 0;
    long written=sc(64,fd[1],address,n,0,0,0);
    int ok=written==(long)n&&io(63,fd[0],dest,n,0);
    long a=sc(57,fd[1],0,0,0,0,0),b=sc(57,fd[0],0,0,0,0,0);
    return ok&&!a&&!b;
}
/* Whole owned image preparation and native lifecycle, without publication.
 * Only the new mmap, its native allocations and the fixed reply are written.
 * No original application entry, registry, parameter, CP, SAVE or WBI write.
 */
#include "main-preparation-profile.h"
#define PREP_KIND 38u
#define PREP_SIZE 2048u
#define PREP_MAGIC 0x3150455250333246ULL
static void finish(u32 status);
static char task_root[64];
/* Read-only task census. IDs come solely from /proc/self/task getdents64.
 * No signal, ptrace, clone, or application-memory write syscall exists here.
 */
#define MAX_TASKS 512u
#define ROW_BYTES 896u
#define STATUS_AT 256u
#define MAPS_AT (STATUS_AT+4096u)
#define ROWS_AT (MAPS_AT+131072u)
#define SECOND_AT (ROWS_AT+MAX_TASKS*ROW_BYTES)
static u8 dents[4096];static u32 first[MAX_TASKS],second[MAX_TASKS];
static void finish(u32 status);
static void census_u64(u8 *p,u64 value){for(u32 i=0;i<8;i++)p[i]=(u8)(value>>(8*i));}
static long read_text(const char *path,u8 *dest,u32 capacity){
    long fd=sc(56,-100,(long)path,0x80000,0,0,0);
    if(fd<0)return fd;
    u32 length=0;long answer=-75;
    for(u32 attempt=0;attempt<128;attempt++){
        if(length==capacity){u8 tail;long n=sc(63,fd,(long)&tail,1,0,0,0);answer=n==0?(long)length:-75;break;}
        long n=sc(63,fd,(long)(dest+length),capacity-length,0,0,0);
        if(n==-4)continue;
        if(n<0){answer=n;break;}if(!n){answer=length;break;}
        if((u64)n>capacity-length){answer=-75;break;}length+=(u32)n;
    }
    long closed=sc(57,fd,0,0,0,0,0);return closed?closed:answer;
}
static int enum_error(int code,u64 phase,u64 bytes,u64 at,u64 size){
    words[11]=phase;words[12]=bytes;words[13]=at;words[14]=size;
    for(u32 i=0;i<sizeof(dents);i++)result[44+STATUS_AT+i]=dents[i];
    return code;
}
static int enumerate(u32 *ids){
    /* ARM64 UAPI O_DIRECTORY=040000, O_CLOEXEC=02000000. */
    long fd=sc(56,-100,(long)task_root,0x84000,0,0,0);
    if(fd<0)return enum_error((int)fd,1,0,0,0);
    u32 count=0;int result=-75;
    for(u32 batch=0;batch<64;batch++){
        long n=sc(61,fd,(long)dents,sizeof(dents),0,0,0);
        if(n==-4)continue;
        if(n<0){result=enum_error((int)n,2,0,0,0);break;}
        if(!n){result=(int)count;break;}
        if((u64)n>sizeof(dents)){result=enum_error(-75,3,n,0,0);break;}
        u32 at=0;
        while(at<(u32)n){
            if((u32)n-at<20){result=enum_error(-22,4,n,at,0);goto done;}
            u32 size=dents[at+16]|((u32)dents[at+17]<<8);
            if(size<20||size%8||size>(u32)n-at){result=enum_error(-22,5,n,at,size);goto done;}
            u8 *name=dents+at+19;u32 available=size-19,tid=0,used=0;
            if(name[0]=='.'&&available>=2&&(name[1]==0||(available>=3&&name[1]=='.'&&name[2]==0))){at+=size;continue;}
            for(;used<available&&name[used];used++){
                u32 digit=name[used]-'0';if(digit>9||used>=10||tid>(2147483647u-digit)/10){result=enum_error(-22,6,n,at,size);goto done;}
                tid=tid*10+digit;
            }
            if(!used||used==available||!tid||count==MAX_TASKS){result=enum_error(-75,7,n,at,size);goto done;}
            ids[count++]=tid;at+=size;
        }
    }
done:
    if(sc(57,fd,0,0,0,0,0))return -5;
    if(result<0)return result;
    for(u32 i=1;i<count;i++){u32 value=ids[i],j=i;while(j&&ids[j-1]>value){ids[j]=ids[j-1];j--;}ids[j]=value;}
    for(u32 i=1;i<count;i++)if(ids[i-1]==ids[i])return -22;
    return result;
}
static void task_path(char *dest,u32 tid,const char *suffix){
    const char root[]="/proc/self/task/";u32 at=0;for(;at<sizeof(root)-1;at++)dest[at]=root[at];
    char reverse[10];u32 n=0;do{reverse[n++]='0'+tid%10;tid/=10;}while(tid);
    while(n)dest[at++]=reverse[--n];for(u32 i=0;suffix[i];i++)dest[at++]=suffix[i];dest[at]=0;
}
/* Internal same-cohort preflight/word planner. No constructor or USB entry.
 * This unit never pokes the parent. Its ready flag means a byte plan exists,
 * not that prepared containers/bindings may be published on real hardware.
 */
typedef unsigned char u8;
#include "fl23_main_state.c"
/* preparation profile already included by the bound prefix */
#include "main-publication-profile.h"
struct main_pub_word {u64 address,before,after;};
struct main_bind_result {u64 version,status,calls,words,enum_map;long error;};
static u64 main_bind_value(struct main_bind_arg a,u64 bias,u64 owned,const u64 *roots,u64 map){
 if(a.kind==1)return bias+a.value;if(a.kind==2)return owned+a.value;
 if(a.kind==3)return roots[a.value];if(a.kind==4)return map;return a.value;
}
/* roots/map are freshly read parent values when used by the stopped planner.
 * This checks exact shared-owned words without executing any application
 * routine in the child. It cannot itself authorize writes or resume.
 */
int fl23_main_publication_binding_check(u64 bias,u64 owned,const u64 *roots,u64 map){
 if(!main_state_addresses(bias,owned)||!roots||((u64)roots&7)||!map||(map&7))return 1;
 for(u32 i=0;i<sizeof(main_bind_guards)/sizeof(main_bind_guards[0]);i++){
  const struct main_bind_store *g=main_bind_guards+i;
  if(*(const u64*)(owned+g->offset)!=main_bind_value(g->value,bias,owned,roots,map))return 2;
 }return 0;
}
/* Called in the original parent BEFORE cloning/stopping threads. All native
 * containers already exist. This only writes module bindings in the owned
 * region; it does not publish hooks or modify an original cache/registry.
 * Failure leaves the owner unpublished and requiring ordinary cleanup.
 */
int fl23_main_publication_bind(u64 bias,u64 owned,struct main_bind_result *r){
 if(!main_state_result_valid(bias,owned,(struct main_state_result*)r))return 1;
 for(u32 i=0;i<sizeof(*r)/8;i++)((u64*)r)[i]=0;r->version=1;
 if(!main_guard_check(bias)){r->status=2;return 2;}
 const u64 *context=(void*)(owned+MAIN_CONTEXT);
 if(context[0]!=0x314e49414d333246ULL||context[1]!=0x0000000100000002ULL||
    *(const u64*)(owned+MAIN_INPUT_MAPS+24)!=18||*(const u64*)(owned+MAIN_INPUT_MAPS+64+24)!=2||
    *(const u64*)(owned+MAIN_OBSERVER_MAP+24)!=18||*(const u64*)(owned+MAIN_FLAG_CACHE+24)!=22){r->status=2;return 2;}
 u64 roots[4]={*(const u64*)(bias+0x4e53d30),*(const u64*)(bias+0x4e958f8),bias+0x8d22a70,bias+MAIN_PUB_FLAG_ROOT};
 for(u32 i=0;i<4;i++)if(!roots[i]||(roots[i]&7)||roots[i]>=0x8000000000ULL-56){r->status=2;return 2;}
 u32 key=0x530;
 u64 node=((u64(*)(u64,const u32*))(bias+0x2ca7cac))(roots[1],&key);
 if(!node||(node&7)||node>=0x8000000000ULL-72||*(const u32*)(node+8)!=key||
    *(const u64*)(node+24)==0||*(const u64*)(node+40)!=17){r->status=2;return 2;}
 u64 map=node+16;r->enum_map=map;
 /* kind38's preparation queries bound these containers to original
  * lookups. No original entry reaches the owned maps yet. Disable those
  * private bindings before assigning the permanent delegate trampolines.
  */
 ((void(*)(void))(owned+MAIN_FN_FL23_INPUTS_DISABLE))();
 ((void(*)(u64))(owned+MAIN_FN_FL23_OBSERVER_DISABLE))(owned+MAIN_OBSERVER_MAP);
 ((void(*)(void))(owned+MAIN_FN_FL23_FLAGS_DISABLE))();
 /* Fill argument words only after all fixed original/owned guards pass. */
 for(u32 i=0;i<sizeof(main_bind_stores)/sizeof(main_bind_stores[0]);i++){
  const struct main_bind_store *s=main_bind_stores+i;
  *(u64*)(owned+s->offset)=main_bind_value(s->value,bias,owned,roots,map);r->words++;
 }
 for(u32 i=0;i<sizeof(main_bind_calls)/sizeof(main_bind_calls[0]);i++){
  const struct main_bind_call *c=main_bind_calls+i;u64 args[6];
  for(u32 j=0;j<6;j++)args[j]=main_bind_value(c->args[j],bias,owned,roots,map);
  long rc=((long(*)(u64,u64,u64,u64,u64,u64))(owned+c->entry))(args[0],args[1],args[2],args[3],args[4],args[5]);
  if(rc){r->status=3;r->error=rc;return 3;}r->calls++;
 }
 if(fl23_main_publication_binding_check(bias,owned,roots,map)){r->status=4;return 4;}
 return 0;
}
struct main_pub_result {
 struct main_state_result state;
 u64 version,status,ready,edits,words,peeks,flag_cell,enum_cell,flag_descriptor,flags_seeded;
 long error;u64 safe_to_publish,enum_overlay[2];
};
static struct main_pub_result *main_pub_report;
static int main_pub_span(u64 at,u64 n){return at>=4096&&!(at&7)&&n&&at<0x8000000000ULL-n;}
static int main_pub_overlap(u64 a,u64 n,u64 b,u64 m){return a<b+m&&b<a+n;}
static int main_pub_hex(const u8 *p,u32 n,u32 *at,u64 *value){
 *value=0;u32 first=*at;
 while(*at<n){u32 c=p[*at],d=c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:16;
  if(d==16)break;if(*value>0xfffffffffffffffULL)return 0;*value=*value*16+d;(*at)++;
 }return *at>first;
}
static int main_pub_shared_owner(const struct main_state_capture_args *a){
 const u64 *h=(const void*)a->cohort;u64 cursor=a->owned,limit=cursor+0x200000;
 if(!h[5]||h[5]>131072)return 0;
 const u8 *maps=(const void*)(a->cohort+256);u32 n=(u32)h[5],at=0;int enum_readonly=0;
 while(at<n){u64 lo,hi;
  if(!main_pub_hex(maps,n,&at,&lo)||at>=n||maps[at++]!='-'||!main_pub_hex(maps,n,&at,&hi)||
     hi<=lo||at+5>n||maps[at++]!=' ')return 0;
  const u8 *perm=maps+at;at+=4;
  if(main_pub_overlap(lo,hi-lo,a->owned,0x200000)){
   if(perm[3]!='s'||lo>cursor)return 0;
   if(lo<=a->owned+MAIN_MAIN_ENUM&&a->owned+MAIN_MAIN_ENUM+4096<=hi)
    enum_readonly=perm[0]=='r'&&perm[1]=='-'&&perm[2]=='-';
   if(hi>cursor)cursor=hi<limit?hi:limit;
  }
  while(at<n&&maps[at]!='\n')at++;if(at<n)at++;
 }return cursor==limit&&enum_readonly;
}
static long main_pub_read(u64 at,u64 *out){
 if(!main_pub_span(at,8))return -22;
 if(!(main_pub_report->peeks&63)&&main_capture_expired())return -110;
 main_pub_report->peeks++;return main_capture_sc(117,2,main_capture_tid,at,(long)out);
}
static long main_pub_bytes(u64 at,u8 *out,u32 n){
 for(u32 i=0;i<n;){u64 word=0;long rc=main_pub_read((at+i)&~7ULL,&word);if(rc)return rc;
  for(u32 j=(at+i)&7;j<8&&i<n;j++,i++)out[i]=(u8)(word>>(8*j));
 }return 0;
}
static u64 main_pub_bucket(u32 key,u64 n,int is_enum){return (is_enum?(u64)(long)(int)key:(u64)key)%n;}
static int main_pub_node(u64 at,u64 root,u64 buckets,u64 count){
 return main_pub_span(at,24)&&!main_pub_overlap(at,24,root,56)&&
  !main_pub_overlap(at,24,buckets,count*8)&&!main_pub_overlap(at,24,main_capture_owner,0x200000);
}
static long main_pub_find(u64 root,u32 key,int is_enum,u64 *cell){
 u64 h[4],pred=0,node=0,seen[128];*cell=0;
 long rc=main_pub_bytes(root,(u8*)h,32);if(rc)return rc;
 if(!h[1]||h[1]>32768||!h[3]||h[3]>16384||!h[2]||!main_pub_span(h[0],h[1]*8))return -116;
 u64 bucket=main_pub_bucket(key,h[1],is_enum);
 if((rc=main_pub_read(h[0]+bucket*8,&pred)))return rc;
 if(!pred)return -2;
 if(pred!=root+16&&!main_pub_node(pred,root,h[0],h[1]))return -116;
 if((rc=main_pub_read(pred,&node)))return rc;if(!node)return -116;
 if(pred!=root+16){u64 prev_key=0;if((rc=main_pub_read(pred+8,&prev_key)))return rc;
  if(main_pub_bucket((u32)prev_key,h[1],is_enum)==bucket)return -116;
 }
 for(u32 i=0;i<128&&i<h[3];i++){
  if(!main_pub_node(node,root,h[0],h[1])||node==pred)return -116;
  for(u32 j=0;j<i;j++)if(main_pub_overlap(node,24,seen[j],24))return -116;seen[i]=node;
  u64 row[3];if((rc=main_pub_bytes(node,(u8*)row,24)))return rc;
  if(row[0]==node||main_pub_bucket((u32)row[1],h[1],is_enum)!=bucket)return -2;
  if((u32)row[1]==key){*cell=node+16;return 0;}
  if(!row[0])return -2;node=row[0];
 }return -116;
}
static long main_pub_old_descriptor(u32 key,u64 group,u32 count,const u64 *options,u64 *cell,u64 *descriptor){
 long rc=main_pub_find(main_capture_bias+MAIN_PUB_FLAG_ROOT,key,0,cell);if(rc)return rc;
 if((rc=main_pub_read(*cell,descriptor)))return rc;
 if(!main_pub_span(*descriptor,16+count*8))return -116;
 u64 h[2];if((rc=main_pub_bytes(*descriptor,(u8*)h,16)))return rc;
 if(h[0]!=main_capture_bias+group||h[1]!=count)return -116;
 for(u32 i=0;i<count;i++){u64 p;if((rc=main_pub_read(*descriptor+16+i*8,&p)))return rc;
  if(p!=main_capture_bias+options[i])return -116;
 }return 0;
}
static long main_pub_seed_flags(const struct main_state_capture_args *a){
 u64 flags=a->owned+MAIN_FLAG_CACHE,context=a->owned+MAIN_CONTEXT;
 if(*(const u64*)context!=0x314e49414d333246ULL||*(const u32*)(context+8)!=2||*(const u32*)(context+12)!=1||
    *(const u64*)(flags+24)!=22)return -116;
 const u32 *counters=(const void*)(a->owned+MAIN_STATE_COUNTER);
 for(u32 group=0;group<11;group++){
  const struct main_pub_flag_group *g=main_pub_flag_groups+group;u64 cell,old;u8 ref=0,option[10]={0};
  long rc=main_pub_old_descriptor(g->key,g->group,g->count,g->options,&cell,&old);if(rc)return rc;
  if((rc=main_pub_bytes(a->bias+g->group,&ref,1)))return rc;
  for(u32 v=0;v<g->count;v++)if((rc=main_pub_bytes(a->bias+g->options[v],option+v,1)))return rc;
  for(u32 slot=0;slot<2;slot++){
   u32 id=group*2+slot,n=group<3?12:0;u64 d=flags+0x1000+id*128,p=context+28+id*24;
   const struct main_pub_flag_seed *s=main_pub_flag_seeds+id;
   if(((const u64*)(flags+0x100))[id*2]!=10563+id||((const u64*)(flags+0x100))[id*2+1]!=d||
      ((const u64*)d)[0]!=p||((const u64*)d)[1]!=n)return -116;
   for(u32 v=0;v<n;v++)if(((const u64*)d)[v+2]!=flags+0x3000+id*16+v)return -116;
   *(u8*)p=s->binding==0xffffffffU?ref:(u8)!!counters[s->binding];main_pub_report->flags_seeded++;
   for(u32 v=0;v<n;v++){
    *(u8*)(flags+0x3000+id*16+v)=s->domain==0xffffffffU?(v<10?option[v]:0):(u8)!!counters[s->domain+v];
    main_pub_report->flags_seeded++;
   }
  }
 }
 for(u32 slot=0;slot<2;slot++){
  *(u8*)(a->owned+MAIN_MAIN_FLAGS+slot)=(u8)!!counters[MAIN_PUB_MAIN_FL_BINDING];main_pub_report->flags_seeded++;
 }return 0;
}
#include "a1-core-guard.c"
static long main_pub_add(u64 address,u64 before,u64 after,u32 bytes){
 if(!bytes||bytes>8||address<4096||address>=0x8000000000ULL-bytes)return -22;
 if(a1_boot_protected(address,bytes))return -22;
 struct main_pub_word *words=(void*)(main_capture_owner+MAIN_JOURNAL);
 u64 *masks=(void*)(main_capture_owner+MAIN_JOURNAL+2048*24);
 for(u32 done=0;done<bytes;){
  u64 at=(address+done)&~7ULL;u32 slot=0,shift=(address+done)&7,n=8-shift;if(n>bytes-done)n=bytes-done;
  while(slot<main_pub_report->words&&words[slot].address!=at)slot++;
  if(slot==main_pub_report->words){
   if(slot>=2048)return -7;u64 value;long rc=main_pub_read(at,&value);if(rc)return rc;
   words[slot]=(struct main_pub_word){at,value,value};masks[slot]=0;main_pub_report->words++;
  }
  u64 part=n==8?~0ULL:((1ULL<<(n*8))-1),mask=part<<(shift*8);
  u64 old=((before>>(done*8))&part)<<(shift*8),new=((after>>(done*8))&part)<<(shift*8);
  if((words[slot].before&mask)!=old||(masks[slot]&mask&&((words[slot].after^new)&masks[slot]&mask)))return -116;
  words[slot].after=(words[slot].after&~mask)|new;masks[slot]|=mask;done+=n;
 }return 0;
}
int fl23_main_publication_plan(const struct main_state_capture_args *a,struct main_pub_result *r){
 if(!a||((u64)a&7)||(u64)a>=0x8000000000ULL-sizeof(*a)||!r||a->bytes!=MAIN_COHORT_SIZE||!a->cohort||(a->cohort&4095)||!main_pub_span(a->cohort,a->bytes)||
    !main_state_result_valid(a->bias,a->owned,&r->state)||
    (u64)r>=0x8000000000ULL-sizeof(*r)||main_pub_overlap((u64)r,sizeof(*r),a->owned,0x200000)||
    main_pub_overlap((u64)r,sizeof(*r),a->bias,0x15000000)||main_pub_overlap((u64)r,sizeof(*r),a->cohort,a->bytes)||
    main_pub_overlap((u64)r,sizeof(*r),(u64)a,sizeof(*a))||
    main_pub_overlap((u64)a,sizeof(*a),a->owned,0x200000)||main_pub_overlap((u64)a,sizeof(*a),a->bias,0x15000000)||
    main_pub_overlap((u64)a,sizeof(*a),a->cohort,a->bytes)||main_pub_overlap(a->cohort,a->bytes,a->owned,0x200000)||
    main_pub_overlap(a->cohort,a->bytes,a->bias,0x15000000))return 1;
 for(u32 i=0;i<sizeof(*r)/8;i++)((u64*)r)[i]=0;r->version=1;main_pub_report=r;
 if(!main_pub_shared_owner(a)){r->status=2;r->error=-116;return 2;}
 long rc=fl23_main_state_capture_stopped(a,&r->state);if(rc)goto failed;
 if((rc=a1_boot_page_guard(a)))goto failed;
 u64 roots[4]={0,0,a->bias+0x8d22a70,a->bias+MAIN_PUB_FLAG_ROOT},input_map=0;
 if((rc=main_pub_read(a->bias+0x4e53d30,roots))||
    (rc=main_pub_read(a->bias+0x4e958f8,roots+1)))goto failed;
 for(u32 i=0;i<4;i++)if(!main_pub_span(roots[i],56)){rc=-116;goto failed;}
 if((rc=main_pub_find(roots[1],0x530,0,&input_map)))goto failed;
 if(fl23_main_publication_binding_check(a->bias,a->owned,roots,input_map)){rc=-116;goto failed;}
 u64 cell=0,descriptor=0;
 rc=main_pub_old_descriptor(0x530,MAIN_PUB_MAIN_GROUP,17,main_pub_main_options,&cell,&descriptor);if(rc)goto failed;
 if(descriptor!=a->bias+MAIN_PUB_MAIN_DESCRIPTOR){rc=-116;goto failed;}r->flag_cell=cell;r->flag_descriptor=descriptor;
 rc=main_pub_find(a->bias+MAIN_PUB_ENUM_ROOT,0x530,1,&cell);if(rc)goto failed;r->enum_cell=cell;
 u64 record=0;if((rc=main_pub_read(cell,&record)))goto failed;
 if(record!=a->bias+MAIN_PUB_ENUM_RECORD){rc=-116;goto failed;}
 u64 body[5];if((rc=main_pub_bytes(record,(u8*)body,40)))goto failed;
 if(body[0]!=0x0000000100000530ULL||body[3]!=17||body[4]!=a->bias+MAIN_PUB_ENUM_TABLE){rc=-116;goto failed;}
 /* The original metadata's middle16B are runtime data. Preserve them in
    the new enum record instead of assuming the ELF's zero initializer.
    Only this controller's alias changes permissions; MAP_SHARED is required
    so the paused parent sees the staged data through its read-only alias. */
 {u64 at=a->owned+MAIN_MAIN_ENUM,*new_record=(void*)at;
  if(new_record[0]!=body[0]||new_record[3]!=19||new_record[4]!=at+40){rc=-116;goto failed;}
  if((rc=main_capture_sc(226,at,4096,3,0)))goto failed;
  new_record[1]=body[1];new_record[2]=body[2];__asm__ volatile("dmb ish":::"memory");
  rc=main_capture_sc(226,at,4096,1,0);if(rc)goto failed;
  r->enum_overlay[0]=body[1];r->enum_overlay[1]=body[2];
 }
 rc=main_pub_seed_flags(a);if(rc)goto failed;
 for(u32 i=0;i<sizeof(main_pub_code)/sizeof(main_pub_code[0]);i++){
  const struct main_pub_edit *e=main_pub_code+i;
  rc=main_pub_add(a->bias+e->offset,e->before,e->after,e->bytes);if(rc)goto failed;r->edits++;
 }
 for(u32 i=0;i<26;i++){
  u64 at;u32 old,new;if(fl23_main_state_code(a->bias,a->owned,i,&at,&old,&new)){rc=-22;goto failed;}
  if((rc=main_pub_add(at,old,new,4)))goto failed;r->edits++;
 }
 for(u32 i=0;i<5;i++){
  u64 at,old,new;if(fl23_main_state_pointer(a->bias,a->owned,i,&at,&old,&new)){rc=-22;goto failed;}
  if((rc=main_pub_add(at,old,new,8)))goto failed;r->edits++;
 }
 if((rc=main_pub_add(a->bias+MAIN_PUB_CUSTOM_CAPTION_POINTER,a->bias+MAIN_PUB_CUSTOM_CAPTION_ORIGINAL,a->owned+MAIN_PUB_CUSTOM_CAPTION_OWNED,8)))goto failed;r->edits++;
 if((rc=main_pub_add(a->bias+MAIN_PUB_ROW+0x40,17,19,4)))goto failed;r->edits++;
 if((rc=main_pub_add(a->bias+MAIN_PUB_ROW+0x48,a->bias+MAIN_PUB_LEAVES,a->owned+MAIN_MENU+0x100,8)))goto failed;r->edits++;
 if((rc=main_pub_add(r->flag_cell,a->bias+MAIN_PUB_MAIN_DESCRIPTOR,a->owned+MAIN_MAIN_DESCRIPTOR,8)))goto failed;r->edits++;
 if((rc=main_pub_add(r->enum_cell,a->bias+MAIN_PUB_ENUM_RECORD,a->owned+MAIN_MAIN_ENUM,8)))goto failed;r->edits++;
 for(u32 i=0,j=0;i<sizeof(main_cave)/sizeof(main_cave[0]);i++){
  u64 old=0,value=main_cave[i];if((rc=main_pub_read(a->bias+0x378b000ULL+i*8,&old)))goto failed;
  if(old!=a1_boot_expected(0x378b000ULL+i*8)){rc=-116;goto failed;}
  if(a1_boot_contains(0x378b000ULL+i*8,8)){if(value){rc=-22;goto failed;}continue;}
  if(j<sizeof(main_cave_relocs)/sizeof(main_cave_relocs[0])&&(main_cave_relocs[j]&0x7fffffffU)==i*8){
   value+=(main_cave_relocs[j]&0x80000000U)?a->bias:a->owned;j++;
  }
  if(value&&(rc=main_pub_add(a->bias+0x378b000ULL+i*8,0,value,8)))goto failed;
 }
 {struct main_pub_word *w=(void*)(a->owned+MAIN_JOURNAL);
  for(u32 i=1;i<r->words;i++){struct main_pub_word v=w[i];u32 j=i;while(j&&w[j-1].address>v.address){w[j]=w[j-1];j--;}w[j]=v;}
 }
 if(r->edits!=96||r->flags_seeded!=96||!main_capture_cohort(a)||
    ((const u64*)a->cohort)[15]!=main_capture_start||((const u64*)a->cohort)[2]!=main_capture_tid||
    main_capture_expired()){rc=-110;goto failed;}
 r->ready=1;return 0;
failed:r->status=2;r->error=rc;return 2;
}

typedef u64(*prep_fn)(u64,u64,u64,u64,u64,u64);
#define FN(name) ((prep_fn)(owned+MAIN_FN_##name))
#define CALL0(name) FN(name)(0,0,0,0,0,0)
#define CALL1(name,a) FN(name)((u64)(a),0,0,0,0,0)
#define CALL2(name,a,b) FN(name)((u64)(a),(u64)(b),0,0,0,0)
#define CALL3(name,a,b,c) FN(name)((u64)(a),(u64)(b),(u64)(c),0,0,0)
#define CALL4(name,a,b,c,d) FN(name)((u64)(a),(u64)(b),(u64)(c),(u64)(d),0,0)
static u64 prep_u64(const u8 *p){u64 v=0;for(u32 i=0;i<8;i++)v|=(u64)p[i]<<(i*8);return v;}
/* mmap's hint may be ignored. Choose a COMPLETE2MiB gap, with64KiB
 * alignment for shared mappings, then validate the actual kernel return.
 * No MAP_FIXED: an intervening allocation can only make mmap choose a new
 * address, never replace an existing application VMA.
 */
static int prep_gap(u32 length,u64 low,u64 high,u64 *hint){
 if(low>0x8000000000ULL-65535||high>0x8000000000ULL||high<MAIN_RESERVE)return 0;
 u64 candidate=(low+65535)&~65535ULL,previous=0;const u8 *at=maps,*end=maps+length;
 while(at<end){
  const u8 *line=at;while(at<end&&*at!='\n')at++;if(at==end)return 0;
  const u8 *p=line;u64 start=hex(&p,at);if(p==at||*p++!='-')return 0;
  u64 stop=hex(&p,at);if(!start||stop<=start||start<previous||stop>0x8000000000ULL)return 0;previous=stop;
  if(candidate<stop&&start<candidate+MAIN_RESERVE){
   if(stop>0x8000000000ULL-65535)return 0;candidate=(stop+65535)&~65535ULL;
  }
  at++;
 }
 if(candidate<low||candidate>high-MAIN_RESERVE)return 0;*hint=candidate;return 1;
}
static int prep_address(u64 bias,u64 owned){
 return bias&&owned&&!(owned&4095)&&owned<0x8000000000ULL-MAIN_RESERVE&&
  !(owned<bias+0x15000000ULL&&bias<owned+MAIN_RESERVE)&&
  (owned>=bias?owned-bias:bias-owned)<0x100000000ULL;
}
static int prep_find_app(u32 length,u64 *bias){
 const u8 *at=maps,*end=maps+length;u32 found=0;
 while(at<end){
  const u8 *line=at;while(at<end&&*at!='\n')at++;if(at==end)return 0;
  const u8 *p=line;u64 start=hex(&p,at);if(p==at||*p++!='-')return 0;
  u64 stop=hex(&p,at);if(!start||stop<=start||p+6>=at||*p++!=' ')return 0;
  int rx=same(p,(const u8*)"r-xp ",5);p+=5;u64 offset=hex(&p,at);if(p==at||*p!=' ')return 0;
  const char suffix[]="/usr/lib/appFw.so";
  if(rx&&!offset&&at-line>=sizeof(suffix)-1&&same(at-(sizeof(suffix)-1),(const u8*)suffix,sizeof(suffix)-1)){
   if(stop-start!=0x4489000)return 0;*bias=start;found++;
  }at++;
 }
 return found==1&&!(*bias&4095)&&*bias<0x8000000000ULL-0x15000000ULL;
}
static int prep_maps(u64 *bias,u64 *hint){
 long fd=sc(56,-100,(long)"/proc/self/maps",0x80000,0,0,0);if(fd<0)return 0;
 u32 n=0;int eof=0;
 for(u32 i=0;i<1024&&n<sizeof(maps);i++){
  long got=sc(63,fd,(long)(maps+n),sizeof(maps)-n,0,0,0);
  if(got==-4)continue;if(got<0)break;if(!got){eof=1;break;}
  if((u64)got>sizeof(maps)-n)break;n+=got;
 }
 long closed=sc(57,fd,0,0,0,0,0);if(!eof||closed||!prep_find_app(n,bias))return 0;
 u64 high=0x8000000000ULL,bottom=*bias>0xffff0000ULL?*bias-0xffff0000ULL:65536;
 if(*bias<high-0x100000000ULL)high=*bias+0x100000000ULL;
 return (prep_gap(n,*bias+0x20000000ULL,high,hint)||
         prep_gap(n,*bias+0x15000000ULL,high,hint)||prep_gap(n,bottom,*bias,hint))&&prep_address(*bias,*hint);
}
static int prep_roots(u64 bias,u64 *roots,u8 *headers){
 const u64 got[2]={0x4e53d30,0x4e958f8};u8 raw[8];
 for(u32 i=0;i<2;i++){
  if(!checked_copy(bias+got[i],raw,8))return 0;roots[i]=prep_u64(raw);
 }
 roots[2]=bias+0x8d22a70;roots[3]=bias+0x138bd850;
 for(u32 i=0;i<4;i++){
  if(!roots[i]||(roots[i]&7)||roots[i]>=0x8000000000ULL-56||!checked_copy(roots[i],headers+i*56,56))return 0;
  for(u32 j=0;j<i;j++)if(roots[i]==roots[j])return 0;
 }
 return 1;
}
static void prep_cache(u64 first,u64 bytes){
 u64 ctr;__asm__ volatile("mrs %0,ctr_el0":"=r"(ctr));
 u64 ds=4ULL<<((ctr>>16)&15),is=4ULL<<(ctr&15),end=first+bytes;
 for(u64 p=first&~(ds-1);p<end;p+=ds)__asm__ volatile("dc cvau,%0"::"r"(p):"memory");
 __asm__ volatile("dsb ish":::"memory");
 for(u64 p=first&~(is-1);p<end;p+=is)__asm__ volatile("ic ivau,%0"::"r"(p):"memory");
 __asm__ volatile("dsb ish;isb":::"memory");
}
static int prep_protect(u64 owned){
 if(sc(226,owned,MAIN_RESERVE,0,0,0,0))return 1;
 for(u32 i=0;i<sizeof(main_permissions)/sizeof(main_permissions[0]);i++)
  if(sc(226,owned+main_permissions[i].offset,4096,main_permissions[i].permissions,0,0,0))return 2;
 return sc(226,owned+MAIN_JOURNAL,MAIN_RESERVE-MAIN_JOURNAL,3,0,0,0)?3:0;
}
static u32 prep_cycle(u64 bias,u64 owned,const u64 *roots,u8 *data){
 u64 groups=owned+MAIN_INPUT_MAPS,enums=groups+64,temp=groups+128,source=groups+256,bases=groups+512;
 u64 observer=owned+MAIN_OBSERVER_MAP,flags=owned+MAIN_FLAG_CACHE,context=owned+MAIN_CONTEXT;
 u32 rc=0,status=0,mask=0,queries=0;u32 *scratch=(u32*)(owned+MAIN_JOURNAL);
 words[18]=CALL1(FL23_MAIN_INITIALIZE,context);
 if(words[18]||CALL1(FL23_MAIN_BIND,context))return 7;
 /* Fixed native constructor input ABI; no host snapshot callback pointers. */
 for(u32 i=0;i<18;i++){((u32*)source)[i*2]=10563+(i<2?i:i+4);((u32*)source)[i*2+1]=3393+i;}
 for(u32 s=0;s<2;s++){
  u32 *p=(u32*)(bases+s*104);p[0]=10563+s;
  for(u32 v=0;v<12;v++){p[2+v*2]=v;p[3+v*2]=(s?24818:24805)+v;}
 }
 words[19]=FN(FL23_INPUTS_CREATE)(bias,groups,enums,temp,source,bases);
 if(words[19])return 8;mask|=1;words[9]=mask;
 if(FN(FL23_INPUTS_BIND)(bias+0x2ca7184,bias+0x2ca7cac,roots[0],roots[1],groups,enums)){status=8;goto cleanup;}
 words[20]=CALL4(FL23_OBSERVER_CLONE,bias,observer,observer+0x1000,0x3000);
 if(words[20]){if(((u64*)observer)[3]){mask|=2;words[9]=mask;}status=9;goto cleanup;}mask|=2;words[9]=mask;
 if(CALL3(FL23_OBSERVER_BIND,bias+0x2cc1cc0,roots[2],observer)){status=9;goto cleanup;}
 /* Copy the current native FL descriptor's values into private storage.
  * Publication later reseeds rule counters while its readers are stopped. */
 for(u32 group=0;group<11;group++){
  *scratch=0xea7+group*16+5;
  u64 cell=((u64(*)(u64,const u32*))(bias+0x2face8c))(roots[3],scratch);
  if(!cell){status=10;goto cleanup;}u64 original=*(const u64*)cell;
  if(!original||((const u64*)original)[1]!=(group<3?10:0)){status=10;goto cleanup;}
  for(u32 s=0;s<2;s++){
   u32 id=group*2+s,count=group<3?12:0;u64 *descriptor=(u64*)(flags+0x1000+id*128);
   u64 flag=context+16+id*24+12,options=flags+0x3000+id*16;
   *(u8*)flag=*(const u8*)(*(const u64*)original);
   descriptor[0]=flag;descriptor[1]=count;
   for(u32 v=0;v<count;v++){
    *(u8*)(options+v)=v<10?*(const u8*)(((const u64*)original)[v+2]):0;descriptor[v+2]=options+v;
   }
   ((u64*)(flags+0x100))[id*2]=10563+id;((u64*)(flags+0x100))[id*2+1]=(u64)descriptor;
  }
 }
 words[21]=CALL4(FL23_FLAGS_CREATE,bias,flags,flags+0x100,flags+0x100+22*16);
 if(words[21]){if(((u64*)flags)[3]){mask|=4;words[9]=mask;}status=10;goto cleanup;}mask|=4;words[9]=mask;
 if(CALL4(FL23_FLAGS_BIND,bias+0x2face54,bias+0x2face8c,roots[3],flags)){status=10;goto cleanup;}
 put32(data+800,((u64*)groups)[3]);put32(data+804,((u64*)enums)[3]);
 put32(data+808,((u64*)observer)[3]);put32(data+812,((u64*)flags)[3]);
 for(u32 i=0;i<22;i++){
  u64 cell=FN(FL23_MAIN_CELL)(context,bias,i/2,17+i%2,0,0);
  const u32 defaults[8]={0,0,0,0,0,4,3,1};u32 expected=i<6?10+i%2:defaults[i/2-3];
  if(!cell||*(const u32*)cell!=expected){status=11;goto cleanup;}queries++;
  *scratch=10563+i;u64 p=CALL2(FL23_FLAG_CELL,roots[3],scratch);
  if(!p||*(const u64*)p!=flags+0x1000+i*128){status=11;goto cleanup;}queries++;
 }
 for(u32 i=0;i<18;i++){
  *scratch=10563+(i<2?i:i+4);u64 p=CALL2(FL23_INPUT_GROUP_LOOKUP,roots[0],scratch);
  if(!p||*(const u32*)(p+12)!=3393+i){status=11;goto cleanup;}queries++;
  u64 prev=CALL3(FL23_OBSERVER_LOOKUP,roots[2],0,scratch),node=prev?*(const u64*)prev:0;
  if(!node||*(const u32*)(node+8)!=*scratch){status=11;goto cleanup;}queries++;
 }
 for(u32 s=0;s<2;s++){
  *scratch=10563+s;u64 p=CALL2(FL23_INPUT_ENUM_LOOKUP,roots[1],scratch);
  if(!p||((u64*)(p+16))[3]!=12){status=11;goto cleanup;}queries++;
  for(u32 v=0;v<12;v++){
   *scratch=v;u64 n=((u64(*)(u64,const u32*))(bias+0x2ca7184))(p+16,scratch);
   if(!n||*(const u32*)(n+12)!=(s?24818:24805)+v){status=11;goto cleanup;}queries++;
  }
 }
 u64 *binding=(u64*)(owned+MAIN_FN_FL23_OPTION_BINDING);
 binding[0]=context;binding[1]=bias;binding[2]=owned+MAIN_FN_FL23_MAIN_CELL;binding[3]=owned+MAIN_FN_FL23_MAIN_VALUE_ALLOWED;
 if(CALL4(FL23_MENU_BIND_LEAVES,bias+0x22dabd0,bias+0x22dac8c,owned+MAIN_MENU+0x100+6*160,owned+MAIN_MENU+0x100+7*160)){status=11;goto cleanup;}
 for(u32 s=0;s<2;s++){
  if(CALL4(FL23_MAIN_MODEL_PROJECTION,context,bias,17+s,data+736+s*16)||CALL2(FL23_OPTION_BYTES,17+s,data+768+s*8)){status=11;goto cleanup;}
  queries+=2;
  if(CALL2(FL23_MENU_LEAF,0,0x23c3+s)!=owned+MAIN_MENU+0x100+(6+s)*160||CALL2(FL23_MENU_PARENT,0,0x23c3+s)!=0x197){status=11;goto cleanup;}
  queries+=2;
 }
words[10]=queries;return 0;
cleanup:
 words[10]=queries;
 if(mask&4){CALL0(FL23_FLAGS_DISABLE);words[22]=CALL2(FL23_FLAGS_DESTROY,bias,flags);if(!words[22])words[14]|=4;}
 if(mask&2){CALL1(FL23_OBSERVER_DISABLE,observer);words[23]=CALL2(FL23_OBSERVER_DESTROY,bias,observer);if(!words[23])words[14]|=2;}
 if(mask&1){CALL0(FL23_INPUTS_DISABLE);words[24]=CALL3(FL23_INPUTS_DESTROY,bias,groups,enums);if(!words[24])words[14]|=1;}
 CALL0(FL23_MAIN_UNBIND);
 put32(data+816,((u64*)groups)[3]);put32(data+820,((u64*)enums)[3]);
 put32(data+824,((u64*)observer)[3]);put32(data+828,((u64*)flags)[3]);
 if(words[14]!=mask)status=12;
 return status;
}

#define FL23_AUDIT_KIND 40u
#define FL23_AUDIT_EXTRA 12288u
#define FL23_AUDIT_EXTENSION
/* Snapshot only. No POKEDATA, register writes, app calls or host PID input.
 * Child and parent communicate through a new shared anonymous mapping, never
 * a second writer to the service file. The parent alone encodes the response.
 */
#include "fl23_stack_bounds.h"
#define AT_HEADER 256u
#define AT_MAPS 256u
#define AT_IDS (AT_MAPS+131072u)
#define AT_SECOND (AT_IDS+MAX_TASKS*4u)
#define AT_THIRD (AT_SECOND+MAX_TASKS*4u)
#define AT_ROWS (AT_THIRD+MAX_TASKS*4u)
#define AT_ROW 1536u
#ifndef FL23_AUDIT_EXTRA
#define FL23_AUDIT_EXTRA 0u
#endif
#ifndef FL23_AUDIT_KIND
#define FL23_AUDIT_KIND 18u
#endif
#define AT_SIZE (AT_ROWS+MAX_TASKS*AT_ROW+FL23_AUDIT_EXTRA)
#define AT_ALLOC ((AT_SIZE+4095u)&~4095u)
#define AT_BUDGET 250000000ULL
static u8 *shared;
static u64 target_pid,target_tid;
static char maps_path[64];
static u32 third[MAX_TASKS];
#ifdef FL23_AUDIT_EXTENSION
static long audit_extension(u64 *h,long count);
#endif
static void set64(u8 *p,u64 value){for(u32 i=0;i<8;i++)p[i]=(u8)(value>>(8*i));}
static u64 load64(const u8 *p){u64 v=0;for(u32 i=0;i<8;i++)v|=(u64)p[i]<<(8*i);return v;}
static u64 now(void){
    u64 ts[2]={0,0};if(sc(113,1,(long)ts,0,0,0,0)||ts[1]>=1000000000ULL||ts[0]>0xffffffffULL)return 0;
    return ts[0]*1000000000ULL+ts[1];
}
static int expired(u64 start){u64 n=now();return !n||n<start||n-start>=AT_BUDGET;}
static void delay(u64 ns){u64 ts[2]={0,ns};sc(101,(long)ts,0,0,0,0,0);}
static u32 decimal(char *out,u32 value){char rev[10];u32 n=0;do{rev[n++]='0'+value%10;value/=10;}while(value);u32 length=n;while(n)*out++=rev[--n];return length;}
static void process_path(char *out,u32 pid,const char *suffix){
    const char prefix[]="/proc/";u32 n=0;for(;n<6;n++)out[n]=prefix[n];n+=decimal(out+n,pid);
    for(u32 i=0;suffix[i];i++)out[n++]=suffix[i];out[n]=0;
}
static void stat_path(char *out,u32 tid){
    u32 n=0;while(task_root[n]){out[n]=task_root[n];n++;}out[n++]='/';n+=decimal(out+n,tid);
    const char suffix[]="/stat";for(u32 i=0;i<sizeof(suffix);i++)out[n+i]=suffix[i];
}
static int same_ids(u32 *other,long count,long expected){if(count!=expected)return 0;for(u32 i=0;i<(u32)count;i++)if(first[i]!=other[i])return 0;return 1;}
__attribute__((noreturn,noinline)) static void controller(void){
    u64 *h=(void*)shared;u32 status=0;long count=0;
    h[0]=0x0000000141333246ULL;h[1]=target_pid;h[2]=target_tid;h[3]=sc(172,0,0,0,0,0,0);h[25]=AT_BUDGET;
    for(u32 i=0;i<16;i++)shared[26*8+i]=request[8+i];
    {
        u64 action[4]={0},clean[4]={0},confirm[4]={0},block=1ULL<<16,old=0,current=0;
        if(sc(134,17,0,(long)action,8,0,0)||sc(134,17,(long)clean,0,8,0,0)||
           sc(134,17,0,(long)confirm,8,0,0)||confirm[0]||confirm[1]||confirm[2]||confirm[3]||
           sc(135,0,(long)&block,(long)&old,8,0,0)||sc(135,0,0,(long)&current,8,0,0)||current!=(old|block)){
            status=1;goto done;
        }
        h[21]=old;h[22]=current;h[23]=action[0];h[24]=action[1];
    }
    count=enumerate(first);h[28]=count<0?count:0;
    if(count<=0){status=2;goto done;}h[4]=count;
    {
        u32 have_pid=0,have_tid=0;
        for(u32 i=0;i<(u32)count;i++){have_pid|=first[i]==target_pid;have_tid|=first[i]==target_tid;if(first[i]==h[3]){status=3;goto done;}}
        if(!have_pid||!have_tid){status=3;goto done;}
    }
    for(u32 i=0;i<(u32)count;i++){
        char path[64];u8 *row=shared+AT_ROWS+i*AT_ROW;put32(row,first[i]);put32(shared+AT_IDS+i*4,first[i]);
        for(u32 j=0;j<5;j++)set64(row+16+j*8,-999);
        stat_path(path,first[i]);long n=read_text(path,row+80,512);if(n<=0){h[28]=n;status=5;goto done;}put32(row+72,n);h[6]++;
        u32 close=0;for(u32 j=0;j<(u32)n;j++)if(row[80+j]==')')close=j;
        u8 state=close+3<(u32)n?row[80+close+2]:0;
        if(!close||row[80+close+1]!=' '||(state!='R'&&state!='S'&&state!='D')){h[28]=-22;status=5;goto done;}
    }
    h[15]=now();if(!h[15]){status=6;goto done;}
    for(u32 i=0;i<(u32)count;i++){
        if(expired(h[15])){status=7;h[29]=1;goto cleanup;}
        u8 *row=shared+AT_ROWS+i*AT_ROW;long rc=sc(117,0x4206,first[i],0,0,0,0);set64(row+16,rc);
        if(rc){status=8;goto cleanup;}put32(row+4,1);h[7]++;
    }
    h[16]=now();
    for(u32 i=0;i<(u32)count;i++){
        if(expired(h[15])){status=7;h[29]=1;goto cleanup;}
        u8 *row=shared+AT_ROWS+i*AT_ROW;long rc=sc(117,0x4207,first[i],0,0,0,0);set64(row+24,rc);
        if(rc){status=9;goto cleanup;}put32(row+4,get32(row+4)|2);h[8]++;
    }
    for(u32 i=0;i<(u32)count;i++){
        u8 *row=shared+AT_ROWS+i*AT_ROW;int event=0;long rc=0;
        for(u32 attempt=0;attempt<250;attempt++){
            if(expired(h[15])){status=7;h[29]=1;goto cleanup;}
            rc=sc(260,first[i],(long)&event,0x40000001,0,0,0);
            if(rc==first[i]||(rc<0&&rc!=-4))break;
            delay(1000000);
        }
        set64(row+32,rc);put32(row+8,event);
        if(rc==first[i]&&((u32)event&255)==127){put32(row+4,get32(row+4)|4);h[9]++;}
        if(rc!=first[i]||(u32)event!=0x80057f){status=10;goto cleanup;}
    }
    h[17]=now();
    {
        long n=enumerate(second);h[30]=n<0?0:n;
        for(u32 i=0;n>0&&i<(u32)n;i++)put32(shared+AT_SECOND+i*4,second[i]);
        if(!same_ids(second,n,count)){h[28]=n<0?n:0;status=11;goto cleanup;}h[12]=1;
    }
    {
        long n=read_text(maps_path,shared+AT_MAPS,131072);if(n<=0){h[28]=n;status=4;goto cleanup;}h[5]=n;
    }
    for(u32 i=0;i<(u32)count;i++){
        if(expired(h[15])){status=7;h[29]=1;goto cleanup;}
        u8 *row=shared+AT_ROWS+i*AT_ROW;struct {u64 base,length;} iov={(u64)(row+592),272};
        long rc=sc(117,0x4204,first[i],1,(long)&iov,0,0);set64(row+40,rc);set64(row+56,iov.length);
        if(rc||iov.length!=272){status=12;goto cleanup;}put32(row+4,get32(row+4)|8);h[10]++;
    }
    fl23_sb_word stack_sp[MAX_TASKS],stack_lo[MAX_TASKS],stack_hi[MAX_TASKS];
    for(u32 i=0;i<(u32)count;i++)stack_sp[i]=load64(shared+AT_ROWS+i*AT_ROW+592+31*8);
    int stack_bounds_ready=fl23_stack_bounds_many(shared+AT_MAPS,(u32)h[5],stack_sp,(u32)count,stack_lo,stack_hi);
    for(u32 i=0;i<(u32)count;i++){
        u8 *row=shared+AT_ROWS+i*AT_ROW;u64 sp=load64(row+592+31*8),fp=load64(row+592+29*8);
        u32 frames=0,reason=0;
        if(!sp||sp&15||sp>=0x8000000000ULL){put32(row+64,2);continue;}
        if(!stack_bounds_ready){put32(row+64,2);continue;}
        for(;fp&&frames<32;frames++){
            if(expired(h[15])){put32(row+64,4);status=7;h[29]=1;goto cleanup;}
            if(!fl23_stack_frame(sp,fp,stack_lo[i],stack_hi[i])){reason=2;break;}
            u64 next=0,lr=0;
            long a=sc(117,2,first[i],fp,(long)&next,0,0),b=sc(117,2,first[i],fp+8,(long)&lr,0,0);
            if(a||b){reason=3;set64(row+1376,a?a:b);break;}
            set64(row+864+frames*16,next);set64(row+872+frames*16,lr);put32(row+12,frames+1);
            if(next&&next<=fp){reason=2;break;}fp=next;
        }
        if(frames==32&&fp)reason=1;put32(row+64,reason);
    }
    {
        if(expired(h[15])){status=7;h[29]=1;goto cleanup;}
        long n=enumerate(third);h[31]=n<0?0:n;
        for(u32 i=0;n>0&&i<(u32)n;i++)put32(shared+AT_THIRD+i*4,third[i]);
        if(!same_ids(third,n,count)){h[28]=n<0?n:0;status=13;goto cleanup;}h[13]=1;
    }
    if(expired(h[15])){status=7;h[29]=1;goto cleanup;}
#ifdef FL23_AUDIT_EXTENSION
    {long rc=audit_extension(h,count);if(rc){h[28]=rc;status=15;goto cleanup;}}
#endif
cleanup:
    h[18]=now();
    /* Resume the constructor last. It cannot publish a response until this
     * controller exits and is reaped. Never block cleanup on a deadline. */
    for(u32 pass=0;pass<2;pass++)for(u32 i=0;i<(u32)count;i++){
        if((first[i]==target_tid)!=(pass==1))continue;
        u8 *row=shared+AT_ROWS+i*AT_ROW;
        if(get32(row+4)&4){long rc=sc(117,17,first[i],0,0,0,0);set64(row+48,rc);
            if(rc){if(!status)status=14;}else{put32(row+4,get32(row+4)|16);h[11]++;}}
    }
    h[19]=now();if(h[16]&&h[19]>=h[16])h[20]=h[19]-h[16];
done:
    h[14]=status;__asm__ volatile("dmb ish":::"memory");
    /* No EXITKILL. Any still-seized relation is explicitly visible in rows;
     * tracer exit unlinks it. No target memory/register content was written. */
    sc(93,status?1:0,0,0,0,0,0);__builtin_unreachable();
}

/* Whole prepared owner + SAME stopped cohort native planner, no publication.
 * The generated unit reuses the bound kind38 preparation and original audit
 * controller bodies. No POKEDATA, target register changes or CP writes.
 */
#define TRIAL_EXTRA (AT_ROWS+MAX_TASKS*AT_ROW)
#define TRIAL_MAGIC 0x315550333246ULL
static u64 trial_owned,trial_bias,trial_prepare_words[32];static int trial_code_ready;
static struct main_bind_result trial_binding;
static u8 trial_prepare_data[2048];
/* Private child descriptor, two fixed parent prefixes, no arbitrary reader.
 * close is attempted once even after deadline/error; a failed close refuses
 * the plan and is never retried against a possibly reused descriptor. */
static long trial_mem_fd=-1;
static u64 trial_bulk[8];
static long main_capture_bulk_read(u64 pid,u64 source,u64 dest,u64 bytes){
 u64 i=trial_bulk[2];
 if(pid!=main_capture_parent||i>=2||
    source!=main_capture_bias+(i?MAIN_OLD_INPUT:MAIN_OLD_COUNTER)||
    dest!=main_capture_owner+(i?MAIN_STATE_INPUT:MAIN_STATE_COUNTER)||
    bytes!=(i?MAIN_OLD_GROUP_COUNT*24ULL:MAIN_OLD_COUNTER_COUNT*4ULL))return -22;
 if(main_capture_expired())return -110;
 if(!i){
  char path[64];process_path(path,main_capture_parent,"/mem");trial_bulk[1]++;
  trial_mem_fd=sc(56,-100,(long)path,0x80000,0,0,0);
  if(trial_mem_fd<0){trial_bulk[5]=trial_mem_fd;return trial_mem_fd;}
 }
 if(trial_mem_fd<0)return -9;
 trial_bulk[2]++;long rc=sc(67,trial_mem_fd,dest,bytes,source,0,0);
 if(rc>0)trial_bulk[3]+=rc;
 if(rc!=(long)bytes)trial_bulk[6]=rc<0?rc:-5;
 return rc;
}
static long trial_mem_close(void){
 if(trial_mem_fd<0)return 0;
 long fd=trial_mem_fd;trial_mem_fd=-1;trial_bulk[4]++;
 long rc=sc(57,fd,0,0,0,0,0);trial_bulk[7]=rc;return rc;
}
/* Internal stopped-cohort commit adapter. No constructor/device entry.
 * Caller supplies an additional, independently reviewed full-reader/pointer
 * quiescence checker; the planner's patch-site check is not that checker.
 * This module NEVER attaches/detaches, destroys containers or unmaps owner.
 */
/* Original planner is already included by the sealed cohort source. */
#define u64 main_commit_core_u64
#define sc main_commit_core_sc
#define word main_commit_core_word
#define outcome main_commit_core_outcome
#define FL23_COMMIT_EXPIRED() main_capture_expired()
#include "fl23_ptrace_words.c"
#undef FL23_COMMIT_EXPIRED
#undef outcome
#undef word
#undef sc
#undef u64

#define MAIN_COMMIT_WORDS 328u
#define MAIN_COMMIT_HEADER_BYTES 128u
#define MAIN_COMMIT_PLAN_BYTES 176u
#define MAIN_COMMIT_OUTCOME_BYTES 96u
#define MAIN_COMMIT_WORDS_OFFSET 400u
#define MAIN_COMMIT_REPORT_BYTES 8272u
#define MAIN_COMMIT_MAGIC 0x31544d4333324646ULL
enum main_commit_lifecycle {
 MAIN_COMMIT_NO_WRITE=1, MAIN_COMMIT_ROLLED_BACK=2,
 MAIN_COMMIT_PUBLISHED=3, MAIN_COMMIT_UNCERTAIN=4
};
struct main_commit_report {
 u64 magic,version,lifecycle,owner_retained,tracer_required,word_count;
 u64 pid,bias,owned,cohort,cohort_start,service_tid;
 u8 nonce[16];long plan_rc,commit_rc;
 struct main_pub_result plan;
 struct main_commit_core_outcome outcome;
 struct main_commit_core_word words[MAIN_COMMIT_WORDS];
};
_Static_assert(sizeof(struct main_commit_report)==MAIN_COMMIT_REPORT_BYTES,"commit report ABI");
_Static_assert(sizeof(struct main_pub_result)==MAIN_COMMIT_PLAN_BYTES,"planner report ABI");
_Static_assert(sizeof(struct main_commit_core_outcome)==MAIN_COMMIT_OUTCOME_BYTES,"core outcome ABI");
_Static_assert(__builtin_offsetof(struct main_commit_report,plan)==MAIN_COMMIT_HEADER_BYTES,"plan export ABI");
_Static_assert(__builtin_offsetof(struct main_commit_report,words)==MAIN_COMMIT_WORDS_OFFSET,"word export ABI");
typedef long(*main_commit_quiescence)(const struct main_state_capture_args *,const struct main_pub_result *);

static int main_commit_report_valid(const struct main_state_capture_args *a,struct main_commit_report *r){
 if(!a||((u64)a&7)||(u64)a>=0x8000000000ULL-sizeof(*a)||
    !main_state_result_valid(a->bias,a->owned,(struct main_state_result*)r)||
    (u64)r>=0x8000000000ULL-sizeof(*r)||
    main_pub_overlap((u64)r,sizeof(*r),a->owned,0x200000)||
    main_pub_overlap((u64)r,sizeof(*r),a->bias,0x15000000)||
    main_pub_overlap((u64)r,sizeof(*r),(u64)a,sizeof(*a))||
    a->bytes!=MAIN_COHORT_SIZE||!main_pub_span(a->cohort,a->bytes)||
    main_pub_overlap((u64)r,sizeof(*r),a->cohort,a->bytes))return 0;
 return 1;
}
/* Result is a lifecycle, not a cleanup instruction to execute blindly.
 * Published: keep parent owner+native containers. Every path which poked
 * instruction words keeps tracer_required set until the caller separately
 * establishes instruction-cache visibility, even after a complete inverse.
 * Uncertain: DO NOT resume or unload service code. No-write/verified inverse
 * permit unpublished-owner retirement only after the caller has completed
 * its finalization and confirmed child exit. No hot-uninstall is offered.
 */
long fl23_main_commit_stopped(const struct main_state_capture_args *a,
 main_commit_quiescence quiescent,struct main_commit_report *r){
 if(!main_commit_report_valid(a,r))return -22;
 for(u32 i=0;i<sizeof(*r)/8;i++)((u64*)r)[i]=0;
 r->magic=MAIN_COMMIT_MAGIC;r->version=1;r->lifecycle=MAIN_COMMIT_NO_WRITE;
 r->pid=a->pid;r->bias=a->bias;r->owned=a->owned;r->cohort=a->cohort;
 for(u32 i=0;i<16;i++)r->nonce[i]=a->nonce[i];
 if(!quiescent){r->plan_rc=-96;return r->lifecycle;}
 r->plan_rc=fl23_main_publication_plan(a,&r->plan);
 if(r->plan_rc||!r->plan.ready)return r->lifecycle;
 if(r->plan.edits!=96||r->plan.words!=MAIN_COMMIT_WORDS||r->plan.flags_seeded!=96||r->plan.safe_to_publish){
  r->plan_rc=-116;return r->lifecycle;
 }
 const struct main_pub_word *journal=(const void*)(a->owned+MAIN_JOURNAL);
 for(u32 i=0;i<MAIN_COMMIT_WORDS;i++){
  r->words[i].address=journal[i].address;
  r->words[i].before=journal[i].before;
  r->words[i].after=journal[i].after;
 }
 r->word_count=MAIN_COMMIT_WORDS;r->cohort_start=main_capture_start;r->service_tid=main_capture_tid;
 /* Gate must examine the SAME still-stopped snapshot and old references.
  * A boolean from a previous sample is not a supported argument. */
 long gate=quiescent(a,&r->plan);
 if(gate||!main_capture_cohort(a)||((const u64*)a->cohort)[15]!=r->cohort_start||
    ((const u64*)a->cohort)[2]!=r->service_tid||main_capture_expired()){
  r->plan_rc=gate?gate:-116;return r->lifecycle;
 }
 for(u32 i=0;i<MAIN_COMMIT_WORDS;i++)if(journal[i].address!=r->words[i].address||
    journal[i].before!=r->words[i].before||journal[i].after!=r->words[i].after){
  r->plan_rc=-116;return r->lifecycle;
 }
 r->commit_rc=fl23_commit_words(a->pid,r->words,MAIN_COMMIT_WORDS,&r->outcome);
 if(r->outcome.uncertain){r->lifecycle=MAIN_COMMIT_UNCERTAIN;r->owner_retained=1;r->tracer_required=1;}
 else if(!r->commit_rc&&r->outcome.committed){r->lifecycle=MAIN_COMMIT_PUBLISHED;r->owner_retained=1;r->tracer_required=1;}
 else if(r->outcome.rollback_verified){r->lifecycle=MAIN_COMMIT_ROLLED_BACK;r->tracer_required=1;}
 else if(r->outcome.poke_calls){
  /* A changed core contract must fail closed even if its uncertain flag
   * was accidentally omitted. No completed inverse is not a no-write. */
  r->lifecycle=MAIN_COMMIT_UNCERTAIN;r->owner_retained=1;r->tracer_required=1;
 }
 return r->lifecycle;
}

/* Finalize the immutable commit report in this same stopped cohort. The
 * build binds the original camera kernel's POKEDATA->copy_to_user_page path:
 * executable VMAs receive DC CVAU/DSB ISH, IC IALLUIS/DSB ISH before return.
 * No executable words are written through the child shared alias. Owned RX
 * pages were separately made visible by the bound preparation lifecycle.
 * This result is permission for the owning controller to detach, not a
 * detach operation. On any unresolved write it must keep the tracer alive.
 */
#define MAIN_COMMIT_FINALIZE_MAGIC 0x31464d4333324646ULL
#define MAIN_COMMIT_FINALIZE_BYTES 128u
struct main_commit_finalization {
 u64 magic,version,lifecycle,resume_authorized,owner_retained,tracer_required,retire_unpublished,words_checked;
 u64 pid,owned,cohort,cohort_start;
 u8 nonce[16];long error;u64 cache_contract;
};
_Static_assert(sizeof(struct main_commit_finalization)==MAIN_COMMIT_FINALIZE_BYTES,"finalization report ABI");
long fl23_main_commit_finalize_stopped(const struct main_state_capture_args *a,
 const struct main_commit_report *r,struct main_commit_finalization *f){
 if(!a||!r||!f||((u64)f&7)||(u64)f>=0x8000000000ULL-sizeof(*f)||
    !main_commit_report_valid(a,(struct main_commit_report*)r)||
    main_pub_overlap((u64)f,sizeof(*f),a->owned,0x200000)||
    main_pub_overlap((u64)f,sizeof(*f),a->bias,0x15000000)||
    main_pub_overlap((u64)f,sizeof(*f),a->cohort,a->bytes)||
    main_pub_overlap((u64)f,sizeof(*f),(u64)a,sizeof(*a))||
    main_pub_overlap((u64)f,sizeof(*f),(u64)r,sizeof(*r)))return -22;
 for(u32 i=0;i<sizeof(*f)/8;i++)((u64*)f)[i]=0;
 f->magic=MAIN_COMMIT_FINALIZE_MAGIC;f->version=1;f->lifecycle=MAIN_COMMIT_UNCERTAIN;
 f->owner_retained=1;f->tracer_required=1;f->error=-116;
 f->pid=a->pid;f->owned=a->owned;f->cohort=a->cohort;f->cohort_start=r->cohort_start;
 for(u32 i=0;i<16;i++)f->nonce[i]=a->nonce[i];
 if(r->magic!=MAIN_COMMIT_MAGIC||r->version!=1||r->pid!=a->pid||r->bias!=a->bias||
    r->owned!=a->owned||r->cohort!=a->cohort)return f->error;
 for(u32 i=0;i<16;i++)if(r->nonce[i]!=a->nonce[i])return f->error;
 if(r->lifecycle==MAIN_COMMIT_UNCERTAIN||r->outcome.uncertain){f->error=-117;return f->error;}
 if(r->lifecycle==MAIN_COMMIT_NO_WRITE){
  if(r->outcome.poke_calls||r->outcome.committed||r->outcome.rollback_verified||
     r->owner_retained||r->tracer_required)return f->error;
  f->lifecycle=MAIN_COMMIT_NO_WRITE;f->resume_authorized=1;f->owner_retained=0;
  f->tracer_required=0;f->retire_unpublished=1;f->error=0;return 0;
 }
 int published=r->lifecycle==MAIN_COMMIT_PUBLISHED;
 if((!published&&r->lifecycle!=MAIN_COMMIT_ROLLED_BACK)||r->word_count!=MAIN_COMMIT_WORDS||
    !r->outcome.poke_calls||!r->tracer_required||r->owner_retained!=(u64)published||
    r->outcome.version!=1||r->plan_rc||r->plan.edits!=96||r->plan.words!=MAIN_COMMIT_WORDS||
    !r->plan.ready||r->plan.safe_to_publish||r->plan.flags_seeded!=96||
    r->cohort_start!=main_capture_start||r->service_tid!=main_capture_tid||
    a->pid!=main_capture_parent||a->bias!=main_capture_bias||a->owned!=main_capture_owner||
    !main_capture_cohort(a))return f->error;
 if(published){
  if(r->commit_rc||!r->outcome.committed||r->outcome.rollback_verified||r->outcome.phase!=3||
     r->outcome.checked!=MAIN_COMMIT_WORDS||r->outcome.confirmed_after!=MAIN_COMMIT_WORDS||r->outcome.error)return f->error;
 }else if(!r->commit_rc||r->outcome.committed||!r->outcome.rollback_verified||r->outcome.phase!=5||
           r->outcome.restored!=r->outcome.confirmed_after)return f->error;
 const struct main_pub_word *journal=(const void*)(a->owned+MAIN_JOURNAL);
 for(u32 i=0;i<MAIN_COMMIT_WORDS;i++){
  const struct main_commit_core_word *w=r->words+i;
  if(!w->address||(w->address&7)||w->address>=0x8000000000ULL-8||
     (i&&w[-1].address>=w->address)||journal[i].address!=w->address||
     journal[i].before!=w->before||journal[i].after!=w->after)return f->error;
  u64 observed=0;long rc=main_commit_core_sc(2,a->pid,w->address,(long)&observed);
  if(rc||observed!=(published?w->after:w->before)){f->error=rc?rc:-116;return f->error;}
  f->words_checked++;
 }
 if(!main_capture_cohort(a)||((const u64*)a->cohort)[15]!=r->cohort_start||
    ((const u64*)a->cohort)[2]!=r->service_tid)return f->error;
 /* Ptrace kernel has completed cache maintenance on every executable write;
  * publish the decision only after the final whole-plan independent read. */
 __asm__ volatile("dmb ish\nisb":::"memory");
 f->lifecycle=r->lifecycle;f->resume_authorized=1;f->owner_retained=published;
 f->tracer_required=0;f->retire_unpublished=!published;f->cache_contract=1;f->error=0;
 return 0;
}

#define FL23_MAIN_READERS_EMBEDDED
#include "fl23_main_readers.c"
/* Same-cohort main-menu RAM publication extension.
 * The builder reuses the sealed preparation/controller/bulk-reader source.
 * No firmware, parameter, SAVE or WBI write is implemented here.
 */
#define MAIN_PUBLISH_COMMIT_OFFSET 640u
#define MAIN_PUBLISH_FINAL_OFFSET 8912u
#define MAIN_PUBLISH_READERS_OFFSET 9088u
#define MAIN_PUBLISH_ICON_OFFSET 9216u
#include "icon-profile.h"
#define ICON_MAGIC 0x314e4f4349333246ULL
#define ICON_WORDS (1u+ICON_CODE_WORDS+OSD_HOOK_WORDS+OSD_CODE_WORDS+OSD_TABLES)
/* RAM-only FL2/FL3 icon records in the SAME stopped cohort, after the main
 * plan's final readback. Only the first libObj resolver instruction changes;
 * a thread parked inside that prologue keeps executing original words.
 * Records/payloads live in the owner's RW tail; libObj code lives in the
 * zero padding of its own RX page. No bank file, /usr or WBI access. */
struct icon_report {
 u64 magic,version,status,lifecycle,lib,bank,bank_bytes,engine;
 u64 registry,sources,data,words,checked,maps_bytes,guard_words,reserved;
 struct main_commit_core_outcome outcome;
};
_Static_assert(sizeof(struct icon_report)==224,"icon report ABI");
static u8 icon_maps[262144];
static struct main_commit_core_word icon_words[ICON_WORDS];
static u64 icon_hex(const u8 **p,const u8 *end){
 u64 v=0;u32 n=0;
 while(*p<end&&n<16){u8 c=**p;u32 d=c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:99;if(d>15)break;v=v<<4|d;(*p)++;n++;}
 return n?v:~0ULL;
}
static int icon_suffix(const u8 *line,const u8 *eol,const char *path,u32 n){
 if(eol-line<(long)n)return 0;
 for(u32 i=0;i<n;i++)if(eol[-(long)n+i]!=(u8)path[i])return 0;
 return eol-line==(long)n||eol[-(long)n-1]==' ';
}
static long icon_maps_bind(struct icon_report *r){
 long fd=sc(56,-100,(long)"/proc/self/maps",0x80000,0,0,0);if(fd<0)return fd;
 u64 n=0;long got=0;
 while(n<sizeof(icon_maps)){got=sc(63,fd,(long)(icon_maps+n),sizeof(icon_maps)-n,0,0,0);if(got==-4)continue;if(got<=0)break;n+=got;}
 long closed=sc(57,fd,0,0,0,0,0);
 if(got<0)return got;if(n>=sizeof(icon_maps)||closed)return closed?closed:-27;
 r->maps_bytes=n;u32 libs=0,banks=0;
 for(const u8 *at=icon_maps,*end=icon_maps+n;at<end;){
  const u8 *line=at;while(at<end&&*at!='\n')at++;if(at==end)return -5;
  const u8 *p=line;u64 first=icon_hex(&p,at);if(p>=at||*p++!='-')return -5;
  u64 last=icon_hex(&p,at);if(p+6>=at||*p++!=' '||last<=first)return -5;
  const u8 *perm=p;p+=5;u64 offset=icon_hex(&p,at);
  if(icon_suffix(line,at,"/usr/lib/libObj.so",18)&&!offset&&perm[0]=='r'&&perm[1]=='-'&&perm[2]=='x'&&perm[3]=='p'){
   if(last-first<((ICON_CAVE_END+4095)&~4095ULL))return -34;r->lib=first;libs++;
  }
  if(icon_suffix(line,at,"/usr/share/app/image_02_32_IPSX.uxc",35)&&!offset&&perm[0]=='r'&&perm[1]=='-'&&perm[2]=='-'&&perm[3]=='p'){
   r->bank=first;r->bank_bytes=last-first;banks++;
  }
  at++;
 }
 return libs==1&&banks==1&&r->bank_bytes==ICON_BANK_BYTES&&!(r->lib&4095)&&!(r->bank&4095)?0:-116;
}
static long icon_peek(const struct main_state_capture_args *a,u64 at,u64 *out){
 *out=0;return main_commit_core_sc(2,a->pid,at,(long)out);
}
static long icon_publish(const struct main_state_capture_args *a,struct icon_report *r){
 for(u32 i=0;i<sizeof(*r)/8;i++)((u64*)r)[i]=0;
 r->magic=ICON_MAGIC;r->version=1;r->lifecycle=MAIN_COMMIT_NO_WRITE;
 long rc=icon_maps_bind(r);if(rc)return rc;
 u64 v=0;
 for(u32 i=0;i<ICON_GUARD_WORDS;i++){
  if((rc=icon_peek(a,r->lib+ICON_GUARD_START+i*8,&v)))return rc;
  if(v!=icon_guard[i])return -116;r->guard_words++;
 }
 if((rc=icon_peek(a,r->lib+ICON_ENGINE_SLOT,&r->engine)))return rc;
 if(!main_pub_span(r->engine,ICON_REGISTRY_FIELD+8))return -116;
 if((rc=icon_peek(a,r->engine+ICON_REGISTRY_FIELD,&r->registry)))return rc;
 if(!main_pub_span(r->registry,ICON_SOURCE_COUNT+8))return -116;
 if((rc=icon_peek(a,r->registry+ICON_SOURCE_COUNT,&v)))return rc;
 r->sources=v&0xffffffffULL;if(r->sources)return -95;
 if((rc=icon_peek(a,r->bank,&v)))return rc;
 if(v!=0x0000000007647875ULL)return -116;
 for(u32 i=0;i<4;i++){
  u64 at=r->bank+16+2u*icon_indices[i];
  if((rc=icon_peek(a,at&~7ULL,&v)))return rc;
  if(((v>>((at&7)*8))&0xffff)!=0xffff)return -116;
 }
 u8 *data=(u8*)(a->owned+ICON_DATA_OFFSET);
 for(u32 i=0;i<ICON_DATA_LIMIT;i++)if(data[i])return -17;
 r->data=a->owned+ICON_DATA_OFFSET;
 for(u32 i=0;i<ICON_DATA_BYTES;i++)data[i]=icon_data[i];
 __asm__ volatile("dmb ish":::"memory");
 for(u32 i=0;i<ICON_DATA_BYTES;i++)if(data[i]!=icon_data[i])return -5;
 u64 entry=icon_guard[(ICON_RESOLVER-ICON_GUARD_START)/8];
 const u32 osd_n=OSD_HOOK_WORDS+OSD_CODE_WORDS+OSD_TABLES,lib_at=a->bias<r->lib?osd_n:0u,osd_at=a->bias<r->lib?0u:1u+ICON_CODE_WORDS;
 icon_words[lib_at]=(struct main_commit_core_word){r->lib+ICON_RESOLVER,entry,(entry&~0xffffffffULL)|ICON_ENTRY_INSN};
 for(u32 i=0;i<ICON_CODE_WORDS;i++){
  u64 after=icon_code[i];
  for(u32 s=0;s<5;s++)if(icon_slots[s]==i)after=s?r->data+icon_records[s-1]:r->bank+8;
  icon_words[lib_at+1+i]=(struct main_commit_core_word){r->lib+ICON_CAVE+i*8,0,after};
 }
 /* Same-cohort appFw shooting look indicator: the translator's compare word,
  * its cave, and four single-use GOT slots redirected to owner table copies
  * whose rows 0..10 equal the live originals. The commit core needs strictly
  * ascending addresses, so the two mappings' blocks are placed by base. */
 /* Ascending appFw compare/entry words (availability, playback, look,
  * notifier) share one cave; each must still hold its original value. */
 for(u32 h=0;h<OSD_HOOK_WORDS;h++){
  if((rc=icon_peek(a,a->bias+osd_hooks[h][0],&v)))return rc;
  if(v!=osd_hooks[h][1])return -116;
  icon_words[osd_at+h]=(struct main_commit_core_word){a->bias+osd_hooks[h][0],osd_hooks[h][1],osd_hooks[h][2]};
 }
 /* Availability/value entries reach the slot extension through the main
  * cave's relocated owner literal; it must already name this ready owner. */
 if((rc=icon_peek(a,a->bias+OSD_OWNER_LITERAL,&v)))return rc;
 if(v!=a->owned+OSD_OWNER_LITERAL_VALUE)return -116;
 const u64 *extension=(const u64*)(a->owned+OSD_CONTEXT);
 if(extension[0]!=OSD_EXTENSION_MAGIC||extension[1]!=OSD_EXTENSION_STATE)return -116;
#ifdef OSD_SENTINEL
 /* Debug build: in-range markers in every FL2/FL3 tweak cell (rows of 6 words,
  * then 22 shadow words); the seeded inhibit word is kept. */
 {u32 *row=(u32*)(a->owned+OSD_CONTEXT+16),*shadow=row+22*6;
  for(u32 g=3;g<11;g++)for(u32 s=0;s<2;s++){
   const unsigned *m=osd_sentinel[(g-3)*2+s];u32 *c=row+(g*2+s)*6;
   c[0]=m[0];c[1]=m[1];c[2]=m[2];c[4]=m[3];c[5]=m[4];shadow[g*2+s]=m[5];
  }
  __asm__ volatile("dmb ish":::"memory");}
#endif
 for(u32 i=0;i<OSD_CODE_WORDS;i++){
  if((rc=icon_peek(a,a->bias+OSD_CAVE+i*8,&v)))return rc;
  if(v)return -17;
  icon_words[osd_at+OSD_HOOK_WORDS+i]=(struct main_commit_core_word){a->bias+OSD_CAVE+i*8,0,osd_code[i]};
 }
 u8 *tables=(u8*)(a->owned+OSD_TABLE_OFFSET);
 for(u32 i=0;i<OSD_TABLE_LIMIT;i++)if(tables[i])return -17;
 for(u32 k=0;k<OSD_TABLES;k++){
  u64 got=a->bias+osd_got[k],table=a->bias+osd_table[k];
  if((rc=icon_peek(a,got,&v)))return rc;
  if(v!=table)return -116;
  const u8 *expected=osd_tables+k*OSD_TABLE_STRIDE;
  for(u32 at=0;at<OSD_TABLE_ORIGINAL_BYTES;at+=8){
   if((rc=icon_peek(a,table+at,&v)))return rc;
   for(u32 b=0;b<8&&at+b<OSD_TABLE_ORIGINAL_BYTES;b++)if(((v>>(8*b))&0xff)!=expected[at+b])return -116;
  }
  icon_words[osd_at+OSD_HOOK_WORDS+OSD_CODE_WORDS+k]=(struct main_commit_core_word){got,table,a->owned+OSD_TABLE_OFFSET+k*OSD_TABLE_STRIDE};
 }
 for(u32 i=0;i<OSD_TABLES*OSD_TABLE_STRIDE;i++)tables[i]=osd_tables[i];
 __asm__ volatile("dmb ish":::"memory");
 for(u32 i=0;i<OSD_TABLES*OSD_TABLE_STRIDE;i++)if(tables[i]!=osd_tables[i])return -5;
 r->words=ICON_WORDS;
 long commit=fl23_commit_words(a->pid,icon_words,ICON_WORDS,&r->outcome);
 if(r->outcome.uncertain||(r->outcome.poke_calls&&!r->outcome.committed&&!r->outcome.rollback_verified)){
  r->lifecycle=MAIN_COMMIT_UNCERTAIN;return -117;
 }
 if(commit||!r->outcome.committed){
  r->lifecycle=r->outcome.rollback_verified?MAIN_COMMIT_ROLLED_BACK:MAIN_COMMIT_NO_WRITE;
  return r->outcome.error?r->outcome.error:-116;
 }
 r->lifecycle=MAIN_COMMIT_PUBLISHED;
 for(u32 i=0;i<ICON_WORDS;i++){
  if((rc=icon_peek(a,icon_words[i].address,&v)))return rc;
  if(v!=icon_words[i].after)return -116;r->checked++;
 }
 __asm__ volatile("dmb ish\nisb":::"memory");
 return 0;
}
static long selection_revert_main(const struct main_state_capture_args *a,
 struct main_commit_report *r,struct main_commit_finalization *f,long cause){
 if(cause>=0||r->lifecycle!=MAIN_COMMIT_PUBLISHED||r->word_count!=MAIN_COMMIT_WORDS||
    !r->outcome.committed||!main_capture_cohort(a))return -116;
 struct main_commit_core_word reverse[MAIN_COMMIT_WORDS];
 struct main_commit_core_outcome undone;
 u64 prior_pokes=r->outcome.poke_calls,prior_peeks=r->outcome.peek_calls;
 for(u32 i=0;i<MAIN_COMMIT_WORDS;i++)reverse[i]=(struct main_commit_core_word){
  r->words[i].address,r->words[i].after,r->words[i].before};
 long rc=fl23_commit_words(a->pid,reverse,MAIN_COMMIT_WORDS,&undone);
 if(rc||!undone.committed||undone.uncertain||undone.confirmed_after!=MAIN_COMMIT_WORDS)return rc?rc:-116;
 /* Convert a completed reverse commit into the original-direction rollback
  * record. Counts are actual forward+reverse IO; finalizer independently
  * checks every original byte again before authorizing resume/retirement. */
 for(u32 i=0;i<sizeof(undone)/8;i++)((volatile u64*)&r->outcome)[i]=((const u64*)&undone)[i];
 r->outcome.phase=5;r->outcome.rollback_verified=1;
 r->outcome.committed=0;r->outcome.restored=MAIN_COMMIT_WORDS;r->outcome.error=cause;
 r->outcome.poke_calls+=prior_pokes;r->outcome.peek_calls+=prior_peeks;
 r->commit_rc=cause;r->lifecycle=MAIN_COMMIT_ROLLED_BACK;r->owner_retained=0;
 return fl23_main_commit_finalize_stopped(a,r,f);
}

static long main_publish_reader_gate(const struct main_state_capture_args *a,
 const struct main_pub_result *plan){
 long gate=fl23_main_readers_stopped(a,plan);
 /* Close the two-prefix read descriptor BEFORE the first possible POKE.
  * Failed/unknown close refuses publication; never retry its old fd. */
 long closed=trial_mem_close();
 return gate?gate:closed;
}
static long audit_extension(u64 *h,long count){
 (void)h;(void)count;
 u8 *extra=shared+TRIAL_EXTRA;u64 *out=(void*)extra;
 struct main_commit_report *commit=(void*)(extra+MAIN_PUBLISH_COMMIT_OFFSET);
 struct main_commit_finalization *final=(void*)(extra+MAIN_PUBLISH_FINAL_OFFSET);
 out[0]=TRIAL_MAGIC;out[3]=trial_owned;out[4]=trial_bias;
 for(u32 i=0;i<32;i++)((u64*)(extra+64))[i]=trial_prepare_words[i];
 for(u32 i=0;i<sizeof(trial_binding)/8;i++)((u64*)(extra+320))[i]=((u64*)&trial_binding)[i];
 struct main_state_capture_args args={target_pid,trial_bias,trial_owned,(u64)shared,MAIN_COHORT_SIZE,{0}};
 for(u32 i=0;i<16;i++)args.nonce[i]=request[8+i];
 trial_bulk[0]=1;u64 start=now();
 long life=fl23_main_commit_stopped(&args,main_publish_reader_gate,commit);
 long closed=trial_mem_close();
 /* This second attempt is a no-op when the gate already closed its fd.
  * Before-plan failures can leave an open read-only child descriptor. */
 if(closed&&commit->lifecycle==MAIN_COMMIT_NO_WRITE)commit->plan_rc=closed;
 for(u32 i=0;i<sizeof(commit->plan)/8;i++)((u64*)(extra+368))[i]=((u64*)&commit->plan)[i];
 for(u32 i=0;i<8;i++)((u64*)(extra+544))[i]=trial_bulk[i];
 _Static_assert(sizeof(fl23_main_readers_last)<=128,"reader export bound");
 for(u32 i=0;i<sizeof(fl23_main_readers_last);i++)extra[MAIN_PUBLISH_READERS_OFFSET+i]=((u8*)&fl23_main_readers_last)[i];
 long rc=fl23_main_commit_finalize_stopped(&args,commit,final);
 out[6]=now()-start;out[7]=commit->lifecycle;
 __asm__ volatile("dmb ish":::"memory");
 /* No tracer exit, detach or parent/service cleanup after uncertainty.
  * The parent remains stopped; the host's pending-child record is the
  * authoritative obligation if no complete response can be retrieved. */
 if(rc||!final->resume_authorized||life==MAIN_COMMIT_UNCERTAIN)
  for(;;)delay(1000000);
 if(life==MAIN_COMMIT_PUBLISHED){
  /* Icons first, so the leaves never become selectable with a half-bound
   * resolver. A verified no-write/rollback keeps the functional menu with
   * blank icons; an unresolved icon write keeps every reader stopped. */
  struct icon_report *icon=(void*)(extra+MAIN_PUBLISH_ICON_OFFSET);
  long icon_rc=icon_publish(&args,icon);icon->status=(u64)icon_rc;
  __asm__ volatile("dmb ish":::"memory");
  if(icon->lifecycle==MAIN_COMMIT_UNCERTAIN||(icon_rc&&icon->lifecycle==MAIN_COMMIT_PUBLISHED))
   for(;;)delay(1000000);
  /* The new base callback shares this verified OSD transaction. A
   * refusal/inverse must not leave a selectable but incomplete FL23 model. */
  if(icon_rc){
   if(selection_revert_main(&args,commit,final,icon_rc))for(;;)delay(1000000);
   out[7]=MAIN_COMMIT_ROLLED_BACK;
   return icon_rc;
  }
  /* Preparation deliberately inhibits the two extra leaves. Release that
   * independent readiness gate only AFTER the full commit/final readback,
   * while every target reader is still stopped. Keep native rule flags. */
  u8 *ready=(void*)(trial_owned+MAIN_MENU_READY);u64 *stage=(void*)(extra+608);
  stage[0]=1;stage[1]=ready[0]|((u32)ready[1]<<8);
  stage[2]=ready[2]|((u32)ready[3]<<8);
  if(stage[1]!=0x101||ready[2]>1||ready[3]>1)for(;;)delay(1000000);
  __atomic_store_n((unsigned short*)ready,0,__ATOMIC_RELEASE);
  __asm__ volatile("dmb ish":::"memory");
  stage[3]=ready[0]|((u32)ready[1]<<8)|((u32)ready[2]<<16)|((u32)ready[3]<<24);
  if((stage[3]&0xffff)||((stage[3]>>16)!=stage[2]))for(;;)delay(1000000);
 }
 return life==MAIN_COMMIT_PUBLISHED?0:(commit->plan_rc?commit->plan_rc:-116);
}

static long trial_release(void){
 u64 owned=trial_owned,bias=trial_bias;long failed=0;
 if(!owned)return 0;
 if((trial_prepare_words[9]&4)&&!(trial_prepare_words[14]&4)){
  CALL0(FL23_FLAGS_DISABLE);long rc=CALL2(FL23_FLAGS_DESTROY,bias,owned+MAIN_FLAG_CACHE);
  if(rc)failed=rc;else trial_prepare_words[14]|=4;
 }
 if((trial_prepare_words[9]&2)&&!(trial_prepare_words[14]&2)){
  CALL1(FL23_OBSERVER_DISABLE,owned+MAIN_OBSERVER_MAP);
  long rc=CALL2(FL23_OBSERVER_DESTROY,bias,owned+MAIN_OBSERVER_MAP);
  if(rc)failed=rc;else trial_prepare_words[14]|=2;
 }
 if((trial_prepare_words[9]&1)&&!(trial_prepare_words[14]&1)){
  CALL0(FL23_INPUTS_DISABLE);
  long rc=CALL3(FL23_INPUTS_DESTROY,bias,owned+MAIN_INPUT_MAPS,owned+MAIN_INPUT_MAPS+64);
  if(rc)failed=rc;else trial_prepare_words[14]|=1;
 }
 if(trial_code_ready)CALL0(FL23_MAIN_UNBIND);
 if(trial_prepare_words[9]!=trial_prepare_words[14])return failed?failed:-5;
 long rc=sc(215,owned,MAIN_RESERVE,0,0,0,0);trial_prepare_words[15]=rc;
 if(!rc)trial_owned=0;return rc;
}

/* A1 private core ABI. All application-changing code below is inherited
 * verbatim from the frozen publisher. This function does NOT perform the
 * host's CP/AP preflight; an autonomous worker must add that before calling.
 * One attempted transaction per loaded module. No implicit retry/unload. */
static u32 self_state;
long fl23_self_publish_core(const u8 *config,u64 bytes){
 if(!config||bytes!=40||!same(config,(const u8*)"A67P",4)||get32(config+4)!=1||
    get32(config+24)!=40||get32(config+28)||get32(config+32)||get32(config+36)!=DATA_SIZE)return -22;
 u32 expected=0;
 if(!__atomic_compare_exchange_n(&self_state,&expected,1,0,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE))
  return expected==1?-16:-114;
 memset(result,0,sizeof(result));memset(words,0,sizeof(words));
 for(u32 i=0;i<40;i++)request[i]=config[i];
 words[0]=1;words[1]=sc(172,0,0,0,0,0,0);words[2]=sc(178,0,0,0,0,0,0);
 u64 hint=0,roots[4];long status=0;u64 *report=(void*)(result+44+256+TRIAL_EXTRA);
 report[0]=TRIAL_MAGIC;
 if(!prep_maps(&trial_bias,&hint)||!main_guard_check(trial_bias)){status=3;goto done;}
 if(!prep_roots(trial_bias,roots,trial_prepare_data+256)){status=4;goto done;}
 {
  long mapped=sc(222,hint,MAIN_RESERVE,3,0x21,-1,0);
  if((u64)mapped>=(u64)-4095){status=5;goto done;}
  trial_owned=mapped;u64 owned=mapped,bias=trial_bias;
  words[4]=bias;words[5]=owned;words[6]=MAIN_RESERVE;trial_prepare_words[5]=owned;
  if(!prep_address(bias,owned)||main_fill((u8*)owned,bias,owned)){status=5;goto cleanup;}
  if(prep_protect(owned)){status=6;goto cleanup;}
  for(u32 i=0;i<sizeof(main_permissions)/sizeof(main_permissions[0]);i++)
   if(main_permissions[i].permissions&4)prep_cache(owned+main_permissions[i].offset,4096);
  trial_code_ready=1;
  if(main_ro_check(owned,bias,&words[7])){status=6;goto cleanup;}
  status=prep_cycle(bias,owned,roots,trial_prepare_data);
  for(u32 i=0;i<32;i++)trial_prepare_words[i]=words[i];
  if(status)goto cleanup;
  status=fl23_main_publication_bind(bias,owned,&trial_binding);
  if(status){status=7;goto cleanup;}
 }
 memset(words,0,sizeof(words));words[0]=1;
 target_pid=sc(172,0,0,0,0,0,0);target_tid=sc(178,0,0,0,0,0,0);words[1]=target_pid;words[2]=target_tid;
 process_path(task_root,target_pid,"/task");process_path(maps_path,target_pid,"/maps");
 {
  long region=sc(222,0,AT_ALLOC,3,0x21,-1,0);
  if((u64)region>=(u64)-4095){status=8;goto cleanup;}
  shared=(void*)region;words[7]=region;
  for(u32 i=0;i<AT_ALLOC;i+=4096)((volatile u8*)shared)[i]=0;
  long child=sc(220,0,0,0,0,0,0);if(!child)controller();words[3]=child;
  if(child<0){words[8]=sc(215,region,AT_ALLOC,0,0,0,0);status=9;goto cleanup;}
  int exit_status=0;long waited=0;
  for(u32 i=0;i<1000;i++){
   waited=sc(260,child,(long)&exit_status,0x40000001,0,0,0);
   if(waited==child){if(((u32)exit_status&255)==127)waited=-1000;break;}
   if(waited<0&&waited!=-4)break;delay(5000000);
  }
  words[4]=waited;words[5]=(u32)exit_status;
  if(waited!=child){words[8]=-999;status=10;goto done;}
  __asm__ volatile("dmb ish":::"memory");
  for(u32 i=0;i<AT_SIZE;i++)result[44+256+i]=shared[i];words[6]=AT_SIZE;
  words[8]=sc(215,region,AT_ALLOC,0,0,0,0);
  words[9]=sc(172,0,0,0,0,0,0);words[10]=sc(178,0,0,0,0,0,0);
  if(exit_status||words[8]||words[9]!=target_pid||words[10]!=target_tid)status=11;
 }
cleanup:
 /* Published callbacks/containers stay owned by the parent application. */
 if(report[7]!=MAIN_COMMIT_PUBLISHED){report[5]=trial_release();if(report[5])status=12;}
done:
 report[0]=TRIAL_MAGIC;report[1]=status;report[2]=trial_binding.status;
 report[3]=trial_prepare_words[5];report[4]=trial_bias;
 for(u32 i=0;i<32;i++)((u64*)((u8*)report+64))[i]=trial_prepare_words[i];
 for(u32 i=0;i<sizeof(trial_binding)/8;i++)((u64*)((u8*)report+320))[i]=((u64*)&trial_binding)[i];
 finish(status);
 __atomic_store_n(&self_state,status?3u:2u,__ATOMIC_RELEASE);
 return status;
}

static void finish(u32 status) {
    result[0]='A';result[1]='6';result[2]='7';result[3]='B';put32(result+4,1);
    for(u32 i=0;i<16;i++)result[8+i]=request[8+i];
    put32(result+24,status);put32(result+28,40);
    for(u32 i=0;i<8;i++)result[32+i]=request[28+i];
    put32(result+40,DATA_SIZE);
    for(u32 i=0;i<32;i++)for(u32 j=0;j<8;j++)result[44+i*8+j]=(u8)(words[i]>>(8*j));
}

const u8 *fl23_self_publish_reply(void){
 u32 state=__atomic_load_n(&self_state,__ATOMIC_ACQUIRE);
 return state==2||state==3?result:(const u8*)0;
}
