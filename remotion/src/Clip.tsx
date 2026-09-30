import type { CSSProperties, FC } from "react";
import {
  AbsoluteFill,
  OffthreadVideo,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

const ABSOLUTE_SRC = /^(https?:|data:|file:|blob:|[a-zA-Z]:[\\/]|\\\\)/;

// El motor Python puede pasar una URL, una ruta absoluta o un nombre dentro de
// remotion/public. Solo el ultimo caso necesita staticFile().
export const resolveVideoSrc = (src: string): string => {
  if (!src) {
    return "";
  }
  return ABSOLUTE_SRC.test(src) ? src : staticFile(src);
};

export type SubtitleCue = {
  text: string;
  start: number;
  end: number;
};

export type ClipProps = {
  videoSrc: string;
  durationInSeconds: number;
  fps: number;
  title?: string | null;
  subtitles?: SubtitleCue[];
  fit?: "cover" | "contain";
  accentColor?: string;
};

export const defaultClipProps: ClipProps = {
  videoSrc: "",
  durationInSeconds: 3,
  fps: 30,
  title: null,
  subtitles: [],
  fit: "cover",
  accentColor: "#FFD400",
};

const activeCue = (
  subtitles: SubtitleCue[],
  seconds: number,
): SubtitleCue | null => {
  for (const cue of subtitles) {
    if (seconds >= cue.start && seconds < cue.end) {
      return cue;
    }
  }
  return null;
};

export const Clip: FC<ClipProps> = ({
  videoSrc,
  title,
  subtitles = [],
  fit = "cover",
  accentColor = "#FFD400",
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const seconds = frame / fps;

  const cue = activeCue(subtitles, seconds);

  // El titulo es un cartel de apertura, no un rotulo permanente: entra rapido,
  // aguanta unos segundos y se va. Antes se quedaba en pantalla TODO el clip
  // (la opacidad solo caia en los ultimos 0.6s), lo que en un clip de 38s
  // tapaba el borde superior durante 38s y competia con los subtitulos.
  const TITLE_HOLD_FRAMES = Math.round(fps * 2.5);
  const titleEnter = spring({ frame, fps, config: { damping: 200 }, durationInFrames: 12 });
  const titleExit = interpolate(
    frame,
    [TITLE_HOLD_FRAMES, TITLE_HOLD_FRAMES + 15],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const titleOpacity = Math.min(titleEnter, titleExit);

  const objectFit: CSSProperties["objectFit"] = fit;

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {videoSrc ? (
        <OffthreadVideo
          src={resolveVideoSrc(videoSrc)}
          style={{ width: "100%", height: "100%", objectFit }}
        />
      ) : (
        <AbsoluteFill
          style={{
            background: `linear-gradient(160deg, #10131a 0%, #1b2430 55%, #0b0d12 100%)`,
          }}
        />
      )}

      {title ? (
        <AbsoluteFill
          style={{
            justifyContent: "flex-start",
            alignItems: "center",
            paddingTop: 180,
            paddingLeft: 72,
            paddingRight: 72,
            opacity: titleOpacity,
          }}
        >
          <div
            style={{
              fontFamily: "Inter, Arial, sans-serif",
              fontWeight: 800,
              fontSize: 64,
              lineHeight: 1.1,
              color: "white",
              textAlign: "center",
              textShadow: "0 4px 24px rgba(0,0,0,0.75)",
              borderLeft: `10px solid ${accentColor}`,
              paddingLeft: 24,
              paddingRight: 24,
            }}
          >
            {title}
          </div>
        </AbsoluteFill>
      ) : null}

      {cue ? (
        <AbsoluteFill
          style={{
            justifyContent: "flex-end",
            alignItems: "center",
            paddingBottom: 260,
            paddingLeft: 80,
            paddingRight: 80,
          }}
        >
          <div
            style={{
              fontFamily: "Inter, Arial, sans-serif",
              fontWeight: 700,
              fontSize: 56,
              lineHeight: 1.2,
              color: "white",
              textAlign: "center",
              backgroundColor: "rgba(0,0,0,0.62)",
              padding: "14px 28px",
              borderRadius: 12,
              textShadow: "0 2px 12px rgba(0,0,0,0.8)",
            }}
          >
            {cue.text}
          </div>
        </AbsoluteFill>
      ) : null}
    </AbsoluteFill>
  );
};
