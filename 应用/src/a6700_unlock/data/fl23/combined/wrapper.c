/* One /setting/fl23 carrier for every combination of the boot-resident
 * features: bit 1 FL2/FL3, bit 2 Creative Look names, bit 4 custom shutter
 * sound. The primary part is the unchanged FL23/names stage of variant 1..3;
 * the custom part is the unchanged hardware-verified custom-sound stage.
 *
 * The wrapper adds no write path. It only sequences the parts and refuses the
 * custom part whenever the primary part retained any image/mapping. Every
 * guard inside both parts (paused cohort, double reads, inactive patch and old
 * pointer checks, before/after bytes, NO_WRITE classification) is unchanged.
 */
#include "boot-abi.h"

#ifndef COMBINED_FLAGS
#error "COMBINED_FLAGS required"
#endif
#ifndef COMBINED_PRIMARY
#error "COMBINED_PRIMARY required"
#endif

long fl23_part_custom(struct a1_boot_context *c);
#if COMBINED_PRIMARY
long fl23_part_primary(struct a1_boot_context *c);
#else
__attribute__((visibility("hidden"))) void *memcpy(void *to,const void *from,unsigned long n){
    volatile u8 *d=to;const volatile u8 *s=from;for(unsigned long i=0;i<n;i++)d[i]=s[i];return to;
}
__attribute__((visibility("hidden"))) void *memset(void *to,int value,unsigned long n){
    volatile u8 *d=to;for(unsigned long i=0;i<n;i++)d[i]=(u8)value;return to;
}
static inline long a1_sc(long n,long a0,long a1,long a2,long a3,long a4,long a5){
    register long x8 __asm__("x8")=n,x0 __asm__("x0")=a0,x1 __asm__("x1")=a1,
        x2 __asm__("x2")=a2,x3 __asm__("x3")=a3,x4 __asm__("x4")=a4,x5 __asm__("x5")=a5;
    __asm__ volatile("svc #0":"+r"(x0):"r"(x8),"r"(x1),"r"(x2),"r"(x3),"r"(x4),"r"(x5):"memory","cc");
    return x0;
}
static int s2_sleep(long n){
    struct {long sec,nsec;} t={n/1000000000L,n%1000000000L};
    return n>0&&a1_sc(101,(long)&t,0,0,0,0,0)==0;
}
#include "startup-observer.h"
#endif

/* Unused gap between the custom-sound diagnostic [1664,1968) and the
 * process startup observer [2048,4032). */
#define COMBINED_RECORD_OFFSET 1984u
#define COMBINED_RECORD_WORDS 8u
#define COMBINED_MAGIC 0x31574d4f43334c46ULL /* "FL3COMW1" */

__attribute__((section(".text.stage_entry")))
long fl23_combined_stage_entry(struct a1_boot_context *c) {
    if (!c || c->version != 1 || c->worker_state != 5) return -22;
    /* Never replace an existing image or API, including historical records. */
    if (c->reserved[6] || c->reserved[7] || c->reserved[10]) return -16;
    volatile u64 *w = (volatile u64 *)((u8 *)c + COMBINED_RECORD_OFFSET);
    for (u32 i = 0; i < COMBINED_RECORD_WORDS; ++i) if (w[i]) return -16;
    w[0] = COMBINED_MAGIC;
    w[1] = COMBINED_FLAGS;
    long primary = 0;
#if COMBINED_PRIMARY
    primary = fl23_part_primary(c);
    w[2] = 1;
    w[3] = (u64)primary;
    w[4] = c->reserved[10];
    /* A retained mapping or failed unmap means the primary part did not
     * finish cleanup. Do not load another module into this process. */
    if (c->reserved[6] || c->reserved[7] || c->reserved[14] || c->worker_state != 5) {
        w[7] = (u64)-16L;
        return primary ? primary : -16;
    }
    /* reserved[10] is only the finished FL23 thread poll counter (its image
     * is already unmapped: reserved[6]=reserved[7]=0, reserved[14]=0). Keep
     * it in the combined record and present the custom part a fresh slot. */
    c->reserved[10] = 0;
#else
    static const u64 gate_backoff[5] = {
        5000000000ULL, 10000000000ULL, 15000000000ULL, 20000000000ULL, 30000000000ULL
    };
    long observed = -110;
    for (u32 g = 0; g < 6; ++g) {
        observed = s2_process_observe((u8 *)c, c->pid, 60000000000ULL, 1);
        if (!observed) break;
        if (observed != -110 && observed != -16 && observed != -12 && observed != -11) {
            w[7] = (u64)observed;
            return observed;
        }
        if (g < 5 && !s2_sleep((long)gate_backoff[g])) {
            w[7] = (u64)-4L;
            return -4;
        }
    }
    if (observed) {
        w[7] = (u64)observed;
        return observed;
    }
#endif
    long custom = fl23_part_custom(c);
    w[5] = 1;
    w[6] = (u64)custom;
    return primary ? primary : custom;
}
