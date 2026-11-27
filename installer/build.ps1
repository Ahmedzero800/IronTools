# Builds installer\bin\IronTools-Setup.exe with the C# compiler that ships with .NET Framework 4.8.
# Usage:  powershell -ExecutionPolicy Bypass -File installer\build.ps1
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe" }
if (-not (Test-Path (Join-Path $here "icon.ico"))) { & (Join-Path $here "make_icon.ps1") }
$bin = Join-Path $here "bin"
New-Item -ItemType Directory -Force $bin | Out-Null
$out = Join-Path $bin "IronTools-Setup.exe"
& $csc /nologo /target:winexe /optimize+ /platform:anycpu /utf8output `
    "/out:$out" `
    "/win32icon:$(Join-Path $here 'icon.ico')" `
    "/win32manifest:$(Join-Path $here 'app.manifest')" `
    /r:System.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll `
    (Join-Path $here "IronToolsSetup.cs")
if ($LASTEXITCODE -ne 0) { throw "Build failed" }
"built $out  ({0:N0} bytes)" -f (Get-Item $out).Length
