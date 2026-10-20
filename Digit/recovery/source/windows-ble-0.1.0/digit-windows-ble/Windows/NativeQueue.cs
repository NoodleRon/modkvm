using System.Runtime.InteropServices;
[assembly: DefaultDllImportSearchPaths(DllImportSearchPath.AssemblyDirectory | DllImportSearchPath.System32)]
namespace DigitWindowsBLE;
internal sealed class NativeQueue : IDisposable
{
    private const string Dll = "digit_queue.dll";
    private IntPtr handle = digit_create();
    public NativeQueue() { if (handle == IntPtr.Zero) throw new OutOfMemoryException(); }
    public int State => digit_state(handle);
    public int Pending => digit_pending(handle);
    public bool Gate(bool eligible) => digit_gate(handle, eligible ? 1 : 0) == 1;
    public bool Arm() => digit_arm(handle) == 1;
    public bool EnqueueFixedTest() => digit_fixed_test(handle) == 1;
    public bool Take(byte[] bytes, out ulong token) => digit_take(handle, bytes, out token) == 1;
    public void Complete(ulong token, bool success) => digit_complete(handle, token, success ? 1 : 0);
    public void Stop() => digit_stop(handle);
    public void Fault() => digit_fault(handle);
    public bool ConsumeRelease() => digit_release_needed(handle) == 1;
    public void Dispose() { if (handle != IntPtr.Zero) { digit_destroy(handle); handle = IntPtr.Zero; } }
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] private static extern IntPtr digit_create();
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] private static extern void digit_destroy(IntPtr p);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] private static extern int digit_gate(IntPtr p,int eligible);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] private static extern int digit_arm(IntPtr p);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] private static extern int digit_fixed_test(IntPtr p);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] private static extern int digit_take(IntPtr p,[Out] byte[] bytes,out ulong token);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] private static extern int digit_complete(IntPtr p,ulong token,int success);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] private static extern void digit_stop(IntPtr p);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] private static extern void digit_fault(IntPtr p);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] private static extern int digit_state(IntPtr p);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] private static extern int digit_pending(IntPtr p);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] private static extern int digit_release_needed(IntPtr p);
}
