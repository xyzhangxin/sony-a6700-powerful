/* SPDX-License-Identifier: GPL-3.0-only
 * Capture the original constructor frame, x19 object and LR before C entry.
 * Getter/frame observation and admission accounting live in the C wrappers.
 */
.text
.p2align 2
.global plut_trace_eligibility
.type plut_trace_eligibility,%function
plut_trace_eligibility:
    sub sp,sp,#16
    stp x29,x30,[sp]
    add x5,sp,#16
    cmp x29,x5
    csel x5,x5,xzr,eq
    mov x6,x19
    mov x7,x30
    bl plut_pp1_eligibility
    ldp x29,x30,[sp]
    add sp,sp,#16
    ret
.size plut_trace_eligibility,.-plut_trace_eligibility
.p2align 2
.global plut_trace_resolver
.type plut_trace_resolver,%function
plut_trace_resolver:
    sub sp,sp,#48
    stp x29,x30,[sp,#32]
    add x9,sp,#48
    cmp x29,x9
    csel x9,x9,xzr,eq
    stp x9,x19,[sp]
    str x30,[sp,#16]
    bl plut_pp1_resolver
    ldp x29,x30,[sp,#32]
    add sp,sp,#48
    ret
.size plut_trace_resolver,.-plut_trace_resolver
.section .note.GNU-stack,"",%progbits
