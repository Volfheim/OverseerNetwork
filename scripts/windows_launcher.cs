using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class OverseerLauncher
{
    [STAThread]
    private static int Main()
    {
        try
        {
            string root = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", ".."));
            string python = Path.Combine(root, "venv", "Scripts", "pythonw.exe");
            string script = Path.Combine(root, "scripts", "panel.py");
            if (!File.Exists(python) || !File.Exists(script))
                throw new FileNotFoundException("Overseer installation is incomplete. Reinstall its shortcuts.");
#if STOP_PANEL
            const string action = "stop";
#else
            const string action = "open";
#endif
            var start = new ProcessStartInfo(python, "\"" + script + "\" " + action + " --notify")
            {
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            };
            using (Process process = Process.Start(start))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "Overseer Network", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
