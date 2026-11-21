using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

// Framework 4.x is included with supported Windows 10/11 installations.
internal static class Launcher
{
    private static string Quote(string value)
    {
        var output = new StringBuilder("\"");
        int slashes = 0;
        foreach (char ch in value)
        {
            if (ch == '\\') { slashes++; continue; }
            if (ch == '"') { output.Append('\\', slashes * 2 + 1); output.Append(ch); slashes = 0; }
            else { output.Append('\\', slashes); output.Append(ch); slashes = 0; }
        }
        output.Append('\\', slashes * 2);
        return output.Append('"').ToString();
    }

    [STAThread]
    private static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        try
        {
            var command = new StringBuilder(Quote(Path.Combine(root, "app", "bootstrap.py")));
            foreach (string arg in args) command.Append(" ").Append(Quote(arg));
            var info = new ProcessStartInfo(Path.Combine(root, "runtime", "python", "TingmuRuntime.exe"), command.ToString());
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.WorkingDirectory = root;
            info.EnvironmentVariables["TINGMU_PORTABLE_ROOT"] = root;
            info.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            info.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
            info.EnvironmentVariables.Remove("PYTHONPYCACHEPREFIX");
            info.EnvironmentVariables.Remove("PYTHONPATH");
            info.EnvironmentVariables["PATH"] = Path.Combine(root, "bin") + ";" + info.EnvironmentVariables["PATH"];
            using (var process = Process.Start(info))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show("听幕无法启动。请保留完整的便携文件夹。\n\n" + error.Message,
                            "听幕", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
