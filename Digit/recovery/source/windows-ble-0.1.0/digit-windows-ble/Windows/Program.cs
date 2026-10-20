using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;
namespace DigitWindowsBLE;
internal static class Program
{
    [STAThread] public static void Main()
    {
        var app=new Application(); app.Run(new ControlWindow());
    }
}
internal sealed class ControlWindow : Window
{
    private readonly TextBox output=new(){IsReadOnly=true,AcceptsReturn=true,VerticalScrollBarVisibility=ScrollBarVisibility.Auto,Height=290};
    private readonly ComboBox peers=new(){MinWidth=520};
    private readonly CheckBox consent=new(){Content="I verified the selected receiver and opened an empty US-layout text editor"};
    private readonly Controller controller;
    private readonly Button arm=new(){Content="Arm",Margin=new Thickness(4)}, send=new(){Content="Send fixed test: digit",Margin=new Thickness(4)};
    private readonly DispatcherTimer refresh=new(){Interval=TimeSpan.FromMilliseconds(250)};
    private bool closeAllowed;
    public ControlWindow()
    {
        Title="Digit Windows BLE · experimental keyboard controller"; Width=850;Height=580;
        var root=new StackPanel{Margin=new Thickness(16)}; Content=root;
        root.Children.Add(new TextBlock{Text="Source prototype. Windows build and hardware compatibility are unverified.\nThe receiver needs OS Bluetooth support; no receiver agent is supplied.\nNo input until you select one subscribed peer and click Arm. Stop also unpublishes.",TextWrapping=TextWrapping.Wrap});
        controller=new Controller(Dispatcher,Log);
        var buttons=new StackPanel{Orientation=Orientation.Horizontal}; root.Children.Add(buttons);
        Add(buttons,"Publish for manual pairing",async()=>await controller.StartAsync());
        Add(buttons,"Refresh peers",()=>{var previous=peers.SelectedItem as string;peers.ItemsSource=controller.PeerIds();peers.SelectedItem=previous;return Task.CompletedTask;});
        Add(buttons,"Select peer",()=>{controller.Select(peers.SelectedItem as string ?? throw new InvalidOperationException("Choose a subscribed peer first."));consent.IsChecked=false;return Task.CompletedTask;});
        root.Children.Add(peers);root.Children.Add(consent);
        var actions=new StackPanel{Orientation=Orientation.Horizontal};root.Children.Add(actions);actions.Children.Add(arm);actions.Children.Add(send);
        arm.Click+=async(_,__)=>await Run(async()=>{if(consent.IsChecked!=true)throw new InvalidOperationException("Verify the receiver first.");await controller.ArmAsync();});
        send.Click+=async(_,__)=>await Run(controller.SendFixedAsync);
        Add(actions,"Stop",controller.StopAsync);
        root.Children.Add(output);
        refresh.Tick+=(_,__)=>{arm.IsEnabled=!controller.Busy&&!controller.Poisoned&&controller.State!=1;send.IsEnabled=!controller.Busy&&controller.State==1;};refresh.Start();
        Closing+=ClosingWindow;
        // Deactivation is not a stop trigger: receiver pairing dialogs can take focus.
        // OS session lock/suspend is handled through SystemEvents below.
        Microsoft.Win32.SystemEvents.SessionSwitch+=SessionSwitch;
        Microsoft.Win32.SystemEvents.PowerModeChanged+=PowerChanged;
    }
    private void SessionSwitch(object sender,Microsoft.Win32.SessionSwitchEventArgs e)
    { if(e.Reason==Microsoft.Win32.SessionSwitchReason.SessionLock) Dispatcher.BeginInvoke(new Action(()=>_ = Run(controller.StopAsync))); }
    private void PowerChanged(object sender,Microsoft.Win32.PowerModeChangedEventArgs e)
    { if(e.Mode==Microsoft.Win32.PowerModes.Suspend) Dispatcher.BeginInvoke(new Action(()=>_ = Run(controller.StopAsync))); }
    private void Add(Panel panel,string label,Func<Task> action)
    { var b=new Button{Content=label,Margin=new Thickness(4)};b.Click+=async(_,__)=>await Run(action);panel.Children.Add(b); }
    private async Task Run(Func<Task> action) {try{await action();}catch(Exception e){Log(e.Message);}}
    private void Log(string message) {output.AppendText($"{DateTimeOffset.Now:HH:mm:ss} {message}\n");output.ScrollToEnd();}
    private async void ClosingWindow(object? sender,CancelEventArgs e)
    {
        if(closeAllowed)return;e.Cancel=true;
        await Run(controller.StopAsync);
        if(controller.Busy) {Log("Still awaiting a Windows operation. Close again after it settles, or disconnect the receiver before force-quitting. Release cannot be guaranteed after a timeout or process termination.");return;}
        refresh.Stop();Microsoft.Win32.SystemEvents.SessionSwitch-=SessionSwitch;Microsoft.Win32.SystemEvents.PowerModeChanged-=PowerChanged;
        controller.DisposeWhenIdle();closeAllowed=true;Close();
    }
}
