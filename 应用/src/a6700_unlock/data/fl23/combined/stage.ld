SECTIONS { . = 0; .text : { *(.text.stage_entry) *(.text.part_primary*) *(.text.part_custom*) *(.text*) } .rodata (READONLY) : { *(.rodata*) *(.got*) } /DISCARD/ : { *(.comment) *(.eh_frame*) } }
