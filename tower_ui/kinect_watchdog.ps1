# Kinect USB watchdog - restarts kinect_bridge.exe when status.json goes stale
$runtimeDir = Join-Path $PSScriptRoot "kinect_runtime"
$stateFile  = Join-Path $runtimeDir "status.json"
$logFile    = Join-Path $PSScriptRoot "kinect_watchdog.log"
$bridgeExe  = Join-Path $PSScriptRoot "kinect_bridge.exe"
$staleSec   = 15
function Log($msg) {
  Add-Content -Path $logFile -Value ("{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg)
}
Log "watchdog started"
while ($true) {
  Start-Sleep -Seconds 10
  $stale = $false
  if (-not (Test-Path $stateFile)) {
    $stale = $true
    $reason = "status.json missing"
  } else {
    $age = (Get-Date) - (Get-Item $stateFile).LastWriteTime
    if ($age.TotalSeconds -gt $staleSec) {
      $stale = $true
      $reason = ("status.json stale {0:N0}s" -f $age.TotalSeconds)
    }
  }
  if ($stale) {
    Log ("DETECTED: " + $reason + " -> restarting bridge")
    Get-Process kinect_bridge -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
    Start-Process -FilePath $bridgeExe -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
    Log "bridge restarted"
  }
}
