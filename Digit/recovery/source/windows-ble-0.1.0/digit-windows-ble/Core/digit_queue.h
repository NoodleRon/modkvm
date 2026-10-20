#pragma once
#include <cstdint>
#ifdef _WIN32
#define DIGIT_API extern "C" __declspec(dllexport)
#else
#define DIGIT_API extern "C"
#endif
// Calls must be serialized by the host. Tokens are never reused in one instance.
// Result: 0=no report/rejected, 1=accepted/report available, -1=fault.
DIGIT_API void* digit_create();
DIGIT_API void digit_destroy(void*);
DIGIT_API int digit_gate(void*, int eligible);
DIGIT_API int digit_arm(void*);
DIGIT_API int digit_fixed_test(void*);
DIGIT_API int digit_take(void*, std::uint8_t* eight_bytes, std::uint64_t* token);
DIGIT_API int digit_complete(void*, std::uint64_t token, int success);
DIGIT_API void digit_stop(void*);
DIGIT_API void digit_fault(void*);
DIGIT_API int digit_state(void*); // 0 disarmed, 1 armed, 2 faulted
DIGIT_API int digit_pending(void*);
DIGIT_API int digit_release_needed(void*); // consumes one emergency-release intent
DIGIT_API int digit_encode(std::uint8_t modifier, const std::uint8_t* usages, int count, std::uint8_t* eight_bytes);
