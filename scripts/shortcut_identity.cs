using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

public static class OverseerShortcutIdentity
{
    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    private class ShellLink { }

    [StructLayout(LayoutKind.Sequential)]
    private struct PropertyKey
    {
        public Guid Format;
        public uint Id;
    }

    [StructLayout(LayoutKind.Explicit, Size = 24)]
    private struct PropVariant
    {
        [FieldOffset(0)] public ushort Type;
        [FieldOffset(8)] public IntPtr Text;
    }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IPropertyStore
    {
        void GetCount(out uint count);
        void GetAt(uint index, out PropertyKey key);
        void GetValue(ref PropertyKey key, out PropVariant value);
        void SetValue(ref PropertyKey key, ref PropVariant value);
        void Commit();
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern void SHChangeNotify(uint eventId, uint flags, string item, IntPtr unused);

    public static void Set(string path, string appId)
    {
        object link = new ShellLink();
        var value = new PropVariant { Type = 31, Text = Marshal.StringToCoTaskMemUni(appId) };
        try
        {
            var file = (IPersistFile)link;
            file.Load(path, 2);
            var key = new PropertyKey { Format = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), Id = 5 };
            var properties = (IPropertyStore)link;
            properties.SetValue(ref key, ref value);
            properties.Commit();
            file.Save(path, true);
            SHChangeNotify(0x2000, 0x1005, path, IntPtr.Zero);
        }
        finally
        {
            Marshal.FreeCoTaskMem(value.Text);
            Marshal.FinalReleaseComObject(link);
        }
    }
}
