/* Names-only parent/controller lifecycle. Internal entry, no constructor,
 * USB exchange, firmware write or CP access. The caller supplies an already
 * loaded/bound names owner; the child alone edits fixed parent words.
 */
#include "fl23_names_publication.c"
#define sc names_control_sc
#include "names-controller-prefix.h"

static struct names_args names_control_args;
static u64 names_control_busy;
static u64 names_control_inspection;
static unsigned char names_last_snapshot[NAMES_COHORT_BYTES];
struct names_controller_report {
    u64 version, status, parent, service_tid, child, wait_return, wait_status;
    u64 mapping, mapping_bytes, mapping_released, owner_retained, published;
    u64 snapshot_bytes, final_reads, pending_controller, inspection;
    struct names_report transaction;
    u64 cohort_header[32];
};
_Static_assert(sizeof(struct names_controller_report) == 912, "names controller report ABI");
static struct names_report *names_control_transaction(void);
static u64 *names_control_extra(void);
__attribute__((noreturn,noinline)) static void names_control_hold(void);
#define FL23_AUDIT_EXTRA 4096u
#define FL23_AUDIT_EXTENSION
#include "names-controller-audit.h"

static u64 *names_control_extra(void) {return (void *)(shared + NAMES_COHORT_BYTES);}
static struct names_report *names_control_transaction(void) {return (void *)(shared + NAMES_COHORT_BYTES + 64);}
__attribute__((noreturn,noinline)) static void names_control_hold(void) {
    names_control_extra()[3] = 1;
    __asm__ volatile("dmb ish" ::: "memory");
    /* Do not detach, exit, destroy the owner or release the shared snapshot
     * after an unknown write. The parent constructor remains stopped. */
    for (;;) {u64 ts[2] = {0, 100000000};sc(101, (long)ts, 0, 0, 0, 0, 0);}
}

static long audit_extension(u64 *h, long count) {
    (void)count;
    struct names_args args = names_control_args;
    args.cohort = (u64)shared;args.cohort_start = h[15];
    struct names_report *r = names_control_transaction();
    u64 *extra = names_control_extra();
    extra[0] = 0x314c54434e304137ULL;
    long rc;
    if (names_control_inspection) {
        /* The controller created these exact fresh args/report mappings.
         * Choose the inverse plan only to check the current preimage; no
         * commit core is called on this path. */
        r->version=1;r->lifecycle=1;r->word_count=NAMES_WORDS;
        r->owner_retained=!args.enabled;names_deadline_start=args.cohort_start;
        names_plan(&args,r->words);
        r->gate_before=names_reader_gate_stopped(&args,r);
        rc=r->gate_before?r->gate_before:names_guards(&args,r);
        if (!rc) {r->gate_after=names_reader_gate_stopped(&args,r);rc=r->gate_after;}
        r->error=rc;
    } else rc=fl23_names_apply_stopped(&args,r);
    extra[1]=rc;
    if (!r->outcome.poke_calls) return rc;
    if (r->lifecycle == 4 || r->outcome.uncertain ||
        (r->lifecycle != 2 && r->lifecycle != 3) || r->independent_reads != NAMES_WORDS ||
        !r->tracer_required || names_reader_gate_stopped(&args, r)) {
        r->lifecycle = 4;r->error = -116;r->tracer_required = 1;r->owner_retained = 1;extra[4] = -116;return -116;
    }
    if ((r->lifecycle == 3 && (!r->outcome.committed || r->outcome.confirmed_after != NAMES_WORDS)) ||
        (r->lifecycle == 2 && (!r->outcome.rollback_verified || r->outcome.committed))) {
        r->lifecycle = 4;r->error = -116;r->tracer_required = 1;r->owner_retained = 1;extra[4] = -116;return -116;
    }
    for (u32 i = 0; i < NAMES_WORDS; i++) {
        u64 value = 0;long read = names_peek(&args, r->words[i].address, &value);
        extra[2]++;
        if (read || value != (r->lifecycle == 3 ? r->words[i].after : r->words[i].before)) {
            r->lifecycle = 4;r->owner_retained = 1;r->error = read ? read : -116;extra[4] = r->error;return r->error;
        }
    }
    if (NAMES_KERNEL_CACHE_CONTRACT != 1 || names_reader_gate_stopped(&args, r)) {
        r->lifecycle = 4;r->owner_retained = 1;r->error = -116;extra[4] = -116;return -116;
    }
    /* The builder binds the pinned kernel's POKEDATA executable-VMA cache
     * path. Independent whole-journal reads and this same still-stopped
     * cohort authorize only the controller's existing detach loop. */
    __asm__ volatile("dmb ish\nisb" ::: "memory");
    r->resume_authorized = 1;r->tracer_required = 0;
    /* A complete inverse may resume, but is still a failed installation. */
    return rc;
}

const unsigned char *fl23_names_controller_snapshot(void) {return names_last_snapshot;}
u64 fl23_names_controller_snapshot_bytes(void) {return NAMES_COHORT_BYTES;}
static long names_controller_run_mode(const struct names_args *input, struct names_controller_report *out, u64 inspection) {
    if (!input || !out || ((u64)input & 7) || ((u64)out & 7) ||
        !names_address((u64)input, sizeof(*input)) || !names_address((u64)out, sizeof(*out)) ||
        names_overlap((u64)input, sizeof(*input), (u64)out, sizeof(*out))) return -22;
    struct names_args a = *input;
    if (a.version != 1 || a.pid != (u64)sc(172,0,0,0,0,0,0) || a.enabled > 1 ||
        !a.bias || (a.bias & 4095) || !a.owner || (a.owner & 4095) ||
        !names_address(a.bias, 0x15000000) || !names_address(a.owner, NAMES_OWNER_BYTES) ||
        names_overlap(a.bias, 0x15000000, a.owner, NAMES_OWNER_BYTES) ||
        names_overlap((u64)input,sizeof(*input),a.bias,0x15000000) ||
        names_overlap((u64)input,sizeof(*input),a.owner,NAMES_OWNER_BYTES) ||
        names_overlap((u64)out,sizeof(*out),a.bias,0x15000000) ||
        names_overlap((u64)out,sizeof(*out),a.owner,NAMES_OWNER_BYTES)) return -22;
    u32 have_nonce = 0;for (u32 i=0;i<16;i++) have_nonce |= a.nonce[i];
    if (!have_nonce || names_control_busy) return -16;
    names_control_busy = 1;names_control_args = a;names_control_inspection=inspection;
    for (u32 i=0;i<sizeof(*out)/8;i++) ((u64 *)out)[i]=0;
    out->version=1;out->owner_retained=!a.enabled;out->inspection=inspection;
    for (u32 i=0;i<NAMES_COHORT_BYTES;i++) names_last_snapshot[i]=0;
    for (u32 i=0;i<40;i++) request[i]=0;
    for (u32 i=0;i<16;i++) request[8+i]=a.nonce[i];
    target_pid=sc(172,0,0,0,0,0,0);target_tid=sc(178,0,0,0,0,0,0);
    out->parent=target_pid;out->service_tid=target_tid;
    process_path(task_root,target_pid,"/task");process_path(maps_path,target_pid,"/maps");
    long region=sc(222,0,AT_ALLOC,3,0x21,-1,0);
    if ((u64)region>=(u64)-4095) {out->status=2;names_control_busy=0;return region;}
    shared=(void *)region;out->mapping=region;out->mapping_bytes=AT_ALLOC;
    for (u32 i=0;i<AT_ALLOC;i++) shared[i]=0;
    long child=sc(220,0,0,0,0,0,0);
    if (!child) controller();
    out->child=child;
    if (child<0) {
        long unmap=sc(215,region,AT_ALLOC,0,0,0,0);out->mapping_released=!unmap;
        out->status=3;names_control_busy=!!unmap;return unmap?unmap:child;
    }
    int exit_status=0;long waited=0;
    for (u32 i=0;i<1000;i++) {
        waited=sc(260,child,(long)&exit_status,0x40000001,0,0,0);
        if (waited==child) {if (((u32)exit_status&255)==127) waited=-1000;break;}
        if (waited<0&&waited!=-4) break;delay(5000000);
    }
    out->wait_return=waited;out->wait_status=(u32)exit_status;
    if (waited!=child) {out->status=4;out->pending_controller=1;out->owner_retained=1;return -115;}
    __asm__ volatile("dmb ish" ::: "memory");
    for (u32 i=0;i<NAMES_COHORT_BYTES;i++) names_last_snapshot[i]=shared[i];
    for (u32 i=0;i<32;i++) out->cohort_header[i]=((u64 *)shared)[i];
    out->transaction=*names_control_transaction();out->snapshot_bytes=NAMES_COHORT_BYTES;
    out->final_reads=names_control_extra()[2];out->pending_controller=names_control_extra()[3];
    out->published=out->transaction.lifecycle==3;out->owner_retained=!a.enabled||out->transaction.owner_retained;
    long unmap=sc(215,region,AT_ALLOC,0,0,0,0);out->mapping_released=!unmap;
    if (unmap || sc(172,0,0,0,0,0,0)!=(long)target_pid || sc(178,0,0,0,0,0,0)!=(long)target_tid) {
        out->status=5;return unmap?unmap:-116;
    }
    names_control_busy=0;
    if (exit_status || out->cohort_header[14] || (inspection
        ? out->transaction.error || out->transaction.outcome.poke_calls ||
          out->transaction.guard_reads!=NAMES_GUARDS || out->transaction.owner_reads!=NAMES_RO_WORDS+1
        : !out->published || !out->transaction.resume_authorized)) {
        out->status=6;return out->transaction.error?out->transaction.error:-116;
    }
    return 0;
}
long fl23_names_controller_run(const struct names_args *input, struct names_controller_report *out) {
    return names_controller_run_mode(input,out,0);
}
/* input.enabled describes the candidate current state in inspection mode.
 * The native whole guard check, not a cached pointer, establishes its truth. */
long fl23_names_controller_inspect(const struct names_args *input, struct names_controller_report *out) {
    if (!input || ((u64)input&7) || !names_address((u64)input,sizeof(*input)) || input->enabled>1) return -22;
    struct names_args a=*input;a.enabled=!a.enabled;
    return names_controller_run_mode(&a,out,1);
}
