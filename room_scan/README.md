# Kinect v2 房间扫描

拿着 Kinect 在房间里慢慢走一圈，把多帧彩色深度融成一块网格。
做法和手机上的「3D Scanner」同类：一边动相机，一边把看到的表面补进同一个模型。
它不是单帧点云导出。

实时链路不新开传感器。`kinect_direct.py` 继续独占 Kinect，本程序只读它已经写好的：

- `tower_ui/kinect_runtime/color.jpg`（960×540）
- `tower_ui/kinect_runtime/depth_raw.bin`（960×540，uint16 毫米，已配准到彩色）
- `tower_ui/kinect_runtime/depth_meta.json`（fx≈532.159，fy≈532.199，cx≈479.5，cy≈269.5）

悬停和 `kinect_yolo3d.py` 照旧读这些文件，默认行为不变。已有的 `room_model*.glb`、`room_clean.ply` 也不会被改写。新网格进 `scans/`。

## 依赖

已有环境里的 `numpy`、`opencv-python` 继续用。新增：

```bat
pip install open3d
```

跟踪和融合都走 Open3D：混合 RGB-D 里程计（光度 + 点到面）和可扩展 TSDF。
`--demo` 和 `--sequence` 不需要 pykinect2，也不需要接 Kinect。

## 真机怎么扫

1. Kinect v2 接 USB 3.0 和外接电源，先跑采集：

   ```bat
   python kinect_direct.py
   ```

2. 另开一个终端：

   ```bat
   python kinect_room_scan.py
   ```

3. 默认马上开始融合。Kinect 放在大约 1.2–1.5 m 高，镜头看向房间。
4. 平移或转动都要慢，大约每秒 10–20 cm，相邻画面重叠留到七成以上。走一小段弧线比原地猛转更稳。
5. 回到键盘：`S` 保存，`Q` 退出并保存。`Ctrl+C` 也会保存。

输出默认是：

```text
scans/room_YYYYMMDD_HHMM.ply      带顶点颜色，用这个
scans/room_YYYYMMDD_HHMM.obj      几何副本，多数软件不带顶点色
scans/room_YYYYMMDD_HHMM_meta.json
```

按键：

| 键 | 作用 |
| --- | --- |
| 空格 / P | 暂停或继续融合。暂停时仍跟踪，只是不写进体积 |
| R | 清空体积，下一帧重新当原点 |
| S | 立刻保存 |
| Q / Esc | 停止并保存 |
| H | 再打印一次说明 |

想先构图再录，加上 `--start-paused`，按 P 再开始。
彩色窗口在有 GUI 的 OpenCV 上会打开；`--preview` 再尽量打开累积点云。无界面机器用 `--no-preview`。

常用参数（家庭房间的默认值）：

```bat
python kinect_room_scan.py --voxel 0.02 --trunc 0.08 --depth-min 0.5 --depth-max 4.5 --min-move 0.03
```

- `--voxel 0.02`：2 cm 体素。Kinect v2 在一两米处本身就是厘米级噪声，再细主要是在拟合噪声。
- `--trunc`：TSDF 截断，默认 4 倍体素。
- `--depth-min/--depth-max`：0.5–4.5 m。太近的点不可靠，太远的点又稀又吵。
- `--min-move 0.03`：相对上一关键帧挪动 3 cm 或转过 2° 才融入，避免站着不动时反复写同一视角。
- `--downscale 2`：半分辨率，跟踪更快，细节更少。
- `--dump scans/seq`：把原始帧存下来，之后可以重放。

采集目录不是默认位置时：

```bat
python kinect_room_scan.py --kinect-dir D:\kinect_runtime
```

也可以用环境变量 `KINECT_DIR`，和 `kinect_direct.py` 一样。

## 没有 Kinect 时

合成一段带家具的房间，相机缓慢横移并稍微偏航，走和真机相同的里程计 + TSDF：

```bat
python kinect_room_scan.py --demo --no-preview
python kinect_room_scan.py --demo --frames 8 --dump scans/seq
python kinect_room_scan.py --sequence scans/seq --out scans/replay.ply --no-preview
```

离线目录格式：

```text
seq/intrinsics.json
seq/color/000000.png
seq/depth/000000.png    # uint16，毫米，0 表示无效
```

`--demo` 融不进至少两帧，或网格近乎为空时，进程返回码是 2。

## 实际扫描时注意

- 慢，并且一直能看到刚才画面里的一部分。跟丢了终端会提示退回重叠区域；实在换了地方就按 R。
- 避开玻璃、镜子、亮面黑物体、窗外强光。这些地方深度是洞，或者会多出一面假墙。
- 配准深度在 960×540 上有细缝。程序会用邻域一致的深度把一两像素的缝补上，大片缺失不会硬填。
- 人不要在镜头和墙之间来回走，否则会被写进模型。

## 和手机 3D Scanner 的差别

手机那类应用通常有 IMU，并且会做回环。这里只有 Kinect 的彩色和深度，帧间里程计，没有回环，绕房间一圈后接缝会对不齐，轨迹越长漂得越多。

Kinect v2 又大又要供电，不能像手机一样随手举着钻进角落。深度噪声是厘米级，默认 2 cm 体素就是按这个量级取的。贴图来自 960×540 的 JPEG，比手机摄影测量软，颜色会在斜表面和边缘上糊一点。

它适合得到一个房间的粗彩色网格，用来看布局和大致尺寸。它不是施工图，也替换不了原来那套 RTAB-Map 的 `room_model_v5.glb`。
