"""Phase 2, measured half: media facts, cuts, frames, contact sheet.

    .venv/bin/python scripts/probe.py <run-dir> [--scene 0.3] [--cuts 1.0,2.5,…]

Reads <run-dir>/input/reference.mp4 and writes into <run-dir>/analysis/:
  media.json                     duration, fps, size, codecs, audio present
  shots.json                     cuts, shots and the frames that belong to each
  frames/shot-NN-in.jpg          first frame after each cut
  frames/shot-NN-mid.jpg         middle of each shot
  frames/burst/…                 every 0.25 s around dense cut clusters; every
                                 frame of shots shorter than 3 frames
  frames/t/tSSS.SS.jpg           uniform 0.5 s samples (motion inside long shots)
  contact_sheets/contact.jpg     uniform samples tiled, labelled with time + shot

Cut list override (risk #2: whip-pans and motion transitions that scene
detection misses): pass --cuts, or write analysis/cuts.override.json as a JSON
list of seconds. The override replaces detection entirely and is recorded.

Everything here is measured, nothing interpreted — interpretation is Claude
looking at these frames and citing their file names.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ap = argparse.ArgumentParser()
ap.add_argument("run")
ap.add_argument("--scene", type=float, default=0.3, help="scene-change threshold (lower = more sensitive)")
ap.add_argument("--cuts", help="comma-separated cut times in seconds; replaces detection")
args = ap.parse_args()

RUN = Path(args.run).resolve()
SRC = RUN / "input" / "reference.mp4"
if not SRC.exists():
    raise SystemExit(f"no input/reference.mp4 in {RUN} — run download.sh first")
AN = RUN / "analysis"
FRAMES = AN / "frames"
for sub in ("", "burst", "t"):
    (FRAMES / sub).mkdir(parents=True, exist_ok=True)
(AN / "contact_sheets").mkdir(parents=True, exist_ok=True)


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=True)


info = json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(SRC)]).stdout)
vstream = next(s for s in info["streams"] if s["codec_type"] == "video")
astream = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
num, den = (int(x) for x in vstream["avg_frame_rate"].split("/"))
fps = num / den if den else 30.0
# The video stream's own length: the container can be longer (audio tail, encoder padding).
duration = float(vstream.get("duration") or info["format"]["duration"])
width, height = int(vstream["width"]), int(vstream["height"])
frame = 1 / fps

media = {
    "file": "input/reference.mp4",
    "duration": round(duration, 3),
    "fps": round(fps, 3),
    "frames": round(duration * fps),
    "size": [width, height],
    "video_codec": vstream.get("codec_name"),
    "video_bitrate_kbps": round(int(vstream.get("bit_rate") or info["format"].get("bit_rate") or 0) / 1000),
    "audio": None if not astream else {"codec": astream.get("codec_name"), "sample_rate": int(astream.get("sample_rate", 0)), "channels": astream.get("channels")},
}
# Degraded input lowers confidence downstream (risk #4).
quality_notes = []
if min(width, height) < 720:
    quality_notes.append(f"low resolution ({width}×{height}) — small text and fine motion are less reliable")
if media["video_bitrate_kbps"] and media["video_bitrate_kbps"] < 1000:
    quality_notes.append(f"low bitrate ({media['video_bitrate_kbps']} kbps) — compression may blur fast motion")
media["quality_notes"] = quality_notes
(AN / "media.json").write_text(json.dumps(media, indent=2))

# --- cuts
override_file = AN / "cuts.override.json"
if args.cuts:
    cuts, cut_source = sorted(float(c) for c in args.cuts.split(",") if c.strip()), "override (--cuts)"
    override_file.write_text(json.dumps(cuts))
elif override_file.exists():
    cuts, cut_source = sorted(json.loads(override_file.read_text())), "override (cuts.override.json)"
else:
    log = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(SRC), "-map", "0:v:0",
         "-vf", f"select='gt(scene,{args.scene})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    cuts = []
    for t in (float(x) for x in re.findall(r"pts_time:([\d.]+)", log)):
        if t < frame * 1.5 or t > duration - frame:
            continue
        # Scene scores fire on several frames of a fade: merge detections < 2 frames apart.
        if not cuts or t - cuts[-1] > 2 * frame + 1e-6:
            cuts.append(round(t, 3))
    cut_source = f"ffmpeg scene detection (threshold {args.scene})"


def grab(t, dest, scale=None):
    """The frame on screen at time t. -ss returns the first frame at or after the
    seek time, so seek a quarter-frame before that frame's timestamp."""
    vf = ["-vf", f"scale={scale}:-2"] if scale else []
    last = max(0, round(duration * fps) - 1)
    seek = max(0.0, (min(int(t * fps + 1e-6), last) - 0.25) / fps)
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{seek:.4f}", "-i", str(SRC), "-frames:v", "1", *vf, "-q:v", "2", str(dest)])
    return str(dest.relative_to(RUN))


bounds = [0.0, *cuts, duration]
shots = []
for i, (a, b) in enumerate(zip(bounds, bounds[1:]), start=1):
    length = b - a
    n_frames = round(length * fps)
    shot = {
        "shot": i,
        "start": round(a, 3),
        "end": round(b, 3),
        "duration": round(length, 3),
        "frames_count": n_frames,
        # +half a frame: the frame showing just after the cut, robust to rounding in the cut time.
        "frames": {"in": grab(a + frame * 0.5, FRAMES / f"shot-{i:02d}-in.jpg"), "mid": grab(a + length / 2, FRAMES / f"shot-{i:02d}-mid.jpg")},
        "fast": length < 0.75,
    }
    if n_frames < 3:
        shot["frames"]["every"] = [grab(a + k * frame + frame * 0.5, FRAMES / "burst" / f"shot-{i:02d}-f{k}.jpg") for k in range(max(n_frames, 1))]
        shot["note"] = f"{n_frames} frame(s) long — a flash; observations about it are unverified"
    shots.append(shot)

# Bursts: every 0.25 s from 0.5 s before to 0.5 s after any cut that borders a fast shot.
dense = sorted({s["start"] for s in shots if s["fast"] and s["start"] > 0} | {s["end"] for s in shots if s["fast"] and s["end"] < duration})
bursts = []
for c in dense:
    for k in range(-2, 3):
        t = c + k * 0.25
        if 0 <= t < duration:
            bursts.append(grab(t + frame * 0.5, FRAMES / "burst" / f"around-{c:07.3f}-{k:+d}.jpg", 540))

samples = []
t = 0.0
while t < duration:
    samples.append((round(t, 2), grab(t, FRAMES / "t" / f"t{t:06.2f}.jpg", 360)))
    t += 0.5

# Contact sheet.
thumbs = [Image.open(RUN / p).convert("RGB") for _, p in samples]
tw, th = thumbs[0].size
cols = 8 if tw < th else 6
rows = (len(thumbs) + cols - 1) // cols
pad, label_h = 6, 22
sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + label_h + pad) + pad), (18, 18, 18))
draw = ImageDraw.Draw(sheet)
try:
    font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 16)
except OSError:
    font = ImageFont.load_default()
for n, ((ts, _), im) in enumerate(zip(samples, thumbs)):
    x = pad + (n % cols) * (tw + pad)
    y = pad + (n // cols) * (th + label_h + pad)
    sheet.paste(im, (x, y + label_h))
    shot_no = next((s["shot"] for s in shots if s["start"] <= ts < s["end"]), shots[-1]["shot"])
    cut_inside = any(ts <= c < ts + 0.5 for c in cuts)
    draw.text((x + 2, y + 2), f"{ts:5.2f}s  S{shot_no}{'  CUT' if cut_inside else ''}", fill=(255, 170, 60) if cut_inside else (220, 220, 220), font=font)
sheet.save(AN / "contact_sheets" / "contact.jpg", quality=88)

(AN / "shots.json").write_text(json.dumps({
    "cut_source": cut_source,
    "cuts": cuts,
    "shots": shots,
    "burst_frames": bursts,
    "uniform_samples": [{"t": ts, "file": p} for ts, p in samples],
    "contact_sheet": "analysis/contact_sheets/contact.jpg",
}, indent=2))
print(f"{duration:.2f} s @ {fps:.2f} fps {width}x{height}; {len(cuts)} cuts ({cut_source}) → {len(shots)} shots, "
      f"{sum(s['fast'] for s in shots)} fast" + (f"; quality: {'; '.join(quality_notes)}" if quality_notes else ""))
