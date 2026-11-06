/* Internal self-contained names operation. Derives its own App bias and
 * candidate owner; caller supplies action+nonce, never PID/address/program.
 * No constructor, persistent write or CP access. */
#include "fl23_names_controller.c"
#define u64 names_loader_u64
#define memcpy names_loader_memcpy
#define memset names_loader_memset
#define SHA256 names_loader_SHA256
#define rr names_loader_rr
#define sha_block names_loader_sha_block
#define sha_init names_loader_sha_init
#define sha_add names_loader_sha_add
#define sha_end names_loader_sha_end
#include "fl23_a1_loader.c"
#undef sha_end
#undef sha_add
#undef sha_init
#undef sha_block
#undef rr
#undef SHA256
#undef memset
#undef memcpy
#undef u64
#include "names-bootstrap-profile.h"

struct names_bootstrap_report {
    u64 version, status, parent, bias, observed_pointer, current_known, current_enabled, target_enabled;
    u64 action, no_op, new_owner, owner_retained, owner_released, controller_called;
    long cleanup_error, error;
    struct a1_loaded loaded;
    struct names_controller_report controller;
};
_Static_assert(sizeof(struct names_bootstrap_report)==1104,"names bootstrap report ABI");
static int names_bootstrap_busy;

/* 0 inspect, 1 enable, 2 close. An already-target state is fully inspected,
 * never sent to the writer again. Module lifetime after close is deliberately
 * retained until process exit; closing is not permission for hot-unmap. */
long fl23_names_bootstrap_run(u64 action, const u8 *nonce, struct names_bootstrap_report *out) {
    if (action>2 || !nonce || !out || ((u64)out&7) || !names_address((u64)nonce,16) ||
        !names_address((u64)out,sizeof(*out)) || names_overlap((u64)nonce,16,(u64)out,sizeof(*out))) return -22;
    u8 saved_nonce[16];u32 nonzero=0;
    for (u32 i=0;i<16;i++) {saved_nonce[i]=nonce[i];nonzero|=nonce[i];}
    if (!nonzero || names_bootstrap_busy) return -16;
    names_bootstrap_busy=1;
    for (u32 i=0;i<sizeof(*out)/8;i++) ((u64*)out)[i]=0;
    out->version=1;out->action=action;out->parent=sc(172,0,0,0,0,0,0);
    u64 hint=0;long length=read_text("/proc/self/maps",maps,sizeof(maps));
    if (length<=0 || !locate(length,&out->bias,&hint)) {out->status=2;out->error=length<0?length:-116;goto done;}
    if (!checked_copy(out->bias+NAMES_CAPTION_POINTER,(u8*)&out->observed_pointer,8)) {
        out->status=3;out->error=-5;goto done;
    }
    u64 owner=0;
    if (out->observed_pointer==out->bias+NAMES_ORIGINAL_CAPTIONS) out->current_enabled=0;
    else {
        if (out->observed_pointer<NAMES_FULL_TABLE) {out->status=4;out->error=-116;goto done;}
        owner=out->observed_pointer-NAMES_FULL_TABLE;out->current_enabled=1;
        if ((owner&4095) || !names_address(owner,NAMES_OWNER_BYTES) ||
            names_overlap(owner,NAMES_OWNER_BYTES,out->bias,0x15000000)) {
            out->status=4;out->error=-116;goto done;
        }
        out->owner_retained=1;
    }
    out->target_enabled=action==0?out->current_enabled:action==1;
    out->no_op=action==0||out->target_enabled==out->current_enabled;
    if (!owner) {
        out->error=fl23_a1_load(names_package,sizeof(names_package),names_package_sha256,&out->loaded);
        if (out->error) {
            out->status=5;out->owner_retained=out->loaded.state==2;
            out->cleanup_error=out->loaded.cleanup_error;goto done;
        }
        owner=out->loaded.mapping;out->new_owner=owner;
        long bound=((long(*)(u64))out->loaded.entry)(out->bias+NAMES_ORIGINAL_HELP);
        if (bound) {out->status=5;out->error=bound;goto cleanup;}
    }
    {
        struct names_args args={1,out->parent,out->bias,owner,
            out->no_op?out->current_enabled:out->target_enabled,0,0,{0}};
        for (u32 i=0;i<16;i++) args.nonce[i]=saved_nonce[i];
        out->controller_called=1;
        out->error=out->no_op?fl23_names_controller_inspect(&args,&out->controller):
                            fl23_names_controller_run(&args,&out->controller);
        out->owner_retained|=out->controller.owner_retained||out->controller.pending_controller||
                             out->controller.transaction.tracer_required;
        if (out->error) out->status=6;
        else {out->current_known=1;out->current_enabled=out->target_enabled;}
    }
cleanup:
    if (out->new_owner && !out->owner_retained) {
        out->cleanup_error=sc(215,out->new_owner,NAMES_OWNER_BYTES,0,0,0,0);
        out->owner_released=!out->cleanup_error;
        if (out->cleanup_error) {out->status=7;out->error=out->cleanup_error;out->owner_retained=1;}
    }
done:
    if (!out->controller.pending_controller && !out->controller.transaction.tracer_required) names_bootstrap_busy=0;
    return out->error;
}
