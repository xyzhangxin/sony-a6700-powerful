/* SPDX-License-Identifier: GPL-3.0-only
 * AP selection/transfer lifecycle observation. Fixed callsites and bounded
 * native input/output structures only; never reads LUT pixel data or CP RAM.
 * Each wrapper calls its original exactly once, with unchanged arguments and
 * return value. No allocation, file I/O, waiting, setter or color override.
 */
#include "photo_lut_lifecycle_trace.h"
__attribute__((section(".trace_state"), used)) struct plut_locator plut_locator;
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
    e->kind=kind;e->sequence=index+1;e->callsite=kind==7?0:caller-s->bias-4;
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
    e->fields[0]=(u32)result;e->fields[1]=(u32)(result>>32);
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
    struct plut_trace *s=shared();u64 old=__atomic_load_n(&s->capture_control,__ATOMIC_ACQUIRE);
    u64 enabled=action==PLUT_PAUSE?0:action==PLUT_RESUME?PLUT_CAPTURE:old&PLUT_CAPTURE;
    u64 next=enabled|(((PLUT_GENERATION(old)+1)&0x7fffffffULL)<<32)|
             (action==PLUT_PAUSE?(u32)old:marker);
    __atomic_store_n(&s->capture_control,next,__ATOMIC_RELEASE);
    s->control_flags=0;s->control_ns=0;s->control_tid=0;
    if(now(&s->control_ns))s->control_flags|=1;
    long tid=query(178,0,0);if(tid>0){s->control_tid=(u64)tid;s->control_flags|=2;}
    __atomic_fetch_add(&s->seen[6],1,__ATOMIC_RELAXED);
    u64 args[9]={action,marker,enabled!=0,0,0,0,0,0,0};done(reserve(7,0,args,next),0);
}
typedef u32 (*eligibility_fn)(u32,u32,u32,u32,u32);
typedef u32 (*resolver_fn)(u32,u32,u32,u32,u32,u32,u32,u32);
typedef u32 (*send_fn)(u32,u32,u32*,u32*);
typedef u32 (*submit_fn)(const void*);
typedef u64 (*lookup_fn)(u64,const u32*);
typedef u32 (*release_fn)(void);

u32 plut_trace_eligibility(u32 a,u32 b,u32 c,u32 d,u32 f) {
    u64 args[9]={a,b,c,d,f,0,0,0,0};
    struct plut_event *e=begin(1,(u64)__builtin_return_address(0),args);
    u32 result=((eligibility_fn)(uintptr_t)shared()->helpers[0])(a,b,c,d,f);
    done(e,result);return result;
}
u32 plut_trace_resolver(u32 a,u32 b,u32 c,u32 d,u32 f,u32 g,u32 h,u32 i) {
    u64 args[9]={a,b,c,d,f,g,h,i,0};
    struct plut_event *e=begin(2,(u64)__builtin_return_address(0),args);
    u32 result=((resolver_fn)(uintptr_t)shared()->helpers[1])(a,b,c,d,f,g,h,i);
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
    u64 args[9]={0,0,0,0,0,0,0,0,0};
    struct plut_event *e=begin(6,(u64)__builtin_return_address(0),args);
    u32 result=((release_fn)(uintptr_t)shared()->helpers[5])();done(e,result);return result;
}
