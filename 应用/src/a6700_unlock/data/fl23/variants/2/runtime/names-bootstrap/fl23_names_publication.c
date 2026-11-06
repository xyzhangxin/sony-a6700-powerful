/* Internal names-only transaction over the existing stopped-cohort word
 * core. No constructor, device entry, attach/detach, allocator or CP access.
 * The owning controller must supply its actual same-cohort reader gate and
 * finish cache/trace/lifetime cleanup; this unit alone cannot resume threads.
 */
typedef unsigned long u64;
typedef unsigned int u32;
__attribute__((visibility("hidden"))) void *memcpy(void *to, const void *from, unsigned long bytes) {
    volatile unsigned char *d = to;const volatile unsigned char *s = from;
    for (unsigned long i = 0; i < bytes; i++) d[i] = s[i];return to;
}
static u64 names_deadline_start;
static u64 names_now(void) {
    u64 ts[2] = {0, 0};
    register long x0 __asm__("x0") = 1;
    register long x1 __asm__("x1") = (long)ts;
    register long x8 __asm__("x8") = 113;
    __asm__ volatile("svc 0" : "+r"(x0) : "r"(x1), "r"(x8) : "memory", "cc");
    if (x0 || ts[1] >= 1000000000ULL || ts[0] > 0xffffffffULL) return 0;
    return ts[0] * 1000000000ULL + ts[1];
}
static int names_expired(void) {
    u64 now = names_now();
    return !now || now < names_deadline_start || now - names_deadline_start >= 250000000ULL;
}
#define FL23_COMMIT_EXPIRED() names_expired()
#include "fl23_ptrace_words.c"
#undef FL23_COMMIT_EXPIRED
#include "names-publication-profile.h"

struct names_args {
    u64 version, pid, bias, owner, enabled, cohort, cohort_start;
    unsigned char nonce[16];
};
struct names_report {
    u64 version, lifecycle, word_count, guard_reads, owner_reads, independent_reads;
    u64 owner_retained, tracer_required, resume_authorized;
    long error, gate_before, gate_after;
    struct outcome outcome;
    struct word words[NAMES_WORDS];
};
typedef long (*names_reader_gate)(const struct names_args *, const struct names_report *);
static int names_overlap(u64 a, u64 n, u64 b, u64 size) {return a < b + size && b < a + n;}
static int names_address(u64 a, u64 n) {return a && a < 0x8000000000ULL && n <= 0x8000000000ULL - a;}
static u64 names_expected(const struct names_profile_word *w, const struct names_args *a, int after) {
    if (!after) return w->before + (w->old_relocation ? a->bias : 0);
    return w->after + (w->new_relocation == 1 ? a->bias : w->new_relocation == 2 ? a->owner : 0);
}
static void names_plan(const struct names_args *a, struct word *words) {
    for (u32 i = 0; i < NAMES_WORDS; i++) {
        const struct names_profile_word *w = names_profile_words + i;
        words[i].address = a->bias + w->offset;
        words[i].before = names_expected(w, a, !a->enabled);
        words[i].after = names_expected(w, a, a->enabled);
    }
}
static long names_peek(const struct names_args *a, u64 at, u64 *value) {
    if (names_expired()) return -110;
    *value = 0;return sc(2, a->pid, at, (long)value);
}
static long names_guards(const struct names_args *a, struct names_report *r) {
    for (u32 i = 0; i < NAMES_GUARDS; i++) {
        const struct names_profile_guard *g = names_profile_guards + i;
        u64 value = 0, expected = g->value + (g->relocation ? a->bias : 0);
        for (u32 j = 0; j < NAMES_WORDS; j++) {
            if (g->offset == names_profile_words[j].offset) {
                expected = r->words[j].before;break;
            }
        }
        long rc = names_peek(a, a->bias + g->offset, &value);r->guard_reads++;
        if (rc || value != expected) return rc ? rc : -116;
    }
    for (u32 i = 0; i < NAMES_RO_WORDS; i++) {
        u64 value = 0;
        long rc = names_peek(a, a->owner + names_ro_words[i].offset, &value);r->owner_reads++;
        if (rc || value != names_ro_words[i].value) return rc ? rc : -116;
    }
    u64 binding = 0;long rc = names_peek(a, a->owner + NAMES_BIND_OFFSET, &binding);r->owner_reads++;
    return rc ? rc : binding == a->bias + NAMES_ORIGINAL_HELP ? 0 : -116;
}

long fl23_names_commit_stopped(const struct names_args *input, names_reader_gate gate,
                              struct names_report *r) {
    if (!input || !r || ((u64)input & 7) || ((u64)r & 7) ||
        !names_address((u64)input, sizeof(*input)) || !names_address((u64)r, sizeof(*r)) ||
        names_overlap((u64)input, sizeof(*input), (u64)r, sizeof(*r))) return -22;
    struct names_args a = *input;
    if (a.version != 1 || !a.pid || a.pid >= 0x80000000ULL || a.enabled > 1 ||
        !a.bias || (a.bias & 4095) || !a.owner || (a.owner & 4095) || !a.cohort ||
        !a.cohort_start || !names_address(a.bias, 0x15000000) || !names_address(a.owner, NAMES_OWNER_BYTES) ||
        !names_address(a.cohort, NAMES_COHORT_BYTES) ||
        names_overlap(a.owner, NAMES_OWNER_BYTES, a.bias, 0x15000000) ||
        names_overlap(a.cohort, NAMES_COHORT_BYTES, a.bias, 0x15000000) ||
        names_overlap(a.cohort, NAMES_COHORT_BYTES, a.owner, NAMES_OWNER_BYTES) ||
        names_overlap((u64)r, sizeof(*r), a.cohort, NAMES_COHORT_BYTES) ||
        names_overlap((u64)input, sizeof(*input), a.cohort, NAMES_COHORT_BYTES) ||
        names_overlap((u64)r, sizeof(*r), a.bias, 0x15000000) ||
        names_overlap((u64)r, sizeof(*r), a.owner, NAMES_OWNER_BYTES) ||
        names_overlap((u64)input, sizeof(*input), a.bias, 0x15000000) ||
        names_overlap((u64)input, sizeof(*input), a.owner, NAMES_OWNER_BYTES)) return -22;
    u32 nonce = 0;for (u32 i = 0; i < 16; i++) nonce |= a.nonce[i];
    if (!nonce) return -22;
    for (u32 i = 0; i < sizeof(*r) / 8; i++) ((u64 *)r)[i] = 0;
    r->version = 1;r->lifecycle = 1;r->owner_retained = !a.enabled;
    names_deadline_start = a.cohort_start;
    if (!gate) {r->error = -96;return r->error;}
    names_plan(&a, r->words);r->word_count = NAMES_WORDS;
    r->gate_before = gate(&a, r);
    if (r->gate_before) {r->error = r->gate_before;return r->error;}
    struct word expected[NAMES_WORDS];names_plan(&a, expected);
    for (u32 i = 0; i < sizeof(expected) / 8; i++) {
        if (((u64 *)expected)[i] != ((u64 *)r->words)[i]) {r->error = -116;return r->error;}
    }
    r->error = names_guards(&a, r);if (r->error) return r->error;
    long rc = fl23_commit_words(a.pid, r->words, NAMES_WORDS, &r->outcome);
    if (r->outcome.poke_calls) r->tracer_required = 1;
    if (r->outcome.uncertain || (r->outcome.poke_calls && !r->outcome.committed && !r->outcome.rollback_verified)) {
        r->lifecycle = 4;r->owner_retained = 1;r->error = -117;return r->error;
    }
    if (r->outcome.committed) {r->lifecycle = 3;r->owner_retained = 1;}
    else if (r->outcome.rollback_verified) r->lifecycle = 2;
    if (r->outcome.poke_calls) {
        for (u32 i = 0; i < NAMES_WORDS; i++) {
            u64 value = 0;long read = names_peek(&a, r->words[i].address, &value);
            r->independent_reads++;
            if (read || value != (r->outcome.committed ? expected[i].after : expected[i].before)) {
                r->lifecycle = 4;r->owner_retained = 1;r->error = read ? read : -116;return r->error;
            }
        }
        r->gate_after = gate(&a, r);
        if (r->gate_after || names_expired()) {
            r->lifecycle = 4;r->owner_retained = 1;
            r->error = r->gate_after ? r->gate_after : -110;return r->error;
        }
    }
    /* resume_authorized deliberately remains zero. Full live-controller,
     * cache-contract and detach/final lifetime accounting are separate. */
    r->error = rc ? (r->outcome.error ? r->outcome.error : -5) : 0;
    return r->error;
}

#include "fl23_names_cohort.h"
/* Non-fixture entry: fixed native gate, no caller-supplied permission flag. */
long fl23_names_apply_stopped(const struct names_args *input, struct names_report *r) {
    return fl23_names_commit_stopped(input, names_reader_gate_stopped, r);
}
