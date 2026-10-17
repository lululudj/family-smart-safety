import React from "react";
import { AbsoluteFill, Audio, staticFile } from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { Card } from "./Card";
import { ClipWithCaption } from "../promo/ClipWithCaption";

// ============ EDITABLE CONSTANTS ============
const TIMING = {
  cardFrames: 60, // 2.5s @ 24fps
  clipFrames: 120, // 5s @ 24fps
  transitionFrames: 16, // 0.67s crossfade
};

const INTRO = {
  title: "家有塔台",
  subtitle: "场景二 · 独居老人跌倒看护",
  footnote: "从发现到救援，全程自动闭环",
};

const OUTRO = {
  title: "发现 · 抵达 · 救援",
  subtitle: "Ganglion 塔台系统",
  footnote: "让独居的父母，始终有人在场",
};

const SHOTS: { src: string; tag: string; caption: string }[] = [
  { src: "assets/e1.mp4", tag: "跌倒", caption: "独居老人在家中意外摔倒" },
  { src: "assets/e2.mp4", tag: "发现", caption: "家庭摄像头捕捉异常画面" },
  { src: "assets/e3.mp4", tag: "告警", caption: "塔台判定跌倒，锁定位置" },
  { src: "assets/e4.mp4", tag: "出动", caption: "无人机与机器狗同时出发" },
  { src: "assets/e5.mp4", tag: "抵达", caption: "空中照明，地面靠近查看" },
  { src: "assets/e6.mp4", tag: "询问", caption: "语音询问老人身体状况" },
  { src: "assets/e7.mp4", tag: "通报", caption: "家人第一时间收到通知" },
  { src: "assets/e8.mp4", tag: "救援", caption: "急救人员抵达现场" },
];

const BGM = { src: "assets/bgm.ogg", volume: 0.22 };
// ============================================

export const PromoComposition2: React.FC = () => {
  return (
    <AbsoluteFill style={{ backgroundColor: "#0B1220" }}>
      <TransitionSeries>
        <TransitionSeries.Sequence durationInFrames={TIMING.cardFrames}>
          <Card title={INTRO.title} subtitle={INTRO.subtitle} footnote={INTRO.footnote} titleSize={100} />
        </TransitionSeries.Sequence>

        {SHOTS.map((shot) => (
          <React.Fragment key={shot.src}>
            <TransitionSeries.Transition
              presentation={fade()}
              timing={linearTiming({ durationInFrames: TIMING.transitionFrames })}
            />
            <TransitionSeries.Sequence durationInFrames={TIMING.clipFrames}>
              <ClipWithCaption src={shot.src} tag={shot.tag} caption={shot.caption} />
            </TransitionSeries.Sequence>
          </React.Fragment>
        ))}

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: TIMING.transitionFrames })}
        />
        <TransitionSeries.Sequence durationInFrames={TIMING.cardFrames}>
          <Card title={OUTRO.title} subtitle={OUTRO.subtitle} footnote={OUTRO.footnote} titleSize={72} />
        </TransitionSeries.Sequence>
      </TransitionSeries>

      <Audio src={staticFile(BGM.src)} volume={BGM.volume} />
    </AbsoluteFill>
  );
};
