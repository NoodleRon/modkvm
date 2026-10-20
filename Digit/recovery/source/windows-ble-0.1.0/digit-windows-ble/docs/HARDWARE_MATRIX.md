# Hardware qualification matrix

All rows are **NOT RUN**. The controller in this bundle is Windows BLE only.
The separate macOS controller experiment must be qualified independently.

| Controller | Receiver | Native compile | Peripheral role | GATT/security | OS HID binding | Actual fixed input | Stop/fault cases | Overall |
|---|---|---|---|---|---|---|---|---|
| Windows 11, exact build + adapter TBD | macOS, exact version/model TBD | Not run | Not run | Not run | Not run | Not run | Not run | Unqualified |
| Windows 11, exact build + adapter TBD | Windows 11, exact build/adapter TBD | Not run | Not run | Not run | Not run | Not run | Not run | Unqualified |
| Windows 10, exact supported build + adapter TBD | macOS, exact version/model TBD | Not run | Not run | Not run | Not run | Not run | Not run | Unqualified |
| Windows 10, exact supported build + adapter TBD | Windows 10/11, exact build/adapter TBD | Not run | Not run | Not run | Not run | Not run | Not run | Unqualified |

Repeat a row for every distinct OS/adapter/driver pair. Never infer compatibility
from Bluetooth version, one successful pair, or peripheral-role support alone.

## Gate order and evidence

1. **Build/package:** native tests, actual C#/WinRT build, x64 DLL load, MSIX
   identity, declared Bluetooth capability. Record compiler and packaging logs
2. **Adapter/driver:** verify `IsPeripheralRoleSupported`, driver version, radio
   enabled and exact hardware. Fail closed if unavailable
3. **GATT publication:** record both service advertisement statuses and errors;
   this prototype requires `Started`, rejecting partial advertisement status.
   Inspect 1812, 180F, Report Map, HID Information, Control Point, Report 2A4D,
   Report Reference 2908={1,1}, and one OS-generated CCCD per notifiable attribute
4. **Security:** inspect reads/writes/subscriptions over an unencrypted link,
   pairing/encryption negotiation, and behavior after bond removal. Confirm report
   notifications cannot bypass encryption in the real Windows stack. Configuring
   `EncryptionRequired` is not on-air proof or peer authentication by itself
5. **System-owned profile fields:** enumerate receiver-side DIS 180A/PnP ID 2A50,
   GAP Appearance, and relevant mandatory HOGP fields. Record actual values and
   HID driver's reason if it rejects the device. Do not add a reserved service,
   invent a vendor ID or declare HOGP compliance to get past this gate
6. **Native host binding:** establish that macOS/Windows binds its HID keyboard
   driver without a receiver agent. Pairing alone is insufficient
7. **Peer selection:** one active subscriber is required. Explicitly select and
   verify identity. Unselected encrypted reads return only a neutral report.
   A second subscriber must disarm; no data notification may reach that peer
8. **Fixed input:** empty text editor, US layout, physical stop/disconnect ready.
   Arm => neutral; Send once => exactly `digit`; no Enter. Observe host results
   independently of notification API completion
9. **Failures:** stop during publication; stop while a report is pending; second
   send click; queue overflow via a test harness; disconnect before/after key-down;
   wrong completion token; duplicate completion; unsubscribe; reconnect; radio
   loss; receiver suspend/resume; sender lock/suspend; notification error/timeout;
   process exit. Confirm no queued input resumes without a fresh manual arm
10. **Release limits:** verify ordered best-effort neutral report to the original
    selected peer. If delivery is uncertain or impossible, confirm the visible
    warning, lockout, and receiver-side recovery. Never claim a release was
    received based only on a submitted notification or disconnected link

## Evidence template per run

- Run ID/date/operator; controller OS/build, adapter/driver, package hash
- Receiver OS/build/model/adapter and keyboard layout
- Actual service/characteristic/descriptor dump and security observations
- System DIS/PnP/Appearance fields and native HID driver bind evidence
- Selected peer verification (redact stable IDs before publishing logs)
- Input observed and stop/reconnect/fault outcomes
- Verdict: blocked at gate N, failed at gate N, or qualified exact matrix row

Boot protocol, LEDs, mouse, arbitrary Digit action batches and HOGP certification
are out of scope. Only broaden scope after the keyboard-first evidence is good.
