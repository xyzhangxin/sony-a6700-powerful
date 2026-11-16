ENTRY(bridge_eligibility_entry)
SECTIONS {
    . = 0;
    .text : { KEEP(*(.text*)) }
    .rodata : { *(.rodata*) }
    ASSERT(. < 0x4000, "adapter exceeds code window")
    . = 0x4000;
    .bridge_state (NOLOAD) : { *(.bridge_state) }
    ASSERT(. == 0x4040, "adapter config ABI mismatch")
    /DISCARD/ : { *(.comment) *(.eh_frame*) *(.note*) }
}
