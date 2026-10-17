# 守护：Kinect 桥接 + 三维识别器 挂了自动拉起（计划任务每分钟调用）
if (-not (Get-Process -Name 'kinect_bridge' -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath 'E:\全家智能安全调度系统\tower_ui\kinect_bridge.exe' -WindowStyle Hidden
}
$yolo = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'kinect_yolo3d' }
if (-not $yolo) {
    Start-Process -FilePath 'python' `
        -ArgumentList '-u', 'E:\全家智能安全调度系统\kinect_yolo3d.py', '--interval', '2' `
        -WindowStyle Hidden
}
