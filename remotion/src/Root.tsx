import type { FC } from "react";
import { Composition } from "remotion";
import { Clip, defaultClipProps } from "./Clip";

export const RemotionRoot: FC = () => {
  return (
    <>
      <Composition
        id="Clip"
        component={Clip}
        durationInFrames={90}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={defaultClipProps}
        calculateMetadata={({ props }) => {
          const fps = props.fps > 0 ? props.fps : 30;
          const seconds = props.durationInSeconds > 0 ? props.durationInSeconds : 1;
          return {
            durationInFrames: Math.max(1, Math.round(seconds * fps)),
            fps,
            width: 1080,
            height: 1920,
          };
        }}
      />
    </>
  );
};
