/**
 * The storyboard as data. Phase 5 writes src/storyboard.json; nothing else in
 * the template should need editing for an ordinary recreation. Times are in
 * seconds, boxes in % of the frame, font sizes in % of frame height — the same
 * units the brief measures in, so numbers carry straight across.
 */
export type Easing = "linear" | "easeOut" | "easeIn" | "easeInOut" | "snap" | "spring" | "overshoot";

export type Anim = {
  type: "none" | "fade" | "rise" | "drop" | "slide-left" | "slide-right" | "scale" | "pop" | "wipe" | "typewriter" | "blur";
  /** Seconds after the shot starts (enter) or before it ends (exit). */
  at?: number;
  duration?: number;
  easing?: Easing;
  /** Distance for rise/drop/slide in % of frame; start scale for scale/pop. */
  amount?: number;
};

export type Box = { x: number; y: number; w: number; h: number };

export type TextStyle = {
  font?: "display" | "body" | "mono";
  sizePct: number;
  color: string;
  weight?: number;
  case?: "upper" | "lower" | "none";
  letterSpacing?: number;
  lineHeight?: number;
  align?: "left" | "center" | "right";
  shadow?: string;
};

export type Layer =
  | { type: "text"; text: string; box: Box; style: TextStyle; enter?: Anim; exit?: Anim }
  | { type: "image" | "logo"; src: string; box: Box; fit?: "cover" | "contain"; radius?: number; shadow?: boolean; enter?: Anim; exit?: Anim }
  | {
      type: "video";
      src: string;
      box: Box;
      fit?: "cover" | "contain";
      radius?: number;
      shadow?: boolean;
      /** Seconds into the source clip to start from. */
      trimStart?: number;
      playbackRate?: number;
      enter?: Anim;
      exit?: Anim;
    }
  | { type: "rect"; box: Box; color: string; radius?: number; enter?: Anim; exit?: Anim };

export type Shot = {
  id: string;
  /** Which reference shot this mirrors (brief.json shots[].shot). */
  ref?: number;
  start: number;
  end: number;
  background: string;
  transitionIn?: { type: "cut" | "fade" | "slide-left" | "slide-up" | "zoom" | "whip" | "flash"; duration?: number };
  camera?: { type: "static" | "push-in" | "pull-out" | "pan-left" | "pan-right" | "drift-up" | "shake"; amount?: number; easing?: Easing };
  layers: Layer[];
};

export type Storyboard = {
  width: number;
  height: number;
  fps: number;
  duration: number;
  fonts?: { display?: string; body?: string; mono?: string; files?: { family: string; src: string; weight?: string }[] };
  audio?: {
    bed?: string;
    bedVolume?: number;
    cues?: { t: number; src: string; volume?: number; label?: string }[];
  };
  shots: Shot[];
};
