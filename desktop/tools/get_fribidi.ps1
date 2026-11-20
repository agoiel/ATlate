# FriBiDi (LGPL-2.1-or-later) for Pillow: with it Pillow lays out complex scripts through
# HarfBuzz (raqm), so Arabic letters join and run right to left and Thai marks sit correctly
# in Studio. Without it Studio still works, with simple layout.
#
# Taken from MSYS2 (preinstalled on GitHub Windows runners, or https://www.msys2.org) and put
# into desktop\libs, where the program loads it from (app/__init__.py) and the build copies it.
#
#   powershell -ExecutionPolicy Bypass -File tools\get_fribidi.ps1
param([string]$Msys = "C:\msys64")
$ErrorActionPreference = "Stop"

$bash = Join-Path $Msys "usr\bin\bash.exe"
if (-not (Test-Path $bash)) {
    Write-Host "MSYS2 not found in $Msys - FriBiDi skipped (Studio uses simple text layout)"
    exit 1
}
& $bash -lc "pacman -Sy --noconfirm --needed mingw-w64-x86_64-fribidi"
if ($LASTEXITCODE -ne 0) { throw "pacman failed with code $LASTEXITCODE" }
$version = (& $bash -lc "pacman -Q mingw-w64-x86_64-fribidi").Trim()

$libs = Join-Path (Split-Path $PSScriptRoot -Parent) "libs"
New-Item -ItemType Directory -Force $libs | Out-Null
Copy-Item (Join-Path $Msys "mingw64\bin\libfribidi-0.dll") $libs -Force
$licenses = Join-Path $Msys "mingw64\share\licenses\fribidi"
if (Test-Path $licenses) {
    Get-ChildItem $licenses -File | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $libs ("fribidi-" + $_.BaseName + ".txt")) -Force
    }
}
@"
libfribidi-0.dll - GNU FriBiDi, the Unicode Bidirectional Algorithm library.
License: GNU Lesser General Public License, version 2.1 or later
(https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html).
Source code: https://github.com/fribidi/fribidi
Binary: MSYS2 package $version (https://packages.msys2.org/base/mingw-w64-fribidi).
It is loaded at run time and can be replaced with any compatible build of the same name.
"@ | Set-Content -Encoding ascii (Join-Path $libs "fribidi-README.txt")

Write-Host "FriBiDi ($version) is in $libs"
Get-ChildItem $libs | Format-Table Name, Length
