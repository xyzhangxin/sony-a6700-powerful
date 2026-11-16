/* Offline lab addresses, never an approved camera memory allocation. */
ENTRY(pp1_bind)
PHDRS { text PT_LOAD FLAGS(5); data PT_LOAD FLAGS(6); }
SECTIONS {
 . = 0x12000000;
 .text : { KEEP(*(.text .text.*)) *(.rodata .rodata.*) } :text
 . = 0x12010000;
 .data : { *(.data .data.*) } :data
 .bss : { *(.bss .bss.*) *(COMMON) } :data
 /DISCARD/ : { *(.ARM.exidx* .ARM.extab* .comment .note* .eh_frame*) }
}
