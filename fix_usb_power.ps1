# USB 掉线修复：关掉 USB 设备/集线器的"空闲就断电"行为
# 背景：本机只有一个 Intel xHCI 控制器，Kinect + 鼠标 + 键盘 + 摄像头 + 蓝牙全挂在
#       同一条 USB 3.0 总线上，Kinect 一出事就全掉。这里先把"系统主动让它们休眠"
#       这条软件诱因消除掉。
# 安全：先导出注册表备份，只改已知的安全键，绝不删 anything。
$ErrorActionPreference = 'SilentlyContinue'

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backup =Join-Path $env:USERPROFILE "Desktop\usb_reg_backup_$stamp.reg"
reg export "HKLM\SYSTEM\CurrentControlSet\Enum\USB" $backup /y | Out-Null
Write-Output "注册表备份 -> $backup"
Write-Output ""

# 目标键值：这些=1 时系统会在设备"看起来空闲"时让它进 D3 断电
$targets = @(
    'EnhancedPowerManagementEnabled',
    'AllowIdleIrpInD3',
    'IdleInWorkingState',
    'EnableIdlePowerManagement'
)

$changed = @()

function Disable-Idle {
    param($KeyPath, $Label)
    $p = Get-ItemProperty -Path $KeyPath
    if (-not $p) { return }
    foreach ($name in $targets) {
        $val = $p.PSObject.Properties[$name]
        if ($null -ne $val -and $val.Value -ne 0) {
            New-ItemProperty -Path $KeyPath -Name $name -Value 0 -PropertyType DWord -Force | Out-Null
            $script:changed += "$Label  ::  $name  $($val.Value) -> 0"
        }
    }
}

# 1) 全局开关：USB 选择性暂停
$usbKey = 'HKLM:\SYSTEM\CurrentControlSet\Services\USB'
New-ItemProperty -Path $usbKey -Name 'DisableSelectiveSuspend' -Value 1 -PropertyType DWord -Force | Out-Null
Write-Output "[全局] Services\USB\DisableSelectiveSuspend = 1"

# 2) 所有 USB 设备的 Device Parameters
$params = Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Enum\USB' -Recurse -Depth 3 |
    Where-Object { $_.PSChildName -eq 'Device Parameters' }
foreach ($k in $params) {
    $ver = ($k.PSPath -split '\\')
    $label = ($ver[-3] -replace '^USB\\', '')
    Disable-Idle -KeyPath $k.PSPath -Label $label
}

# 3) HID 设备（鼠标/键盘的最终形态）
$hidParams = Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Enum\HID' -Recurse -Depth 3 |
    Where-Object { $_.PSChildName -eq 'Device Parameters' }
foreach ($k in $hidParams) {
    Disable-Idle -KeyPath $k.PSPath -Label ("HID\" + (( $k.PSPath -split '\\')[-3]))
}

Write-Output ""
Write-Output "================================================================"
Write-Output "改动清单"
Write-Output "================================================================"
if ($changed.Count -eq 0) {
    Write-Output "  没有键值处于启用状态，无需改动（说明不是休眠开关导致）"
} else {
    $changed | Sort-Object | ForEach-Object { Write-Output "  $_" }
    Write-Output ""
    Write-Output ("  共 {0} 处改动" -f $changed.Count)
}
Write-Output ""
Write-Output "注意：改动需重启电脑后生效。"
