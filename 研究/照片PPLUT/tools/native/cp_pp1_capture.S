/* SPDX-License-Identifier: GPL-3.0-only */
.syntax unified
.arm
.section .text.pp1_boot,"ax",%progbits
.global pp1_boot_hook
.type pp1_boot_hook,%function
pp1_boot_hook:
    push {r0-r3,r12,lr}
    mov r0, #0x110000
    mov r1, #512
    bl original_boot_clear
    movw r0, #0xf000
    movt r0, #0x10
    mov r1, #4096
    mov r2, r0
    mov r3, #15
    bl original_boot_map
    mov r0, #0x110000
    mov r1, #4096
    mov r2, r0
    mov r3, #12
    bl original_boot_map
    pop {r0-r3,r12,lr}
    b.w original_boot_finish

.thumb
.section .text.pp1_hooks,"ax",%progbits

.macro entry name
.global \name
.thumb_func
\name:
.endm

.macro save_flags
    push.w {r0-r3, r12, lr}
    mrs r12, apsr
    push.w {r12, lr}
.endm

.macro restore_flags
    pop.w {r12, lr}
    msr apsr_nzcvq, r12
    pop.w {r0-r3, r12, lr}
.endm

entry pp1_desc_hook
    str.w r0, [r4, #0xc0]
    save_flags
    ldr r1, =0x15be310
    ldr r1, [r1]
    ldr r2, [r4, #0x78]
    ldr r3, [r4, #0x7c]
    movs r0, #1
    bl cp_pp1_record
    restore_flags
    b.w desc_next

entry pp1_submit_hook
    save_flags
    ldr r1, [sp, #32]
    ldr r1, [r1]
    ldr r3, [r4, #0x70]
    movs r0, #2
    bl cp_pp1_record
    restore_flags
    b.w original_user3d

entry pp1_irq_hook
    save_flags
    mov r1, r0
    ldr r2, =0x19c52d8
    ldr r2, [r2]
    ldr r3, =0x19b6a0c
    ldr r3, [r3]
    movs r0, #3
    bl cp_pp1_record
    restore_flags
    movw r1, #0x52d8
    b.w irq_next

entry pp1_wait_hook
    save_flags
    mov r1, r0
    ldr r2, =0x19c52d8
    ldr r2, [r2]
    ldr r3, =0x19b6a0c
    ldr r3, [r3]
    movs r0, #4
    bl cp_pp1_record
    restore_flags
    mov r5, r0
    cbz r5, 1f
    b.w wait_error
1:  b.w wait_success

entry pp1_photo_hook
    save_flags
    ldr.w r1, [sl]
    ldr r2, =0x199a1a0
    cmp r1, r2
    bne 1f
    ldr r1, [r1, #0x24]
    ldr r2, [r4]
    ldrb.w r3, [sp, #0x6c]
    movs r0, #5
    bl cp_pp1_record
1:  restore_flags
    ldrb.w r0, [sp, #0x4c]
    b.w photo_next

entry pp1_query_hook
    push {r0, r1}
    mrs r0, apsr
    ldr r1, [sp]
    ldrb r1, [r1]
    cmp r1, #0x43
    beq 1f
    msr apsr_nzcvq, r0
    pop {r0, r1}
    push {r4-r7, lr}
    add r7, sp, #12
    b.w query_next
1:  pop {r0, r1}
    b.w cp_pp1_query

.ltorg
