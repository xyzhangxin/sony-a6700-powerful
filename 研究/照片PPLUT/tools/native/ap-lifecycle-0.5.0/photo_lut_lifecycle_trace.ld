/* SPDX-License-Identifier: GPL-3.0-only */
ENTRY(plut_trace_eligibility)
SECTIONS {
    . = 0;
    .text : { KEEP(*(.text*)) }
    .rodata : { *(.rodata*) }
    ASSERT(. < 0x4000, "trace code exceeds owned code pages")
    . = 0x4000;
    .trace_state (NOLOAD) : { *(.trace_state) }
    ASSERT(. < 0x5000, "trace locator exceeds owned near mapping")
    /DISCARD/ : { *(.comment) *(.eh_frame*) *(.note*) }
}
