/* Native transaction core only. No constructor, USB entry, attach or detach.
 * Caller owns the stopped cohort and has checked complete structure guards.
 * On uncertain=true it MUST NOT treat this result as permission to resume.
 */
typedef unsigned long u64;
typedef unsigned int u32;
struct word { u64 address,before,after; };
struct outcome {
    u64 version,phase,checked,confirmed_after,rollback_verified,uncertain;
    u64 failed_index,poke_calls,peek_calls,committed;
    long error;
    u64 restored;
};
static long sc(long request,long pid,long address,long data) {
    register long x0 __asm__("x0")=request;
    register long x1 __asm__("x1")=pid;
    register long x2 __asm__("x2")=address;
    register long x3 __asm__("x3")=data;
    register long x8 __asm__("x8")=117;
    __asm__ volatile("svc 0":"+r"(x0):"r"(x1),"r"(x2),"r"(x3),"r"(x8):"memory");
    return x0;
}
static long peek(long pid,u64 address,u64 *data,struct outcome *r) {
    r->peek_calls++;
    return sc(2,pid,address,(long)data);
}
static long poke(long pid,u64 address,u64 data,struct outcome *r) {
    r->poke_calls++;
    return sc(5,pid,address,data);
}
long fl23_commit_words(long pid,const struct word *words,u32 count,struct outcome *r) {
    for(u32 i=0;i<sizeof(*r)/8;i++)((u64*)r)[i]=0;
    r->version=1;r->failed_index=(u64)-1;
    if(pid<=0||pid>=0x80000000L||!words||!count||count>2048){r->error=-22;return 1;}
    r->phase=1;
    for(u32 i=0;i<count;i++) {
#ifdef FL23_COMMIT_EXPIRED
        if(!(i&31)&&FL23_COMMIT_EXPIRED()){r->error=-110;r->failed_index=i;return 2;}
#endif
        if(!words[i].address||(words[i].address&7)||words[i].address>=0x8000000000UL-8){r->error=-22;r->failed_index=i;return 1;}
        if(i&&words[i-1].address>=words[i].address){r->error=-22;r->failed_index=i;return 1;}
        u64 value=0;long rc=peek(pid,words[i].address,&value,r);
        if(rc||value!=words[i].before){r->error=rc?rc:-116;r->failed_index=i;return 2;}
        r->checked++;
    }
    r->phase=2;
    for(u32 i=0;i<count;i++) {
#ifdef FL23_COMMIT_EXPIRED
        if(FL23_COMMIT_EXPIRED()){r->error=-110;r->failed_index=i;goto rollback;}
#endif
        long rc=poke(pid,words[i].address,words[i].after,r);u64 value=0;
        long pr=peek(pid,words[i].address,&value,r);
        if(!pr&&value==words[i].after)r->confirmed_after=i+1;
        if(rc||pr||value!=words[i].after) {
            r->error=rc?rc:(pr?pr:-5);r->failed_index=i;
            if(pr||(value!=words[i].before&&value!=words[i].after))r->uncertain=1;
            goto rollback;
        }
    }
    r->committed=1;r->phase=3;return 0;
rollback:
    r->phase=4;
    /* Check the entire confirmed prefix before undoing any word. */
    for(u32 i=0;i<r->confirmed_after;i++) {
        u64 value=0;long rc=peek(pid,words[i].address,&value,r);
        if(rc||value!=words[i].after){r->uncertain=1;r->phase=6;return 4;}
    }
    for(u32 n=r->confirmed_after;n;n--) {
        u32 i=n-1;u64 value=0;
        poke(pid,words[i].address,words[i].before,r);
        long rc=peek(pid,words[i].address,&value,r);
        if(rc||value!=words[i].before)r->uncertain=1;
        else r->restored++;
    }
    r->rollback_verified=!r->uncertain&&r->restored==r->confirmed_after;
    r->phase=r->uncertain?6:5;return r->uncertain?4:3;
}
