/* SPDX-License-Identifier: GPL-3.0-only
 * Bounded observation, no allocation, I/O, LUT buffer dereference or setter.
 * Each hook calls its original helper exactly once with unchanged arguments.
 * Raw clock_gettime/gettid syscalls do not touch libc errno. Their failure only
 * removes telemetry validity flags. Paused/full paths avoid these syscalls.
 */
#include "photo_lut_trace.h"
__attribute__((section(".trace_state"), used)) struct plut_locator plut_locator;
static struct plut_trace *shared(void) {
    return(struct plut_trace *)(uintptr_t)__atomic_load_n(&plut_locator.state_address,__ATOMIC_RELAXED);
}

static long query(long number,long a,long b) {
    register long x0 __asm__("x0")=a, x1 __asm__("x1")=b;
    register long x8 __asm__("x8")=number;
    __asm__ volatile("svc #0":"+r"(x0):"r"(x1),"r"(x8):"memory","cc");
    return x0;
}
static u32 now(u64 *value) {
    struct {long seconds,nanoseconds;} t={0,0};
    if(query(113,1,(long)&t)||t.seconds<0||t.nanoseconds<0||t.nanoseconds>=1000000000L)return 0;
    *value=(u64)t.seconds*1000000000ULL+(u64)t.nanoseconds;return 1;
}
static u32 models(u32 to[PLUT_FIELDS][2]) {
    /* Fixed process-lifetime globals, validated before publication. This is a
     * bounded double read, not a locked transaction or a CP readiness check. */
    struct plut_trace *s=shared();u32 stable=0;
    for(u32 i=0;i<PLUT_FIELDS;i++){
        const u32 *p=(const u32 *)(uintptr_t)s->model_addresses[i];
        for(u32 j=0;j<2;j++)to[i][j]=__atomic_load_n(p+j,__ATOMIC_RELAXED);
    }
    for(u32 i=0;i<PLUT_FIELDS;i++){
        const u32 *p=(const u32 *)(uintptr_t)s->model_addresses[i];u32 equal=1;
        for(u32 j=0;j<2;j++)if(to[i][j]!=__atomic_load_n(p+j,__ATOMIC_RELAXED))equal=0;
        if(equal)stable|=1u<<i;
    }
    return stable;
}
static struct plut_event *reserve(u32 kind,u64 caller,const u64 *arguments,u8 *message,u64 control) {
    struct plut_trace *s=shared();
    u64 index=__atomic_fetch_add(&s->reserved_slots,1,__ATOMIC_RELAXED);
    if(index>=PLUT_EVENTS){__atomic_fetch_add(&s->dropped,1,__ATOMIC_RELAXED);return 0;}
    __atomic_fetch_add(&s->inflight,1,__ATOMIC_RELAXED);
    struct plut_event *e=&s->events[index];
    e->kind=kind;e->sequence=index+1;e->callsite=kind==4?0:caller-s->bias-4;
    e->message_address=(u64)(uintptr_t)message;e->marker=(u32)control;
    e->generation=(u32)PLUT_GENERATION(control);
    long tid=query(178,0,0);if(tid>0){e->tid=(u64)tid;e->flags|=PLUT_TID;}
    if(now(&e->start_ns))e->flags|=PLUT_START_TIME;
    for(u32 i=0;i<9;i++)e->arguments[i]=arguments[i];
    e->before_stable=models(e->before);
    return e;
}
static struct plut_event *begin(u32 kind,u64 caller,const u64 *arguments,u8 *message) {
    struct plut_trace *s=shared();
    if(!__atomic_load_n(&s->active,__ATOMIC_ACQUIRE))return 0;
    __atomic_fetch_add(&s->seen[kind-1],1,__ATOMIC_RELAXED);
    u64 control=__atomic_load_n(&s->capture_control,__ATOMIC_ACQUIRE);
    if(!(control&PLUT_CAPTURE)){
        __atomic_fetch_add(&s->skipped[kind-1],1,__ATOMIC_RELAXED);return 0;
    }
    return reserve(kind,caller,arguments,message,control);
}
static void done(struct plut_event *e,const u8 *message,u32 kind) {
    if(!e)return;
    if(kind==3){
        for(u32 i=0;i<6;i++){
            u32 v=0;for(u32 j=0;j<4;j++)v|=(u32)message[8+i*4+j]<<(8*j);
            e->fields[i]=v;
        }
        /* Original helper wrote 63 bytes via strncpy. Copy only its output,
         * never dereference the filename pointer or the LUT data buffers. */
        u32 length=63;
        for(u32 i=0;i<63;i++){
            e->name[i]=message[0x20+i];
            if(length==63&&!e->name[i])length=i;
        }
        e->name[63]=0;e->name_bytes=length;e->flags|=PLUT_NAME;
        if(length==63)e->flags|=PLUT_NAME_TRUNCATED;
    }else if(kind!=4){
        for(u32 i=0;i<5;i++)e->fields[i]=message[8+i];
    }
    e->after_stable=models(e->after);
    if(now(&e->end_ns))e->flags|=PLUT_END_TIME;
    __atomic_store_n(&e->committed,1,__ATOMIC_RELEASE);
    __atomic_fetch_add(&shared()->completed,1,__ATOMIC_RELAXED);
    __atomic_fetch_sub(&shared()->inflight,1,__ATOMIC_RELAXED);
}

/* Fixed owner-checked actions only; never re-patch instructions or reuse slots.
 * In-flight events keep their admission label. Pause permits late commits and
 * does not claim quiescence. */
void plut_trace_control(u32 action,u32 marker) {
    struct plut_trace *s=shared();
    u64 old=__atomic_load_n(&s->capture_control,__ATOMIC_ACQUIRE);
    u64 enabled=action==PLUT_PAUSE?0:action==PLUT_RESUME?PLUT_CAPTURE:old&PLUT_CAPTURE;
    u64 next=enabled|(((PLUT_GENERATION(old)+1)&0x7fffffffULL)<<32)|
             (action==PLUT_PAUSE?(u32)old:marker);
    __atomic_store_n(&s->capture_control,next,__ATOMIC_RELEASE);
    s->control_flags=0;s->control_ns=0;s->control_tid=0;
    if(now(&s->control_ns))s->control_flags|=1;
    long tid=query(178,0,0);if(tid>0){s->control_tid=(u64)tid;s->control_flags|=2;}
    __atomic_fetch_add(&s->seen[3],1,__ATOMIC_RELAXED);
    u64 args[9]={action,marker,enabled!=0,0,0,0,0,0,0};
    done(reserve(4,0,args,0,next),0,4);
}

typedef void (*base_fn)(u32,u32,u32,u32,u32,u32,u32,u32,u32,u8*);
typedef void (*output_fn)(u32,u32,u32,u32,u32,u32,u8*);
typedef void (*data_fn)(u32,u32,u32,u32,u32,u32,const char*,u32,u8*);

void plut_trace_base(u32 a,u32 b,u32 c,u32 d,u32 e,u32 f,u32 g,u32 h,u32 i,u8 *m) {
    u64 args[9]={a,b,c,d,e,f,g,h,i};
    struct plut_event *event=begin(1,(u64)__builtin_return_address(0),args,m);
    ((base_fn)(uintptr_t)shared()->helpers[0])(a,b,c,d,e,f,g,h,i,m);
    done(event,m,1);
}
void plut_trace_outputs(u32 a,u32 b,u32 c,u32 d,u32 e,u32 f,u8 *m) {
    u64 args[9]={a,b,c,d,e,f,0,0,0};
    struct plut_event *event=begin(2,(u64)__builtin_return_address(0),args,m);
    ((output_fn)(uintptr_t)shared()->helpers[1])(a,b,c,d,e,f,m);
    done(event,m,2);
}
void plut_trace_data(u32 a,u32 b,u32 c,u32 d,u32 e,u32 f,const char *name,u32 h,u8 *m) {
    u64 args[9]={a,b,c,d,e,f,(u64)(uintptr_t)name,h,0};
    struct plut_event *event=begin(3,(u64)__builtin_return_address(0),args,m);
    ((data_fn)(uintptr_t)shared()->helpers[2])(a,b,c,d,e,f,name,h,m);
    done(event,m,3);
}
