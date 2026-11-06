/* Snapshot only. No POKEDATA, register writes, app calls or host PID input.
 * Child and parent communicate through a new shared anonymous mapping, never
 * a second writer to the service file. The parent alone encodes the response.
 */
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
    int stack_ready=fl23_stack_bounds_many(shared+AT_MAPS,(u32)h[5],stack_sp,(u32)count,stack_lo,stack_hi);
    for(u32 i=0;i<(u32)count;i++){
        u8 *row=shared+AT_ROWS+i*AT_ROW;u64 sp=load64(row+592+31*8),fp=load64(row+592+29*8);
        u32 frames=0,reason=0;
        if(!sp||sp&15||sp>=0x8000000000ULL||!stack_ready){put32(row+64,2);continue;}
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
    if(names_control_transaction()->tracer_required)names_control_hold();
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
