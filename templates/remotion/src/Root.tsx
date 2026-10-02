import { Composition } from "remotion";
import storyboard from "./storyboard.json";
import type { Storyboard } from "./types";
import { Main } from "./Video";

const sb = storyboard as Storyboard;

export const Root = () => (
  <Composition
    id="Main"
    component={Main}
    width={sb.width}
    height={sb.height}
    fps={sb.fps}
    durationInFrames={Math.round(sb.duration * sb.fps)}
    defaultProps={{ storyboard: sb }}
  />
);
