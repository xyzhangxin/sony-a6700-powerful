/* Fixed A6700 recv-entry structural gate. This is NOT a ready predicate.
 * No static state, allocator or library call occurs before the gate passes.
 * The linked dispatcher must return promptly and defer work to a pthread.
 * Full context is restored before replaying the displaced original stp.
 */
#include "trigger-profile.h"
.text
.p2align 3
.global fl23_a1_trigger_entry
.type fl23_a1_trigger_entry,%function
fl23_a1_trigger_entry:
 sub sp,sp,#800
 stp x0,x1,[sp,#0]
 stp x2,x3,[sp,#16]
 stp x4,x5,[sp,#32]
 stp x6,x7,[sp,#48]
 stp x8,x9,[sp,#64]
 stp x10,x11,[sp,#80]
 stp x12,x13,[sp,#96]
 stp x14,x15,[sp,#112]
 stp x16,x17,[sp,#128]
 stp x18,x19,[sp,#144]
 stp x20,x21,[sp,#160]
 stp x22,x23,[sp,#176]
 stp x24,x25,[sp,#192]
 stp x26,x27,[sp,#208]
 stp x28,x29,[sp,#224]
 str x30,[sp,#240]
 mrs x0,nzcv
 mrs x1,fpcr
 mrs x2,fpsr
 stp x0,x1,[sp,#256]
 str x2,[sp,#272]
 stp q0,q1,[sp,#288]
 stp q2,q3,[sp,#320]
 stp q4,q5,[sp,#352]
 stp q6,q7,[sp,#384]
 stp q8,q9,[sp,#416]
 stp q10,q11,[sp,#448]
 stp q12,q13,[sp,#480]
 stp q14,q15,[sp,#512]
 stp q16,q17,[sp,#544]
 stp q18,q19,[sp,#576]
 stp q20,q21,[sp,#608]
 stp q22,q23,[sp,#640]
 stp q24,q25,[sp,#672]
 stp q26,q27,[sp,#704]
 stp q28,q29,[sp,#736]
 stp q30,q31,[sp,#768]

 adr x16,fl23_a1_trigger_entry
 ldr x17,entry_rva
 sub x16,x16,x17
 ldr x17,wrapper_return_rva
 add x17,x16,x17
 cmp x30,x17
 b.ne restore_context
 cbnz w28,restore_context
 /* Original wrapper holds exactly its canonical 64-byte frame. At this
  * first direct call it has not adjusted sp again: entry x29 == entry sp.
  * Check before reading caller memory; unknown callers never get walked. */
 add x0,sp,#800
 cmp x29,x0
 b.ne restore_context
 ldr x17,[x29,#8]
 ldr x0,loop_return_rva
 add x0,x16,x0
 cmp x17,x0
 b.ne restore_context
 mov x0,x16
 ldr x1,[sp,#0]
 bl fl23_a1_trigger_dispatch

restore_context:
 ldp q0,q1,[sp,#288]
 ldp q2,q3,[sp,#320]
 ldp q4,q5,[sp,#352]
 ldp q6,q7,[sp,#384]
 ldp q8,q9,[sp,#416]
 ldp q10,q11,[sp,#448]
 ldp q12,q13,[sp,#480]
 ldp q14,q15,[sp,#512]
 ldp q16,q17,[sp,#544]
 ldp q18,q19,[sp,#576]
 ldp q20,q21,[sp,#608]
 ldp q22,q23,[sp,#640]
 ldp q24,q25,[sp,#672]
 ldp q26,q27,[sp,#704]
 ldp q28,q29,[sp,#736]
 ldp q30,q31,[sp,#768]
 ldp x0,x1,[sp,#256]
 ldr x2,[sp,#272]
 msr nzcv,x0
 msr fpcr,x1
 msr fpsr,x2
 ldp x0,x1,[sp,#0]
 ldp x2,x3,[sp,#16]
 ldp x4,x5,[sp,#32]
 ldp x6,x7,[sp,#48]
 ldp x8,x9,[sp,#64]
 ldp x10,x11,[sp,#80]
 ldp x12,x13,[sp,#96]
 ldp x14,x15,[sp,#112]
 ldp x16,x17,[sp,#128]
 ldp x18,x19,[sp,#144]
 ldp x20,x21,[sp,#160]
 ldp x22,x23,[sp,#176]
 ldp x24,x25,[sp,#192]
 ldp x26,x27,[sp,#208]
 ldp x28,x29,[sp,#224]
 ldr x30,[sp,#240]
 add sp,sp,#800
 .inst 0xa9ba7bfd
 b fl23_a1_trigger_resume
.size fl23_a1_trigger_entry,.-fl23_a1_trigger_entry
.p2align 3
entry_rva: .quad A1_TRIGGER_CAVE
wrapper_return_rva: .quad 0x190c958
loop_return_rva: .quad 0x190cfb4
.section .note.GNU-stack,"",%progbits
