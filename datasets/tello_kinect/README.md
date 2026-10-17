# Tello 专用检测数据集（Kinect 彩图）

本目录供「给 Tello 建模」采集 / 标注 / 训练使用。
推理侧仍走 Kinect 深度 → 三维；训练目标是 **2D 框更稳地认出 Tello**。

## 建议流程
1. 用采集脚本从 `tower_ui/kinect_runtime/color.jpg` 按时保存到 `raw/color/`
2. 标注类别建议：`tello`（唯一类即可；可选 `tello_flying` / `tello_grounded`）
3. 划分 train/val 后放到 `images/` + `labels/`（YOLO txt）
4. 训练得到 `weights/tello_yolov8n.pt`
5. 在 `kinect_yolo3d.py` 切换为专用权重（见项目方案文档）

## 注意
- 采的是 **彩色图**；深度用于上线后算 xyz / verified，训练阶段可不强制标深度
- mock 无人机不会出现在真彩图里，必须用 **实体 Tello** 入镜采集
## 采集脚本

根目录: `capture_tello_frames.py`

```bat
cd /d E:\全家智能安全调度系统
python capture_tello_frames.py --once
python capture_tello_frames.py --interval 0.8
```

连续模式可用 Ctrl+C 停止；若环境支持 msvcrt，按 S 加拍、Q 退出。
