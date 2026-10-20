/* SPDX-License-Identifier: MIT */
#import <Foundation/Foundation.h>
#import <IOBluetooth/IOBluetooth.h>
#import "digit_hid.h"

NS_ASSUME_NONNULL_BEGIN
/* FUTURE ADAPTER CONTRACT, NOT AN IMPLEMENTATION OR A RUNNABLE TRANSPORT.
 * The capability executable does not implement or instantiate this protocol.
 * Swift callers may import this header via a bridging header.
 *
 * Native release gates before implementing/enabling this contract:
 *  - Full standards-correct SDP HID descriptor + mandatory Device ID record.
 *  - Public SDK compilation and packet capture on authorized macOS hardware.
 *  - Per-session explicit user-selected paired peer; authenticated/encrypted ACL.
 *  - Both inbound channels belong to that exact peer and expected PSMs.
 *  - Query isConnected, isPaired and getEncryptionMode before every write;
 *    a paired record alone does not prove encryption of a live connection.
 *  - Explicit human arm of the live session, never restored across reconnect.
 *  - Bounded one-in-flight writeAsync + retained buffer + completion deadline;
 *    stale callbacks cannot affect another session; no ambiguous-write retry.
 *  - Local Stop tries one neutral release only if the existing link is safe,
 *    then closes channels; disconnect erases work without reconnect/replay.
 *  - HID virtual unplug requires correct bond/virtual-cable lifecycle support;
 *    merely closing channels is insufficient. Never secretly delete OS bonds.
 *  - Handle sleep, adapter-off, security downgrade, peer changes and process exit.
 *  - OS-level host tests (Windows first, then macOS), user-present only.
 *
 * Peer selection/arm requests arrive from trusted local UI. No network API is
 * provided. The core has no authority to establish facts about the radio link.
 * Transport completion means local stack completion, not text delivered to an
 * app. Never convert it into Digit's application-level observation/ACK.
 */
@protocol DigitClassicTransport <NSObject>
@property(nonatomic, readonly, getter=isArmed) BOOL armed;
@property(nonatomic, readonly) BOOL nativeValidationComplete;
- (BOOL)selectExplicitPeer:(IOBluetoothDevice *)peer error:(NSError **)error;
- (BOOL)armCurrentSessionAfterHumanConfirmation:(NSError **)error;
- (BOOL)enqueueModifiers:(uint8_t)modifiers
                 usages:(NSData *)upToSixDistinctKeyboardUsages
                  error:(NSError **)error;
- (void)stopAndDiscardQueuedInput;
@end
NS_ASSUME_NONNULL_END
