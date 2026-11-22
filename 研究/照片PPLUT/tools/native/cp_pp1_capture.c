/* SPDX-License-Identifier: GPL-3.0-only
 * One-shot A6700 2.00 CP observer. Recorder writes only its private buffer.
 */
typedef unsigned int u32;
typedef unsigned char u8;
enum { ARMED = 0x11, FREEZE = 2, LOST = 4, WRITING = 8, CAPACITY = 24, PHOTO = 5 };

struct row {
    u32 event, descriptor_generation, a, b, c;
};
struct trace {
    u32 state, count, reserved[6];
    struct row rows[CAPACITY];
};
struct query {
    u8 selector, status;
    unsigned short reserved;
    u32 operation, low, high, bytes, tag;
};

#define TRACE ((struct trace *)0x110000u)
_Static_assert(sizeof(struct trace) == 512, "capture ABI");
_Static_assert(sizeof(struct query) == 24, "direct CP query ABI");

void cp_pp1_record(u32 event, u32 a, u32 b, u32 c) {
    struct trace *trace = TRACE;
    u32 state = __atomic_load_n(&trace->state, __ATOMIC_RELAXED);
    if (state != ARMED && state != (ARMED | WRITING))
        return;
    state = ARMED;
    if (!__atomic_compare_exchange_n(&trace->state, &state, ARMED | WRITING, 1,
                                     __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) {
        /* A conflicting event invalidates the window. */
        __atomic_fetch_or(&trace->state, LOST, __ATOMIC_RELAXED);
        return;
    }
    if (trace->count < CAPACITY) {
        if (event == PHOTO) {
            const volatile u32 *ledger = (const volatile u32 *)0x19ca078u;
            for (u32 bank = 0; bank < 4; ++bank) {
                if (ledger[2 * bank] == a && ledger[2 * bank + 1] == 1)
                    c |= 1u << (8 + bank);
            }
        }
        struct row *row = &trace->rows[trace->count++];
        row->event = event;
        row->descriptor_generation = *(const volatile u32 *)0x15bf420u;
        row->a = a;
        row->b = b;
        row->c = c;
    } else {
        __atomic_fetch_or(&trace->state, LOST, __ATOMIC_RELAXED);
    }
    __atomic_fetch_and(&trace->state, ~WRITING, __ATOMIC_RELEASE);
}

void cp_pp1_query(struct query *query) {
    struct trace *trace = TRACE;
    u32 valid = !query->reserved && query->tag == 0x31544350u;
    u32 operation = query->operation;
    query->status = 0x80;
    query->low = query->high = query->bytes = 0;
    if (!valid)
        return;

    u32 state = 0;
    if (operation == 1 &&
        __atomic_compare_exchange_n(&trace->state, &state, WRITING, 0,
                                    __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) {
        __atomic_store_n(&trace->state, ARMED, __ATOMIC_RELEASE);
        query->status = 0x5a;
    } else if (operation == FREEZE) {
        state = ARMED;
        if (!__atomic_compare_exchange_n(&trace->state, &state, FREEZE, 1,
                                         __ATOMIC_ACQ_REL, __ATOMIC_RELAXED))
            return;
        typedef void (*clean)(u32, u32);
        ((clean)0x5fa164u)((u32)trace, sizeof(*trace));
        query->low = (u32)trace;
        query->bytes = sizeof(*trace);
        query->status = 0x5b;
    }
}
