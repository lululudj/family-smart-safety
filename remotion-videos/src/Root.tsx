import React from "react";
import { Composition } from "remotion";
import { PromoComposition } from "./compositions/promo/VideoComposition";
import { PromoComposition2 } from "./compositions/promo2/VideoComposition2";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="tower-promo"
        component={PromoComposition}
        durationInFrames={832}
        fps={24}
        width={1920}
        height={1080}
      />
      <Composition
        id="tower-promo-2"
        component={PromoComposition2}
        durationInFrames={936}
        fps={24}
        width={1920}
        height={1080}
      />
    </>
  );
};
