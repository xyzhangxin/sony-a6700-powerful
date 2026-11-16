/* Preserve the native AAPCS64 call; capture the constructor's own frame,
 * sequence object in x19 and original LR before the C core creates a frame. */
.text
.p2align 2
.global bridge_eligibility_entry
.type bridge_eligibility_entry,%function
bridge_eligibility_entry:
    sub sp,sp,#16
    stp x29,x30,[sp]
    add x5,sp,#16
    cmp x29,x5
    csel x5,x5,xzr,eq
    mov x6,x19
    mov x7,x30
    bl bridge_eligibility_core
    ldp x29,x30,[sp]
    add sp,sp,#16
    ret
.size bridge_eligibility_entry,.-bridge_eligibility_entry

.p2align 2
.global bridge_resolver_entry
.type bridge_resolver_entry,%function
bridge_resolver_entry:
    sub sp,sp,#48
    stp x29,x30,[sp,#32]
    add x9,sp,#48
    cmp x29,x9
    csel x9,x9,xzr,eq
    stp x9,x19,[sp]
    str x30,[sp,#16]
    bl bridge_resolver_core
    ldp x29,x30,[sp,#32]
    add sp,sp,#48
    ret
.size bridge_resolver_entry,.-bridge_resolver_entry
.section .note.GNU-stack,"",%progbits
