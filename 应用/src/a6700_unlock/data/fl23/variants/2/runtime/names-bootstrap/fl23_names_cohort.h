/* Current caller-owned audit snapshot, never a detached historical report.
 * The caller still owns capture/stop/detach. No attach, writes or resume.
 */
#include "fl23_stack_bounds.h"
static u64 names_u64(const unsigned char *p) {
    u64 v = 0;for (u32 i = 0; i < 8; i++) v |= (u64)p[i] << (i * 8);return v;
}
static u32 names_u32(const unsigned char *p) {
    u32 v = 0;for (u32 i = 0; i < 4; i++) v |= (u32)p[i] << (i * 8);return v;
}
static long names_self(long nr) {
    register long x0 __asm__("x0") = 0;
    register long x8 __asm__("x8") = nr;
    __asm__ volatile("svc 0" : "+r"(x0) : "r"(x8) : "memory", "cc");return x0;
}
static int names_reader_active(u64 address, int returning, const struct names_args *a) {
    for (u32 delta = 0; delta <= (returning ? 4u : 0u); delta += 4) {
        if (address < delta) continue;
        u64 p = address - delta;
        if (a->owner <= p && p < a->owner + NAMES_OWNER_BYTES) return 1;
        for (u32 i = 0; i < NAMES_READER_RANGES; i++) {
            if (a->bias + names_reader_ranges[i][0] <= p && p < a->bias + names_reader_ranges[i][1]) return 1;
        }
    }
    return 0;
}
static long names_reader_gate_stopped(const struct names_args *a, const struct names_report *r) {
    (void)r;
    const unsigned char *data = (const void *)a->cohort;
    const u64 *h = (const void *)data;
    u64 now = names_now();
    if (names_expired() || !now) return -110;
    if (h[0] != 0x0000000141333246ULL || h[1] != a->pid || !h[2] || h[2] >= 0x80000000ULL ||
        h[3] != (u64)names_self(172) || h[3] != (u64)names_self(178) || h[3] == a->pid || h[3] == h[2] ||
        !h[4] || h[4] > 512 || !h[5] || h[5] > 131072 || h[11] || h[12] != 1 || h[13] != 1 || h[14] ||
        h[15] != a->cohort_start || h[16] < h[15] || h[17] < h[16] || h[17] > now || h[18] || h[19] ||
        h[22] != (h[21] | (1ULL << 16)) || h[25] != 250000000ULL ||
        h[28] || h[29] || h[30] != h[4] || h[31] != h[4]) return -116;
    for (u32 i = 6; i <= 10; i++) if (h[i] != h[4]) return -116;
    for (u32 i = 0; i < 16; i++) if (data[208 + i] != a->nonce[i]) return -116;
    fl23_sb_word sp[512], lower[512], upper[512];
    for (u32 i = 0; i < h[4]; i++) sp[i] = names_u64(data + NAMES_COHORT_ROWS + i * 1536 + 592 + 31 * 8);
    if (!fl23_stack_bounds_many(data + 256, (u32)h[5], sp, (u32)h[4], lower, upper)) return -116;
    u32 have_pid = 0, have_service = 0, previous = 0, busy = 0;
    for (u32 i = 0; i < h[4]; i++) {
        const unsigned char *row = data + NAMES_COHORT_ROWS + i * 1536;
        u32 tid = names_u32(data + NAMES_COHORT_IDS + i * 4), frames = names_u32(row + 12);
        if (!tid || tid >= 0x80000000U || tid <= previous || tid == h[3] || names_u32(row) != tid ||
            names_u32(data + NAMES_COHORT_SECOND + i * 4) != tid ||
            names_u32(data + NAMES_COHORT_THIRD + i * 4) != tid ||
            names_u32(row + 4) != 15 || names_u32(row + 8) != 0x80057f || frames > 32 ||
            names_u64(row + 16) || names_u64(row + 24) || names_u64(row + 32) != tid ||
            names_u64(row + 40) || names_u64(row + 56) != 272 || names_u32(row + 64) ||
            names_u64(row + 1376)) return -116;
        previous = tid;have_pid |= tid == a->pid;have_service |= tid == h[2];
        u64 fp = names_u64(row + 592 + 29 * 8), lr = names_u64(row + 592 + 30 * 8), pc = names_u64(row + 592 + 32 * 8);
        if (!pc || (pc & 3) || pc >= 0x8000000000ULL || (lr & 3) || lr >= 0x8000000000ULL) return -116;
        busy |= names_reader_active(pc, 0, a) || names_reader_active(lr, 1, a);
        for (u32 j = 0; j < frames; j++) {
            u64 next = names_u64(row + 864 + j * 16), ret = names_u64(row + 872 + j * 16);
            if (!fl23_stack_frame(sp[i], fp, lower[i], upper[i]) || (next && next <= fp) ||
                (ret & 3) || ret >= 0x8000000000ULL) return -116;
            busy |= names_reader_active(ret, 1, a);fp = next;
        }
        if (fp) return -116;
    }
    if (!have_pid || !have_service || names_expired()) return -116;
    return busy ? -16 : 0;
}
