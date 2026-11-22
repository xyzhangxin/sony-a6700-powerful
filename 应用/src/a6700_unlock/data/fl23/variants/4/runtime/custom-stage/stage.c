#include "boot-abi.h"
#include "loader.c"
#include "custom-sound-stage-profile.h"
__attribute__((section(".text.stage_entry")))
long fl23_custom_sound_stage_entry(struct a1_boot_context *context) {
    if (!context || context->version != 1 || context->worker_state != 5 ||
        context->parent_state != 2 || context->pid != (u64)a1_sc(172,0,0,0,0,0,0) ||
        context->reserved[6] || context->reserved[7] || context->reserved[10]) return -22;
    u64 *diagnostic = (void *)((u8 *)context + CSND_AUTOLOAD_DIAG_OFFSET);
    if (__atomic_load_n(diagnostic, __ATOMIC_ACQUIRE)) return -16;
    struct a1_loaded coordinator = {0};
    long rc = fl23_a1_load(csnd_package, sizeof(csnd_package), csnd_digest, &coordinator);
    if (rc) return rc;
    rc = ((long (*)(struct a1_boot_context *))coordinator.entry)(context);
    if (diagnostic[0] == 0x314c444e554f5343ULL && !diagnostic[3 + 15]) {
        long release = a1_sc(215,coordinator.mapping,coordinator.bytes,0,0,0,0);
        diagnostic[35] = release;
        if (!release) return rc;
    }
    diagnostic[36] = coordinator.mapping;
    diagnostic[37] = coordinator.bytes;
    return rc ? rc : -115;
}
