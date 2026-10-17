import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  spring,
  useVideoConfig,
} from "remotion";

// ============ EDITABLE CONSTANTS ============
const CONTENT = {
  title: "家有塔台",
  subtitle: "空地协同 · 离家看护系统",
  footnote: "无人机 × 机器狗 × 三维视觉中枢",
};
const STYLE = {
  bg: "#0B1220",
  titleColor: "#FFFFFF",
  subColor: "#9FC3EE",
  accent: "#4A90D9",
  font: "Microsoft YaHei, PingFang SC, sans-serif",
};
// ============================================

export const TitleCard: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleScale = spring({ frame, fps, config: { damping: 100, stiffness: 120 } });
  const titleOpacity = interpolate(frame, [0, 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const subOpacity = interpolate(frame, [18, 36], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const subY = interpolate(frame, [18, 40], [26, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const footOpacity = interpolate(frame, [36, 54], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const lineWidth = interpolate(frame, [24, 50], [0, 320], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: STYLE.bg,
        justifyContent: "center",
        alignItems: "center",
        fontFamily: STYLE.font,
      }}
    >
      <div
        style={{
          fontSize: 110,
          fontWeight: 700,
          color: STYLE.titleColor,
          opacity: titleOpacity,
          transform: `scale(${titleScale})`,
          letterSpacing: 12,
        }}
      >
        {CONTENT.title}
      </div>
      <div
        style={{
          width: lineWidth,
          height: 4,
          backgroundColor: STYLE.accent,
          marginTop: 28,
          borderRadius: 2,
        }}
      />
      <div
        style={{
          fontSize: 44,
          color: STYLE.subColor,
          marginTop: 30,
          opacity: subOpacity,
          transform: `translateY(${subY}px)`,
          letterSpacing: 6,
        }}
      >
        {CONTENT.subtitle}
      </div>
      <div
        style={{
          fontSize: 24,
          color: "#5C7185",
          marginTop: 46,
          opacity: footOpacity,
          letterSpacing: 3,
        }}
      >
        {CONTENT.footnote}
      </div>
    </AbsoluteFill>
  );
};
