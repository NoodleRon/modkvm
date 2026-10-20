# Primary sources consulted

Consulted 2026-10-06. These links describe public Windows APIs; they are not proof
that this source compiles or a particular host accepts this GATT profile.

- Microsoft GATT server overview, reserved services and generated descriptors:
  https://learn.microsoft.com/en-us/windows/apps/develop/devices-sensors/gatt-server
- Peripheral capability gate:
  https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.bluetoothadapter.isperipheralrolesupported
- Targeted notification overload:
  https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.genericattributeprofile.gattlocalcharacteristic.notifyvalueasync
- Notification result:
  https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.genericattributeprofile.gattclientnotificationresult
- Session and its status event:
  https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.genericattributeprofile.gattsession
- Advertisement statuses:
  https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.genericattributeprofile.gattserviceprovideradvertisementstatus
- Characteristic parameters/protection levels:
  https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.genericattributeprofile.gattlocalcharacteristicparameters
- Read/write request APIs:
  https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.genericattributeprofile.gattreadrequest
  https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.genericattributeprofile.gattwriterequest
- Microsoft BluetoothLEExplorer VirtualKeyboard reference (moving branch, not
  vendored or pinned; no upstream source bytes are incorporated here):
  https://github.com/microsoft/BluetoothLEExplorer/blob/master/BluetoothLEExplorer/BluetoothLEExplorer/Models/VirtualKeyboard.cs

## Implementation interpretation

The prototype requests encrypted attribute access and uses the explicitly
selected-client notification overload. The OS controls pairing, generated CCCD,
and security negotiation. Encrypted access is not proof of authenticated peer
identity; verify the selected receiver manually. Advertised HID UUID and a valid
Report Map do not establish complete HOGP support. The Windows system-reserved
DIS restriction specifically requires receiver-side qualification of PnP ID and
other system-provided profile fields before any compatibility claim.
