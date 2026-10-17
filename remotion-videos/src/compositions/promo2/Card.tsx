import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  spring,
  useVideoConfig,
} from "remotion";

// ============ EDITABLE CONSTANTS ============
const STYLE = {
  bg: "#0B1220",
  titleColor: "#FFFFFF",
  subColor: "#9FC3EE",
  accent: "#4A90D9",
  footColor: "#5C7185",
  font: "Microsoft YaHei, PingFang SC, sans-serif",
};
// ============================================

export const Card: React.FC<{
  title: string;
  subtitle: string;
  footnote: string;
  titleSize?: number;
}> = ({ title, subtitle, footnote, titleSize = 82 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const scale = spring({ frame, fps, config: { damping: 100, stiffness: 120 } });
  const titleOpacity = interpolate(frame, [0, 18], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const titleY = interpolate(frame, [0, 22], [22, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const subOpacity = interpolate(frame, [14, 30], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const footOpacity = interpolate(frame, [28, 44], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const lineWidth = interpolate(frame, [18, 40], [0, 300], {
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
          fontSize: titleSize,
          fontWeight: 700,
          color: STYLE.titleColor,
          opacity: titleOpacity,
          transform: `translateY(${titleY}px) scale(${scale})`,
          letterSpacing: 8,
        }}
      >
        {title}
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
        {subtitle}
      </div>
      <div
        style={{
          fontSize: 24,
          color: STYLE.footColor,
          marginTop: 38,
          opacity: footOpacity,
          letterSpacing: 3,
        }}
      >
        {footnote}
      </div>
    </AbsoluteFill>
  );
};
