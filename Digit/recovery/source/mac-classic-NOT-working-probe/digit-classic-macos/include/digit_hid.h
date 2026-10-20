/* SPDX-License-Identifier: MIT */
#ifndef DIGIT_HID_H
#define DIGIT_HID_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define DIGIT_CONTROL_PSM 0x0011u
#define DIGIT_INTERRUPT_PSM 0x0013u
#define DIGIT_HID_SERVICE_UUID 0x1124u
#define DIGIT_REPORT_ID 1u
#define DIGIT_REPORT_BYTES 9u
#define DIGIT_PACKET_BYTES 16u
#define DIGIT_QUEUE_CAPACITY 16u

/* Confined to one executor/thread. This core trusts link facts supplied by the
 * native adapter; they are not a substitute for querying actual link security. */
typedef enum {
    DIGIT_OK, DIGIT_NOT_READY, DIGIT_INVALID, DIGIT_BACKPRESSURE,
    DIGIT_EMPTY, DIGIT_BUSY, DIGIT_STALE
} digit_result;
typedef struct { uint8_t bytes[DIGIT_PACKET_BYTES]; size_t length; } digit_packet;
typedef struct {
    uint8_t peer[6];
    bool peer_selected, sdp_verified, paired, encrypted, control, interrupt;
    bool armed, suspended, faulted, stopped, release_pending, unplug_requested;
    uint8_t protocol, leds, last[DIGIT_REPORT_BYTES];
    uint8_t queue[DIGIT_QUEUE_CAPACITY][DIGIT_REPORT_BYTES];
    size_t head, count;
    uint64_t next_token, inflight_token;
    bool inflight, inflight_release;
    uint8_t inflight_report[DIGIT_REPORT_BYTES];
} digit_hid;
extern const uint8_t digit_keyboard_descriptor[];
extern const size_t digit_keyboard_descriptor_length;
void digit_hid_init(digit_hid *h);
/* Exact assigned PSMs only. Mismatch permanently closes this core until init. */
bool digit_hid_verify_psms(digit_hid *h, uint16_t control, uint16_t interrupt);
/* Caller must stop/close old channels BEFORE selecting another explicit peer. */
digit_result digit_hid_select_peer(digit_hid *h, const uint8_t peer[6]);
digit_result digit_hid_link(digit_hid *h, const uint8_t peer[6], bool paired,
                            bool encrypted, bool control, bool interrupt);
/* Called only in response to an explicit human action for this live session. */
digit_result digit_hid_arm(digit_hid *h);
/* Dropped work is never replayed; release is best effort on the current link. */
void digit_hid_stop(digit_hid *h);
void digit_hid_disconnect(digit_hid *h);
digit_result digit_keyboard_report(uint8_t modifiers, const uint8_t *keys,
                                   size_t count, uint8_t out[DIGIT_REPORT_BYTES]);
digit_result digit_hid_enqueue(digit_hid *h, uint8_t modifiers,
                               const uint8_t *keys, size_t count);
/* One in-flight write. Never retry a packet after an ambiguous transport error. */
digit_result digit_hid_next(digit_hid *h, digit_packet *out, uint64_t *token);
digit_result digit_hid_complete(digit_hid *h, uint64_t token, bool success);
/* One complete L2CAP SDU per call. No fragmentation or arbitrary buffering. */
digit_result digit_hid_control(digit_hid *h, const uint8_t *data,
                               size_t length, digit_packet *response);
digit_result digit_hid_interrupt(digit_hid *h, const uint8_t *data, size_t length);
#endif
