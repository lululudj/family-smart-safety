import React from "react";
import {
  AbsoluteFill,
  Video,
  Audio,
  staticFile,
  useCurrentFrame,
  interpolate,
} from "remotion";

// ============ EDITABLE CONSTANTS ============
const PLAYBACK = { rate: 0.9, trimBefore: 10 }; // slow-mo masks AI jitter, trim unstable head
const STYLE = {
  captionBg: "rgba(8, 12, 20, 0.62)",
  captionAccent: "#4A90D9",
  captionColor: "#FFFFFF",
  captionSize: 36,
  tagSize: 22,
  tagColor: "#9FC3EE",
  font: "Microsoft YaHei, PingFang SC, sans-serif",
};
// ============================================

export const ClipWithCaption: React.FC<{
  src: string;
  tag: string;
  caption: string;
  narration?: string;
}> = ({ src, tag, caption }) => {
  const frame = useCurrentFrame();

  const capIn = interpolate(frame, [10, 24], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const capOut = interpolate(frame, [104, 116], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const capY = interpolate(frame, [10, 26], [24, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      <Video
        src={staticFile(src)}
        volume={0}
        playbackRate={PLAYBACK.rate}
        trimBefore={PLAYBACK.trimBefore}
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
      <div
        style={{
          position: "absolute",
          left: 64,
          bottom: 64,
          opacity: Math.min(capIn, capOut),
          transform: `translateY(${capY}px)`,
        }}
      >
        <div
          style={{
            background: STYLE.captionBg,
            padding: "16px 26px",
            borderRadius: 12,
            borderLeft: `5px solid ${STYLE.captionAccent}`,
          }}
        >
          <div
            style={{
              color: STYLE.tagColor,
              fontSize: STYLE.tagSize,
              fontFamily: STYLE.font,
              letterSpacing: 4,
              marginBottom: 6,
            }}
          >
            {tag}
          </div>
          <div
            style={{
              color: STYLE.captionColor,
              fontSize: STYLE.captionSize,
              fontWeight: 600,
              fontFamily: STYLE.font,
            }}
          >
            {caption}
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};
