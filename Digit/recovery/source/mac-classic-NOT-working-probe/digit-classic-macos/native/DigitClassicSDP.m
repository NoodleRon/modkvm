/* SPDX-License-Identifier: MIT
 * Original public-API-only macOS capability probe. UNCOMPILED in this release.
 */
#import "DigitClassicSDP.h"
#import "digit_hid.h"

static NSError *Failure(NSString *message) {
    return [NSError errorWithDomain:@"DigitClassicProbe" code:1
                          userInfo:@{NSLocalizedDescriptionKey: message}];
}
static NSData *UUID16(uint16_t value) {
    uint8_t bytes[] = {(uint8_t)(value >> 8), (uint8_t)value};
    return [NSData dataWithBytes:bytes length:sizeof bytes];
}
static NSDictionary *UInt16(uint16_t value) {
    return @{@"DataElementType": @1, @"DataElementSize": @2,
             @"DataElementValue": @(value)};
}
static NSArray *ProtocolList(uint16_t psm) {
    return @[@[UUID16(0x0100), UInt16(psm)], @[UUID16(0x0011)]];
}
/* Read actual published attributes, not the request dictionary. Strict shape
 * rejects missing, ambiguous, non-numeric, wrong-type and reassigned PSMs. */
static BOOL ReadPSM(IOBluetoothSDPDataElement *element, BOOL additional, uint16_t *psm) {
    if ([element getTypeDescriptor] != 6) return NO;
    NSArray *outer = [element getArrayValue];
    if (additional) {
        if (outer.count != 1 || ![outer[0] isKindOfClass:[IOBluetoothSDPDataElement class]]
            || [outer[0] getTypeDescriptor] != 6) return NO;
        outer = [outer[0] getArrayValue];
    }
    if (outer.count != 2) return NO;
    if (![outer[0] isKindOfClass:[IOBluetoothSDPDataElement class]] ||
        ![outer[1] isKindOfClass:[IOBluetoothSDPDataElement class]]) return NO;
    if ([outer[0] getTypeDescriptor] != 6 || [outer[1] getTypeDescriptor] != 6) return NO;
    NSArray *l2cap = [outer[0] getArrayValue];
    NSArray *hidp = [outer[1] getArrayValue];
    if (l2cap.count != 2 || hidp.count != 1) return NO;
    for (id item in l2cap) if (![item isKindOfClass:[IOBluetoothSDPDataElement class]]) return NO;
    if (![hidp[0] isKindOfClass:[IOBluetoothSDPDataElement class]]) return NO;
    IOBluetoothSDPUUID *l2capUUID = [l2cap[0] getUUIDValue];
    IOBluetoothSDPUUID *hidpUUID = [hidp[0] getUUIDValue];
    if (![l2capUUID isEqual:[IOBluetoothSDPUUID uuid16:0x0100]] ||
        ![hidpUUID isEqual:[IOBluetoothSDPUUID uuid16:0x0011]]) return NO;
    IOBluetoothSDPDataElement *number = l2cap[1];
    NSNumber *value = [number getNumberValue];
    if ([number getTypeDescriptor] != 1 || [number getSize] != 2 ||
        !value || value.unsignedLongLongValue > UINT16_MAX) return NO;
    *psm = value.unsignedShortValue; return YES;
}
@interface DigitClassicSDP ()
@property(nonatomic, strong, nullable) IOBluetoothSDPServiceRecord *record;
@property(nonatomic, strong, nullable) IOBluetoothUserNotification *controlNotification;
@property(nonatomic, strong, nullable) IOBluetoothUserNotification *interruptNotification;
@property(nonatomic, readwrite) BOOL exactPSMsVerified;
@property(nonatomic, readwrite) NSUInteger observedIncomingChannels;
@end
@implementation DigitClassicSDP
- (BOOL)publishAndVerify:(NSError **)error {
    if (![NSThread isMainThread] || self.record) {
        if (error) *error = Failure(@"Requires main thread and no existing publication");
        return NO;
    }
    /* No persistent record, TargetApplication, discoverability change, pairing,
     * daemon modification, private API, adapter-class change or input path. */
    NSDictionary *dictionary = @{
        @"0001 ServiceClassIDList": @[UUID16(DIGIT_HID_SERVICE_UUID)],
        @"0004 ProtocolDescriptorList": ProtocolList(DIGIT_CONTROL_PSM),
        @"000D AdditionalProtocolDescriptorLists": @[ProtocolList(DIGIT_INTERRUPT_PSM)],
        @"0100 ServiceName": @"Digit capability probe (incomplete HID service)",
        @"LocalAttributes": @{@"Persistent": @NO}
    };
    self.record = [IOBluetoothSDPServiceRecord publishedServiceRecordWithDictionary:dictionary];
    if (!self.record) {
        if (error) *error = Failure(@"Transient SDP publication failed"); return NO;
    }
    IOBluetoothSDPDataElement *classes = [self.record getAttributeDataElement:0x0001];
    NSArray *classList = [classes getArrayValue];
    BOOL hidClass = [classes getTypeDescriptor] == 6 && classList.count == 1
        && [classList[0] isKindOfClass:[IOBluetoothSDPDataElement class]]
        && [[classList[0] getUUIDValue] isEqual:[IOBluetoothSDPUUID uuid16:DIGIT_HID_SERVICE_UUID]];
    uint16_t control = 0, interrupt = 0;
    BOOL exact = hidClass && ReadPSM([self.record getAttributeDataElement:0x0004], NO, &control)
        && ReadPSM([self.record getAttributeDataElement:0x000D], YES, &interrupt)
        && control == DIGIT_CONTROL_PSM && interrupt == DIGIT_INTERRUPT_PSM;
    if (!exact) {
        NSError *cleanup = nil; BOOL removed = [self remove:&cleanup];
        if (error) *error = Failure([NSString stringWithFormat:
            @"PSM gate failed (control 0x%04x, interrupt 0x%04x). %@",
            control, interrupt, removed ? @"Record removed; no workaround attempted."
                                        : @"Removal failed; exit this probe to retire transient record."]);
        return NO;
    }
    self.controlNotification = [IOBluetoothL2CAPChannel registerForChannelOpenNotifications:self
        selector:@selector(channelOpened:channel:) withPSM:DIGIT_CONTROL_PSM
        direction:kIOBluetoothUserNotificationChannelDirectionIncoming];
    self.interruptNotification = [IOBluetoothL2CAPChannel registerForChannelOpenNotifications:self
        selector:@selector(channelOpened:channel:) withPSM:DIGIT_INTERRUPT_PSM
        direction:kIOBluetoothUserNotificationChannelDirectionIncoming];
    if (!self.controlNotification || !self.interruptNotification) {
        BOOL removed = [self remove:NULL];
        if (error) *error = Failure(removed ? @"Channel notification registration failed; record removed"
                                           : @"Notification registration/removal failed; exit probe");
        return NO;
    }
    self.exactPSMsVerified = YES; return YES;
}
- (void)channelOpened:(IOBluetoothUserNotification *)notification
             channel:(IOBluetoothL2CAPChannel *)channel {
    (void)notification;
    ++self.observedIncomingChannels;
    /* Notifications do not establish ownership. Do not close or attach a
     * delegate to possibly OS-owned traffic. Probe mode never writes reports. */
    (void)channel;
}
- (BOOL)remove:(NSError **)error {
    if (![NSThread isMainThread]) {
        if (error) *error = Failure(@"Teardown requires the main thread"); return NO;
    }
    self.exactPSMsVerified = NO;
    [self.controlNotification unregister]; self.controlNotification = nil;
    [self.interruptNotification unregister]; self.interruptNotification = nil;
    if (self.record) {
        IOReturn status = [self.record removeServiceRecord];
        if (status != kIOReturnSuccess) {
            if (error) *error = Failure([NSString stringWithFormat:@"SDP removal failed: 0x%08x", status]);
            return NO;
        }
        self.record = nil;
    }
    return YES;
}
@end
