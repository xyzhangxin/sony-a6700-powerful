/* ILCE-6700 2.00 local constructor adapter, offline development build.
 * Callsite-specific; no installer, file I/O, upload or imaging output setter.
 * Original constructor holds its settings lock across both calls. Its exact
 * hash-guarded frame leaves [SP+0x58,SP+0x68) unused. This per-frame record pairs
 * admission and resolution without a shared thread table or a global PP write.
 */
#include <stdint.h>
typedef uint32_t u32;
typedef uint64_t u64;
struct bridge_config {
    u64 magic;
    u32 abi, bytes;
    u64 bias;
    u32 enabled, reserved;
    u64 padding[4];
};
struct admission { u64 object; u32 selection, seal; };
_Static_assert(sizeof(struct bridge_config)==64, "config ABI");
_Static_assert(sizeof(struct admission)==16, "frame ABI");
__attribute__((section(".bridge_state"),used)) struct bridge_config bridge_config;
#define MAGIC 0x3141474232433641ULL
#define SEAL 0x42325031u
#define FRAME_OFFSET 0x58
typedef u32 (*eligible_fn)(u32,u32,u32,u32,u32);
typedef u32 (*resolver_fn)(u32,u32,u32,u32,u32,u32,u32,u32);
typedef const volatile u32 *(*getter_fn)(void);

static u32 get(u64 offset) {
    return *(((getter_fn)(uintptr_t)(bridge_config.bias+offset))());
}
static u32 slot_exists(u32 selection) {
    const volatile u32 *p=((getter_fn)(uintptr_t)(bridge_config.bias+0x2f928c8))();
    return p[selection-3]==1;
}
static u32 config_valid(void) {
    return bridge_config.magic==MAGIC && bridge_config.abi==1 &&
           bridge_config.bytes==64 && !bridge_config.reserved &&
           !bridge_config.padding[0] && !bridge_config.padding[1] &&
           !bridge_config.padding[2] && !bridge_config.padding[3];
}
static u32 photo_snapshot(u32 selection) {
    /* General-mode getter is derived from the producer of source +0x6fc.
     * It is a separate model from the eligibility_context getter. */
    return get(0x2f88d28)==0 && get(0x2f6f630)==2 && get(0x2f77a54)==1 &&
           get(0x2f75aa0)==0 && get(0x2f770cc)==1 && get(0x2f7b974)==1 &&
           get(0x2f929b8)==selection && selection>=3 && selection<=18 &&
           slot_exists(selection);
}

u32 bridge_eligibility_core(u32 context,u32 log,u32 pp,u32 mode,u32 look,
                           u64 frame,u64 object,u64 caller) {
    eligible_fn original=(eligible_fn)(uintptr_t)(bridge_config.bias+0x3216168);
    u32 exact=frame && !(frame&15) && object &&
              caller==bridge_config.bias+0x334e228;
    struct admission *record=(struct admission *)(uintptr_t)(frame+FRAME_OFFSET);
    if(exact) { record->object=0;record->selection=0;record->seal=0; }
    u32 selected=0, admitted=0;
    if(exact && config_valid() && __atomic_load_n(&bridge_config.enabled,__ATOMIC_ACQUIRE)==1 &&
       context==2 && log==0 && pp==1 && mode==1 && look==1) {
        selected=get(0x2f929b8);
        admitted=photo_snapshot(selected);
    }
    u32 result=original(admitted?3:context,log,admitted?12:pp,mode,look);
    if(admitted && result==1) {
        record->object=object;record->selection=selected;record->seal=SEAL;
    }
    return result;
}

u32 bridge_resolver_core(u32 direct,u32 pp,u32 link0,u32 link1,u32 link2,
                        u32 link3,u32 mode,u32 look,u64 frame,u64 object,u64 caller) {
    resolver_fn original=(resolver_fn)(uintptr_t)(bridge_config.bias+0x3215fc0);
    u32 admitted=0;
    if(frame && !(frame&15) && object && caller==bridge_config.bias+0x334e334) {
        struct admission *record=(struct admission *)(uintptr_t)(frame+FRAME_OFFSET);
        u64 saved_object=record->object;
        u32 selected=record->selection,seal=record->seal;
        /* Consume before entering any getter/original helper. A later call
         * cannot reuse the same admission. Disable affects NEW admissions;
         * a pair already admitted finishes with its latched binding. */
        record->object=0;record->selection=0;record->seal=0;
        if(config_valid() && seal==SEAL && saved_object==object && selected==link0 &&
           pp==1 && mode==1 && look==1 && photo_snapshot(selected) &&
           direct==get(0x2f77a7c) && link1==get(0x2f929e0) &&
           link2==get(0x2f92a08) && link3==get(0x2f92a30)) admitted=1;
    }
    return original(direct,admitted?12:pp,link0,link1,link2,link3,mode,look);
}
