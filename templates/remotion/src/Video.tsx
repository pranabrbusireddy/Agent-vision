import { AbsoluteFill, Audio, Img, OffthreadVideo, Sequence, continueRender, delayRender, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { useEffect, useState } from "react";
import { animStyle, progress } from "./anim";
import type { Layer, Shot, Storyboard } from "./types";

const FALLBACK = {
  display: "'Helvetica Neue', Arial, sans-serif",
  body: "'Helvetica Neue', Arial, sans-serif",
  mono: "Menlo, ui-monospace, monospace",
};

/** Load brand font files from public/ before the first frame renders. */
function useFonts(sb: Storyboard) {
  const [handle] = useState(() => (sb.fonts?.files?.length ? delayRender("fonts") : null));
  useEffect(() => {
    if (handle === null) return;
    Promise.all(
      sb.fonts!.files!.map(async (f) => {
        const face = new FontFace(f.family, `url(${staticFile(f.src)})`, { weight: f.weight ?? "400" });
        document.fonts.add(await face.load());
      }),
    ).then(() => continueRender(handle));
  }, [handle, sb]);
}

const boxStyle = (b: Layer["box"]): React.CSSProperties => ({
  position: "absolute",
  left: `${b.x}%`,
  top: `${b.y}%`,
  width: `${b.w}%`,
  height: `${b.h}%`,
});

function LayerView({ layer, sb, shotLen }: { layer: Layer; sb: Storyboard; shotLen: number }) {
  const frame = useCurrentFrame();
  const { fps, height } = useVideoConfig();
  const { style, reveal } = animStyle(frame, fps, shotLen, layer.enter, layer.exit);

  if (layer.type === "text") {
    const s = layer.style;
    const family = (s.font && sb.fonts?.[s.font]) || FALLBACK[s.font ?? "display"];
    const text = s.case === "upper" ? layer.text.toUpperCase() : s.case === "lower" ? layer.text.toLowerCase() : layer.text;
    const shown = reveal < 1 ? text.slice(0, Math.round(text.length * reveal)) : text;
    const justify = s.align === "left" ? "flex-start" : s.align === "right" ? "flex-end" : "center";
    return (
      <div style={{ ...boxStyle(layer.box), display: "flex", alignItems: "center", justifyContent: justify, ...style }}>
        <div
          style={{
            fontFamily: family,
            fontSize: (s.sizePct / 100) * height,
            fontWeight: s.weight ?? 700,
            color: s.color,
            letterSpacing: s.letterSpacing ? `${s.letterSpacing}em` : undefined,
            lineHeight: s.lineHeight ?? 1.05,
            textAlign: s.align ?? "center",
            textShadow: s.shadow,
            whiteSpace: "pre-wrap",
          }}
        >
          {shown}
        </div>
      </div>
    );
  }
  if (layer.type === "rect") {
    return <div style={{ ...boxStyle(layer.box), background: layer.color, borderRadius: layer.radius, ...style }} />;
  }
  const media: React.CSSProperties = {
    width: "100%",
    height: "100%",
    objectFit: layer.fit ?? "contain",
    borderRadius: layer.radius,
    boxShadow: layer.shadow ? "0 30px 80px rgba(0,0,0,0.35)" : undefined,
  };
  return (
    <div style={{ ...boxStyle(layer.box), ...style }}>
      {layer.type === "video" ? (
        <OffthreadVideo
          src={staticFile(layer.src)}
          muted
          style={media}
          startFrom={Math.round((layer.trimStart ?? 0) * fps)}
          playbackRate={layer.playbackRate ?? 1}
        />
      ) : (
        <Img src={staticFile(layer.src)} style={media} />
      )}
    </div>
  );
}

function ShotView({ shot, sb, len }: { shot: Shot; sb: Storyboard; len: number }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = shot.transitionIn ?? { type: "cut" };
  const tp = t.type === "cut" ? 1 : progress(frame, fps, 0, t.duration ?? 0.25, t.type === "whip" ? "snap" : "easeOut");

  // Incoming transition (the outgoing shot stays underneath until this covers it).
  let opacity = 1;
  let transform = "";
  let filter: string | undefined;
  switch (t.type) {
    case "fade":
      opacity = tp;
      break;
    case "slide-left":
      transform = `translateX(${(1 - tp) * 100}%)`;
      break;
    case "slide-up":
      transform = `translateY(${(1 - tp) * 100}%)`;
      break;
    case "zoom":
      opacity = Math.min(1, tp * 2);
      transform = `scale(${1.25 - 0.25 * tp})`;
      break;
    case "whip":
      transform = `translateX(${(1 - tp) * 100}%)`;
      filter = tp < 1 ? `blur(${(1 - tp) * 24}px)` : undefined;
      break;
  }

  // Camera move over the whole shot.
  const c = shot.camera ?? { type: "static" };
  const cp = progress(frame, fps, 0, len, c.easing ?? "linear");
  const amt = c.amount ?? 0.08;
  let cam = "";
  switch (c.type) {
    case "push-in":
      cam = `scale(${1 + amt * cp})`;
      break;
    case "pull-out":
      cam = `scale(${1 + amt * (1 - cp)})`;
      break;
    case "pan-left":
      cam = `scale(${1 + amt}) translateX(${(0.5 - cp) * amt * 100}%)`;
      break;
    case "pan-right":
      cam = `scale(${1 + amt}) translateX(${(cp - 0.5) * amt * 100}%)`;
      break;
    case "drift-up":
      cam = `scale(${1 + amt}) translateY(${(0.5 - cp) * amt * 100}%)`;
      break;
    case "shake": {
      const k = interpolate(frame % 4, [0, 1, 2, 3], [0, 1, -1, 0.5]);
      cam = `translate(${k * amt * 10}px, ${-k * amt * 6}px)`;
      break;
    }
  }

  const flash = t.type === "flash" ? 1 - progress(frame, fps, 0, t.duration ?? 0.15, "easeOut") : 0;

  return (
    <AbsoluteFill style={{ background: shot.background, opacity, transform, filter, overflow: "hidden" }}>
      <AbsoluteFill style={{ transform: cam }}>
        {shot.layers.map((l, i) => (
          <LayerView key={i} layer={l} sb={sb} shotLen={len} />
        ))}
      </AbsoluteFill>
      {flash > 0 && <AbsoluteFill style={{ background: "#fff", opacity: flash }} />}
    </AbsoluteFill>
  );
}

export function Main({ storyboard: sb }: { storyboard: Storyboard }) {
  useFonts(sb);
  const { fps } = useVideoConfig();
  const f = (s: number) => Math.round(s * fps);
  return (
    <AbsoluteFill style={{ background: sb.shots[0]?.background ?? "#000" }}>
      {sb.shots.map((shot, i) => {
        // Hold each shot until the next one's transition has fully covered it.
        const next = sb.shots[i + 1];
        const overlap = next?.transitionIn && next.transitionIn.type !== "cut" ? next.transitionIn.duration ?? 0.25 : 0;
        return (
          <Sequence key={shot.id} from={f(shot.start)} durationInFrames={Math.max(1, f(shot.end + overlap) - f(shot.start))} name={shot.id}>
            <ShotView shot={shot} sb={sb} len={shot.end - shot.start} />
          </Sequence>
        );
      })}
      {sb.audio?.bed && <Audio src={staticFile(sb.audio.bed)} volume={sb.audio.bedVolume ?? 0.8} />}
      {sb.audio?.cues?.map((c, i) => (
        <Sequence key={`cue${i}`} from={f(c.t)} name={c.label ?? c.src}>
          <Audio src={staticFile(c.src)} volume={c.volume ?? 1} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
}
