/* Independent A6700 native-name adapter. No CP, selection or value storage.
 * Bound only inside caller-owned memory; publication is a separate transaction.
 */
typedef unsigned int u32;
typedef unsigned long long u64;

const u32 fl23_names_full[10] = {
    0x21c71eb, 0x21c718e, 0x21c7172, 0x21c71bc, 0x21c70dd,
    0x21c725d, 0x21c7129, 0x21c70fb, 0x21c71a2, 0x21c7273
};
static const u32 original_names[10] = {
    0x21c658e, 0x21c6654, 0x21c6434, 0x21c687b, 0x21c689a,
    0x21c6cc9, 0x21c6857, 0x21c6961, 0x21c65f8, 0x21c658a
};
static struct { u64 help; u64 cookie; } binding = {0, 0x31454d414e304137ULL};

int fl23_names_bind(u64 original_help) {
    if (!original_help || (original_help & 3) || original_help >= 0x8000000000ULL)
        return 1;
    /* A second different owner is never silently rebound. */
    if (binding.help && binding.help != original_help) return 2;
    binding.help = original_help;
    return 0;
}

u32 fl23_names_help(u64 object, u32 caption) {
    if (!binding.help) return 0x20025ed;
    for (u32 i = 0; i < 10; i++) {
        if (caption == fl23_names_full[i]) {
            caption = original_names[i];
            break;
        }
    }
    return ((u32 (*)(u64, u32))binding.help)(object, caption);
}
