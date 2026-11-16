/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef PHOTO_LUT_OVERRIDE_TRACE_H
#define PHOTO_LUT_OVERRIDE_TRACE_H
#include <stdint.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
#define PLUT_ABI 6u
#define PLUT_NEAR_BYTES 0x5000u
#define PLUT_DATA_BYTES 0x51000u
#define PLUT_MAP_BYTES (PLUT_NEAR_BYTES+PLUT_DATA_BYTES)
#define PLUT_CODE_BYTES 0x4000u
#define PLUT_EVENTS 1024u
#define PLUT_SITES 12u
#define PLUT_FIELDS 5u
#define PLUT_HELPERS 9u
#define PLUT_KINDS 10u
#define PLUT_VENEER_BYTES (16u*PLUT_HELPERS)
#define PLUT_MAGIC 0x314f525455373641ULL
#define PLUT_LOCATOR_MAGIC 0x314f4f4c55373641ULL
#define PLUT_TOKEN_BYTES 16u
#define PLUT_CAPTURE (1ULL<<63)
#define PLUT_GENERATION(x) (((x)>>32)&0x7fffffffULL)
#define PLUT_HEADER_BYTES 512u
#define PP1_OPEN (1ULL<<63)
#define PP1_RUNTIME_MAGIC 0x314f525055373641ULL
#define PP1_CONFIG_MAGIC 0x3141474232433641ULL
#define PP1_SEAL 0x42325031u

/* Identical to the separately validated constructor adapter's configuration. */
struct bridge_config {
    u64 magic;u32 abi,bytes;u64 bias;u32 enabled,reserved;u64 padding[4];
};
struct pp1_runtime {
    u64 magic,gate,pending_pairs,admitted,resolved,valid_frames,bad_frames,override_state;
};
struct pp1_diagnostic {
    u8 magic[4];u32 bytes;
    u64 general_pointer,context_pointer;
    u32 general_first,general_second,context_first,context_second;
    u32 enabled,flags;
    u64 gate,pending_pairs,admitted,resolved,valid_frames,bad_frames;
    u64 config_address,runtime_address,override_state,reserved;
};
extern struct bridge_config bridge_config;
extern struct pp1_runtime plut_pp1_runtime;
_Static_assert(sizeof(struct bridge_config)==64,"bridge config ABI");
_Static_assert(sizeof(struct pp1_runtime)==64,"runtime ABI");
_Static_assert(sizeof(struct pp1_diagnostic)==128,"diagnostic ABI");

enum plut_event_flags {
    PLUT_START_TIME=1, PLUT_END_TIME=2, PLUT_TID=4,
    PLUT_DETAIL=8
};

/* Exactly one writer per event. committed is published with release ordering.
 * The trace fills once and drops subsequent events, never reuses a live slot. */
struct plut_event {
    u32 committed, kind;
    u64 sequence, callsite, tid, start_ns, end_ns, message_address;
    u64 arguments[9];
    u32 fields[6];
    u32 before[PLUT_FIELDS][2], after[PLUT_FIELDS][2];
    u32 before_stable, after_stable, marker, flags;
    u8 detail[64];
    u32 detail_bytes, generation;
};
struct plut_trace {
    u64 magic;
    u32 abi, bytes;
    u8 token[16];
    u64 pid, bias, helpers[PLUT_HELPERS];
    u32 original[PLUT_SITES], patched[PLUT_SITES];
    u32 active, phase, faults, reserved0;
    /* One atomic value labels admitted events, even across a pause/resume.
     * bit63=capture; bits32..62=control generation; bits0..31=user marker. */
    u64 capture_control, model_addresses[PLUT_FIELDS];
    u64 reserved_slots, completed, dropped, inflight;
    u64 seen[PLUT_KINDS], skipped[PLUT_KINDS];
    u64 control_ns, control_tid;
    u32 control_flags, capacity, event_bytes, reserved1;
    u64 reserved2;
    struct plut_event events[PLUT_EVENTS];
};
/* Fixed text-tail veneers lead to code and this small locator. The immutable data
 * address points to separately allocated process-lifetime event storage. */
struct plut_locator {
    u64 magic;u32 abi,bytes;
    u64 state_address,state_bytes,pid,bias;
    u8 token[16];u32 near_bytes,data_bytes;u64 reserved;
};
/* Snapshot does not label adjacent model words as saved settings. */
struct plut_model { u32 id, first, adjacent, reserved; };
struct plut_response {
    u8 magic[4];
    u32 abi;
    u8 nonce[16];
    u32 action, status, bytes, flags;
    u64 pid, bias, resident;
    struct plut_model models[PLUT_FIELDS];
    struct plut_trace trace;
    struct pp1_diagnostic experiment;
};
struct plut_request {
    u8 magic[4];
    u32 abi;
    u8 nonce[16];
    u32 action, marker;
    u8 token[16];
};
enum plut_action {
    PLUT_SNAPSHOT=1, PLUT_INSTALL=2, PLUT_READ=3, PLUT_STOP=4,
    PLUT_PAUSE=5, PLUT_RESUME=6, PLUT_MARK=7,PLUT_ENABLE=8,PLUT_DISABLE=9
};
enum plut_status {
    PLUT_OK=0, PLUT_BAD_REQUEST=2, PLUT_MAPS=3, PLUT_MEMORY=4,
    PLUT_IDENTITY=5, PLUT_CHANGED=6, PLUT_IO=7, PLUT_OCCUPIED=8,
    PLUT_NO_TRACE=9, PLUT_OWNER=10, PLUT_NO_SPACE=11,
    PLUT_PROTECTION=12, PLUT_TRANSACTION=13, PLUT_TERMINAL=14,PLUT_ALLOCATION=15,
    PLUT_READINESS=16,PLUT_BUSY=17
};
enum plut_phase { PLUT_READY=1, PLUT_RUNNING=2, PLUT_STOPPED=3, PLUT_UNCERTAIN=4 };
_Static_assert(sizeof(struct plut_event)==320, "event ABI");
_Static_assert(__builtin_offsetof(struct plut_trace,events)==PLUT_HEADER_BYTES, "trace header ABI");
_Static_assert(sizeof(struct plut_trace)==328192, "trace ABI");
_Static_assert(sizeof(struct plut_response)==328464, "response ABI");
_Static_assert(sizeof(struct plut_request)==48, "request ABI");
_Static_assert(sizeof(struct plut_locator)==80, "locator layout");
#endif
