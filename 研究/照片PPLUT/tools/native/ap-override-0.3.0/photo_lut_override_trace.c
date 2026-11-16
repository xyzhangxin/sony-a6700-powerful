/* SPDX-License-Identifier: GPL-3.0-only
 * PP1 constructor loading and local base/output argument experiment.
 * The two cluster-B parameter calls may adapt an observed photo PP1 tuple.
 * Native helpers still build the messages; no CP patch or firmware write.
 */
#include "photo_lut_override_trace.h"
__attribute__((section(".trace_state"), used)) struct plut_locator plut_locator;
__attribute__((section(".pp1_state"), used)) struct pp1_runtime plut_pp1_runtime;
static struct plut_trace *shared(void) {
    return(struct plut_trace *)(uintptr_t)__atomic_load_n(&plut_locator.state_address,__ATOMIC_RELAXED);
}
static long query(long number,long a,long b) {
    register long x0 __asm__("x0")=a,x1 __asm__("x1")=b,x8 __asm__("x8")=number;
    __asm__ volatile("svc #0":"+r"(x0):"r"(x1),"r"(x8):"memory","cc");return x0;
}
static u32 now(u64 *value) {
    struct {long seconds,nanoseconds;} t={0,0};
    if(query(113,1,(long)&t)||t.seconds<0||t.nanoseconds<0||t.nanoseconds>=1000000000L)return 0;
    *value=(u64)t.seconds*1000000000ULL+(u64)t.nanoseconds;return 1;
}
static u32 models(u32 to[PLUT_FIELDS][2]) {
    struct plut_trace *s=shared();u32 stable=0;
    for(u32 i=0;i<PLUT_FIELDS;i++)for(u32 j=0;j<2;j++)
        to[i][j]=__atomic_load_n((const u32 *)(uintptr_t)s->model_addresses[i]+j,__ATOMIC_RELAXED);
    for(u32 i=0;i<PLUT_FIELDS;i++){
        u32 equal=1;
        for(u32 j=0;j<2;j++)if(to[i][j]!=__atomic_load_n((const u32 *)(uintptr_t)s->model_addresses[i]+j,__ATOMIC_RELAXED))equal=0;
        if(equal)stable|=1u<<i;
    }
    return stable;
}
static struct plut_event *reserve(u32 kind,u64 caller,const u64 *args,u64 control) {
    struct plut_trace *s=shared();
    u64 index=__atomic_fetch_add(&s->reserved_slots,1,__ATOMIC_RELAXED);
    if(index>=PLUT_EVENTS){__atomic_fetch_add(&s->dropped,1,__ATOMIC_RELAXED);return 0;}
    __atomic_fetch_add(&s->inflight,1,__ATOMIC_RELAXED);
    struct plut_event *e=&s->events[index];
    e->kind=kind;e->sequence=index+1;e->callsite=kind==10?0:caller-s->bias-4;
    e->marker=(u32)control;e->generation=(u32)PLUT_GENERATION(control);
    for(u32 i=0;i<9;i++)e->arguments[i]=args[i];
    long tid=query(178,0,0);if(tid>0){e->tid=(u64)tid;e->flags|=PLUT_TID;}
    if(now(&e->start_ns))e->flags|=PLUT_START_TIME;
    e->before_stable=models(e->before);return e;
}
static struct plut_event *begin(u32 kind,u64 caller,const u64 *args) {
    struct plut_trace *s=shared();
    if(!__atomic_load_n(&s->active,__ATOMIC_ACQUIRE))return 0;
    __atomic_fetch_add(&s->seen[kind-1],1,__ATOMIC_RELAXED);
    u64 control=__atomic_load_n(&s->capture_control,__ATOMIC_ACQUIRE);
    if(!(control&PLUT_CAPTURE)){
        __atomic_fetch_add(&s->skipped[kind-1],1,__ATOMIC_RELAXED);return 0;
    }
    return reserve(kind,caller,args,control);
}
static void done(struct plut_event *e,u64 result) {
    if(!e)return;
    /* Parameter helpers return void; retain their bounded message outputs. */
    if(e->kind<7||e->kind==10){e->fields[0]=(u32)result;e->fields[1]=(u32)(result>>32);}
    e->after_stable=models(e->after);
    if(now(&e->end_ns))e->flags|=PLUT_END_TIME;
    __atomic_store_n(&e->committed,1,__ATOMIC_RELEASE);
    __atomic_fetch_add(&shared()->completed,1,__ATOMIC_RELAXED);
    __atomic_fetch_sub(&shared()->inflight,1,__ATOMIC_RELAXED);
}
static int bounded_pointer(u64 pointer,u32 size) {
    /* These pointers are consumed by the original callees in this same call.
     * Only canonical/aligned fixed-size copies are added, never a scan, probe
     * of arbitrary memory or lifetime check. Invalid telemetry is omitted;
     * the original callee still receives its original input. */
    return pointer>=4096&&pointer<0x8000000000ULL-size&&!(pointer&3);
}
static void copy(u8 *to,const void *from,u32 count) {
    const volatile u8 *p=from;for(u32 i=0;i<count;i++)to[i]=p[i];
}
static u32 word(const u8 *p) {
    return (u32)p[0]|((u32)p[1]<<8)|((u32)p[2]<<16)|((u32)p[3]<<24);
}
static u64 qword(const u8 *p) {return (u64)word(p)|((u64)word(p+4)<<32);}

void plut_trace_control(u32 action,u32 marker) {
    __atomic_store_n(&plut_pp1_runtime.override_state,0,__ATOMIC_RELEASE);
    struct plut_trace *s=shared();u64 old=__atomic_load_n(&s->capture_control,__ATOMIC_ACQUIRE);
    u64 enabled=action==PLUT_PAUSE?0:action==PLUT_RESUME?PLUT_CAPTURE:old&PLUT_CAPTURE;
    u64 next=enabled|(((PLUT_GENERATION(old)+1)&0x7fffffffULL)<<32)|
             (action==PLUT_PAUSE?(u32)old:marker);
    __atomic_store_n(&s->capture_control,next,__ATOMIC_RELEASE);
    s->control_flags=0;s->control_ns=0;s->control_tid=0;
    if(now(&s->control_ns))s->control_flags|=1;
    long tid=query(178,0,0);if(tid>0){s->control_tid=(u64)tid;s->control_flags|=2;}
    __atomic_fetch_add(&s->seen[9],1,__ATOMIC_RELAXED);
    u64 args[9]={action,marker,enabled!=0,0,0,0,0,0,0};done(reserve(10,0,args,next),0);
}
typedef u32 (*eligibility_fn)(u32,u32,u32,u32,u32);
typedef u32 (*resolver_fn)(u32,u32,u32,u32,u32,u32,u32,u32);
typedef u32 (*send_fn)(u32,u32,u32*,u32*);
typedef u32 (*submit_fn)(const void*);
typedef u64 (*lookup_fn)(u64,const u32*);
typedef u32 (*release_fn)(void);

u32 bridge_eligibility_core(u32,u32,u32,u32,u32,u64,u64,u64);
u32 bridge_resolver_core(u32,u32,u32,u32,u32,u32,u32,u32,u64,u64,u64);
struct admission {u64 object;u32 selection,seal;};
struct pp1_detail {
    u64 frame,object,caller,general_pointer;
    u32 general,context,enabled,seal_before,seal_after,selection_before,selection_after,reserved;
};
_Static_assert(sizeof(struct pp1_detail)==64,"PP1 event detail");
static int exact_frame(u64 frame,u64 object,u64 caller,u32 kind) {
    int good=frame>=4096&&frame<0x8000000000ULL-0x70&&!(frame&15)&&
             object>=4096&&object<0x8000000000ULL&&
             caller==bridge_config.bias+(kind==1?0x334e228:0x334e334);
    __atomic_fetch_add(good?&plut_pp1_runtime.valid_frames:&plut_pp1_runtime.bad_frames,1,__ATOMIC_RELAXED);
    return good;
}
static void close_gate(void) {
    __atomic_fetch_and(&plut_pp1_runtime.gate,~PP1_OPEN,__ATOMIC_ACQ_REL);
    __atomic_store_n(&bridge_config.enabled,0,__ATOMIC_RELEASE);
    __atomic_store_n(&plut_pp1_runtime.override_state,0,__ATOMIC_RELEASE);
}
static int enter_gate(void) {
    u64 old=__atomic_load_n(&plut_pp1_runtime.gate,__ATOMIC_ACQUIRE);
    while(old&PP1_OPEN){
        if((old&~PP1_OPEN)>=1024){close_gate();return 0;}
        if(__atomic_compare_exchange_n(&plut_pp1_runtime.gate,&old,old+1,0,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE))return 1;
    }
    return 0;
}
static void detail(struct plut_event *e,u64 frame,u64 object,u64 caller,
                   u32 enabled,u32 before,u32 after,u32 selection_before,u32 selection_after) {
    if(!e)return;
    typedef const volatile u32 *(*getter)(void);
    const volatile u32 *general=((getter)(uintptr_t)(bridge_config.bias+0x2f88d28))();
    const volatile u32 *context=((getter)(uintptr_t)(bridge_config.bias+0x2f6f630))();
    struct pp1_detail d={frame,object,caller,(u64)(uintptr_t)general,
        *general,*context,enabled,before,after,selection_before,selection_after,0};
    copy(e->detail,&d,sizeof(d));e->detail_bytes=sizeof(d);e->flags|=PLUT_DETAIL;
}
u32 plut_pp1_eligibility(u32 a,u32 b,u32 c,u32 d,u32 f,u64 frame,u64 object,u64 caller) {
    /* A new constructor invalidates the prior binding, including a rejected
     * photo/video transition. Resolver selection alone is not CP readiness. */
    __atomic_store_n(&plut_pp1_runtime.override_state,0,__ATOMIC_RELEASE);
    u64 args[9]={a,b,c,d,f,frame,object,caller,0};
    struct plut_event *e=begin(1,caller,args);
    int exact=exact_frame(frame,object,caller,1);
    if(!e)close_gate(); /* No new unobserved adaptation after pause/full/stop. */
    int entered=exact&&e&&enter_gate();
    u32 enabled=__atomic_load_n(&bridge_config.enabled,__ATOMIC_ACQUIRE);
    u32 result,seal=0,selection=0;
    if(entered){
        result=bridge_eligibility_core(a,b,c,d,f,frame,object,caller);
        struct admission *record=(struct admission *)(uintptr_t)(frame+0x58);
        seal=record->seal;selection=record->selection;
        if(seal==PP1_SEAL&&record->object==object){
            __atomic_fetch_add(&plut_pp1_runtime.pending_pairs,1,__ATOMIC_RELEASE);
            __atomic_fetch_add(&plut_pp1_runtime.admitted,1,__ATOMIC_RELAXED);
        }
        /* Closing the gate and observing zero readers also observes the
         * pending increment above. STOP refuses to restore while it is set. */
        __atomic_fetch_sub(&plut_pp1_runtime.gate,1,__ATOMIC_RELEASE);
    }else result=((eligibility_fn)(uintptr_t)shared()->helpers[0])(a,b,c,d,f);
    detail(e,frame,object,caller,enabled,0,seal,0,selection);
    done(e,result);return result;
}
u32 plut_pp1_resolver(u32 a,u32 b,u32 c,u32 d,u32 f,u32 g,u32 h,u32 i,
                      u64 frame,u64 object,u64 caller) {
    u64 args[9]={a,b,c,d,f,g,h,i,frame};
    struct plut_event *e=begin(2,caller,args);
    int exact=exact_frame(frame,object,caller,2);
    u32 before=0,selection=0,owned=0;
    if(exact&&__atomic_load_n(&plut_pp1_runtime.pending_pairs,__ATOMIC_ACQUIRE)){
        struct admission *record=(struct admission *)(uintptr_t)(frame+0x58);
        before=record->seal;selection=record->selection;
        owned=before==PP1_SEAL&&record->object==object;
        if(!owned){before=0;selection=0;}
    }
    u32 enabled=__atomic_load_n(&bridge_config.enabled,__ATOMIC_ACQUIRE);
    u32 result=owned?bridge_resolver_core(a,b,c,d,f,g,h,i,frame,object,caller):
        ((resolver_fn)(uintptr_t)shared()->helpers[1])(a,b,c,d,f,g,h,i);
    if(owned){
        u64 control=__atomic_load_n(&shared()->capture_control,__ATOMIC_ACQUIRE);
        if(e&&selection>=17&&selection<=18&&result==selection&&enabled&&
           __atomic_load_n(&bridge_config.enabled,__ATOMIC_ACQUIRE)&&
           (__atomic_load_n(&plut_pp1_runtime.gate,__ATOMIC_ACQUIRE)&PP1_OPEN)&&
           (control&PLUT_CAPTURE)&&e->generation==PLUT_GENERATION(control))
            __atomic_store_n(&plut_pp1_runtime.override_state,
                ((u64)e->generation<<32)|((u64)selection<<1)|1,__ATOMIC_RELEASE);
        else __atomic_store_n(&plut_pp1_runtime.override_state,0,__ATOMIC_RELEASE);
        __atomic_fetch_add(&plut_pp1_runtime.resolved,1,__ATOMIC_RELAXED);
        __atomic_fetch_sub(&plut_pp1_runtime.pending_pairs,1,__ATOMIC_RELEASE);
    }
    detail(e,frame,object,caller,enabled,before,0,selection,0);
    done(e,result);return result;
}
u32 plut_trace_send(u32 selection,u32 exists,u32 *handle,u32 *bytes) {
    u64 args[9]={selection,exists,(u64)(uintptr_t)handle,(u64)(uintptr_t)bytes,0,0,0,0,0};
    struct plut_event *e=begin(3,(u64)__builtin_return_address(0),args);
    u32 result=((send_fn)(uintptr_t)shared()->helpers[2])(selection,exists,handle,bytes);
    if(e&&bounded_pointer((u64)(uintptr_t)handle,4)&&bounded_pointer((u64)(uintptr_t)bytes,4)){
        copy(e->detail,handle,4);copy(e->detail+4,bytes,4);e->detail_bytes=8;e->flags|=PLUT_DETAIL;
        e->fields[2]=word(e->detail);e->fields[3]=word(e->detail+4);
    }
    u64 binding=__atomic_load_n(&plut_pp1_runtime.override_state,__ATOMIC_ACQUIRE);
    if((binding&1)&&((binding>>1)&0x3f)==selection&&
       (!e||!e->detail_bytes||result!=1||!exists||!e->fields[2]||
        e->fields[2]==0xffffffffu||e->fields[3]!=48128u))close_gate();
    done(e,result);return result;
}
u32 plut_trace_submit(const void *request) {
    u64 args[9]={(u64)(uintptr_t)request,0,0,0,0,0,0,0,0};
    struct plut_event *e=begin(4,(u64)__builtin_return_address(0),args);
    if(e&&bounded_pointer((u64)(uintptr_t)request,40)){
        copy(e->detail,request,40);
        u64 source=qword(e->detail),destination=qword(e->detail+8);
        if(bounded_pointer(source,8)&&bounded_pointer(destination,8)){
            copy(e->detail+40,(const void *)(uintptr_t)source,8);
            copy(e->detail+48,(const void *)(uintptr_t)destination,8);
            e->detail_bytes=56;e->flags|=PLUT_DETAIL;
            e->fields[2]=word(e->detail+40);e->fields[3]=word(e->detail+48);
            e->fields[4]=word(e->detail+16);e->fields[5]=word(e->detail+20);
        }else{for(u32 i=0;i<40;i++)e->detail[i]=0;}
    }
    u32 result=((submit_fn)(uintptr_t)shared()->helpers[3])(request);
    done(e,result);return result;
}
u64 plut_trace_callback_lookup(u64 container,const u32 *cookie) {
    u64 args[9]={container,(u64)(uintptr_t)cookie,0,0,0,0,0,0,0};
    struct plut_event *e=begin(5,(u64)__builtin_return_address(0),args);
    if(e&&bounded_pointer((u64)(uintptr_t)cookie,4)){
        copy(e->detail,cookie,4);e->detail_bytes=4;e->flags|=PLUT_DETAIL;e->fields[2]=word(e->detail);
    }
    u64 result=((lookup_fn)(uintptr_t)shared()->helpers[4])(container,cookie);
    done(e,result);return result;
}
u32 plut_trace_release(void) {
    /* Never retain an admitted binding across an observed resource release. */
    __atomic_store_n(&plut_pp1_runtime.override_state,0,__ATOMIC_RELEASE);
    u64 args[9]={0,0,0,0,0,0,0,0,0};
    struct plut_event *e=begin(6,(u64)__builtin_return_address(0),args);
    u32 result=((release_fn)(uintptr_t)shared()->helpers[5])();done(e,result);return result;
}

/* Both clusters are observed. Only B, which was hit by both recorded photo
 * loads, is eligible for local argument adaptation. No message pointer is
 * retained beyond its original helper call. */
typedef void (*base_fn)(u32,u32,u32,u32,u32,u32,u32,u32,u32,u8*);
typedef void (*output_fn)(u32,u32,u32,u32,u32,u32,u8*);
typedef void (*data_fn)(u32,u32,u32,u32,u32,u32,const char*,u32,u8*);
struct parameter_detail {
    u64 general_pointer;
    u32 general_before,general_after,context_before,context_after;
    u8 message_before[16],message_after[16];
    u32 stable_flags,override_slot;
};
_Static_assert(sizeof(struct parameter_detail)==64,"parameter detail ABI");
static void parameter_modes(struct parameter_detail *d,u32 after) {
    typedef const volatile u32 *(*getter)(void);
    const volatile u32 *g=((getter)(uintptr_t)(bridge_config.bias+0x2f88d28))();
    const volatile u32 *c=((getter)(uintptr_t)(bridge_config.bias+0x2f6f630))();
    if(!after)d->general_pointer=(u64)(uintptr_t)g;
    if((u64)(uintptr_t)g==bridge_config.bias+0x8d7ea50){
        if(after)d->general_after=*g;else d->general_before=*g;
        d->stable_flags|=after?4:1;
    }
    if((u64)(uintptr_t)c==bridge_config.bias+0x8d2e298){
        if(after)d->context_after=*c;else d->context_before=*c;
        d->stable_flags|=after?8:2;
    }
    if(after){
        if((d->stable_flags&5)==5&&d->general_before==d->general_after)d->stable_flags|=16;
        if((d->stable_flags&10)==10&&d->context_before==d->context_after)d->stable_flags|=32;
    }
}
static void parameter_before(struct plut_event *e,u8 *m,struct parameter_detail *d) {
    if(!e)return;
    e->message_address=(u64)(uintptr_t)m;
    parameter_modes(d,0);
    if(bounded_pointer((u64)(uintptr_t)m,16)){
        copy(d->message_before,m,16);d->stable_flags|=64;
    }
}
static void parameter_after(struct plut_event *e,u8 *m,struct parameter_detail *d) {
    if(!e)return;
    if(bounded_pointer((u64)(uintptr_t)m,16)){
        copy(d->message_after,m,16);d->stable_flags|=128;
        for(u32 i=0;i<5;i++)e->fields[i]=d->message_after[8+i];
    }
    parameter_modes(d,1);
    copy(e->detail,d,sizeof(*d));e->detail_bytes=64;e->flags|=PLUT_DETAIL;
}
static u32 parameter_override_slot(struct plut_event *e) {
    typedef const volatile u32 *(*getter)(void);
    u64 state=__atomic_load_n(&plut_pp1_runtime.override_state,__ATOMIC_ACQUIRE);
    u32 slot=(u32)((state>>1)&0x3f);
    u64 control=__atomic_load_n(&shared()->capture_control,__ATOMIC_ACQUIRE);
    if(!e||!__atomic_load_n(&shared()->active,__ATOMIC_ACQUIRE)||
       !(control&PLUT_CAPTURE)||e->generation!=PLUT_GENERATION(control)||
       (state>>32)!=e->generation||!(state&1)||slot<17||slot>18||
       !__atomic_load_n(&bridge_config.enabled,__ATOMIC_ACQUIRE)||
       !(__atomic_load_n(&plut_pp1_runtime.gate,__ATOMIC_ACQUIRE)&PP1_OPEN))return 0;
    const volatile u32 *general=((getter)(uintptr_t)(bridge_config.bias+0x2f88d28))();
    const volatile u32 *context=((getter)(uintptr_t)(bridge_config.bias+0x2f6f630))();
    const volatile u32 *pp=((getter)(uintptr_t)(bridge_config.bias+0x2f77a54))();
    const volatile u32 *log=((getter)(uintptr_t)(bridge_config.bias+0x2f75aa0))();
    const volatile u32 *cin=((getter)(uintptr_t)(bridge_config.bias+0x2f770cc))();
    const volatile u32 *look=((getter)(uintptr_t)(bridge_config.bias+0x2f7b974))();
    const volatile u32 *lut=((getter)(uintptr_t)(bridge_config.bias+0x2f929b8))();
    const volatile u32 *slots=((getter)(uintptr_t)(bridge_config.bias+0x2f928c8))();
    if((u64)(uintptr_t)general!=bridge_config.bias+0x8d7ea50||
       (u64)(uintptr_t)context!=bridge_config.bias+0x8d2e298||
       (u64)(uintptr_t)pp!=bridge_config.bias+0x8d37d68||
       (u64)(uintptr_t)log!=bridge_config.bias+0x8d36d58||
       (u64)(uintptr_t)cin!=bridge_config.bias+0x8d377c8||
       (u64)(uintptr_t)look!=bridge_config.bias+0x8d3a024||
       (u64)(uintptr_t)lut!=bridge_config.bias+0x8d91630||
       (u64)(uintptr_t)slots!=bridge_config.bias+0x8d90bb0)return 0;
    /* Double sampling detects changes; it is not a settings lock. */
    for(u32 pass=0;pass<2;pass++)
        if(*general!=0||*context!=2||*pp!=1||*log!=0||*cin!=1||*look!=1||
           *lut!=slot||slots[slot-3]!=1)return 0;
    if(state!=__atomic_load_n(&plut_pp1_runtime.override_state,__ATOMIC_ACQUIRE)||
       control!=__atomic_load_n(&shared()->capture_control,__ATOMIC_ACQUIRE)||
       !__atomic_load_n(&bridge_config.enabled,__ATOMIC_ACQUIRE))return 0;
    return slot;
}
void plut_trace_base(u32 lut,u32 exists,u32 log,u32 input_gamut,u32 gamut,
                     u32 pp,u32 general_mode,u32 cinematic_mode,u32 look,u8 *m) {
    u64 args[9]={lut,exists,log,input_gamut,gamut,pp,general_mode,cinematic_mode,look};
    struct parameter_detail observed={0};
    struct plut_event *e=begin(7,(u64)__builtin_return_address(0),args);
    parameter_before(e,m,&observed);
    if(!e)close_gate();
    int entered=e&&e->callsite==0x34f4780&&enter_gate();
    u32 slot=entered?parameter_override_slot(e):0;
    if(slot&&lut==0&&exists==0&&log==0&&input_gamut==1&&gamut==0&&
       pp==1&&general_mode==0&&cinematic_mode==1&&look==1){
        lut=slot;exists=1;pp=12;general_mode=1;observed.override_slot=slot;
    }
    ((base_fn)(uintptr_t)shared()->helpers[6])(lut,exists,log,input_gamut,gamut,
        pp,general_mode,cinematic_mode,look,m);
    if(entered)__atomic_fetch_sub(&plut_pp1_runtime.gate,1,__ATOMIC_RELEASE);
    parameter_after(e,m,&observed);done(e,0);
}
void plut_trace_outputs(u32 pp,u32 secondary,u32 cinematic_mode,u32 look,
                        u32 general_mode,u32 main_policy,u8 *m) {
    u64 args[9]={pp,secondary,cinematic_mode,look,general_mode,main_policy,0,0,0};
    struct parameter_detail observed={0};
    struct plut_event *e=begin(8,(u64)__builtin_return_address(0),args);
    parameter_before(e,m,&observed);
    if(!e)close_gate();
    int entered=e&&e->callsite==0x34f480c&&enter_gate();
    u32 slot=entered?parameter_override_slot(e):0;
    if(slot&&pp==1&&secondary==0&&cinematic_mode==1&&look==1&&general_mode==0&&main_policy<=1){
        pp=12;general_mode=1;observed.override_slot=slot;
    }
    ((output_fn)(uintptr_t)shared()->helpers[7])(pp,secondary,cinematic_mode,look,general_mode,main_policy,m);
    if(entered)__atomic_fetch_sub(&plut_pp1_runtime.gate,1,__ATOMIC_RELEASE);
    parameter_after(e,m,&observed);done(e,0);
}
void plut_trace_data(u32 a,u32 b,u32 c,u32 d,u32 f,u32 g,const char *name,u32 h,u8 *m) {
    u64 args[9]={a,b,c,d,f,g,(u64)(uintptr_t)name,h,0};
    struct plut_event *e=begin(9,(u64)__builtin_return_address(0),args);
    if(e)e->message_address=(u64)(uintptr_t)m;
    ((data_fn)(uintptr_t)shared()->helpers[8])(a,b,c,d,f,g,name,h,m);
    if(e&&bounded_pointer((u64)(uintptr_t)m,95)){
        for(u32 n=0;n<6;n++)e->fields[n]=word(m+8+4*n);
        copy(e->detail,m+0x20,63);e->detail[63]=0;
        e->detail_bytes=64;e->flags|=PLUT_DETAIL;
    }
    done(e,0);
}
