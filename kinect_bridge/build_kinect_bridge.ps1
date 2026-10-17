$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$sdk = "C:\Program Files\Microsoft SDKs\Kinect\v2.0_1409"
$out = Join-Path $root "tower_ui\kinect_bridge.exe"

if (-not (Test-Path -LiteralPath $csc)) { throw "C# compiler not found: $csc" }
if (-not (Test-Path -LiteralPath (Join-Path $sdk "Assemblies\Microsoft.Kinect.dll"))) { throw "Kinect SDK v2 not found: $sdk" }

& $csc /nologo /target:exe /platform:x64 /optimize+ `
    /out:$out `
    /reference:"$sdk\Assemblies\Microsoft.Kinect.dll" `
    /reference:"$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\System.dll" `
    /reference:"$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\System.Core.dll" `
    /reference:"$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\System.Drawing.dll" `
    /reference:"$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\System.Web.Extensions.dll" `
    (Join-Path $PSScriptRoot "KinectBridge.cs")

Write-Host "Built $out"
