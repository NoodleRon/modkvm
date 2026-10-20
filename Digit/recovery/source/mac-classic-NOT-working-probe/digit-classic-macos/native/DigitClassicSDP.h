/* SPDX-License-Identifier: MIT */
#import <Foundation/Foundation.h>
#import <IOBluetooth/IOBluetooth.h>

NS_ASSUME_NONNULL_BEGIN
/* Capability probe only: intentionally incomplete HID SDP profile.
 * All calls, callbacks and teardown must run on the main run loop. */
@interface DigitClassicSDP : NSObject
@property(nonatomic, readonly) BOOL exactPSMsVerified;
@property(nonatomic, readonly) NSUInteger observedIncomingChannels;
- (BOOL)publishAndVerify:(NSError * _Nullable * _Nullable)error;
- (BOOL)remove:(NSError * _Nullable * _Nullable)error;
@end
NS_ASSUME_NONNULL_END
