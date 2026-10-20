/* SPDX-License-Identifier: MIT
 * Original implementation of a deliberately bounded keyboard-only HIDP subset.
 * Wire constants/layouts derive from Bluetooth HID and USB HID specifications.
 */
#include "digit_hid.h"
#include <string.h>

const uint8_t digit_keyboard_descriptor[] = {
    0x05,0x01, 0x09,0x06, 0xA1,0x01, 0x85,0x01,
    0x05,0x07, 0x19,0xE0, 0x29,0xE7, 0x15,0x00, 0x25,0x01,
    0x75,0x01, 0x95,0x08, 0x81,0x02,
    0x75,0x08, 0x95,0x01, 0x81,0x01,
    0x05,0x08, 0x19,0x01, 0x29,0x05, 0x75,0x01, 0x95,0x05,
    0x91,0x02, 0x75,0x03, 0x95,0x01, 0x91,0x01,
    0x05,0x07, 0x19,0x00, 0x29,0x65, 0x15,0x00, 0x25,0x65,
    0x75,0x08, 0x95,0x06, 0x81,0x00, 0xC0
};
const size_t digit_keyboard_descriptor_length = sizeof digit_keyboard_descriptor;
static void neutral(uint8_t report[DIGIT_REPORT_BYTES]) {
    memset(report, 0, DIGIT_REPORT_BYTES); report[0] = DIGIT_REPORT_ID;
}
static bool control_ready(const digit_hid *h) {
    return h->sdp_verified && h->peer_selected && h->paired && h->encrypted && h->control;
}
static bool live(const digit_hid *h) {
    return control_ready(h) && h->interrupt;
}
static void drop(digit_hid *h) {
    h->armed = false; h->head = h->count = 0;
    memset(h->queue, 0, sizeof h->queue);
    neutral(h->last);
}
void digit_hid_init(digit_hid *h) {
    memset(h, 0, sizeof *h); h->protocol = 1; h->next_token = 1; neutral(h->last);
}
bool digit_hid_verify_psms(digit_hid *h, uint16_t c, uint16_t i) {
    h->sdp_verified = c == DIGIT_CONTROL_PSM && i == DIGIT_INTERRUPT_PSM;
    if (!h->sdp_verified) { digit_hid_disconnect(h); h->faulted = true; }
    return h->sdp_verified;
}
void digit_hid_disconnect(digit_hid *h) {
    drop(h); h->paired = h->encrypted = h->control = h->interrupt = false;
    h->inflight = h->release_pending = false; h->inflight_token = 0;
    neutral(h->inflight_report); h->protocol = 1; h->leds = 0; h->suspended = false;
}
digit_result digit_hid_select_peer(digit_hid *h, const uint8_t peer[6]) {
    if (!peer || h->control || h->interrupt || h->inflight || h->faulted)
        return DIGIT_NOT_READY;
    digit_hid_disconnect(h); memcpy(h->peer, peer, 6); h->peer_selected = true;
    h->stopped = false; h->unplug_requested = false;
    return DIGIT_OK;
}
digit_result digit_hid_link(digit_hid *h, const uint8_t peer[6], bool paired,
                            bool encrypted, bool control, bool interrupt) {
    if (!peer || !h->peer_selected || memcmp(h->peer, peer, 6)) return DIGIT_INVALID;
    if ((h->control && !control) || (h->interrupt && !interrupt)
        || (h->encrypted && !encrypted) || (h->paired && !paired)) {
        digit_hid_disconnect(h); return DIGIT_NOT_READY;
    }
    h->paired = paired; h->encrypted = encrypted;
    h->control = control; h->interrupt = interrupt;
    return live(h) ? DIGIT_OK : DIGIT_NOT_READY;
}
digit_result digit_hid_arm(digit_hid *h) {
    if (!live(h) || h->faulted || h->stopped || h->suspended) return DIGIT_NOT_READY;
    if (h->armed) return DIGIT_OK; /* Idempotent: never erase a queued key-up. */
    if (h->inflight || h->release_pending) return DIGIT_NOT_READY;
    drop(h); h->armed = true; return DIGIT_OK;
}
void digit_hid_stop(digit_hid *h) {
    bool needs_release = h->armed || h->inflight || h->release_pending;
    drop(h); h->stopped = true;
    h->release_pending = needs_release && live(h) && !h->suspended;
}
digit_result digit_keyboard_report(uint8_t modifiers, const uint8_t *keys,
                                   size_t count, uint8_t out[DIGIT_REPORT_BYTES]) {
    if (!out || count > 6 || (count && !keys)) return DIGIT_INVALID;
    for (size_t i = 0; i < count; ++i) {
        if (keys[i] < 4 || keys[i] > 0x65) return DIGIT_INVALID;
        for (size_t j = 0; j < i; ++j) if (keys[i] == keys[j]) return DIGIT_INVALID;
    }
    neutral(out); out[1] = modifiers;
    if (count) memcpy(out + 3, keys, count);
    return DIGIT_OK;
}
digit_result digit_hid_enqueue(digit_hid *h, uint8_t modifiers,
                               const uint8_t *keys, size_t count) {
    uint8_t report[DIGIT_REPORT_BYTES];
    if (!h->armed || !live(h) || h->suspended || h->faulted || h->stopped)
        return DIGIT_NOT_READY;
    if (digit_keyboard_report(modifiers, keys, count, report) != DIGIT_OK)
        return DIGIT_INVALID;
    if (h->count == DIGIT_QUEUE_CAPACITY) {
        digit_hid_stop(h); h->faulted = true; return DIGIT_BACKPRESSURE;
    }
    memcpy(h->queue[(h->head + h->count) % DIGIT_QUEUE_CAPACITY], report,
           DIGIT_REPORT_BYTES); ++h->count; return DIGIT_OK;
}
digit_result digit_hid_next(digit_hid *h, digit_packet *out, uint64_t *token) {
    if (!out || !token) return DIGIT_INVALID;
    memset(out, 0, sizeof *out); *token = 0;
    if (h->inflight) return DIGIT_BUSY;
    if (!live(h) || h->suspended) return DIGIT_NOT_READY;
    if (!h->release_pending && (!h->armed || !h->count)) return DIGIT_EMPTY;
    if (h->next_token == UINT64_MAX) {
        digit_hid_disconnect(h); h->faulted = true; return DIGIT_NOT_READY;
    }
    h->inflight_release = h->release_pending;
    if (h->release_pending) { neutral(h->inflight_report); h->release_pending = false; }
    else {
        memcpy(h->inflight_report, h->queue[h->head], DIGIT_REPORT_BYTES);
        memset(h->queue[h->head], 0, DIGIT_REPORT_BYTES);
        h->head = (h->head + 1) % DIGIT_QUEUE_CAPACITY; --h->count;
    }
    h->inflight = true; h->inflight_token = h->next_token++;
    *token = h->inflight_token; out->bytes[0] = 0xA1;
    memcpy(out->bytes + 1, h->inflight_report, DIGIT_REPORT_BYTES);
    out->length = DIGIT_REPORT_BYTES + 1; return DIGIT_OK;
}
digit_result digit_hid_complete(digit_hid *h, uint64_t token, bool success) {
    if (!h->inflight || token != h->inflight_token) return DIGIT_STALE;
    h->inflight = false; h->inflight_token = 0;
    if (!success) { digit_hid_disconnect(h); h->faulted = true; return DIGIT_NOT_READY; }
    if (h->armed && !h->suspended) memcpy(h->last, h->inflight_report, DIGIT_REPORT_BYTES);
    neutral(h->inflight_report); return DIGIT_OK;
}
static digit_result handshake(digit_packet *p, uint8_t result) {
    p->bytes[0] = result; p->length = 1;
    return result ? DIGIT_INVALID : DIGIT_OK;
}
static digit_result output_report(digit_hid *h, const uint8_t *p, size_t n) {
    if (n != 2 || p[0] != DIGIT_REPORT_ID || (p[1] & 0xE0)) return DIGIT_INVALID;
    h->leds = p[1]; return DIGIT_OK;
}
digit_result digit_hid_control(digit_hid *h, const uint8_t *p, size_t n,
                               digit_packet *out) {
    if (!out) return DIGIT_INVALID;
    memset(out, 0, sizeof *out);
    if (!control_ready(h) || h->faulted || h->stopped) return DIGIT_NOT_READY;
    if (!p || !n || n > DIGIT_PACKET_BYTES) return handshake(out, 4);
    uint8_t kind = p[0] & 0xF0, param = p[0] & 0x0F;
    switch (kind) {
    case 0x60: /* GET_PROTOCOL: DATA/Other, followed by the protocol byte. */
        if (n != 1 || param) return handshake(out, 4);
        out->bytes[0] = 0xA0; out->bytes[1] = h->protocol; out->length = 2;
        return DIGIT_OK;
    case 0x70: /* SET_PROTOCOL: changing mode requires a fresh human arm. */
        if (n != 1 || param > 1) return handshake(out, 4);
        if (param != h->protocol) {
            bool active = h->armed || h->inflight || h->release_pending;
            drop(h); h->release_pending = active && !h->suspended; h->protocol = param;
        }
        return handshake(out, 0);
    case 0x40: { /* GET_REPORT, optional little-endian BufferSize. */
        uint8_t type = param & 3;
        bool sized = (param & 8) != 0;
        if ((param & 4) || (type != 1 && type != 2) || n != (sized ? 4u : 2u))
            return handshake(out, 4);
        if (p[1] != DIGIT_REPORT_ID) return handshake(out, 2);
        uint8_t report[DIGIT_REPORT_BYTES]; size_t length;
        if (type == 1) { memcpy(report, h->last, DIGIT_REPORT_BYTES); length = DIGIT_REPORT_BYTES; }
        else { report[0] = DIGIT_REPORT_ID; report[1] = h->leds; length = 2; }
        if (sized) {
            size_t limit = (size_t)p[2] | ((size_t)p[3] << 8);
            if (!limit) return handshake(out, 4);
            if (limit < length) length = limit;
        }
        out->bytes[0] = (uint8_t)(0xA0 | type);
        memcpy(out->bytes + 1, report, length); out->length = length + 1;
        return DIGIT_OK;
    }
    case 0x50: /* Only the declared LED output report is writable. */
        if (param != 2 || n != 3) return handshake(out, 4);
        if (p[1] != DIGIT_REPORT_ID) return handshake(out, 2);
        return handshake(out, output_report(h, p + 1, n - 1) == DIGIT_OK ? 0 : 4);
    case 0x10:
        if (n != 1) return handshake(out, 4);
        if (param == 0) return DIGIT_OK;
        if (param == 3) {
            drop(h); h->release_pending = false; h->suspended = true; return DIGIT_OK;
        }
        if (param == 4) { h->suspended = false; return DIGIT_OK; }
        if (param == 5) {
            digit_hid_disconnect(h); h->peer_selected = false;
            h->stopped = true; h->unplug_requested = true; return DIGIT_OK;
        }
        return handshake(out, 3);
    default: return handshake(out, 3);
    }
}
digit_result digit_hid_interrupt(digit_hid *h, const uint8_t *p, size_t n) {
    if (!live(h) || h->faulted || h->stopped) return DIGIT_NOT_READY;
    if (!p || n != 3 || p[0] != 0xA2 || output_report(h, p + 1, n - 1) != DIGIT_OK) {
        digit_hid_stop(h); h->faulted = true; return DIGIT_INVALID;
    }
    return DIGIT_OK;
}
