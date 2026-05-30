using System;
using System.Diagnostics;
using System.IO;
using System.Text;

internal static class Program
{
    private static int Main(string[] args)
    {
        try
        {
            var repoRoot = args.Length > 0 && !string.IsNullOrWhiteSpace(args[0])
                ? Path.GetFullPath(args[0])
                : Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", ".."));

            var venvDir = Path.Combine(repoRoot, ".venv");
            var baseHome = ReadVenvHome(Path.Combine(venvDir, "pyvenv.cfg"));
            var venvPythonw = Path.Combine(venvDir, "Scripts", "pythonw.exe");
            var basePythonw = !string.IsNullOrWhiteSpace(baseHome)
                ? Path.Combine(baseHome, "pythonw.exe")
                : "";
            var pythonw = File.Exists(basePythonw) ? basePythonw : venvPythonw;
            if (string.IsNullOrWhiteSpace(pythonw) || !File.Exists(pythonw))
            {
                throw new FileNotFoundException("pythonw.exe not found", venvPythonw);
            }

            var entryScript = Path.Combine(repoRoot, "vcp_hunter_qt.pyw");
            var sitePackages = Path.Combine(venvDir, "Lib", "site-packages");

            var startInfo = new ProcessStartInfo
            {
                FileName = pythonw,
                Arguments = Quote(entryScript),
                WorkingDirectory = repoRoot,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            };

            startInfo.EnvironmentVariables["VCP_SKIP_VENV_RELAUNCH"] = "1";
            startInfo.EnvironmentVariables["VIRTUAL_ENV"] = venvDir;
            startInfo.EnvironmentVariables["PYTHONPATH"] = MergePathList(
                sitePackages,
                repoRoot,
                startInfo.EnvironmentVariables["PYTHONPATH"]
            );
            startInfo.EnvironmentVariables["PATH"] = MergePathList(
                Path.Combine(venvDir, "Scripts"),
                baseHome,
                startInfo.EnvironmentVariables["PATH"]
            );

            Process.Start(startInfo);
            return 0;
        }
        catch (Exception ex)
        {
            TryWriteErrorLog(ex);
            return 1;
        }
    }

    private static void TryWriteErrorLog(Exception ex)
    {
        try
        {
            var logDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "ZijinResearch",
                "Launcher"
            );
            Directory.CreateDirectory(logDir);
            File.AppendAllText(
                Path.Combine(logDir, "launcher_error.log"),
                DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + Environment.NewLine + ex + Environment.NewLine
            );
        }
        catch
        {
        }
    }

    private static string ReadVenvHome(string configPath)
    {
        if (!File.Exists(configPath))
        {
            return "";
        }

        foreach (var line in File.ReadLines(configPath, Encoding.UTF8))
        {
            var trimmed = line.Trim();
            if (!trimmed.StartsWith("home", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            var parts = trimmed.Split(new[] { '=' }, 2);
            if (parts.Length == 2)
            {
                return parts[1].Trim();
            }
        }

        return "";
    }

    private static string MergePathList(params string[] parts)
    {
        var builder = new StringBuilder();
        foreach (var part in parts)
        {
            if (string.IsNullOrWhiteSpace(part))
            {
                continue;
            }
            if (builder.Length > 0)
            {
                builder.Append(';');
            }
            builder.Append(part);
        }
        return builder.ToString();
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }
}
