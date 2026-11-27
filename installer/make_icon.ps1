# Builds installer\icon.ico (256, 48, 32, 16 px, PNG-compressed entries): IronTools purple badge
param([string]$OutFile = (Join-Path $PSScriptRoot "icon.ico"))
Add-Type -AssemblyName System.Drawing

function New-Frame([int]$S) {
  $bmp = New-Object System.Drawing.Bitmap $S, $S
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = 'AntiAlias'; $g.TextRenderingHint = 'AntiAliasGridFit'
  $g.Clear([System.Drawing.Color]::Transparent)
  $k = $S / 96.0
  $r = 20 * $k; $d = $r * 2; $m = 2 * $k; $w = $S - 2 * $m
  $p = New-Object System.Drawing.Drawing2D.GraphicsPath
  $p.AddArc($m, $m, $d, $d, 180, 90); $p.AddArc($m + $w - $d, $m, $d, $d, 270, 90)
  $p.AddArc($m + $w - $d, $m + $w - $d, $d, $d, 0, 90); $p.AddArc($m, $m + $w - $d, $d, $d, 90, 90); $p.CloseFigure()
  $rect = New-Object System.Drawing.RectangleF $m, $m, $w, $w
  $br = New-Object System.Drawing.Drawing2D.LinearGradientBrush $rect, ([System.Drawing.Color]::FromArgb(116, 87, 245)), ([System.Drawing.Color]::FromArgb(176, 120, 252)), 45
  $g.FillPath($br, $p)
  # three stacked "tool" bars with dots, like the IronTools icons
  $pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::White), (7 * $k)
  $pen.StartCap = 'Round'; $pen.EndCap = 'Round'
  $dot = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(245, 165, 36))
  foreach ($row in @(@(30, 52), @(48, 32), @(66, 60))) {
    $y = $row[0] * $k
    $g.DrawLine($pen, 24 * $k, $y, 72 * $k, $y)
    $g.FillEllipse($dot, ($row[1] - 7) * $k, $y - 7 * $k, 14 * $k, 14 * $k)
  }
  $g.Dispose()
  $ms = New-Object System.IO.MemoryStream
  $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
  return ,$ms.ToArray()
}

$sizes = @(256, 48, 32, 16)
$frames = @(); foreach ($s in $sizes) { $frames += ,(New-Frame $s) }
$out = New-Object System.IO.MemoryStream
$bw = New-Object System.IO.BinaryWriter $out
$bw.Write([UInt16]0); $bw.Write([UInt16]1); $bw.Write([UInt16]$sizes.Count)
$offset = 6 + 16 * $sizes.Count
for ($i = 0; $i -lt $sizes.Count; $i++) {
  $s = $sizes[$i]; $data = $frames[$i]
  $bw.Write([byte]($(if ($s -ge 256) { 0 } else { $s }))); $bw.Write([byte]($(if ($s -ge 256) { 0 } else { $s })))
  $bw.Write([byte]0); $bw.Write([byte]0); $bw.Write([UInt16]1); $bw.Write([UInt16]32)
  $bw.Write([UInt32]$data.Length); $bw.Write([UInt32]$offset)
  $offset += $data.Length
}
foreach ($data in $frames) { $bw.Write($data) }
$bw.Flush()
[System.IO.File]::WriteAllBytes($OutFile, $out.ToArray())
"saved $OutFile"
