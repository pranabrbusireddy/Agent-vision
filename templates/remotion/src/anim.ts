import { Easing as E, interpolate, spring } from "remotion";
import type { Anim, Easing } from "./types";

const CURVES: Record<Exclude<Easing, "spring">, (t: number) => number> = {
  linear: (t) => t,
  easeOut: E.out(E.cubic),
  easeIn: E.in(E.cubic),
  easeInOut: E.inOut(E.cubic),
  // Fast start, hard settle: the "snappy" feel of most launch edits.
  snap: E.out(E.exp),
  overshoot: E.out(E.back(1.6)),
};

/** 0→1 progress of an animation that starts at `fromSec` and lasts `durSec`. */
export function progress(frame: number, fps: number, fromSec: number, durSec: number, easing: Easing = "easeOut"): number {
  const start = fromSec * fps;
  const len = Math.max(1, durSec * fps);
  if (easing === "spring") {
    return spring({ frame: frame - start, fps, config: { damping: 14, stiffness: 170, mass: 0.6 }, durationInFrames: len });
  }
  const t = interpolate(frame, [start, start + len], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return CURVES[easing](t);
}

/** CSS for a layer given its enter/exit animations, at `frame` within its shot. */
export function animStyle(
  frame: number,
  fps: number,
  shotLenSec: number,
  enter?: Anim,
  exit?: Anim,
): { style: React.CSSProperties; reveal: number } {
  let opacity = 1;
  let tx = 0;
  let ty = 0;
  let scale = 1;
  let blur = 0;
  let clip: string | undefined;
  let reveal = 1; // typewriter: fraction of characters shown

  const apply = (a: Anim, p: number) => {
    // p: 0 = hidden state, 1 = resting state.
    const amt = a.amount;
    switch (a.type) {
      case "fade":
        opacity *= p;
        break;
      case "rise":
        opacity *= Math.min(1, p * 1.5);
        ty += (1 - p) * (amt ?? 6);
        break;
      case "drop":
        opacity *= Math.min(1, p * 1.5);
        ty -= (1 - p) * (amt ?? 6);
        break;
      case "slide-left":
        tx += (1 - p) * (amt ?? 100);
        break;
      case "slide-right":
        tx -= (1 - p) * (amt ?? 100);
        break;
      case "scale":
        opacity *= Math.min(1, p * 2);
        scale *= (amt ?? 0.85) + (1 - (amt ?? 0.85)) * p;
        break;
      case "pop":
        scale *= (amt ?? 0.4) + (1 - (amt ?? 0.4)) * p;
        opacity *= Math.min(1, p * 3);
        break;
      case "wipe":
        clip = `inset(0 ${(1 - p) * 100}% 0 0)`;
        break;
      case "typewriter":
        reveal = p;
        break;
      case "blur":
        opacity *= p;
        blur += (1 - p) * (amt ?? 12);
        break;
      case "none":
        break;
    }
  };

  if (enter && enter.type !== "none") {
    const ease = enter.type === "pop" ? enter.easing ?? "overshoot" : enter.easing;
    apply(enter, progress(frame, fps, enter.at ?? 0, enter.duration ?? 0.35, ease));
  }
  if (exit && exit.type !== "none") {
    const dur = exit.duration ?? 0.25;
    const from = shotLenSec - (exit.at ?? 0) - dur;
    apply(exit, 1 - progress(frame, fps, from, dur, exit.easing ?? "easeIn"));
  }

  return {
    reveal,
    style: {
      opacity,
      transform: `translate(${tx}%, ${ty}%) scale(${scale})`,
      filter: blur ? `blur(${blur}px)` : undefined,
      clipPath: clip,
    },
  };
}
