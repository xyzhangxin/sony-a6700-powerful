/* Minimal AArch64 Linux shared object: constructor writes a marker file via raw syscalls.
   No libc dependency; safe to load with dlopen from a glibc-2.17 process. */
static long sys_call3(long n, long a, long b, long c) {
    register long x8 __asm__("x8") = n;
    register long x0 __asm__("x0") = a;
    register long x1 __asm__("x1") = b;
    register long x2 __asm__("x2") = c;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory");
    return x0;
}

static const char marker[] = "/tmp/jiritsu-exec-proof\n";

__attribute__((constructor)) static void on_load(void) {
    long fd = sys_call3(56 /* openat */, -100 /* AT_FDCWD */,
                        (long)marker, 0x241 /* O_WRONLY|O_CREAT|O_TRUNC */);
    if (fd >= 0) {
        sys_call3(64 /* write */, fd, (long)marker, sizeof(marker) - 1);
        sys_call3(57 /* close */, fd, 0, 0);
    }
}
