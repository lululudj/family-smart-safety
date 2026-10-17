using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Web.Script.Serialization;
using Microsoft.Kinect;

namespace GanglionKinectBridge
{
    internal static class Program
    {
        private static readonly object Gate = new object();
        private static readonly ManualResetEvent StopEvent = new ManualResetEvent(false);
        private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();

        private static KinectSensor _sensor;
        private static ColorFrameReader _colorReader;
        private static DepthFrameReader _depthReader;
        private static BodyFrameReader _bodyReader;
        private static string _runtimeDir;
        private static string _statePath;
        private static string _colorPath;
        private static string _depthPath;
        private static byte[] _colorPixels;
        private static ushort[] _depthPixels;
        private static ushort[] _depthSnapshot;
        private static ColorSpacePoint[] _colorPoints;
        private static ushort[] _registeredDepth;
        private static string _depthRawPath;
        private static string _depthMetaPath;
        private static string _motionPath;
        private const int RegisteredWidth = 960;
        private const int RegisteredHeight = 540;
        private static int _colorWidth = 1920;
        private static int _colorHeight = 1080;
        private static readonly System.Globalization.CultureInfo Inv = System.Globalization.CultureInfo.InvariantCulture;

        // ---- 变化检测：把深度降采样成 96×54 网格逐格比较，只在“有变化”或心跳时写大帧
        private const int MotionW = 96;
        private const int MotionH = 54;
        private const int MotionBlockW = RegisteredWidth / MotionW;    // 10
        private const int MotionBlockH = RegisteredHeight / MotionH;   // 10
        private const int MotionThresholdMm = 80;      // 单格变化超过 80mm 才算“变了”
        private const double MotionRatioTrigger = 0.02; // 变化格占比超过 2% 触发
        private const int MotionCellsTrigger = 8;       // 且至少 8 格，防止噪点误触发
        private const int HeartbeatSeconds = 30;        // 无变化时最长 30 秒也写一帧（防“死机无变化”混淆）
        private const int ColorIntervalMs = 50;         // 彩色图 20fps 写（MJPEG 链路的实际上限）
        private static ushort[] _motionNow;
        private static ushort[] _motionPrev;
        private static bool _motionHasPrev;
        private static bool _motionChanged;
        private static double _motionRatio;
        private static double _motionMeanMm;
        private static DateTime _lastFrameWrite = DateTime.MinValue;
        private static DateTime _lastColorWrite = DateTime.MinValue;
        private static int _trackedBodies;
        private static List<Dictionary<string, object>> _bodies = new List<Dictionary<string, object>>();
        private static DateTime _lastMotionWork = DateTime.MinValue;   // 变化检测节奏（150ms）
        private static DateTime _lastDepthWrite = DateTime.MinValue;   // 深度预览图节奏（独立，避免与彩色图互相饿死）
        private static DateTime _lastStateWrite = DateTime.MinValue;

        private static void Main()
        {
            _runtimeDir = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "kinect_runtime");
            _runtimeDir = Path.GetFullPath(_runtimeDir);
            Directory.CreateDirectory(_runtimeDir);
            _statePath = Path.Combine(_runtimeDir, "status.json");
            _colorPath = Path.Combine(_runtimeDir, "color.jpg");
            _depthPath = Path.Combine(_runtimeDir, "depth.jpg");
            _depthRawPath = Path.Combine(_runtimeDir, "depth_raw.bin");
            _depthMetaPath = Path.Combine(_runtimeDir, "depth_meta.json");
            _motionPath = Path.Combine(_runtimeDir, "motion.json");

            Console.CancelKeyPress += delegate(object sender, ConsoleCancelEventArgs args)
            {
                args.Cancel = true;
                StopEvent.Set();
            };

            try
            {
                _sensor = KinectSensor.GetDefault();
                if (_sensor == null)
                {
                    WriteState(false, false, "Kinect v2 sensor not found");
                    Console.Error.WriteLine("Kinect v2 sensor not found.");
                    return;
                }

                _sensor.IsAvailableChanged += Sensor_IsAvailableChanged;
                _colorReader = _sensor.ColorFrameSource.OpenReader();
                _depthReader = _sensor.DepthFrameSource.OpenReader();
                _bodyReader = _sensor.BodyFrameSource.OpenReader();
                _colorReader.FrameArrived += ColorReader_FrameArrived;
                _depthReader.FrameArrived += DepthReader_FrameArrived;
                _bodyReader.FrameArrived += BodyReader_FrameArrived;

                FrameDescription colorDescription = _sensor.ColorFrameSource.CreateFrameDescription(ColorImageFormat.Bgra);
                _colorWidth = colorDescription.Width;
                _colorHeight = colorDescription.Height;
                _colorPixels = new byte[_colorWidth * _colorHeight * 4];
                FrameDescription depthDescription = _sensor.DepthFrameSource.FrameDescription;
                _depthPixels = new ushort[depthDescription.Width * depthDescription.Height];
                _depthSnapshot = new ushort[depthDescription.Width * depthDescription.Height];
                _colorPoints = new ColorSpacePoint[depthDescription.Width * depthDescription.Height];
                _registeredDepth = new ushort[RegisteredWidth * RegisteredHeight];
                _motionNow = new ushort[MotionW * MotionH];
                _motionPrev = new ushort[MotionW * MotionH];

                _sensor.Open();
                WriteState(_sensor.IsAvailable, false, _sensor.IsAvailable ? "Kinect v2 bridge running" : "Kinect sensor unavailable");
                Console.WriteLine("Kinect bridge running.");
                Console.WriteLine("Runtime: " + _runtimeDir);
                Console.WriteLine("Press Ctrl+C to stop.");
                StopEvent.WaitOne();
            }
            catch (Exception ex)
            {
                WriteState(false, false, ex.GetType().Name + ": " + ex.Message);
                Console.Error.WriteLine(ex.ToString());
                Environment.ExitCode = 1;
            }
            finally
            {
                DisposeReaders();
                if (_sensor != null)
                {
                    try { _sensor.Close(); } catch { }
                }
            }
        }

        private static void Sensor_IsAvailableChanged(object sender, IsAvailableChangedEventArgs e)
        {
            WriteState(e.IsAvailable, false, e.IsAvailable ? "Kinect v2 bridge running" : "Kinect sensor unavailable");
        }

        private static void ColorReader_FrameArrived(object sender, ColorFrameArrivedEventArgs e)
        {
            try
            {
                using (ColorFrame frame = e.FrameReference.AcquireFrame())
                {
                    if (frame == null) return;
                    frame.CopyConvertedFrameDataToArray(_colorPixels, ColorImageFormat.Bgra);

                    // 变化检测：按固定节奏算（150ms）
                    if ((DateTime.UtcNow - _lastMotionWork).TotalMilliseconds >= 150)
                    {
                        BuildRegisteredDepth();
                        WriteMotionJson();
                        // 深度大文件（1MB）只在“有变化 / 心跳”时写，省硬盘
                        bool heartbeat = (DateTime.UtcNow - _lastFrameWrite).TotalSeconds >= HeartbeatSeconds;
                        if (_motionChanged || heartbeat) WriteDepthFiles();
                        _lastMotionWork = DateTime.UtcNow;
                    }

                    // 彩色图：只有自己的节流，保证网页视频连续（约 4fps）
                    if ((DateTime.UtcNow - _lastColorWrite).TotalMilliseconds >= ColorIntervalMs)
                    {
                        WriteColorFile();
                        _lastColorWrite = DateTime.UtcNow;
                    }
                    WriteState(true, true, "RGB frame streaming");
                }
            }
            catch (Exception ex)
            {
                WriteState(false, false, "Color frame error: " + ex.Message);
            }
        }

        private static void DepthReader_FrameArrived(object sender, DepthFrameArrivedEventArgs e)
        {
            try
            {
                using (DepthFrame frame = e.FrameReference.AcquireFrame())
                {
                    if (frame == null) return;
                    frame.CopyFrameDataToArray(_depthPixels);
                    if ((DateTime.UtcNow - _lastDepthWrite).TotalMilliseconds >= 150)
                    {
                        FrameDescription description = frame.FrameDescription;
                        WriteDepthJpeg(_depthPixels, description.Width, description.Height,
                            frame.DepthMinReliableDistance, frame.DepthMaxReliableDistance, _depthPath);
                        _lastDepthWrite = DateTime.UtcNow;
                    }
                    WriteState(true, true, "Depth frame streaming");
                }
            }
            catch (Exception ex)
            {
                WriteState(false, false, "Depth frame error: " + ex.Message);
            }
        }

        private static void BodyReader_FrameArrived(object sender, BodyFrameArrivedEventArgs e)
        {
            try
            {
                using (BodyFrame frame = e.FrameReference.AcquireFrame())
                {
                    if (frame == null) return;
                    Body[] bodies = new Body[_sensor.BodyFrameSource.BodyCount];
                    frame.GetAndRefreshBodyData(bodies);
                    List<Dictionary<string, object>> nextBodies = new List<Dictionary<string, object>>();
                    int tracked = 0;

                    foreach (Body body in bodies)
                    {
                        if (body == null || !body.IsTracked) continue;
                        tracked++;
                        Dictionary<string, object> joints = new Dictionary<string, object>();
                        foreach (KeyValuePair<JointType, Joint> item in body.Joints)
                        {
                            CameraSpacePoint p = item.Value.Position;
                            joints[item.Key.ToString()] = new Dictionary<string, object>
                            {
                                { "x", Math.Round(p.X, 4) },
                                { "y", Math.Round(p.Y, 4) },
                                { "z", Math.Round(p.Z, 4) },
                                { "tracking_state", item.Value.TrackingState.ToString() }
                            };
                        }
                        nextBodies.Add(new Dictionary<string, object>
                        {
                            { "tracking_id", body.TrackingId.ToString() },
                            { "joints", joints }
                        });
                    }

                    lock (Gate)
                    {
                        _trackedBodies = tracked;
                        _bodies = nextBodies;
                    }
                    WriteState(true, true, tracked > 0 ? "Body tracking active" : "RGB-D streaming; no tracked body");
                }
            }
            catch (Exception ex)
            {
                WriteState(false, false, "Body frame error: " + ex.Message);
            }
        }

        private static void WriteState(bool available, bool framesActive, string note)
        {
            if ((DateTime.UtcNow - _lastStateWrite).TotalMilliseconds < 250) return;
            _lastStateWrite = DateTime.UtcNow;

            int bodyCount;
            List<Dictionary<string, object>> bodies;
            lock (Gate)
            {
                bodyCount = _trackedBodies;
                bodies = _bodies;
            }

            Dictionary<string, object> state = new Dictionary<string, object>
            {
                { "connected", available },
                { "available", available },
                { "frames_active", framesActive },
                { "sensor", "Kinect v2" },
                { "note", note },
                { "body_count", bodyCount },
                { "bodies", bodies },
                { "color_width", 1920 },
                { "color_height", 1080 },
                { "depth_width", 512 },
                { "depth_height", 424 },
                { "color_url", "/kinect/color.jpg" },
                { "depth_url", "/kinect/depth.jpg" },
                { "updated_at", DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") }
            };

            try
            {
                WriteTextAtomically(_statePath, Json.Serialize(state));
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("State write failed: " + ex.Message);
            }
        }

        /// <summary>
        /// 计算与彩色像素对齐的原始深度（uint16，毫米），并做变化检测。
        /// 只计算不落盘；是否写盘由调用方按“有变化 / 心跳”决定。
        /// </summary>
        private static void BuildRegisteredDepth()
        {
            int depthLen = _depthPixels.Length;
            lock (Gate)
            {
                Array.Copy(_depthPixels, _depthSnapshot, depthLen);
            }
            ushort[] depths = _depthSnapshot;

            _sensor.CoordinateMapper.MapDepthFrameToColorSpace(depths, _colorPoints);
            Array.Clear(_registeredDepth, 0, _registeredDepth.Length);

            for (int i = 0; i < depthLen; i++)
            {
                ushort d = depths[i];
                if (d == 0) continue;
                ColorSpacePoint cs = _colorPoints[i];
                if (float.IsNegativeInfinity(cs.X) || float.IsNegativeInfinity(cs.Y)) continue;
                int px = (int)(cs.X * 0.5f + 0.5f);   // 1920 -> 960
                int py = (int)(cs.Y * 0.5f + 0.5f);   // 1080 -> 540
                if (px < 0 || px >= RegisteredWidth || py < 0 || py >= RegisteredHeight) continue;
                int offset = py * RegisteredWidth + px;
                ushort prev = _registeredDepth[offset];
                // 同一格有多个深度时保留最近的，避免人物边缘串到背后墙上
                if (prev == 0 || d < prev) _registeredDepth[offset] = d;
            }

            ComputeMotion();
        }

        /// <summary>
        /// 变化检测：把深度降采样成 96×54 网格逐格比较。
        /// 每格取块内最近的有效深度作为代表值，与上一帧同格比。
        /// </summary>
        private static void ComputeMotion()
        {
            int changed = 0, valid = 0;
            double sum = 0;
            for (int gy = 0; gy < MotionH; gy++)
            {
                int y0 = gy * MotionBlockH;
                for (int gx = 0; gx < MotionW; gx++)
                {
                    int x0 = gx * MotionBlockW;
                    ushort best = 0;
                    for (int y = y0; y < y0 + MotionBlockH; y++)
                    {
                        int rowOffset = y * RegisteredWidth;
                        for (int x = x0; x < x0 + MotionBlockW; x++)
                        {
                            ushort v = _registeredDepth[rowOffset + x];
                            if (v != 0 && (best == 0 || v < best)) best = v;
                        }
                    }
                    int idx = gy * MotionW + gx;
                    _motionNow[idx] = best;
                    if (_motionHasPrev)
                    {
                        ushort p = _motionPrev[idx];
                        if (p != 0 && best != 0)
                        {
                            valid++;
                            int diff = Math.Abs((int)best - (int)p);
                            sum += diff;
                            if (diff > MotionThresholdMm) changed++;
                        }
                    }
                }
            }

            if (_motionHasPrev && valid > 0)
            {
                _motionRatio = changed / (double)valid;
                _motionMeanMm = sum / valid;
                _motionChanged = changed >= MotionCellsTrigger && _motionRatio >= MotionRatioTrigger;
            }
            else
            {
                _motionRatio = 0.0;
                _motionMeanMm = 0.0;
                _motionChanged = false;
            }

            ushort[] swap = _motionPrev;   // 本帧变成下一帧的“上一帧”
            _motionPrev = _motionNow;
            _motionNow = swap;
            _motionHasPrev = true;
        }

        /// <summary>
        /// 写彩色图（网页视频流用）。960×540 + 后台线程编码：
        /// GDI+ 编码 1080p 约 300ms 会把帧事件线程拖到 3fps；540p 约 70ms 且不阻塞事件。
        /// </summary>
        private static void WriteColorFile()
        {
            byte[] pixels;
            int width, height;
            lock (Gate)
            {
                pixels = (byte[])_colorPixels.Clone();
                width = _colorWidth;
                height = _colorHeight;
            }
            ThreadPool.QueueUserWorkItem(_ =>
            {
                try
                {
                    using (Bitmap full = new Bitmap(width, height, PixelFormat.Format32bppArgb))
                    {
                        BitmapData data = full.LockBits(new Rectangle(0, 0, width, height), ImageLockMode.WriteOnly, PixelFormat.Format32bppArgb);
                        try
                        {
                            Marshal.Copy(pixels, 0, data.Scan0, pixels.Length);
                        }
                        finally
                        {
                            full.UnlockBits(data);
                        }
                        using (Bitmap small = new Bitmap(RegisteredWidth, RegisteredHeight))
                        using (Graphics g = Graphics.FromImage(small))
                        {
                            g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.Bilinear;
                            g.DrawImage(full, 0, 0, RegisteredWidth, RegisteredHeight);
                            SaveJpegAtomically(small, _colorPath, 78L);
                        }
                    }
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("color encode failed: " + ex.Message);
                }
            });
        }

        /// <summary>写原始深度 + 内参。仅在“有变化 / 心跳”时调用，省硬盘。</summary>
        private static void WriteDepthFiles()
        {
            byte[] raw = new byte[_registeredDepth.Length * 2];
            Buffer.BlockCopy(_registeredDepth, 0, raw, 0, raw.Length);
            WriteBytesAtomically(_depthRawPath, raw);

            // 彩色内参（Kinect v2 彩色视场 84.1° × 53.8°），供 Python 反投影使用
            double fx = (RegisteredWidth / 2.0) / Math.Tan(84.1 * Math.PI / 360.0);
            double fy = (RegisteredHeight / 2.0) / Math.Tan(53.8 * Math.PI / 360.0);
            string meta = "{"
                + "\"width\":" + RegisteredWidth
                + ",\"height\":" + RegisteredHeight
                + ",\"unit\":\"mm\""
                + ",\"source\":\"color_registered\""
                + ",\"fx\":" + fx.ToString("F3", Inv)
                + ",\"fy\":" + fy.ToString("F3", Inv)
                + ",\"cx\":" + ((RegisteredWidth - 1) / 2.0).ToString("F1", Inv)
                + ",\"cy\":" + ((RegisteredHeight - 1) / 2.0).ToString("F1", Inv)
                + ",\"updated_at\":\"" + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "\""
                + "}";
            WriteTextAtomically(_depthMetaPath, meta);

            _lastFrameWrite = DateTime.UtcNow;
        }

        /// <summary>写变化度（小文件，每帧都写，让网页实时看到变化程度）。</summary>
        private static void WriteMotionJson()
        {
            string json = "{"
                + "\"changed\":" + (_motionChanged ? "true" : "false")
                + ",\"ratio\":" + _motionRatio.ToString("F4", Inv)
                + ",\"mean_mm\":" + _motionMeanMm.ToString("F1", Inv)
                + ",\"threshold_mm\":" + MotionThresholdMm
                + ",\"updated_at\":\"" + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "\""
                + "}";
            WriteTextAtomically(_motionPath, json);
        }

        private static void WriteBytesAtomically(string path, byte[] data)
        {
            string temp = path + ".tmp";
            File.WriteAllBytes(temp, data);
            ReplaceFile(temp, path);
        }

        private static void WriteBgraJpeg(byte[] pixels, int width, int height, string path)
        {
            using (Bitmap bitmap = new Bitmap(width, height, PixelFormat.Format32bppArgb))
            {
                BitmapData data = bitmap.LockBits(new Rectangle(0, 0, width, height), ImageLockMode.WriteOnly, PixelFormat.Format32bppArgb);
                try
                {
                    Marshal.Copy(pixels, 0, data.Scan0, pixels.Length);
                }
                finally
                {
                    bitmap.UnlockBits(data);
                }
                SaveJpegAtomically(bitmap, path);
            }
        }

        private static void WriteDepthJpeg(ushort[] pixels, int width, int height, ushort minDepth, ushort maxDepth, string path)
        {
            byte[] bgra = new byte[width * height * 4];
            int range = Math.Max(1, maxDepth - minDepth);
            for (int i = 0; i < pixels.Length; i++)
            {
                int value = pixels[i];
                byte intensity = value < minDepth || value > maxDepth ? (byte)0 : (byte)(255 - Math.Min(255, (value - minDepth) * 255 / range));
                int offset = i * 4;
                bgra[offset] = intensity;
                bgra[offset + 1] = intensity;
                bgra[offset + 2] = intensity;
                bgra[offset + 3] = 255;
            }
            WriteBgraJpeg(bgra, width, height, path);
        }

        private static void SaveJpegAtomically(Bitmap bitmap, string path)
        {
            SaveJpegAtomically(bitmap, path, 80L);
        }

        private static void SaveJpegAtomically(Bitmap bitmap, string path, long quality)
        {
            string temp = path + ".tmp";
            var encoder = ImageCodecInfo.GetImageEncoders().First(c => c.FormatID == ImageFormat.Jpeg.Guid);
            using (var parameters = new EncoderParameters(1))
            {
                parameters.Param[0] = new EncoderParameter(System.Drawing.Imaging.Encoder.Quality, quality);
                bitmap.Save(temp, encoder, parameters);
            }
            ReplaceFile(temp, path);
        }

        private static void WriteTextAtomically(string path, string text)
        {
            string temp = path + ".tmp";
            File.WriteAllText(temp, text);
            ReplaceFile(temp, path);
        }

        private static void ReplaceFile(string temp, string target)
        {
            try
            {
                if (File.Exists(target)) File.Replace(temp, target, null);
                else File.Move(temp, target);
            }
            catch
            {
                File.Copy(temp, target, true);
                File.Delete(temp);
            }
        }

        private static void DisposeReaders()
        {
            if (_colorReader != null) _colorReader.Dispose();
            if (_depthReader != null) _depthReader.Dispose();
            if (_bodyReader != null) _bodyReader.Dispose();
        }
    }
}
