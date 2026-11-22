/* Fixed custom shutter-sound slot import for A6700 AArch64 Linux.
 * Reads one validated request from /tmp/jiritsu/tmmc.jit, atomically publishes
 * /setting/csnd/slotN.bin, independently reads it back, and returns an A67R
 * response. This candidate is offline-built and has not been hardware-validated.
 */

#define REQUEST_HEADER 72u
#define RESPONSE 44u
#define AT_FDCWD -100L
#define SYS_OPENAT 56L
#define SYS_CLOSE 57L
#define SYS_READ 63L
#define SYS_WRITE 64L
#define SYS_FSYNC 82L
#define SYS_MKDIRAT 34L
#define SYS_UNLINKAT 35L
#define SYS_RENAMEAT 38L
#define SYS_GETDENTS64 61L
#define O_RDONLY 0
#define O_WRONLY 1
#define O_CREAT 0100
#define O_TRUNC 01000
#define O_EXCL 0200
#define O_NOFOLLOW 0x20000
#define DIR_FLAGS 0x8c000
#define AT_REMOVEDIR 0x200
#define DT_DIR 4
#define EINTR 4
#define ENOENT 2
#define EEXIST 17
#define EISDIR 21
#define MAX_RECORD (128u + 255u + 512u * 1024u)
#define SLOT_COUNT 10u

static unsigned char request[REQUEST_HEADER + MAX_RECORD];
static unsigned char preimage[MAX_RECORD];
static unsigned char response[RESPONSE];
static unsigned char dirent_buf[1024];
static char wipe_stack[4][384];
static const char result_path[] = "/tmp/jiritsu/tmmc.jit";
static const char setting_directory[] = "/setting";
static const char directory[] = "/setting/csnd";
static const char pending_marker_path[] = "/setting/csnd/.shutter-autoload-pending";
static const char quarantine_marker_path[] = "/setting/csnd/.shutter-autoload-quarantine";
static char primary_path[32];
static char temporary_path[32];
static char backup_path[32];

static long call4(long n, long a, long b, long c, long d) {
    register long x8 __asm__("x8") = n;
    register long x0 __asm__("x0") = a;
    register long x1 __asm__("x1") = b;
    register long x2 __asm__("x2") = c;
    register long x3 __asm__("x3") = d;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2), "r"(x3) : "memory");
    return x0;
}

static unsigned get32(unsigned off) {
    return (unsigned)request[off] | ((unsigned)request[off + 1] << 8) |
           ((unsigned)request[off + 2] << 16) | ((unsigned)request[off + 3] << 24);
}

static unsigned record_get32(unsigned off) {
    return get32(REQUEST_HEADER + off);
}

static void copy_text(char *output, const char *text) {
    unsigned index = 0;
    while (text[index]) {
        output[index] = text[index];
        ++index;
    }
    output[index] = 0;
}

static unsigned append_text(char *output, unsigned at, const char *text) {
    unsigned index = 0;
    while (text[index]) {
        output[at + index] = text[index];
        ++index;
    }
    output[at + index] = 0;
    return at + index;
}

static unsigned append_number(char *output, unsigned at, unsigned value) {
    char digits[8];
    unsigned count = 0;
    if (!value) digits[count++] = '0';
    while (value && count < sizeof(digits)) {
        digits[count++] = (char)('0' + value % 10u);
        value /= 10u;
    }
    while (count) output[at++] = digits[--count];
    output[at] = 0;
    return at;
}

static void build_slot_paths(unsigned slot) {
    unsigned at;
    copy_text(primary_path, "/setting/csnd/slot");
    at = append_number(primary_path, 18u, slot);
    append_text(primary_path, at, ".bin");

    copy_text(temporary_path, "/setting/csnd/slot");
    at = append_number(temporary_path, 18u, slot);
    append_text(temporary_path, at, ".tmp");

    copy_text(backup_path, "/setting/csnd/slot");
    at = append_number(backup_path, 18u, slot);
    append_text(backup_path, at, ".bak");
}

static void put32(unsigned off, unsigned value) {
    for (unsigned i = 0; i < 4; ++i) response[off + i] = (unsigned char)(value >> (i * 8));
}

static int same(const unsigned char *a, const unsigned char *b, unsigned long n) {
    for (unsigned long i = 0; i < n; ++i) if (a[i] != b[i]) return 0;
    return 1;
}

static int io(long syscall, long fd, unsigned char *buf, unsigned long size) {
    unsigned long done = 0;
    unsigned attempts = 0;
    while (done < size && attempts++ < 1024) {
        long count = call4(syscall, fd, (long)(buf + done), (long)(size - done), 0);
        if (count == -EINTR) continue;
        if (count <= 0 || (unsigned long)count > size - done) return 0;
        done += (unsigned long)count;
    }
    return done == size;
}

static int read_request(void) {
    long fd = call4(SYS_OPENAT, AT_FDCWD, (long)result_path, O_RDONLY, 0);
    if (fd < 0) return 0;
    int ok = io(SYS_READ, fd, request, REQUEST_HEADER);
    unsigned version = get32(4);
    unsigned record_bytes = get32(64);
    if (ok && (version == 4u || version == 5u)) {
        if (record_bytes != 0u) ok = 0;
    } else {
        if (ok && (record_bytes < 128 || record_bytes > MAX_RECORD)) ok = 0;
        if (ok) ok = io(SYS_READ, fd, request + REQUEST_HEADER, record_bytes);
        if (ok && version == 2u && get32(68) != 0xffffffffu) {
            unsigned size = get32(68);
            if (size < 128 || size > MAX_RECORD) ok = 0;
            else ok = io(SYS_READ, fd, preimage, size);
        }
    }
    unsigned char extra;
    if (ok && call4(SYS_READ, fd, (long)&extra, 1, 0) != 0) ok = 0;
    call4(SYS_CLOSE, fd, 0, 0, 0);
    return ok;
}

static void finish(unsigned status, unsigned failed, unsigned flags) {
    response[0] = 'A'; response[1] = '6'; response[2] = '7'; response[3] = 'R';
    put32(4, 1);
    for (unsigned i = 0; i < 16; ++i) response[8 + i] = request[8 + i];
    put32(24, status); put32(28, 0); put32(32, failed); put32(36, 0); put32(40, flags);
    long fd = call4(SYS_OPENAT, AT_FDCWD, (long)result_path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd >= 0) {
        if (io(SYS_WRITE, fd, response, RESPONSE)) {
            for (unsigned i = 0; i < 16; ++i) {
                    long result = call4(SYS_FSYNC, fd, 0, 0, 0);
                    if (result == 0) break;
                    if (result != -EINTR) break;
            }
        }
        call4(SYS_CLOSE, fd, 0, 0, 0);
    }
}

static int sync_path_directory(const char *path) {
    long fd = call4(SYS_OPENAT, AT_FDCWD, (long)path, DIR_FLAGS, 0);
    if (fd < 0) return 0;
    int ok = 0;
    for (unsigned i = 0; i < 16; ++i) {
        long result = call4(SYS_FSYNC, fd, 0, 0, 0);
        if (result == 0) { ok = 1; break; }
        if (result != -EINTR) break;
    }
    call4(SYS_CLOSE, fd, 0, 0, 0);
    return ok;
}

static int sync_directory(void) {
    return sync_path_directory(directory);
}

static int sync_optional_directory(const char *path) {
    long fd = call4(SYS_OPENAT, AT_FDCWD, (long)path, DIR_FLAGS, 0);
    if (fd == -ENOENT) return 1;
    if (fd < 0) return 0;
    int ok = 0;
    for (unsigned i = 0; i < 16; ++i) {
        long result = call4(SYS_FSYNC, fd, 0, 0, 0);
        if (result == 0) { ok = 1; break; }
        if (result != -EINTR) break;
    }
    call4(SYS_CLOSE, fd, 0, 0, 0);
    return ok;
}

static int remove_optional(const char *path) {
    long result = call4(SYS_UNLINKAT, AT_FDCWD, (long)path, 0, 0);
    return result == 0 || result == -ENOENT;
}

static int rollback_primary(const char *primary, const char *backup, int old_moved) {
    remove_optional(primary);
    if (old_moved && call4(SYS_RENAMEAT, AT_FDCWD, (long)backup,
                           AT_FDCWD, (long)primary) != 0) return 0;
    return sync_directory();
}

static int verified_absent(const char *path) {
    long fd = call4(SYS_OPENAT, AT_FDCWD, (long)path, O_RDONLY | O_NOFOLLOW, 0);
    if (fd == -ENOENT) return 1;
    if (fd >= 0) call4(SYS_CLOSE, fd, 0, 0, 0);
    return 0;
}

static int append_child(char *dst, unsigned cap, const char *parent, const char *name) {
    unsigned at = 0;
    while (parent[at]) {
        if (at + 2u >= cap) return 0;
        dst[at] = parent[at];
        ++at;
    }
    dst[at++] = '/';
    unsigned idx = 0;
    while (name[idx]) {
        if (name[idx] == '/' || at + 1u >= cap) return 0;
        dst[at++] = name[idx++];
    }
    dst[at] = 0;
    return idx > 0u;
}

static int wipe_directory_recursive(const char *path, unsigned depth) {
    if (depth >= 4u) return 0;
    for (unsigned pass = 0; pass < 256u; ++pass) {
        long dirfd = call4(SYS_OPENAT, AT_FDCWD, (long)path, DIR_FLAGS, 0);
        if (dirfd == -ENOENT) return verified_absent(path);
        if (dirfd < 0) return 0;
        int removed_any = 0;
        int recurse = 0;
        for (;;) {
            long n = call4(SYS_GETDENTS64, dirfd, (long)dirent_buf, (long)sizeof(dirent_buf), 0);
            if (n == -EINTR) continue;
            if (n < 0) { call4(SYS_CLOSE, dirfd, 0, 0, 0); return 0; }
            if (n == 0) break;
            long pos = 0;
            while (pos + 20L <= n) {
                unsigned short reclen = (unsigned short)dirent_buf[pos + 16]
                    | ((unsigned short)dirent_buf[pos + 17] << 8);
                if (reclen < 20u || pos + (long)reclen > n) {
                    call4(SYS_CLOSE, dirfd, 0, 0, 0);
                    return 0;
                }
                unsigned char dtype = dirent_buf[pos + 18];
                const char *name = (const char *)(dirent_buf + pos + 19);
                unsigned name_limit = (unsigned)reclen - 19u;
                unsigned name_len = 0u;
                int has_nul = 0;
                while (name_len < name_limit) {
                    if (name[name_len] == 0) { has_nul = 1; break; }
                    ++name_len;
                }
                if (!has_nul || name_len == 0u) {
                    call4(SYS_CLOSE, dirfd, 0, 0, 0);
                    return 0;
                }
                pos += (long)reclen;
                if (name[0] == '.' && (name[1] == 0 || (name[1] == '.' && name[2] == 0))) {
                    continue;
                }
                if (dtype == DT_DIR) {
                    if (!append_child(wipe_stack[depth], (unsigned)sizeof(wipe_stack[depth]), path, name)) {
                        call4(SYS_CLOSE, dirfd, 0, 0, 0);
                        return 0;
                    }
                    recurse = 1;
                    break;
                }
                long u = call4(SYS_UNLINKAT, dirfd, (long)name, 0, 0);
                if (u == -EISDIR) {
                    if (!append_child(wipe_stack[depth], (unsigned)sizeof(wipe_stack[depth]), path, name)) {
                        call4(SYS_CLOSE, dirfd, 0, 0, 0);
                        return 0;
                    }
                    recurse = 1;
                    break;
                }
                if (u != 0 && u != -ENOENT) {
                    call4(SYS_CLOSE, dirfd, 0, 0, 0);
                    return 0;
                }
                removed_any = 1;
            }
            if (!recurse && pos != n) {
                call4(SYS_CLOSE, dirfd, 0, 0, 0);
                return 0;
            }
            if (recurse) break;
        }
        call4(SYS_CLOSE, dirfd, 0, 0, 0);
        if (recurse) {
            if (!wipe_directory_recursive(wipe_stack[depth], depth + 1u)) return 0;
            removed_any = 1;
        }
        if (!removed_any) {
            long r = call4(SYS_UNLINKAT, AT_FDCWD, (long)path, AT_REMOVEDIR, 0);
            if (r != 0 && r != -ENOENT) return 0;
            return verified_absent(path);
        }
    }
    return 0;
}

static int matches_primary(const unsigned char *expected, unsigned size) {
    long fd = call4(SYS_OPENAT, AT_FDCWD, (long)primary_path, O_RDONLY | O_NOFOLLOW, 0);
    if (size == 0xffffffffu) {
        if (fd >= 0) call4(SYS_CLOSE, fd, 0, 0, 0);
        return fd == -ENOENT;
    }
    if (fd < 0) return 0;
    static unsigned char current[MAX_RECORD];
    unsigned char extra;
    int ok = io(SYS_READ, fd, current, size)
        && call4(SYS_READ, fd, (long)&extra, 1, 0) == 0
        && same(current, expected, size);
    if (call4(SYS_CLOSE, fd, 0, 0, 0)) ok = 0;
    return ok;
}

__attribute__((constructor)) static void run(void) {
    if (!read_request() || !same(request, (const unsigned char *)"CSND", 4)
        || get32(4) < 1 || get32(4) > 5) {
        finish(2, 0xffffffffU, 0); return;
    }
    unsigned version = get32(4);
    unsigned slot = get32(56);
    unsigned generation = get32(60);
    unsigned record_bytes = get32(64);
    if (version == 4u) {
        if (slot || generation || record_bytes || get32(68)) {
            finish(2, 0xffffffffU, 0); return;
        }
        if (!wipe_directory_recursive(directory, 0u)
            || !sync_path_directory(setting_directory)
            || !verified_absent(directory)) {
            finish(8, 0xffffffffU, 0); return;
        }
        finish(0, 0xffffffffU, 0); return;
    }
    if (version == 5u) {
        if (slot || generation || record_bytes || get32(68)) {
            finish(2, 0xffffffffU, 0); return;
        }
        int had_markers = !verified_absent(pending_marker_path)
            || !verified_absent(quarantine_marker_path);
        if (!remove_optional(pending_marker_path)
            || !remove_optional(quarantine_marker_path)
            || !sync_optional_directory(directory)
            || !verified_absent(pending_marker_path)
            || !verified_absent(quarantine_marker_path)) {
            finish(8, 0xffffffffU, 0); return;
        }
        finish(0, 0xffffffffU, had_markers ? 4u : 0u); return;
    }
    if (version != 2u && get32(68)) { finish(2, 0xffffffffU, 0); return; }
    if (record_bytes < 128 || record_bytes > MAX_RECORD
        || !slot || slot > SLOT_COUNT || generation == 0
        || (generation & 0xf0000000U) != (slot << 28)) {
        finish(2, 0xffffffffU, 0); return;
    }

    unsigned char *record = request + REQUEST_HEADER;
    unsigned wav_bytes = record_get32(16);
    unsigned name_bytes = record_get32(20);
    unsigned declared_total = record_get32(24);
    if (record[0] != 'C' || record[1] != 'S' || record[2] != 'N' || record[3] != 'D'
        || record_get32(4) != 2 || record_get32(8) != slot
        || record_get32(12) != generation || record_get32(28) != 2
        || declared_total != record_bytes
        || 128u + name_bytes + wav_bytes != record_bytes) {
        finish(2, 0xffffffffU, 0); return;
    }

    build_slot_paths(slot);
    if (get32(4) == 2 && !matches_primary(preimage, get32(68))) {
        finish(10, 0xffffffffU, 0); return;
    }
    if (get32(4) == 3) {
        if (!verified_absent(temporary_path) || !verified_absent(backup_path)) {
            finish(9, 0xffffffffU, 0); return;
        }
        if (!matches_primary(record, record_bytes)) { finish(10, 0xffffffffU, 0); return; }
        if (!remove_optional(primary_path) || !sync_directory()) {
            finish(8, 0xffffffffU, 0); return;
        }
        finish(0, 0xffffffffU, 0); return;
    }
    long mkdir_result = call4(SYS_MKDIRAT, AT_FDCWD, (long)directory, 0700, 0);
    if (mkdir_result != 0) {
        if (mkdir_result != -EEXIST) { finish(3, 0xffffffffU, 0); return; }
    }
    build_slot_paths(slot);
    const char *primary = primary_path;
    const char *temporary = temporary_path;
    const char *backup = backup_path;
    if (!verified_absent(temporary) || !verified_absent(backup)) {
        finish(9, 0xffffffffU, 0); return;
    }

    long fd = call4(SYS_OPENAT, AT_FDCWD, (long)temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (fd < 0) { finish(3, 0xffffffffU, 0); return; }
    if (!io(SYS_WRITE, fd, record, record_bytes)) {
        call4(SYS_CLOSE, fd, 0, 0, 0); remove_optional(temporary);
        finish(4, 0xffffffffU, 0); return;
    }
    int synced = 0;
    for (unsigned i = 0; i < 16; ++i) {
        long result = call4(SYS_FSYNC, fd, 0, 0, 0);
        if (result == 0) { synced = 1; break; }
        if (result != -EINTR) break;
    }
    call4(SYS_CLOSE, fd, 0, 0, 0);
    if (!synced) { remove_optional(temporary); finish(4, 0xffffffffU, 0); return; }

    int old_moved = 0;
    long rename_result = call4(SYS_RENAMEAT, AT_FDCWD, (long)primary,
                               AT_FDCWD, (long)backup);
    if (rename_result == 0) old_moved = 1;
    else {
        if (rename_result != -ENOENT) {
            remove_optional(temporary); finish(5, 0xffffffffU, 0); return;
        }
    }
    if (call4(SYS_RENAMEAT, AT_FDCWD, (long)temporary, AT_FDCWD, (long)primary) != 0
        || !sync_directory()) {
        unsigned rollback = 0;
        remove_optional(temporary);
        rollback = !rollback_primary(primary, backup, old_moved);
        finish(rollback ? 8 : 5, 0xffffffffU, rollback ? 0 : 1);
        return;
    }

    long read_fd = call4(SYS_OPENAT, AT_FDCWD, (long)primary, O_RDONLY, 0);
    static unsigned char observed[MAX_RECORD];
    if (read_fd < 0 || !io(SYS_READ, read_fd, observed, record_bytes)) {
        if (read_fd >= 0) call4(SYS_CLOSE, read_fd, 0, 0, 0);
        unsigned rollback = !rollback_primary(primary, backup, old_moved);
        finish(rollback ? 8 : 4, 0xffffffffU, rollback); return;
    }
    unsigned char extra;
    int trailing = call4(SYS_READ, read_fd, (long)&extra, 1, 0);
    call4(SYS_CLOSE, read_fd, 0, 0, 0);
    if (trailing != 0 || !same(observed, record, record_bytes)) {
        unsigned rollback = 0;
        rollback = !rollback_primary(primary, backup, old_moved);
        finish(rollback ? 8 : 6, 0xffffffffU, rollback ? 0 : 1);
        return;
    }
    unsigned cleanup_flags = 0;
    if (old_moved) {
        if (!remove_optional(backup) || !sync_directory()) cleanup_flags = 2u;
    }
    finish(0, 0xffffffffU, cleanup_flags);
}
