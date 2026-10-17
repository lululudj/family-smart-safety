import React from "react";
import { AbsoluteFill, Audio, staticFile } from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { TitleCard } from "./TitleCard";
import { OutroCard } from "./OutroCard";
import { ClipWithCaption } from "./ClipWithCaption";

// ============ EDITABLE CONSTANTS ============
const TIMING = {
  cardFrames: 60, // 2.5s @ 24fps
  clipFrames: 120, // 5s @ 24fps (matches 24fps AI source clips exactly)
  transitionFrames: 16, // 0.67s crossfade, softer cuts
};

const SHOTS: {
  src: string;
  tag: string;
  caption: string;
  narration: string;
}[] = [
  {
    src: "assets/shot1.mp4",
    tag: "隐患",
    caption: "离家后的厨房，火还开着",
    narration: "assets/narration/n1.mp3",
  },
  {
    src: "assets/shot2.mp4",
    tag: "起飞",
    caption: "空中之眼，开始巡逻",
    narration: "assets/narration/n2.mp3",
  },
  {
    src: "assets/shot3.mp4",
    tag: "塔台",
    caption: "全屋三维感知中枢",
    narration: "assets/narration/n3.mp3",
  },
  {
    src: "assets/shot4.mp4",
    tag: "发现",
    caption: "异常，被实时捕捉",
    narration: "assets/narration/n4.mp3",
  },
  {
    src: "assets/shot4b.mp4",
    tag: "出动",
    caption: "带机械臂的机器狗，直奔厨房",
    narration: "assets/narration/n5.mp3",
  },
  {
    src: "assets/shot5.mp4",
    tag: "处置",
    caption: "机械臂转动旋钮，关闭阀门",
    narration: "assets/narration/n6.mp3",
  },
  {
    src: "assets/shot6.mp4",
    tag: "安心",
    caption: "实时画面，直达家人",
    narration: "assets/narration/n7.mp3",
  },
];

const BGM = { src: "assets/bgm.ogg", volume: 0.22 };
const OUTRO_NARRATION = "assets/narration/n8.mp3";
// ============================================

export const PromoComposition: React.FC = () => {
  return (
    <AbsoluteFill style={{ backgroundColor: "#0B1220" }}>
      <TransitionSeries>
        <TransitionSeries.Sequence durationInFrames={TIMING.cardFrames}>
          <TitleCard />
        </TransitionSeries.Sequence>

        {SHOTS.map((shot) => (
          <React.Fragment key={shot.src}>
            <TransitionSeries.Transition
              presentation={fade()}
              timing={linearTiming({ durationInFrames: TIMING.transitionFrames })}
            />
            <TransitionSeries.Sequence durationInFrames={TIMING.clipFrames}>
              <ClipWithCaption
                src={shot.src}
                tag={shot.tag}
                caption={shot.caption}
                narration={shot.narration}
              />
            </TransitionSeries.Sequence>
          </React.Fragment>
        ))}

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: TIMING.transitionFrames })}
        />
        <TransitionSeries.Sequence durationInFrames={TIMING.cardFrames}>
          <OutroCard narration={OUTRO_NARRATION} />
        </TransitionSeries.Sequence>
      </TransitionSeries>

      <Audio src={staticFile(BGM.src)} volume={BGM.volume} />
    </AbsoluteFill>
  );
};
