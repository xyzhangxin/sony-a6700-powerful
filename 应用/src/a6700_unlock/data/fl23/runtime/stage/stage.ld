SECTIONS { . = 0; .text : { *(.text.stage_entry) *(.text*) } .rodata (READONLY) : { *(.rodata*) *(.got*) } /DISCARD/ : { *(.comment) *(.eh_frame*) } }
