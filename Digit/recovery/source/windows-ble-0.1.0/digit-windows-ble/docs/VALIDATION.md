# Validation record

Date: 2026-10-06 UTC. Execution: Linux cloud workspace, GCC/G++ 14.2.0,
Python 3.12.14. No Windows/.NET SDK was installed or downloaded.

## Passed on the actual source

Command: `./scripts/test-portable.sh`

- C++17 compile with `-Wall -Wextra -Werror -pedantic`
- 51,877 portable assertions, including 10,000 deterministic-seed mixed state
  transitions: arm gating, fixed report ordering, held modifiers, invalid usages,
  six-key limit, duplicate usages, whole-batch overflow, one-in-flight restriction,
  stale/duplicate completion rejection, stop/fault flushing, release intent,
  delayed completion, gate loss, queue bounds and no implicit rearm
- Same 51,877 assertions under AddressSanitizer + UndefinedBehaviorSanitizer
- Independent wraparound stress harness: 10,000 repeated fixed batches and
  100,000 ordered report completions under ASan/UBSan; included as
  `Tests/test_wrap.cpp` and run by the same portable script
- Four Python checks: XML project/manifest parsing, actual HID descriptor yields
  one 64-bit input report with ID 1, required source security guards/reserved
  service exclusions, and only the explicit selected-client notification overload

The initial sanitizer execution hit LeakSanitizer's known ptrace-runtime failure.
The final script disables **only leak detection**, using
`ASAN_OPTIONS=detect_leaks=0`; ASan/UBSan then pass. Leak checking is not claimed.

## Independent source review

A separate reviewer read the portable core, adapter, descriptor and GUI, verified
relevant API members against Microsoft documentation, and reran portable tests.
Two concrete findings were fixed and re-reviewed:

1. A pending notification's cached pressed-key report could still be read by the
   selected peer after disarm. Reads now return neutral unless armed and eligible;
   Stop/Fault/timeout also clear the read snapshot immediately
2. A dynamically allocating queue could throw across the native C ABI and leave
   partial admission. The queue is now a fixed 16-report array ring, with no
   allocation during arm/admission/dequeue/completion

Additional review hardening: WinRT peer/session getter failures fault closed;
outer pump failures are contained; native DLL lookup is restricted to the app
assembly directory/system directory; teardown detaches GATT handlers and guards
late callbacks. No remaining concrete blocking source finding was reported.
This is code review, not native Windows execution or a security certification.

## Not run / not demonstrated

- MSVC native build and native DLL loading in the .NET process
- C# compilation, Windows SDK projections, WPF UI execution, and MSIX packaging
- Bluetooth capability permission, peripheral role and radio/driver behavior
- Actual GATT database publication or descriptor/CCCD enumeration
- Pairing, bonding, encrypted notifications or authenticated receiver identity
- System DIS/PnP ID/Appearance availability or complete HOGP conformance
- Native macOS/Windows HID driver binding or fixed keyboard input
- Windows lock/suspend event behavior and the on-air fault/release matrix
- Mac-to-anything controller behavior (outside this Windows controller bundle)

No device was paired or connected; no real input was sent. No network listener,
provider integration, public repository or accepted Digit core change was made.

## Why portable passes are limited evidence

The tested C++ core is exactly the source used by the Windows adapter via its C
ABI. This gives evidence for report admission and queue/state behavior. Python
source checks are guardrails rather than a compiler or semantic proof. Actual
WinRT calls, permission/package behavior, radio concurrency, receiver discovery,
security, notification delivery and host keyboard events need the separately
recorded Windows and hardware qualification gates.
