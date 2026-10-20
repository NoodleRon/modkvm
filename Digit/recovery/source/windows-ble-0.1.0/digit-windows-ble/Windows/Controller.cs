using System.Runtime.InteropServices.WindowsRuntime;
using System.Windows.Threading;
using Windows.ApplicationModel;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Storage.Streams;
namespace DigitWindowsBLE;

// All queue and mutable adapter access is serialized on the WPF dispatcher.
// Never connects outward, changes pairing, installs drivers, or broadcasts input.
internal sealed class Controller
{
    private readonly Dispatcher ui;
    private readonly NativeQueue queue = new();
    private readonly Action<string> log;
    private GattServiceProvider? hid, battery;
    private GattLocalCharacteristic? input, controlPoint;
    private GattSession? selectedSession;
    private string? selectedId;
    private bool starting, stopping, suspended, pumping, poisoned;
    private bool startedOnce, stopRequested, disposed;
    private byte[] current = new byte[8];
    public Controller(Dispatcher dispatcher, Action<string> logger) { ui=dispatcher; log=logger; }
    public int State => queue.State;
    public bool Busy => pumping || starting || stopping;
    public bool Poisoned => poisoned;
    private Task OnUi(Func<Task> action) => ui.InvokeAsync(action).Task.Unwrap();
    private void CheckUi() => ui.VerifyAccess();
    private static void Check(BluetoothError error, string operation)
    { if(error != BluetoothError.Success) throw new InvalidOperationException($"{operation}: {error}"); }
    private static bool Published(GattServiceProvider? p) => p?.AdvertisementStatus == GattServiceProviderAdvertisementStatus.Started;
    private void SnapshotFailed(Exception error)
    {
        poisoned=true; current=new byte[8]; queue.Fault();
        log("Peer/session query failed: "+error.Message+". Disarmed; restart required.");
    }
    private GattSubscribedClient[] Clients()
    {
        try { return input?.SubscribedClients.ToArray() ?? Array.Empty<GattSubscribedClient>(); }
        catch(Exception e) { SnapshotFailed(e); return Array.Empty<GattSubscribedClient>(); }
    }
    private static string Id(GattSubscribedClient c) => c.Session.DeviceId.Id;
    public string[] PeerIds() {
        CheckUi(); try { return Clients().Select(Id).Distinct().ToArray(); }
        catch(Exception e) { SnapshotFailed(e); return Array.Empty<string>(); }
    }
    private GattSubscribedClient? SelectedClient() {
        try { return Clients().FirstOrDefault(c => Id(c)==selectedId && c.Session.SessionStatus==GattSessionStatus.Active && c.MaxNotificationSize>=8); }
        catch(Exception e) { SnapshotFailed(e); return null; }
    }
    private bool Eligible() {
        try { return !disposed && !poisoned && !stopping && !suspended && Published(hid) && Published(battery) &&
            selectedId != null && Clients().Length==1 && SelectedClient()!=null && !poisoned; }
        catch(Exception e) { SnapshotFailed(e); return false; }
    }
    private void RefreshGate()
    {
        CheckUi(); if(disposed)return;
        if(!queue.Gate(Eligible())) { log("Peer/publication eligibility changed: disarmed; pending input discarded"); _=PumpAsync(); }
    }
    public async Task StartAsync()
    {
        CheckUi(); if(starting || startedOnce) throw new InvalidOperationException("Publication is one-shot per process. Restart the app to publish again.");
        starting=true; startedOnce=true;
        try {
            // Fail closed on missing identity; the package must declare bluetooth capability.
            _=Package.Current.Id;
            var adapter=await BluetoothAdapter.GetDefaultAsync();
            if(adapter==null || !adapter.IsPeripheralRoleSupported) throw new InvalidOperationException("No LE peripheral-capable adapter. Do not install a workaround driver.");
            log($"Peripheral capability present. Adapter: {adapter.DeviceId}. This is not HID compatibility proof.");
            var h=await GattServiceProvider.CreateAsync(HidProfile.Uuid(0x1812)); Check(h.Error,"HID service"); hid=h.ServiceProvider;
            var b=await GattServiceProvider.CreateAsync(HidProfile.Uuid(0x180F)); Check(b.Error,"Battery service"); battery=b.ServiceProvider;
            await StaticAsync(hid,0x2A4A,HidProfile.HidInformation);
            await StaticAsync(hid,0x2A4B,HidProfile.ReportMap);
            await StaticAsync(battery,0x2A19,new byte[]{100}); // explicit fixed development value
            var result=await hid.Service.CreateCharacteristicAsync(HidProfile.Uuid(0x2A4D),new GattLocalCharacteristicParameters {
                CharacteristicProperties=GattCharacteristicProperties.Read|GattCharacteristicProperties.Notify,
                ReadProtectionLevel=GattProtectionLevel.EncryptionRequired,
                WriteProtectionLevel=GattProtectionLevel.EncryptionRequired
            }); Check(result.Error,"Input report"); input=result.Characteristic;
            var descriptor=await input.CreateDescriptorAsync(HidProfile.Uuid(0x2908),new GattLocalDescriptorParameters {
                StaticValue=HidProfile.ReportReference.AsBuffer(), ReadProtectionLevel=GattProtectionLevel.EncryptionRequired
            }); Check(descriptor.Error,"Report Reference");
            // CCCD (0x2902) is generated by Windows. Never create a second one.
            input.ReadRequested+=ReadInput;
            input.SubscribedClientsChanged+=SubscriptionsChanged;
            var cp=await hid.Service.CreateCharacteristicAsync(HidProfile.Uuid(0x2A4C),new GattLocalCharacteristicParameters {
                CharacteristicProperties=GattCharacteristicProperties.WriteWithoutResponse,
                WriteProtectionLevel=GattProtectionLevel.EncryptionRequired
            }); Check(cp.Error,"Control Point"); controlPoint=cp.Characteristic; controlPoint.WriteRequested+=WriteControl;
            hid.AdvertisementStatusChanged+=AdvertisementChanged;
            battery.AdvertisementStatusChanged+=AdvertisementChanged;
            if(stopRequested) throw new OperationCanceledException("Publication was stopped before completion.");
            battery.StartAdvertising(new GattServiceProviderAdvertisingParameters{IsConnectable=true,IsDiscoverable=false});
            hid.StartAdvertising(new GattServiceProviderAdvertisingParameters{IsConnectable=true,IsDiscoverable=true});
            log("Publication requested. Pair manually in the receiver OS; then refresh and explicitly select its subscribed ID. Input is disarmed.");
        } catch {
            queue.Fault(); StopAdvertising(); throw;
        } finally { starting=false; RefreshGate(); }
    }
    private async Task StaticAsync(GattServiceProvider provider,ushort uuid,byte[] value)
    {
        var r=await provider.Service.CreateCharacteristicAsync(HidProfile.Uuid(uuid),new GattLocalCharacteristicParameters {
            CharacteristicProperties=GattCharacteristicProperties.Read, StaticValue=value.AsBuffer(),
            ReadProtectionLevel=GattProtectionLevel.EncryptionRequired
        }); Check(r.Error,$"Characteristic {uuid:x4}");
    }
    private void SubscriptionsChanged(GattLocalCharacteristic sender,object args)
    { ui.BeginInvoke(new Action(RefreshGate)); }
    private void AdvertisementChanged(GattServiceProvider sender,GattServiceProviderAdvertisementStatusChangedEventArgs args)
    { ui.BeginInvoke(new Action(()=>{log($"Advertisement: {args.Status}; error: {args.Error}"); RefreshGate();})); }
    private async void ReadInput(GattLocalCharacteristic sender,GattReadRequestedEventArgs args)
    {
        var deferral=args.GetDeferral();
        try {
            var request=await args.GetRequestAsync(); if(request==null) return;
            await OnUi(()=>{
                var value=!disposed && queue.State==1 && Eligible() && args.Session.DeviceId.Id==selectedId ? current : new byte[8];
                if(request.Offset>value.Length) request.RespondWithProtocolError(0x07);
                else request.RespondWithValue(value.Skip((int)request.Offset).Take((int)request.Length).ToArray().AsBuffer());
                return Task.CompletedTask;
            });
        } catch(Exception e) { await OnUi(()=>{Fault("Read failure: "+e.Message); return Task.CompletedTask;}); }
        finally { deferral.Complete(); }
    }
    private async void WriteControl(GattLocalCharacteristic sender,GattWriteRequestedEventArgs args)
    {
        var deferral=args.GetDeferral();
        try {
            var request=await args.GetRequestAsync(); if(request==null) return;
            await OnUi(()=>{
                using var reader=DataReader.FromBuffer(request.Value); var bytes=new byte[(int)reader.UnconsumedBufferLength]; reader.ReadBytes(bytes);
                byte error=0;
                if(disposed || args.Session.DeviceId.Id!=selectedId) error=0x08;
                else if(request.Offset!=0) error=0x07;
                else if(bytes.Length!=1 || bytes[0]>1) error=0x0D;
                else {
                    suspended=bytes[0]==0;
                    if(suspended) Fault("Receiver suspended HID input");
                    // Exit Suspend never rearms.
                    RefreshGate();
                }
                if(request.Option==GattWriteOption.WriteWithResponse) {
                    if(error!=0) request.RespondWithProtocolError(error); else request.Respond();
                }
                return Task.CompletedTask;
            });
        } catch(Exception e) { await OnUi(()=>{Fault("Control point failure: "+e.Message);return Task.CompletedTask;}); }
        finally { deferral.Complete(); }
    }
    public void Select(string id)
    {
        CheckUi(); if(Busy || queue.State==1 || queue.Pending!=0) throw new InvalidOperationException("Stop and wait for all notification operations before changing peers.");
        if(!PeerIds().Contains(id)) throw new InvalidOperationException("That subscriber is no longer present.");
        if(selectedSession!=null) selectedSession.SessionStatusChanged-=SessionChanged;
        selectedId=id; selectedSession=SelectedClient()?.Session;
        if(selectedSession!=null) selectedSession.SessionStatusChanged+=SessionChanged;
        suspended=false; RefreshGate(); log("Selected subscriber: "+id+". Verify its identity on both computers before Arm.");
    }
    private void SessionChanged(GattSession sender,GattSessionStatusChangedEventArgs args)
    { ui.BeginInvoke(new Action(()=>{if(args.Status!=GattSessionStatus.Active) Fault("Selected session disconnected");RefreshGate();})); }
    public async Task ArmAsync()
    {
        CheckUi(); RefreshGate(); if(Busy || !queue.Arm()) throw new InvalidOperationException("Cannot arm: require exactly one selected active subscriber, full publication, no unresolved operation, and no suspension.");
        log("Armed manually. Sending a neutral baseline to the selected peer."); await PumpAsync();
    }
    public async Task SendFixedAsync()
    {
        CheckUi(); RefreshGate(); if(!queue.EnqueueFixedTest()) { await PumpAsync(); throw new InvalidOperationException("Fixed test rejected. Queue overflow faults and discards the whole pending batch."); }
        await PumpAsync();
    }
    public void Fault(string reason)
    { CheckUi(); if(disposed)return; current=new byte[8]; queue.Fault(); log(reason+". Disarmed; no automatic retry."); _=PumpAsync(); }
    public async Task StopAsync()
    {
        CheckUi(); stopRequested=true; current=new byte[8]; queue.Stop(); stopping=true;
        log("Stopped: input queue cleared. Attempting one ordered neutral report if the original peer is still reachable.");
        await PumpAsync();
        // If an operation is pending, Pump's finally completes shutdown after it settles.
        if(!pumping) { StopAdvertising(); stopping=false; }
    }
    private void StopAdvertising()
    { try {hid?.StopAdvertising();} catch(Exception e){log("HID stop: "+e.Message);} try {battery?.StopAdvertising();} catch(Exception e){log("Battery stop: "+e.Message);} }
    private async Task<bool> NotifyAsync(byte[] bytes,GattSubscribedClient client)
    {
        // Only the explicit targeted overload exists in this application.
        var task=input!.NotifyValueAsync(bytes.AsBuffer(),client).AsTask();
        if(await Task.WhenAny(task,Task.Delay(2000))!=task) {
            poisoned=true; current=new byte[8]; queue.Fault(); log("Notification timed out; delivery is uncertain. No retry/rearm. Waiting for that operation before a release; disconnect the receiver if a key is held.");
            // Never race a release against a still-pending key-down. Stop remains responsive.
            // Do not cancel and then assume cancellation means non-delivery.
        }
        var result=await task;
        return result.Status==GattCommunicationStatus.Success;
    }
    private async Task PumpAsync()
    {
        CheckUi(); if(disposed || pumping) return; pumping=true;
        try {
            while(true) {
                RefreshGate();
                var bytes=new byte[8];
                if(!queue.Take(bytes,out var token)) break;
                bool success=false;
                try {
                    var peer=SelectedClient();
                    if(peer!=null && Eligible()) {current=(byte[])bytes.Clone();success=await NotifyAsync(bytes,peer);}
                } catch(Exception e){log("Notification failed: "+e.Message);}
                queue.Complete(token,success);
                if(!success) {poisoned=true;log("Notification result is failed/uncertain. Restart and requalify before any new input.");}
                await Task.Delay(25);
            }
            if(queue.ConsumeRelease()) {
                current=new byte[8]; var peer=SelectedClient();
                if(peer==null) {poisoned=true;log("Release undeliverable: selected peer is gone. Disconnect/reconnect at the receiver; no retry.");}
                else {
                    try {
                        if(!await NotifyAsync(current,peer)) {poisoned=true;log("Neutral report failed; host key state is uncertain. No retry.");}
                        else log("One neutral notification completed. This is transport status, not proof of host input delivery.");
                    } catch(Exception e){poisoned=true;log("Neutral report uncertain: "+e.Message);}
                }
            }
        } catch(Exception e) {
            poisoned=true; current=new byte[8]; queue.Fault();
            log("Unexpected transport failure: "+e.Message+". Disarmed; restart required; host state is uncertain.");
        } finally { pumping=false; if(stopping) {StopAdvertising();stopping=false;} }
    }
    // Called only after operations have settled. Abrupt process/OS death cannot guarantee release.
    public void DisposeWhenIdle()
    { CheckUi(); if(pumping || starting) throw new InvalidOperationException("Operation still pending."); StopAdvertising(); disposed=true;
        if(input!=null) {input.ReadRequested-=ReadInput;input.SubscribedClientsChanged-=SubscriptionsChanged;}
        if(controlPoint!=null) controlPoint.WriteRequested-=WriteControl;
        if(hid!=null) hid.AdvertisementStatusChanged-=AdvertisementChanged;
        if(battery!=null) battery.AdvertisementStatusChanged-=AdvertisementChanged;
        if(selectedSession!=null) selectedSession.SessionStatusChanged-=SessionChanged; queue.Dispose(); }
}
