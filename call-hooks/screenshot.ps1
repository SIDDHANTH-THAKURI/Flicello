# Captures the whole virtual screen (every monitor) to a PNG and prints its path.
# Used by `vido screen` (src/vidosuper/screen.py) so Claude can look at the screen.
# Takes one optional arg: the output path; defaults to a fresh temp file so
# concurrent calls never collide on the same filename.
param(
  [string]$OutPath = (Join-Path $env:TEMP ("vido-screen-" + [guid]::NewGuid().ToString("N") + ".png"))
)

Add-Type -AssemblyName System.Windows.Forms, System.Drawing

# Must come before anything reads screen metrics. PowerShell is DPI-unaware by
# default, which makes these two disagree on a scaled display: VirtualScreen
# reports LOGICAL pixels (e.g. 1536x960 at 125%) while CopyFromScreen copies
# PHYSICAL ones, so the capture is the top-left crop of the real screen rather
# than the whole thing -- anything on the right or bottom is simply missing,
# and coordinates read off the image do not match where the cursor goes.
if (-not ([System.Management.Automation.PSTypeName]'VidoDpi').Type) {
  Add-Type @'
using System;
using System.Runtime.InteropServices;
public class VidoDpi { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }
'@
}
[VidoDpi]::SetProcessDPIAware() | Out-Null

$bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bitmap.Save($OutPath, [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bitmap.Dispose()

Write-Output $OutPath
