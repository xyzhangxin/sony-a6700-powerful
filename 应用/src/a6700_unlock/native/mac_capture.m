/* Capture one Sony MSC camera for libusb. No automatic USB reset,
   driver-presence prerequisite or storage-volume prerequisite. */
#import <Foundation/Foundation.h>
#import <IOUSBHost/IOUSBHost.h>
#import <IOKit/IOKitLib.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/select.h>
#include <sys/stat.h>
#include <errno.h>
#include <string.h>
#include <stdlib.h>
#include <fcntl.h>

static BOOL hasMassStorage(io_service_t service) {
    io_iterator_t children = 0;
    if (IORegistryEntryCreateIterator(service, kIOServicePlane, kIORegistryIterateRecursively,
                                      &children) != KERN_SUCCESS) return NO;
    BOOL found = NO;
    io_registry_entry_t child;
    while ((child = IOIteratorNext(children))) {
        if (IOObjectConformsTo(child, "IOUSBMassStorageDriver") || IOObjectConformsTo(child, "IOMedia")) {
            found = YES;
        }
        IOObjectRelease(child);
    }
    IOObjectRelease(children);
    return found;
}

/* USB identity/descriptors establish MSC even after a previous surrender has
   removed the storage driver or when no SD card/volume is mounted. */
static BOOL isMSC(io_service_t service, uint16_t product) {
    if (product == 0x0e76 || product == 0x047d) return YES;
    io_iterator_t children = 0;
    if (IORegistryEntryCreateIterator(service, kIOServicePlane, kIORegistryIterateRecursively,
                                      &children) != KERN_SUCCESS) return NO;
    BOOL found = NO;
    io_registry_entry_t child;
    while ((child = IOIteratorNext(children))) {
        if (IOObjectConformsTo(child, "IOUSBHostInterface")) {
            CFTypeRef cls = IORegistryEntryCreateCFProperty(child, CFSTR("bInterfaceClass"), kCFAllocatorDefault, 0);
            int number = 0;
            if (cls && CFGetTypeID(cls) == CFNumberGetTypeID()) CFNumberGetValue(cls, kCFNumberIntType, &number);
            if (number == 0x08) found = YES;
            if (cls) CFRelease(cls);
        }
        IOObjectRelease(child);
    }
    IOObjectRelease(children);
    return found;
}

/* Return a retained service for exactly one physical Sony MSC camera. During re-enumeration macOS can expose several IOService nodes for
   the same port; deduplicate by locationID and prefer the node that already
   has a mass-storage driver. Different ports still count as different cameras. */
static uint64_t cameraLocation(io_service_t service) {
    uint64_t value = 0;
    CFTypeRef loc = IORegistryEntryCreateCFProperty(service, CFSTR("locationID"), kCFAllocatorDefault, 0);
    if (loc) {
        CFNumberGetValue(loc, kCFNumberSInt64Type, &value);
        CFRelease(loc);
    }
    if (value == 0) {
        CFTypeRef addr = IORegistryEntryCreateCFProperty(service, CFSTR("USB Address"), kCFAllocatorDefault, 0);
        if (addr) {
            CFNumberGetValue(addr, kCFNumberSInt64Type, &value);
            CFRelease(addr);
        }
    }
    if (value == 0) IORegistryEntryGetRegistryEntryID(service, &value);
    return value;
}

static io_service_t findCamera(void) {
    io_iterator_t iter = 0;
    IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching("IOUSBHostDevice"), &iter);
    io_service_t svc = 0, cur;
    uint64_t chosen = 0;
    unsigned unique = 0;
    BOOL chosenHasStorage = NO;
    while ((cur = IOIteratorNext(iter))) {
        CFTypeRef v = IORegistryEntryCreateCFProperty(cur, CFSTR("idVendor"), kCFAllocatorDefault, 0);
        uint16_t vv = 0; if (v) { CFNumberGetValue(v, kCFNumberSInt16Type, &vv); CFRelease(v); }
        CFTypeRef p = IORegistryEntryCreateCFProperty(cur, CFSTR("idProduct"), kCFAllocatorDefault, 0);
        uint16_t pp = 0; if (p) { CFNumberGetValue(p, kCFNumberSInt16Type, &pp); CFRelease(p); }
        if (vv != 0x054c || !isMSC(cur, pp)) { IOObjectRelease(cur); continue; }
        uint64_t loc = cameraLocation(cur);
        if (unique == 0) {
            svc = cur; chosen = loc; unique = 1; chosenHasStorage = hasMassStorage(cur);
            continue;
        }
        if (loc == chosen) {
            BOOL hasStorage = hasMassStorage(cur);
            if (!chosenHasStorage && hasStorage) {
                IOObjectRelease(svc);
                svc = cur; chosenHasStorage = YES;
            } else {
                IOObjectRelease(cur);
            }
            continue;
        }
        unique++;
        IOObjectRelease(cur);
    }
    IOObjectRelease(iter);
    if (unique != 1) {
        if (svc) IOObjectRelease(svc);
        return 0;
    }
    return svc;
}

static int captureOnce(char *message, size_t capacity) {
    io_service_t svc = findCamera();
    if (!svc) {
        snprintf(message, capacity, "need exactly one Sony MSC camera");
        return 1;
    }
    dispatch_queue_t q = dispatch_queue_create("t", NULL);
    NSError *err = nil;
    IOUSBHostDevice *dev = [[IOUSBHostDevice alloc] initWithIOService:svc
        options:IOUSBHostObjectInitOptionsDeviceCapture queue:q error:&err interestHandler:nil];
    if (!dev) {
        snprintf(message, capacity, "%s", err.description.UTF8String ?: "capture failed");
        IOObjectRelease(svc);
        return 2;
    }
    sleep(1);
    [dev destroyWithOptions:IOUSBHostObjectDestroyOptionsDeviceSurrender];
    IOObjectRelease(svc);
    snprintf(message, capacity, "OK");
    return 0;
}

/* A root broker exists only while its controlling app socket is open. The
   socket lives in the caller's 0700 directory, but bind leaves it root-owned;
   0666 lets that directory owner connect. getpeereid still rejects every other
   UID, and the broker never accepts shell text. */
static int runSession(const char *path, uid_t uid) {
    if (!path || strlen(path) >= sizeof(((struct sockaddr_un *)0)->sun_path)) return 2;
    int listener = socket(AF_UNIX, SOCK_STREAM, 0);
    if (listener < 0) return 2;
    struct sockaddr_un address;
    memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    snprintf(address.sun_path, sizeof(address.sun_path), "%s", path);
    umask(077);
    unlink(path);
    if (bind(listener, (struct sockaddr *)&address, sizeof(address)) ||
        chmod(path, 0666) || listen(listener, 1)) {
        close(listener); unlink(path); return 2;
    }
    fd_set ready;
    FD_ZERO(&ready); FD_SET(listener, &ready);
    struct timeval timeout = {30, 0};
    if (select(listener + 1, &ready, NULL, NULL, &timeout) != 1) {
        close(listener); unlink(path); return 3;
    }
    int client = accept(listener, NULL, NULL);
    if (client < 0) { close(listener); unlink(path); return 3; }
    uid_t peer = (uid_t)-1; gid_t group = (gid_t)-1;
    if (getpeereid(client, &peer, &group) || peer != uid) {
        close(client); close(listener); unlink(path); return 4;
    }
    FILE *stream = fdopen(client, "r+");
    if (!stream) { close(client); close(listener); unlink(path); return 4; }
    fprintf(stream, "READY\n"); fflush(stream);
    char command[32], message[1024];
    while (fgets(command, sizeof(command), stream)) {
        if (!strcmp(command, "capture\n")) {
            int rc = captureOnce(message, sizeof(message));
            if (rc) fprintf(stream, "ERR %s\n", message);
            else fprintf(stream, "OK\n");
            fflush(stream);
        } else if (!strcmp(command, "stop\n")) {
            fprintf(stream, "OK\n"); fflush(stream);
            break;
        } else {
            fprintf(stream, "ERR unsupported command\n"); fflush(stream);
        }
    }
    fclose(stream);
    close(listener);
    unlink(path);
    return 0;
}

static int runDetachedSession(const char *path, uid_t uid) {
    /* do shell script may terminate its process group on return. Detach in
       the helper itself instead of relying on a shell background '&'. */
    pid_t child = fork();
    if (child < 0) return 2;
    if (child > 0) return 0;
    if (setsid() < 0) _exit(2);
    int nullfd = open("/dev/null", O_RDWR);
    if (nullfd >= 0) {
        dup2(nullfd, STDIN_FILENO); dup2(nullfd, STDOUT_FILENO); dup2(nullfd, STDERR_FILENO);
        if (nullfd > STDERR_FILENO) close(nullfd);
    }
    _exit(runSession(path, uid));
}

int main(int argc, char **argv) {
    @autoreleasepool {
        if (geteuid() != 0) { printf("need root\n"); return 9; }
        if (argc == 5 && !strcmp(argv[1], "--session") && !strcmp(argv[3], "--uid")) {
            char *end = NULL;
            unsigned long uid = strtoul(argv[4], &end, 10);
            if (!end || *end || uid > 0xffffffffUL) return 2;
            return runDetachedSession(argv[2], (uid_t)uid);
        }
        if (argc != 1) { printf("unsupported option; automatic USB reset is disabled\n"); return 2; }
        char message[1024];
        int rc = captureOnce(message, sizeof(message));
        printf("capture: %s\n", rc ? message : "OK surrendered");
        return rc;
    }
}
