/* Free a Sony MSC device on macOS for raw libusb access.
   Requires root. Uses IOUSBHostObjectInitOptionsDeviceCapture to terminate the
   mass-storage driver, then destroyWithOptions:DeviceSurrender so the driver is not
   re-registered. libusb can then claim interface 0. Re-plug the camera to restore
   the normal mass-storage driver. */
#import <Foundation/Foundation.h>
#import <IOUSBHost/IOUSBHost.h>
#import <IOKit/IOKitLib.h>
#include <stdio.h>
#include <unistd.h>

int main(void) {
    @autoreleasepool {
        printf("euid=%d\n", geteuid());
        if (geteuid() != 0) { printf("need root\n"); return 9; }
        io_iterator_t iter = 0;
        IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching("IOUSBHostDevice"), &iter);
        io_service_t svc = 0, cur;
        while ((cur = IOIteratorNext(iter))) {
            CFTypeRef v = IORegistryEntryCreateCFProperty(cur, CFSTR("idVendor"), kCFAllocatorDefault, 0);
            uint16_t vv = 0; if (v) { CFNumberGetValue(v, kCFNumberSInt16Type, &vv); CFRelease(v); }
            if (vv == 0x054c) { svc = cur; break; }
            IOObjectRelease(cur);
        }
        IOObjectRelease(iter);
        if (!svc) { printf("no device\n"); return 1; }
        dispatch_queue_t q = dispatch_queue_create("t", NULL);
        NSError *err = nil;
        IOUSBHostDevice *dev = [[IOUSBHostDevice alloc] initWithIOService:svc
            options:IOUSBHostObjectInitOptionsDeviceCapture queue:q error:&err interestHandler:nil];
        printf("capture: %s %s\n", dev ? "OK" : "FAIL", dev ? "" : err.description.UTF8String);
        if (!dev) return 2;
        sleep(1);
        [dev destroyWithOptions:IOUSBHostObjectDestroyOptionsDeviceSurrender];
        printf("surrendered\n");
        IOObjectRelease(svc);
    }
    return 0;
}
