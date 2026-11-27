// IronTools Setup - installs / updates / removes the IronTools pyRevit extension from GitHub.
//
// Build:  installer\build.ps1   (uses the C# compiler that ships with .NET Framework 4.8)
// Test :  IronTools-Setup.exe --check              -> prints what it detects, changes nothing
//         IronTools-Setup.exe --screenshot <folder> -> saves pictures of the window, changes nothing
//
// What it does
//   Install : Revit must be closed. Any existing IronTools folder is MOVED to a backup folder
//             (never deleted), then `pyrevit extend ui IronTools <repo> --branch=main --dest=...`
//             clones the extension. If anything fails, the backup is moved back.
//   Update  : `pyrevit extensions update IronTools` (works while Revit is open; reload pyRevit after).
//   Remove  : Revit must be closed. The IronTools folder is moved to the backup folder.
//
// C# 5 only (no string interpolation) so it builds with the compiler included in Windows.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Drawing.Text;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows.Forms;

[assembly: AssemblyTitle("IronTools Setup")]
[assembly: AssemblyDescription("Installs and updates the IronTools pyRevit extension from GitHub")]
[assembly: AssemblyCompany("Ahmed Mahmoud")]
[assembly: AssemblyProduct("IronTools")]
[assembly: AssemblyCopyright("Copyright (c) 2026 Ahmed Mahmoud")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]
[assembly: AssemblyInformationalVersion("1.0.0")]

namespace IronToolsSetup
{
    // ------------------------------------------------------------------
    // Colours (same palette as the IronTools windows inside Revit)
    // ------------------------------------------------------------------
    static class Theme
    {
        public static readonly Color Back = Color.FromArgb(14, 21, 37);
        public static readonly Color Card = Color.FromArgb(21, 31, 53);
        public static readonly Color CardEdge = Color.FromArgb(31, 42, 68);
        public static readonly Color Text = Color.FromArgb(230, 234, 244);
        public static readonly Color Muted = Color.FromArgb(127, 139, 167);
        public static readonly Color Dim = Color.FromArgb(85, 96, 122);
        public static readonly Color Purple = Color.FromArgb(116, 87, 245);
        public static readonly Color Purple2 = Color.FromArgb(160, 108, 251);
        public static readonly Color Lilac = Color.FromArgb(201, 184, 255);
        public static readonly Color Green = Color.FromArgb(34, 197, 94);
        public static readonly Color Amber = Color.FromArgb(245, 165, 36);
        public static readonly Color Red = Color.FromArgb(240, 68, 97);
        public static readonly Color Ghost = Color.FromArgb(26, 37, 64);

        public static GraphicsPath Round(RectangleF r, float radius)
        {
            float d = radius * 2;
            GraphicsPath p = new GraphicsPath();
            p.AddArc(r.X, r.Y, d, d, 180, 90);
            p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }

        public static void Smooth(Graphics g)
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;
            g.PixelOffsetMode = PixelOffsetMode.HighQuality;
        }
    }

    // ------------------------------------------------------------------
    // What is on this computer
    // ------------------------------------------------------------------
    enum InstallState { NotInstalled, Linked, OtherRepo, NotLinked }

    class Detection
    {
        public bool RevitRunning;
        public string CliPath;
        public string CliVersion;
        public InstallState State;
        public string Origin;
        public string Commit;

        public string Describe()
        {
            StringBuilder s = new StringBuilder();
            s.AppendLine("Revit running   : " + (RevitRunning ? "yes" : "no"));
            s.AppendLine("pyRevit CLI     : " + (CliPath ?? "NOT FOUND"));
            s.AppendLine("pyRevit version : " + (CliVersion ?? "-"));
            s.AppendLine("Extension folder: " + Installer.ExtPath);
            s.AppendLine("IronTools state : " + State);
            s.AppendLine("Git origin      : " + (Origin ?? "-"));
            s.AppendLine("Installed commit: " + (Commit ?? "-"));
            s.AppendLine("Main action     : " + Installer.MainAction(this));
            return s.ToString();
        }
    }

    class SetupException : Exception
    {
        public SetupException(string message) : base(message) { }
    }

    static class Installer
    {
        public const string RepoUrl = "https://github.com/Ahmedzero800/IronTools.git";
        public const string RepoPage = "https://github.com/Ahmedzero800/IronTools";
        public const string ExtName = "IronTools";
        static readonly Regex OurRepo = new Regex(@"github\.com[/:]Ahmedzero800/IronTools(\.git)?/?$",
                                                  RegexOptions.IgnoreCase);

        static string Roaming { get { return Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData); } }
        public static string ExtRoot { get { return Path.Combine(Roaming, "pyRevit", "Extensions"); } }
        public static string ExtPath { get { return Path.Combine(ExtRoot, "IronTools.extension"); } }
        public static string BackupRoot { get { return Path.Combine(Roaming, "pyRevit", "IronTools-Backups"); } }
        // path shown in the window (screenshot mode replaces it with a generic one)
        public static string ShownExtPath;
        public static string ExtPathShown { get { return ShownExtPath ?? ExtPath; } }

        public static Detection Detect()
        {
            Detection d = new Detection();
            d.RevitRunning = RevitRunning();
            d.CliPath = FindCli();
            if (d.CliPath != null)
                d.CliVersion = CliVersion(d.CliPath);
            if (!Directory.Exists(ExtPath))
                d.State = InstallState.NotInstalled;
            else if (!Directory.Exists(Path.Combine(ExtPath, ".git")))
                d.State = InstallState.NotLinked;
            else
            {
                d.Origin = ReadOrigin(ExtPath);
                d.Commit = ReadHead(ExtPath);
                d.State = (d.Origin != null && OurRepo.IsMatch(d.Origin.Trim()))
                    ? InstallState.Linked : InstallState.OtherRepo;
            }
            return d;
        }

        public static string MainAction(Detection d)
        {
            return d.State == InstallState.Linked ? "Update" : (d.State == InstallState.NotInstalled ? "Install" : "Repair");
        }

        public static bool RevitRunning()
        {
            Process[] ps = Process.GetProcessesByName("Revit");
            bool any = ps.Length > 0;
            foreach (Process p in ps) p.Dispose();
            return any;
        }

        // pyRevit CLI: PATH first, then every folder the pyRevit installers use
        public static string FindCli()
        {
            List<string> dirs = new List<string>();
            string path = Environment.GetEnvironmentVariable("PATH") ?? "";
            foreach (string part in path.Split(Path.PathSeparator))
            {
                string p = Environment.ExpandEnvironmentVariables(part.Trim().Trim('"'));
                if (p.Length > 0 && Path.IsPathRooted(p)) dirs.Add(p);
            }
            string[] roots = {
                Roaming,
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                Environment.GetEnvironmentVariable("ProgramW6432") ?? "",
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),
                Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData)
            };
            foreach (string r in roots)
            {
                if (string.IsNullOrEmpty(r)) continue;
                dirs.Add(Path.Combine(r, "pyRevit-Master", "bin"));
                dirs.Add(Path.Combine(r, "pyRevit CLI", "bin"));
                dirs.Add(Path.Combine(r, "pyRevit", "bin"));
            }
            foreach (string dir in dirs)
            {
                try
                {
                    string exe = Path.Combine(dir, "pyrevit.exe");
                    if (File.Exists(exe)) return exe;
                }
                catch (ArgumentException) { }
            }
            return null;
        }

        static string CliVersion(string cli)
        {
            try
            {
                string output;
                Run(cli, "--version", out output, 20000);
                Match m = Regex.Match(output, @"v?(\d+\.\d+\.\d+)");
                return m.Success ? m.Groups[1].Value : null;
            }
            catch (Exception) { return null; }
        }

        // read the git origin straight from .git\config (no git needed)
        static string ReadOrigin(string repo)
        {
            try
            {
                string[] lines = File.ReadAllLines(Path.Combine(repo, ".git", "config"));
                bool inOrigin = false;
                foreach (string raw in lines)
                {
                    string line = raw.Trim();
                    if (line.StartsWith("["))
                        inOrigin = line.Replace(" ", "").Equals("[remote\"origin\"]", StringComparison.OrdinalIgnoreCase);
                    else if (inOrigin && line.StartsWith("url", StringComparison.OrdinalIgnoreCase))
                        return line.Substring(line.IndexOf('=') + 1).Trim();
                }
            }
            catch (Exception) { }
            return null;
        }

        static string ReadHead(string repo)
        {
            try
            {
                string head = File.ReadAllText(Path.Combine(repo, ".git", "HEAD")).Trim();
                if (head.StartsWith("ref:"))
                {
                    string refPath = Path.Combine(repo, ".git", head.Substring(4).Trim().Replace('/', '\\'));
                    if (File.Exists(refPath)) head = File.ReadAllText(refPath).Trim();
                    else
                    {
                        string packed = Path.Combine(repo, ".git", "packed-refs");
                        string name = head.Substring(4).Trim();
                        if (File.Exists(packed))
                            foreach (string l in File.ReadAllLines(packed))
                                if (l.EndsWith(" " + name)) { head = l.Split(' ')[0]; break; }
                    }
                }
                return head.Length >= 7 ? head.Substring(0, 7) : null;
            }
            catch (Exception) { return null; }
        }

        public static int Run(string exe, string args, out string output, int timeoutMs)
        {
            ProcessStartInfo si = new ProcessStartInfo(exe, args);
            si.UseShellExecute = false;
            si.CreateNoWindow = true;
            si.RedirectStandardOutput = true;
            si.RedirectStandardError = true;
            using (Process p = Process.Start(si))
            {
                Task<string> o = p.StandardOutput.ReadToEndAsync();
                Task<string> e = p.StandardError.ReadToEndAsync();
                if (!p.WaitForExit(timeoutMs))
                {
                    try { p.Kill(); } catch (Exception) { }
                    throw new SetupException("pyRevit did not finish in time.");
                }
                Task.WaitAll(o, e);
                output = (o.Result + "\n" + e.Result).Trim();
                return p.ExitCode;
            }
        }

        static string Short(string text)
        {
            text = (text ?? "").Trim();
            return text.Length > 400 ? "..." + text.Substring(text.Length - 400) : text;
        }

        static string Stamp() { return DateTime.Now.ToString("yyyyMMdd-HHmmss"); }

        // ---------------- actions (run on a background thread) ----------------
        public static string Install(Detection d, Action<int, StepState> step)
        {
            step(0, StepState.Running);
            if (RevitRunning()) throw new SetupException("Close Revit first, then click Install again.");
            step(0, StepState.Done);

            step(1, StepState.Running);
            if (d.CliPath == null) throw new SetupException("pyRevit was not found. Install pyRevit, then run this setup again.");
            step(1, StepState.Done);

            step(2, StepState.Running);
            string backup = null;
            if (Directory.Exists(ExtPath))
            {
                Directory.CreateDirectory(BackupRoot);
                backup = Path.Combine(BackupRoot, "IronTools.extension-" + Stamp());
                Directory.Move(ExtPath, backup);
            }
            step(2, backup == null ? StepState.Skipped : StepState.Done);

            try
            {
                step(3, StepState.Running);
                Directory.CreateDirectory(ExtRoot);
                string output;
                int code = Run(d.CliPath, "extend ui " + ExtName + " " + RepoUrl + " --branch=main \"--dest=" + ExtRoot + "\"",
                               out output, 10 * 60 * 1000);
                if (code != 0) throw new SetupException("pyRevit could not download IronTools (code " + code + ").\n" + Short(output));
                step(3, StepState.Done);

                step(4, StepState.Running);
                Verify();
                step(4, StepState.Done);
            }
            catch (Exception)
            {
                // put the previous copy back
                if (backup != null && Directory.Exists(backup))
                {
                    if (Directory.Exists(ExtPath))
                        Directory.Move(ExtPath, Path.Combine(BackupRoot, "failed-install-" + Stamp()));
                    Directory.Move(backup, ExtPath);
                }
                throw;
            }
            return backup == null
                ? "IronTools is installed and linked to GitHub. Open Revit and you will find the Iron Tools tab."
                : "IronTools is installed and linked to GitHub. Your previous copy was saved in:\n" + backup;
        }

        public static string Update(Detection d, Action<int, StepState> step)
        {
            step(0, StepState.Running);
            if (d.CliPath == null) throw new SetupException("pyRevit was not found. Install pyRevit, then run this setup again.");
            step(0, StepState.Done);

            step(1, StepState.Running);
            string before = d.Commit;
            string output;
            int code = Run(d.CliPath, "extensions update " + ExtName, out output, 10 * 60 * 1000);
            if (code != 0) throw new SetupException("pyRevit could not update IronTools (code " + code + ").\n" + Short(output));
            step(1, StepState.Done);

            step(2, StepState.Running);
            Verify();
            string after = ReadHead(ExtPath);
            step(2, StepState.Done);

            string tail = RevitRunning() ? " Revit is open: click pyRevit > Reload to load the new version." : "";
            if (before != null && before == after)
                return "IronTools is already up to date (version " + after + ")." + tail;
            return "IronTools was updated" + (after != null ? " to version " + after : "") + "." + tail;
        }

        public static string Remove(Action<int, StepState> step)
        {
            step(0, StepState.Running);
            if (RevitRunning()) throw new SetupException("Close Revit first, then click Remove again.");
            step(0, StepState.Done);

            step(1, StepState.Running);
            if (!Directory.Exists(ExtPath)) throw new SetupException("IronTools is not installed.");
            Directory.CreateDirectory(BackupRoot);
            string backup = Path.Combine(BackupRoot, "IronTools.extension-removed-" + Stamp());
            Directory.Move(ExtPath, backup);
            step(1, StepState.Done);
            return "IronTools was removed. A copy was kept in:\n" + backup;
        }

        static void Verify()
        {
            if (!Directory.Exists(Path.Combine(ExtPath, "Iron Tools.tab")))
                throw new SetupException("The download is incomplete: the Iron Tools tab folder is missing.");
            string origin = ReadOrigin(ExtPath);
            if (origin == null || !OurRepo.IsMatch(origin.Trim()))
                throw new SetupException("The installed copy is not linked to " + RepoPage + ".");
        }
    }

    // ------------------------------------------------------------------
    // Controls
    // ------------------------------------------------------------------
    enum StepState { Pending, Running, Done, Skipped, Failed }

    class PillButton : Control
    {
        public bool Primary;
        bool hover, down;

        public PillButton()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.UserPaint | ControlStyles.ResizeRedraw | ControlStyles.SupportsTransparentBackColor, true);
            Cursor = Cursors.Hand;
            Font = new Font("Segoe UI Semibold", 10.5f);
            BackColor = Theme.Back;
        }

        protected override void OnMouseEnter(EventArgs e) { hover = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { hover = false; down = false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnMouseDown(MouseEventArgs e) { down = true; Invalidate(); base.OnMouseDown(e); }
        protected override void OnMouseUp(MouseEventArgs e) { down = false; Invalidate(); base.OnMouseUp(e); }
        protected override void OnEnabledChanged(EventArgs e) { Invalidate(); base.OnEnabledChanged(e); }
        protected override void OnTextChanged(EventArgs e) { Invalidate(); base.OnTextChanged(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            Theme.Smooth(g);
            g.Clear(Parent != null ? Parent.BackColor : Theme.Back);
            RectangleF r = new RectangleF(0.5f, 0.5f, Width - 1.5f, Height - 1.5f);
            using (GraphicsPath p = Theme.Round(r, (Height - 1) / 2f))
            {
                int alpha = !Enabled ? 90 : (down ? 190 : (hover ? 225 : 255));
                if (Primary)
                {
                    using (LinearGradientBrush b = new LinearGradientBrush(r, Color.FromArgb(alpha, Theme.Purple),
                                                                           Color.FromArgb(alpha, Theme.Purple2), 0f))
                        g.FillPath(b, p);
                }
                else
                {
                    Color c = hover && Enabled ? Color.FromArgb(35, 48, 79) : Theme.Ghost;
                    using (SolidBrush b = new SolidBrush(Color.FromArgb(Enabled ? 255 : 120, c)))
                        g.FillPath(b, p);
                }
            }
            Color fg = Primary ? Color.White : Color.FromArgb(154, 166, 193);
            if (!Enabled) fg = Color.FromArgb(150, fg);
            TextRenderer.DrawText(g, Text, Font, ClientRectangle, fg,
                                  TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
        }
    }

    class LinkText : Label
    {
        public LinkText()
        {
            Cursor = Cursors.Hand;
            ForeColor = Theme.Lilac;
            Font = new Font("Segoe UI", 9.5f, FontStyle.Underline);
            AutoSize = true;
            BackColor = Theme.Back;
        }
    }

    // System check card: three rows with a coloured dot, a title and a detail line
    class CheckCard : Control
    {
        public class Row { public string Title, Value, Detail; public Color Dot; }
        public readonly List<Row> Rows = new List<Row>();

        public CheckCard()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.UserPaint | ControlStyles.ResizeRedraw, true);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            Theme.Smooth(g);
            g.Clear(Theme.Back);
            using (GraphicsPath p = Theme.Round(new RectangleF(0.5f, 0.5f, Width - 1.5f, Height - 1.5f), 16))
            using (SolidBrush b = new SolidBrush(Theme.Card))
                g.FillPath(b, p);
            using (Font head = new Font("Segoe UI Semibold", 9f))
                TextRenderer.DrawText(g, "SYSTEM CHECK", head, new Point(20, 14), Theme.Lilac);
            int y = 42;
            using (Font title = new Font("Segoe UI", 10.5f))
            using (Font value = new Font("Segoe UI Semibold", 10.5f))
            using (Font small = new Font("Segoe UI", 8.75f))
            {
                foreach (Row r in Rows)
                {
                    using (SolidBrush d = new SolidBrush(r.Dot))
                        g.FillEllipse(d, 22, y + 6, 10, 10);
                    TextRenderer.DrawText(g, r.Title, title, new Point(42, y), Theme.Muted);
                    TextRenderer.DrawText(g, r.Value, value, new Rectangle(150, y, Width - 170, 22), Theme.Text,
                                          TextFormatFlags.Right | TextFormatFlags.EndEllipsis);
                    if (!string.IsNullOrEmpty(r.Detail))
                        TextRenderer.DrawText(g, r.Detail, small, new Rectangle(42, y + 22, Width - 62, 18), Theme.Dim,
                                              TextFormatFlags.Right | TextFormatFlags.PathEllipsis);
                    y += 46;
                }
            }
        }
    }

    // Step list with animated state icons
    class StepList : Control
    {
        public readonly List<string> Steps = new List<string>();
        public readonly List<StepState> States = new List<StepState>();
        public string Title = "STEPS";
        float spin;
        readonly Timer timer = new Timer();

        public StepList()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.UserPaint | ControlStyles.ResizeRedraw, true);
            timer.Interval = 40;
            timer.Tick += delegate { spin = (spin + 12) % 360; Invalidate(); };
        }

        public void SetSteps(string title, params string[] steps)
        {
            Title = title;
            Steps.Clear(); States.Clear();
            foreach (string s in steps) { Steps.Add(s); States.Add(StepState.Pending); }
            Invalidate();
        }

        public void Set(int i, StepState s)
        {
            if (i < 0 || i >= States.Count) return;
            States[i] = s;
            bool running = States.Contains(StepState.Running);
            if (running && !timer.Enabled) timer.Start();
            if (!running && timer.Enabled) timer.Stop();
            Invalidate();
        }

        public void FailRunning()
        {
            for (int i = 0; i < States.Count; i++)
                if (States[i] == StepState.Running) States[i] = StepState.Failed;
            timer.Stop();
            Invalidate();
        }

        protected override void Dispose(bool disposing) { if (disposing) timer.Dispose(); base.Dispose(disposing); }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            Theme.Smooth(g);
            g.Clear(Theme.Back);
            using (GraphicsPath p = Theme.Round(new RectangleF(0.5f, 0.5f, Width - 1.5f, Height - 1.5f), 16))
            using (SolidBrush b = new SolidBrush(Theme.Card))
                g.FillPath(b, p);
            using (Font head = new Font("Segoe UI Semibold", 9f))
                TextRenderer.DrawText(g, Title, head, new Point(20, 14), Theme.Lilac);
            int y = 42;
            using (Font f = new Font("Segoe UI", 10.5f))
            {
                for (int i = 0; i < Steps.Count; i++)
                {
                    StepState s = States[i];
                    RectangleF c = new RectangleF(22, y + 2, 18, 18);
                    DrawIcon(g, c, s);
                    Color fg = s == StepState.Pending ? Theme.Dim : (s == StepState.Failed ? Theme.Red : Theme.Text);
                    string text = Steps[i] + (s == StepState.Skipped ? "  (nothing to back up)" : "");
                    TextRenderer.DrawText(g, text, f, new Point(52, y), s == StepState.Skipped ? Theme.Muted : fg);
                    y += 32;
                }
            }
        }

        void DrawIcon(Graphics g, RectangleF c, StepState s)
        {
            if (s == StepState.Pending)
            {
                using (Pen p = new Pen(Theme.Dim, 2f)) g.DrawEllipse(p, c);
            }
            else if (s == StepState.Running)
            {
                using (Pen p = new Pen(Color.FromArgb(60, Theme.Purple2), 2.5f)) g.DrawEllipse(p, c);
                using (Pen p = new Pen(Theme.Purple2, 2.5f)) { p.StartCap = p.EndCap = LineCap.Round; g.DrawArc(p, c, spin, 100); }
            }
            else
            {
                Color fill = s == StepState.Done ? Theme.Green : (s == StepState.Failed ? Theme.Red : Theme.Dim);
                using (SolidBrush b = new SolidBrush(fill)) g.FillEllipse(b, c);
                using (Pen p = new Pen(Color.White, 2.2f))
                {
                    p.StartCap = p.EndCap = LineCap.Round;
                    p.LineJoin = LineJoin.Round;
                    float x = c.X, y = c.Y;
                    if (s == StepState.Done)
                        g.DrawLines(p, new[] { new PointF(x + 5, y + 9.5f), new PointF(x + 8, y + 12.5f), new PointF(x + 13.5f, y + 6) });
                    else if (s == StepState.Failed)
                    {
                        g.DrawLine(p, x + 6, y + 6, x + 12, y + 12);
                        g.DrawLine(p, x + 12, y + 6, x + 6, y + 12);
                    }
                    else
                        g.DrawLine(p, x + 5.5f, y + 9, x + 12.5f, y + 9);
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Main window
    // ------------------------------------------------------------------
    class SetupForm : Form
    {
        [DllImport("dwmapi.dll")]
        static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);

        readonly CheckCard check = new CheckCard();
        readonly StepList steps = new StepList();
        readonly Label message = new Label();
        readonly PillButton mainBtn = new PillButton();
        readonly PillButton closeBtn = new PillButton();
        readonly LinkText removeLink = new LinkText();
        readonly LinkText repoLink = new LinkText();
        readonly LinkText refreshLink = new LinkText();
        Detection det;
        bool busy;
        Image logo;

        public SetupForm()
        {
            Text = "IronTools Setup";
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch (Exception) { }
            Font = new Font("Segoe UI", 10f);
            AutoScaleMode = AutoScaleMode.None;   // fixed layout; Windows scales the whole window on high-DPI screens
            ClientSize = new Size(580, 640);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            BackColor = Theme.Back;
            ForeColor = Theme.Text;
            DoubleBuffered = true;
            try { if (Icon != null) logo = Icon.ToBitmap(); } catch (Exception) { }

            check.SetBounds(28, 112, 524, 186);
            steps.SetBounds(28, 314, 524, 206);

            message.SetBounds(30, 530, 520, 44);
            message.ForeColor = Theme.Muted;
            message.BackColor = Theme.Back;
            message.Font = new Font("Segoe UI", 9.5f);

            mainBtn.Primary = true;
            mainBtn.SetBounds(408, 586, 144, 40);
            mainBtn.Click += delegate { RunMain(); };
            closeBtn.Text = "Close";
            closeBtn.SetBounds(290, 586, 108, 40);
            closeBtn.Click += delegate { Close(); };

            repoLink.Text = "GitHub";
            repoLink.Location = new Point(30, 597);
            repoLink.Click += delegate { try { Process.Start(Installer.RepoPage); } catch (Exception) { } };
            refreshLink.Text = "Check again";
            refreshLink.Location = new Point(92, 597);
            refreshLink.Click += delegate { if (!busy) Refresh2(); };
            removeLink.Text = "Remove";
            removeLink.Location = new Point(190, 597);
            removeLink.Click += delegate { RunRemove(); };

            Controls.AddRange(new Control[] { check, steps, message, mainBtn, closeBtn, repoLink, refreshLink, removeLink });
            AcceptButton = null;
            FormClosing += delegate(object s, FormClosingEventArgs e)
            {
                if (busy && e.CloseReason == CloseReason.UserClosing) e.Cancel = true;
            };
            Refresh2();
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            int on = 1;
            try
            {
                if (DwmSetWindowAttribute(Handle, 20, ref on, 4) != 0)
                    DwmSetWindowAttribute(Handle, 19, ref on, 4);
            }
            catch (Exception) { }
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            Graphics g = e.Graphics;
            Theme.Smooth(g);
            // logo badge
            RectangleF badge = new RectangleF(28, 26, 60, 60);
            using (GraphicsPath p = Theme.Round(badge, 18))
            using (SolidBrush b = new SolidBrush(Color.FromArgb(38, 139, 108, 250)))
                g.FillPath(b, p);
            if (logo != null)
            {
                g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                g.DrawImage(logo, new RectangleF(40, 38, 36, 36));
            }
            using (Font t = new Font("Segoe UI Semibold", 19f))
                TextRenderer.DrawText(g, "IronTools", t, new Point(100, 26), Color.White);
            using (Font s = new Font("Segoe UI", 10f))
                TextRenderer.DrawText(g, "MEP tools for Revit  \u00b7  pyRevit extension", s, new Point(103, 62), Theme.Muted);
            // version pill
            string ver = "Setup " + Assembly.GetExecutingAssembly().GetName().Version.ToString(3);
            using (Font f = new Font("Segoe UI", 8.75f))
            {
                Size sz = TextRenderer.MeasureText(ver, f);
                RectangleF pill = new RectangleF(552 - sz.Width - 16, 34, sz.Width + 16, 24);
                using (GraphicsPath p = Theme.Round(pill, 12))
                using (SolidBrush b = new SolidBrush(Color.FromArgb(31, 27, 58)))
                    g.FillPath(b, p);
                TextRenderer.DrawText(g, ver, f, Rectangle.Round(pill), Theme.Lilac,
                                      TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
            }
        }

        // ---------------- state ----------------
        void Refresh2()
        {
            det = Installer.Detect();
            ShowState(det);
        }

        public void ShowState(Detection d)
        {
            det = d;
            check.Rows.Clear();
            check.Rows.Add(new CheckCard.Row {
                Title = "Revit",
                Value = d.RevitRunning ? "Open" : "Closed",
                Detail = d.RevitRunning ? "Close Revit to install or remove (updating works while it is open)" : "",
                Dot = d.RevitRunning ? Theme.Amber : Theme.Green });
            check.Rows.Add(new CheckCard.Row {
                Title = "pyRevit",
                Value = d.CliPath == null ? "Not found" : ("Found" + (d.CliVersion != null ? "  \u00b7  v" + d.CliVersion : "")),
                Detail = d.CliPath ?? "Install pyRevit first: github.com/pyrevitlabs/pyRevit/releases",
                Dot = d.CliPath == null ? Theme.Red : Theme.Green });
            string value, detail; Color dot;
            switch (d.State)
            {
                case InstallState.Linked:
                    value = "Installed  \u00b7  linked to GitHub"; dot = Theme.Green;
                    detail = "Version " + (d.Commit ?? "?") + "  \u00b7  " + Installer.ExtPathShown; break;
                case InstallState.NotLinked:
                    value = "Installed, not linked to GitHub"; dot = Theme.Amber;
                    detail = "Repair keeps a backup and installs the GitHub version"; break;
                case InstallState.OtherRepo:
                    value = "Linked to another repository"; dot = Theme.Amber;
                    detail = d.Origin ?? ""; break;
                default:
                    value = "Not installed"; dot = Theme.Dim;
                    detail = Installer.ExtPathShown; break;
            }
            check.Rows.Add(new CheckCard.Row { Title = "IronTools", Value = value, Detail = detail, Dot = dot });
            check.Invalidate();

            string action = Installer.MainAction(d);
            mainBtn.Text = action;
            if (action == "Update")
                steps.SetSteps("WHAT UPDATE DOES", "Find pyRevit", "Download the latest IronTools from GitHub", "Check the update");
            else
                steps.SetSteps(action == "Repair" ? "WHAT REPAIR DOES" : "WHAT INSTALL DOES",
                               "Make sure Revit is closed", "Find pyRevit", "Back up the current IronTools folder",
                               "Download IronTools from GitHub", "Check the installation");

            bool needsClosedRevit = action != "Update";
            mainBtn.Enabled = d.CliPath != null && !(needsClosedRevit && d.RevitRunning);
            removeLink.Visible = d.State != InstallState.NotInstalled;
            if (d.CliPath == null)
                Say("pyRevit is needed. Install it, then click Check again.", Theme.Red);
            else if (needsClosedRevit && d.RevitRunning)
                Say("Close Revit, then click Check again.", Theme.Amber);
            else if (action == "Update")
                Say("Get the newest tools. Your settings are kept.", Theme.Muted);
            else if (action == "Repair")
                Say("Your current copy will be moved to a backup folder before installing.", Theme.Muted);
            else
                Say("After installing, open Revit: the Iron Tools tab appears. Later: pyRevit > Update.", Theme.Muted);
        }

        void Say(string text, Color color)
        {
            message.Text = text;
            message.ForeColor = color;
        }

        void SetBusy(bool on)
        {
            busy = on;
            mainBtn.Enabled = !on;
            closeBtn.Enabled = !on;
            removeLink.Enabled = refreshLink.Enabled = !on;
            UseWaitCursor = false;
        }

        Action<int, StepState> StepCallback()
        {
            return delegate(int i, StepState s) { BeginInvoke((Action)delegate { steps.Set(i, s); }); };
        }

        async void RunMain()
        {
            if (busy || det == null) return;
            string action = Installer.MainAction(det);
            Detection d = det;
            SetBusy(true);
            Say(action == "Update" ? "Updating..." : "Installing... this can take a minute.", Theme.Lilac);
            Action<int, StepState> cb = StepCallback();
            try
            {
                string result = await Task.Run(() => action == "Update" ? Installer.Update(d, cb) : Installer.Install(d, cb));
                SetBusy(false);
                // refresh the system check / button, but keep the steps that just ran on screen
                string title = steps.Title;
                string[] names = steps.Steps.ToArray();
                StepState[] done = steps.States.ToArray();
                ShowState(Installer.Detect());
                ShowSteps(title, names, done);
                Say(result, Theme.Green);
            }
            catch (Exception ex)
            {
                SetBusy(false);
                steps.FailRunning();
                Say(ex is SetupException ? ex.Message : ("Something went wrong: " + ex.Message), Theme.Red);
                mainBtn.Enabled = true;
            }
        }

        public void ShowSteps(string title, string[] names, StepState[] states)
        {
            steps.SetSteps(title, names);
            for (int i = 0; i < states.Length && i < names.Length; i++)
                steps.Set(i, states[i] == StepState.Running || states[i] == StepState.Pending ? StepState.Done : states[i]);
        }

        async void RunRemove()
        {
            if (busy || det == null || det.State == InstallState.NotInstalled) return;
            DialogResult ok = MessageBox.Show(this,
                "Remove IronTools from pyRevit?\n\nThe folder is moved to a backup folder, not deleted:\n" + Installer.BackupRoot,
                "Remove IronTools", MessageBoxButtons.YesNo, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2);
            if (ok != DialogResult.Yes) return;
            steps.SetSteps("WHAT REMOVE DOES", "Make sure Revit is closed", "Move IronTools to the backup folder");
            SetBusy(true);
            Action<int, StepState> cb = StepCallback();
            try
            {
                string result = await Task.Run(() => Installer.Remove(cb));
                SetBusy(false);
                ShowState(Installer.Detect());
                steps.SetSteps("WHAT REMOVE DID", "Make sure Revit is closed", "Move IronTools to the backup folder");
                steps.Set(0, StepState.Done); steps.Set(1, StepState.Done);
                Say(result, Theme.Green);
            }
            catch (Exception ex)
            {
                SetBusy(false);
                steps.FailRunning();
                Say(ex.Message, Theme.Red);
            }
        }

        // ---------------- pictures for the README / testing ----------------
        public void SaveShot(string file)
        {
            CreateControl();
            using (Bitmap bmp = new Bitmap(ClientSize.Width, ClientSize.Height))
            {
                DrawToBitmap(bmp, new Rectangle(0, 0, ClientSize.Width, ClientSize.Height));
                // DrawToBitmap includes the title bar offset on some systems; draw client area directly instead
                using (Graphics g = Graphics.FromImage(bmp))
                {
                    g.Clear(BackColor);
                    InvokePaint(this, new PaintEventArgs(g, ClientRectangle));
                    foreach (Control c in Controls)
                    {
                        // the form is not on screen, so Control.Visible is false for every child:
                        // use the state the window would show instead
                        if (c == removeLink && (det == null || det.State == InstallState.NotInstalled)) continue;
                        using (Bitmap cb = new Bitmap(c.Width, c.Height))
                        {
                            c.DrawToBitmap(cb, new Rectangle(0, 0, c.Width, c.Height));
                            g.DrawImage(cb, c.Left, c.Top);
                        }
                    }
                }
                bmp.Save(file, ImageFormat.Png);
            }
        }

        public void DemoSteps(StepState[] states, string text, Color color)
        {
            for (int i = 0; i < states.Length && i < steps.States.Count; i++) steps.States[i] = states[i];
            steps.Invalidate();
            Say(text, color);
        }
    }

    // ------------------------------------------------------------------
    static class Program
    {
        [STAThread]
        static int Main(string[] args)
        {
            if (args.Length > 0 && args[0] == "--check")
            {
                Console.Write(Installer.Detect().Describe());
                return 0;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            if (args.Length > 1 && args[0] == "--screenshot")
            {
                // Demo pictures for the README. Nothing is installed or changed, and a generic
                // user folder is shown instead of the real one.
                Directory.CreateDirectory(args[1]);
                Detection real = Installer.Detect();
                Installer.ShownExtPath = @"C:\Users\you\AppData\Roaming\pyRevit\Extensions\IronTools.extension";
                using (SetupForm f = new SetupForm())
                {
                    Detection demo = new Detection();
                    demo.CliPath = @"C:\Users\you\AppData\Roaming\pyRevit-Master\bin\pyrevit.exe";
                    demo.CliVersion = real.CliVersion ?? "7.0.0";
                    // 1) fresh computer, ready to install
                    demo.State = InstallState.NotInstalled;
                    f.ShowState(demo);
                    f.SaveShot(Path.Combine(args[1], "setup-ready.png"));
                    // 2) installing
                    f.DemoSteps(new[] { StepState.Done, StepState.Done, StepState.Skipped, StepState.Running, StepState.Pending },
                                "Installing... this can take a minute.", Theme.Lilac);
                    f.SaveShot(Path.Combine(args[1], "setup-installing.png"));
                    // 3) installed: the check card refreshes, the steps that ran stay on screen
                    demo.State = InstallState.Linked;
                    demo.Commit = real.Commit ?? "a6e7772";
                    f.ShowState(demo);
                    f.ShowSteps("WHAT INSTALL DID",
                                new[] { "Make sure Revit is closed", "Find pyRevit", "Back up the current IronTools folder",
                                        "Download IronTools from GitHub", "Check the installation" },
                                new[] { StepState.Done, StepState.Done, StepState.Skipped, StepState.Done, StepState.Done });
                    f.DemoSteps(new StepState[0],
                                "IronTools is installed and linked to GitHub. Open Revit and you will find the Iron Tools tab.",
                                Theme.Green);
                    f.SaveShot(Path.Combine(args[1], "setup-installed.png"));
                    // 4) later: run it again -> Update
                    demo.State = InstallState.Linked;
                    demo.Commit = real.Commit ?? "a6e7772";
                    f.ShowState(demo);
                    f.DemoSteps(new[] { StepState.Done, StepState.Done, StepState.Done },
                                "IronTools was updated to version " + demo.Commit + ".", Theme.Green);
                    f.SaveShot(Path.Combine(args[1], "setup-updated.png"));
                }
                return 0;
            }
            Application.Run(new SetupForm());
            return 0;
        }
    }
}
