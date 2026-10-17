import React from "react";
import {
  AbsoluteFill,
  Audio,
  staticFile,
  useCurrentFrame,
  interpolate,
} from "remotion";

// ============ EDITABLE CONSTANTS ============
const CONTENT = {
  title: "眼 · 脑 · 手，三位一体",
  subtitle: "Ganglion 塔台系统",
  footnote: "让每个家都有一双不眨眼的眼",
};
const STYLE = {
  bg: "#0B1220",
  titleColor: "#FFFFFF",
  subColor: "#9FC3EE",
  accent: "#4A90D9",
  font: "Microsoft YaHei, PingFang SC, sans-serif",
};
// ============================================

export const OutroCard: React.FC<{ narration: string }> = ({ narration }) => {
  const frame = useCurrentFrame();

  const titleOpacity = interpolate(frame, [0, 16], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const titleY = interpolate(frame, [0, 20], [24, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const subOpacity = interpolate(frame, [13, 28], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const footOpacity = interpolate(frame, [26, 40], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const lineWidth = interpolate(frame, [16, 36], [0, 260], {
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
          fontSize: 72,
          fontWeight: 700,
          color: STYLE.titleColor,
          opacity: titleOpacity,
          transform: `translateY(${titleY}px)`,
          letterSpacing: 6,
        }}
      >
        {CONTENT.title}
      </div>
      <div
        style={{
          width: lineWidth,
          height: 4,
          backgroundColor: STYLE.accent,
          marginTop: 26,
          borderRadius: 2,
        }}
      />
      <div
        style={{
          fontSize: 40,
          color: STYLE.subColor,
          marginTop: 26,
          opacity: subOpacity,
          letterSpacing: 4,
        }}
      >
        {CONTENT.subtitle}
      </div>
      <div
        style={{
          fontSize: 24,
          color: "#5C7185",
          marginTop: 40,
          opacity: footOpacity,
          letterSpacing: 3,
        }}
      >
        {CONTENT.footnote}
      </div>
    </AbsoluteFill>
  );
};
