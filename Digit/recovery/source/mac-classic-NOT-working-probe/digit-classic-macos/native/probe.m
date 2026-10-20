/* SPDX-License-Identifier: MIT */
#import "DigitClassicSDP.h"
#include <signal.h>
#include <stdio.h>
#include <string.h>
static volatile sig_atomic_t interrupted;
static void stop_signal(int number) { (void)number; interrupted = 1; }
int main(int argc, const char **argv) {
    @autoreleasepool {
        if (argc != 2 || strcmp(argv[1], "--publish-transient-sdp")) {
            puts("Digit Classic macOS capability probe (native source unvalidated)");
            puts("Usage: digit-classic-probe --publish-transient-sdp");
            puts("Publishes an incomplete transient HID SDP record for at most 5 seconds.");
            puts("Verifies exact 0x0011/0x0013 PSMs; only observes incoming notifications.");
            puts("Does not pair, change discoverability, or send keyboard reports.");
            return argc == 2 && !strcmp(argv[1], "--help") ? 0 : 2;
        }
        signal(SIGINT, stop_signal); signal(SIGTERM, stop_signal);
        DigitClassicSDP *probe = [DigitClassicSDP new]; NSError *error = nil;
        if (![probe publishAndVerify:&error]) {
            fprintf(stderr, "BLOCKED: %s\n", error.localizedDescription.UTF8String);
            return 1;
        }
        puts("PASS: published record reads back both requested PSMs; listeners registered.");
        puts("This does not prove exclusive PSM ownership, a complete HID profile, pairing, or host compatibility.");
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:5.0];
        while (!interrupted && deadline.timeIntervalSinceNow > 0)
            [[NSRunLoop mainRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.05]];
        if (![probe remove:&error]) {
            fprintf(stderr, "CLEANUP ERROR: %s\n", error.localizedDescription.UTF8String); return 1;
        }
        printf("Removed transient record. Observed channel notifications: %lu. Input reports: 0.\n",
               (unsigned long)probe.observedIncomingChannels);
        return 0;
    }
}
