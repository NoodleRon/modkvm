/* SPDX-License-Identifier: MIT */
#include "digit_hid.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static unsigned checks;
#define CHECK(x) do { ++checks; assert(x); } while (0)
static const uint8_t peer[6] = {1,2,3,4,5,6};
static digit_hid ready(void) {
    digit_hid h; digit_hid_init(&h);
    CHECK(digit_hid_verify_psms(&h, 0x11, 0x13));
    CHECK(digit_hid_select_peer(&h, peer) == DIGIT_OK);
    CHECK(digit_hid_link(&h, peer, true, true, true, true) == DIGIT_OK);
    return h;
}
static void test_encoder(void) {
    uint8_t r[9], keys[] = {4,5,6,7,8,0x65};
    CHECK(digit_keyboard_report(0x82, keys, 6, r) == DIGIT_OK);
    CHECK(!memcmp(r, (uint8_t[]){1,0x82,0,4,5,6,7,8,0x65}, 9));
    CHECK(digit_keyboard_report(0, NULL, 0, r) == DIGIT_OK);
    CHECK(!memcmp(r, (uint8_t[]){1,0,0,0,0,0,0,0,0}, 9));
    CHECK(digit_keyboard_report(0, keys, 7, r) == DIGIT_INVALID);
    CHECK(digit_keyboard_report(0, NULL, 1, r) == DIGIT_INVALID);
    CHECK(digit_keyboard_report(0, keys, 1, NULL) == DIGIT_INVALID);
    for (unsigned k = 0; k < 256; ++k) {
        uint8_t key = (uint8_t)k;
        CHECK((digit_keyboard_report(0, &key, 1, r) == DIGIT_OK) == (k >= 4 && k <= 0x65));
    }
    CHECK(digit_keyboard_report(0, (uint8_t[]){4,4}, 2, r) == DIGIT_INVALID);
}
static void test_gates(void) {
    for (unsigned flags = 0; flags < 16; ++flags) {
        digit_hid h; digit_hid_init(&h);
        CHECK(digit_hid_arm(&h) == DIGIT_NOT_READY);
        CHECK(digit_hid_verify_psms(&h, 0x11, 0x13));
        CHECK(digit_hid_select_peer(&h, peer) == DIGIT_OK);
        (void)digit_hid_link(&h, peer, flags&1, flags&2, flags&4, flags&8);
        CHECK((digit_hid_arm(&h) == DIGIT_OK) == (flags == 15));
    }
    digit_hid h = ready();
    CHECK(digit_hid_enqueue(&h, 0, (uint8_t[]){4}, 1) == DIGIT_NOT_READY);
    CHECK(digit_hid_link(&h, (uint8_t[]){6,5,4,3,2,1}, true,true,true,true) == DIGIT_INVALID);
    CHECK(digit_hid_arm(&h) == DIGIT_OK);
    CHECK(!digit_hid_verify_psms(&h, 0x1001, 0x13));
    CHECK(!h.armed && !h.control && h.faulted);
    CHECK(digit_hid_verify_psms(&h, 0x11, 0x13));
    CHECK(digit_hid_select_peer(&h, peer) == DIGIT_NOT_READY);
}
static void test_queue_stop(void) {
    digit_hid h = ready(); digit_packet p; uint64_t token;
    CHECK(digit_hid_arm(&h) == DIGIT_OK);
    CHECK(digit_hid_enqueue(&h, 2, (uint8_t[]){4}, 1) == DIGIT_OK);
    CHECK(digit_hid_next(&h, &p, &token) == DIGIT_OK);
    CHECK(p.length == 10 && !memcmp(p.bytes,(uint8_t[]){0xA1,1,2,0,4,0,0,0,0,0},10));
    uint64_t first = token;
    CHECK(digit_hid_next(&h, &p, &token) == DIGIT_BUSY);
    CHECK(digit_hid_enqueue(&h, 0, (uint8_t[]){5}, 1) == DIGIT_OK);
    digit_hid_stop(&h);
    CHECK(h.count == 0 && !h.armed && h.release_pending);
    CHECK(digit_hid_complete(&h, first, true) == DIGIT_OK);
    CHECK(digit_hid_next(&h, &p, &token) == DIGIT_OK);
    CHECK(!memcmp(p.bytes,(uint8_t[]){0xA1,1,0,0,0,0,0,0,0,0},10));
    CHECK(digit_hid_complete(&h, token, true) == DIGIT_OK);
    CHECK(digit_hid_next(&h, &p, &token) == DIGIT_EMPTY);
    CHECK(digit_hid_arm(&h) == DIGIT_NOT_READY);
}
static void test_backpressure_disconnect(void) {
    digit_hid h = ready(); digit_packet p; uint64_t token;
    CHECK(digit_hid_arm(&h) == DIGIT_OK);
    for (unsigned i=0; i<DIGIT_QUEUE_CAPACITY; ++i)
        CHECK(digit_hid_enqueue(&h, 0, (uint8_t[]){4}, 1) == DIGIT_OK);
    CHECK(digit_hid_enqueue(&h, 0, (uint8_t[]){4}, 1) == DIGIT_BACKPRESSURE);
    CHECK(h.count == 0 && !h.armed && h.faulted);
    CHECK(digit_hid_next(&h, &p, &token) == DIGIT_OK);
    CHECK(p.bytes[4] == 0);
    digit_hid_disconnect(&h);
    CHECK(digit_hid_complete(&h, token, true) == DIGIT_STALE);
    CHECK(digit_hid_link(&h, peer,true,true,true,true) == DIGIT_OK);
    CHECK(digit_hid_next(&h,&p,&token) == DIGIT_EMPTY);
    CHECK(digit_hid_arm(&h) == DIGIT_NOT_READY);
    h = ready(); CHECK(digit_hid_arm(&h) == DIGIT_OK);
    CHECK(digit_hid_enqueue(&h, 0, (uint8_t[]){4}, 1) == DIGIT_OK);
    CHECK(digit_hid_next(&h,&p,&token) == DIGIT_OK);
    uint64_t old = token;
    digit_hid_disconnect(&h);
    CHECK(digit_hid_link(&h,peer,true,true,true,true) == DIGIT_OK);
    CHECK(digit_hid_arm(&h) == DIGIT_OK);
    CHECK(digit_hid_enqueue(&h, 0, NULL, 0) == DIGIT_OK);
    CHECK(digit_hid_next(&h,&p,&token) == DIGIT_OK);
    CHECK(token != old);
    CHECK(digit_hid_complete(&h,old,true) == DIGIT_STALE);
    CHECK(digit_hid_complete(&h,token,false) == DIGIT_NOT_READY);
    CHECK(!h.armed && !h.control && h.faulted && !h.release_pending);
}
static void request(digit_hid *h, const uint8_t *p, size_t n,
                    const uint8_t *expected, size_t en) {
    digit_packet response;
    (void)digit_hid_control(h,p,n,&response);
    CHECK(response.length == en);
    CHECK(!memcmp(response.bytes,expected,en));
}
static void test_protocol(void) {
    digit_hid h = ready();
    request(&h,(uint8_t[]){0x60},1,(uint8_t[]){0xA0,1},2);
    request(&h,(uint8_t[]){0x61},1,(uint8_t[]){4},1);
    request(&h,(uint8_t[]){0x70},1,(uint8_t[]){0},1);
    request(&h,(uint8_t[]){0x60},1,(uint8_t[]){0xA0,0},2);
    request(&h,(uint8_t[]){0x72},1,(uint8_t[]){4},1);
    request(&h,(uint8_t[]){0x41,1},2,(uint8_t[]){0xA1,1,0,0,0,0,0,0,0,0},10);
    request(&h,(uint8_t[]){0x49,1,3,0},4,(uint8_t[]){0xA1,1,0,0},4);
    request(&h,(uint8_t[]){0x49,1,0,1},4,(uint8_t[]){0xA1,1,0,0,0,0,0,0,0,0},10);
    request(&h,(uint8_t[]){0x49,1,0,0},4,(uint8_t[]){4},1);
    request(&h,(uint8_t[]){0x41,2},2,(uint8_t[]){2},1);
    request(&h,(uint8_t[]){0x45,1},2,(uint8_t[]){4},1);
    request(&h,(uint8_t[]){0x52,1,0x1F},3,(uint8_t[]){0},1);
    request(&h,(uint8_t[]){0x42,1},2,(uint8_t[]){0xA2,1,0x1F},3);
    request(&h,(uint8_t[]){0x52,1,0x80},3,(uint8_t[]){4},1);
    request(&h,(uint8_t[]){0x52,2,0},3,(uint8_t[]){2},1);
    request(&h,(uint8_t[]){0x51,1,0},3,(uint8_t[]){4},1);
    request(&h,(uint8_t[]){0xB1},1,(uint8_t[]){3},1);
    CHECK(digit_hid_interrupt(&h,(uint8_t[]){0xA2,1,2},3) == DIGIT_OK);
    CHECK(h.leds == 2);
    CHECK(digit_hid_arm(&h) == DIGIT_OK);
    request(&h,(uint8_t[]){0x71},1,(uint8_t[]){0},1);
    CHECK(!h.armed && h.release_pending);
    request(&h,(uint8_t[]){0x70},1,(uint8_t[]){0},1);
    CHECK(h.release_pending);
    digit_packet packet; uint64_t token;
    CHECK(digit_hid_next(&h,&packet,&token) == DIGIT_OK);
    CHECK(digit_hid_complete(&h,token,true) == DIGIT_OK);
    CHECK(digit_hid_arm(&h) == DIGIT_OK);
    request(&h,(uint8_t[]){0x13},1,(uint8_t[]){0},0);
    CHECK(h.suspended && !h.armed && !h.release_pending);
    request(&h,(uint8_t[]){0x14},1,(uint8_t[]){0},0);
    CHECK(!h.suspended && !h.armed);
    request(&h,(uint8_t[]){0x15},1,(uint8_t[]){0},0);
    CHECK(h.unplug_requested && !h.peer_selected && !h.control && h.stopped);
}
static void test_control_before_interrupt(void) {
    digit_hid h; digit_hid_init(&h);
    CHECK(digit_hid_verify_psms(&h,0x11,0x13));
    CHECK(digit_hid_select_peer(&h,peer) == DIGIT_OK);
    CHECK(digit_hid_link(&h,peer,true,true,true,false) == DIGIT_NOT_READY);
    request(&h,(uint8_t[]){0x70},1,(uint8_t[]){0},1);
    request(&h,(uint8_t[]){0x60},1,(uint8_t[]){0xA0,0},2);
    CHECK(digit_hid_arm(&h) == DIGIT_NOT_READY);
    CHECK(digit_hid_link(&h,peer,true,true,true,true) == DIGIT_OK);
    CHECK(h.protocol == 0);
    CHECK(digit_hid_arm(&h) == DIGIT_OK);
}
static void test_rearm_is_idempotent(void) {
    digit_hid h=ready(); digit_packet packet; uint64_t token;
    CHECK(digit_hid_arm(&h) == DIGIT_OK);
    CHECK(digit_hid_enqueue(&h,2,(uint8_t[]){4},1) == DIGIT_OK);
    CHECK(digit_hid_next(&h,&packet,&token) == DIGIT_OK);
    CHECK(digit_hid_complete(&h,token,true) == DIGIT_OK);
    CHECK(h.last[1] == 2 && h.last[3] == 4);
    CHECK(digit_hid_enqueue(&h,0,NULL,0) == DIGIT_OK);
    CHECK(digit_hid_arm(&h) == DIGIT_OK);
    CHECK(h.count == 1 && h.last[1] == 2 && h.last[3] == 4);
    CHECK(digit_hid_next(&h,&packet,&token) == DIGIT_OK);
    CHECK(!memcmp(packet.bytes,(uint8_t[]){0xA1,1,0,0,0,0,0,0,0,0},10));
    CHECK(digit_hid_arm(&h) == DIGIT_OK);
    CHECK(h.inflight && h.inflight_token == token);
    CHECK(digit_hid_complete(&h,token,true) == DIGIT_OK);
}
static void test_security_loss(void) {
    digit_hid h = ready(); digit_packet p; uint64_t token;
    CHECK(digit_hid_arm(&h) == DIGIT_OK);
    CHECK(digit_hid_enqueue(&h,0,(uint8_t[]){4},1) == DIGIT_OK);
    CHECK(digit_hid_link(&h,peer,true,false,true,true) == DIGIT_NOT_READY);
    CHECK(h.count == 0 && !h.armed && !h.control);
    CHECK(digit_hid_next(&h,&p,&token) == DIGIT_NOT_READY);
}
static void test_bounded_parser(void) {
    uint8_t bytes[32]; uint32_t random = 0x12345678;
    for (unsigned n=0; n<=sizeof bytes; ++n) for (unsigned header=0; header<256; ++header) {
        for (size_t i=0;i<sizeof bytes;++i) {
            random ^= random << 13; random ^= random >> 17; random ^= random << 5;
            bytes[i]=(uint8_t)random;
        }
        bytes[0]=(uint8_t)header; digit_hid h=ready(); digit_packet p;
        (void)digit_hid_control(&h,bytes,n,&p);
        CHECK(p.length <= DIGIT_PACKET_BYTES && h.count <= DIGIT_QUEUE_CAPACITY);
        CHECK(!h.armed);
    }
    digit_hid h=ready(); digit_packet p;
    CHECK(digit_hid_control(&h,NULL,10,&p) == DIGIT_INVALID && p.length==1);
    CHECK(digit_hid_control(&h,NULL,0,NULL) == DIGIT_INVALID);
    CHECK(digit_hid_interrupt(&h,NULL,3)==DIGIT_INVALID);
    CHECK(!h.armed && h.faulted);
}
int main(void) {
    test_encoder(); test_gates(); test_queue_stop(); test_backpressure_disconnect();
    test_protocol(); test_control_before_interrupt(); test_rearm_is_idempotent();
    test_security_loss(); test_bounded_parser();
    printf("PASS: %u assertions, including 8448 bounded parser cases\n",checks);
    return 0;
}
