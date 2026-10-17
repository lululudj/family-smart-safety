# USB 掉线根因诊断：拓扑 + 电源管理开关 + 控制器链路伙伴
# 用途：Kinect 与鼠标/键盘同时掉线时，判断是否为共享控制器/过载/过流
$ErrorActionPreference = 'SilentlyContinue'

Write-Output "================================================================"
Write-Output "一、USB 设备树（谁挂在哪条总线上）"
Write-Output "================================================================"

$devs = Get-PnpDevice -PresentOnly | Where-Object {
    $_.InstanceId -match '^(USB|SWD\\MMDEVAPI)' -or $_.Class -in 'Mouse', 'Keyboard', 'HIDClass'
}

$rows = foreach ($d in $devs) {
    $parent = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName DEVPKEY_Device_Parent).Data
    # 逐级向上找，直到 USB Root Hub 或 xHCI 控制器
    $chain = @()
    $cur = $parent
    $depth = 0
    while ($cur -and $depth -lt 8) {
        $pObj = Get-PnpDevice -InstanceId $cur
        if (-not $pObj) { break }
        $chain += $pObj.FriendlyName
        if ($pObj.FriendlyName -match 'Root Hub|主机控制器|Host Controller|xHCI|eXtensible') { break }
        $cur = (Get-PnpDeviceProperty -InstanceId $cur -KeyName DEVPKEY_Device_Parent).Data
        $depth++
    }
    [PSCustomObject]@{
        '设备'     = ($d.FriendlyName -replace '^\s+', '')
        '总线 Roots' = ($chain -join ' < ')
    }
}
$rows | Where-Object { $_.'设备' -ne '' } | Sort-Object '总线 Roots' | Format-List

Write-Output ""
Write-Output "================================================================"
Write-Output "二、USB 集线器空闲断电开关（任何 1/Enable 都可能是元凶）"
Write-Output "================================================================"
$keys = Get-ChildItem "HKLM:\SYSTEM\CurrentControlSet\Enum\USB" -Recurse -Depth 3 |
    Where-Object { $_.PSChildName -eq 'Device Parameters' }
foreach ($k in $keys) {
    $p = Get-ItemProperty -Path $k.PSPath
    if ($null -ne $p.EnhancedPowerManagementEnabled -or
        $null -ne $p.IdleInWorkingState -or
        $null -ne $p.SelectiveSuspendEnabled -or
        $null -ne $p.AllowIdleIrpInD3 -or
        $null -ne $p.DeviceSelectiveSuspended) {
        $name = ($k.PSPath -split '\\')[-2]
        Write-Output "  $name"
        Write-Output ("     EnhancedPowerManagementEnabled = {0}" -f $p.EnhancedPowerManagementEnabled)
        Write-Output ("     IdleInWorkingState             = {0}" -f $p.IdleInWorkingState)
        Write-Output ("     SelectiveSuspendEnabled        = {0}" -f $p.SelectiveSuspendEnabled)
        Write-Output ("     AllowIdleIrpInD3               = {0}" -f $p.AllowIdleIrpInD3)
        Write-Output ("     DeviceSelectiveSuspended       = {0}" -f $p.DeviceSelectiveSuspended)
    }
}

Write-Output ""
Write-Output "================================================================"
Write-Output "三、USB 3.0 控制器链路电源管理 (LPM/U1U2)"
Write-Output "================================================================"
$xhci = Get-PnpDevice | Where-Object { $_.FriendlyName -match '可扩展主机控制器|xHCI|Host Controller' -and $_.FriendlyName -match 'USB' }
foreach ($x in $xhci) {
    Write-Output "  控制器: $($x.FriendlyName)"
    Write-Output ("    Status = {0}" -f $x.Status)
    $pp = "HKLM:\SYSTEM\CurrentControlSet\Enum\$($x.InstanceId)\Device Parameters"
    $vp = Get-ItemProperty -Path $pp
    if ($vp) {
        foreach ($n in $vp.PSObject.Properties.Name) {
            if ($n -match 'Idle|IdlePower|PowerEnable|Selective|AutoPower|U1U2|LPM|PowerDelivery|HubDepth') {
                Write-Output ("      {0} = {1}" -f $n, $vp.$n)
            }
        }
    }
    Write-Output ""
}

Write-Output "================================================================"
Write-Output "四、电源传输模式（是否允许 USB 口断电/低供电）"
Write-Output "================================================================"
Get-CimInstance -Namespace root\wmi -ClassName MSPower_DeviceEnable |
    Select-Object InstanceName, Enable | Format-List
