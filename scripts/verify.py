"""Phase 6: check the render numerically, and lay it beside the reference for eyes.

    .venv/bin/python scripts/verify.py <run-dir>

Reads remotion/out/final.mp4 + remotion/src/storyboard.json + analysis/, writes
remotion/out/verify/ (frames, compare.jpg) and remotion/out/verify.json, prints a
summary. Exit 1 if any hard check fails:

  format      size and fps match the storyboard
  duration    within ±2 frames of the storyboard (and reported against the reference)
  structure   each mirrored cut lands within 2 frames of the reference cut
  legibility  every text layer is found in its box, ≥ 2.5% of frame height, and
              meets WCAG contrast (4.5:1, or 3:1 for text ≥ 4% of height)
  beat sync   every audio cue has an onset within 1.5 frames in the render audio;
              cuts that were on a hit in the reference are on a hit here too
  originality no render shot is a near-duplicate of a reference frame (risk #6)
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

RUN = Path(sys.argv[1]).resolve()
RM = RUN / "remotion"
RENDER = RM / "out" / "final.mp4"
VER = RM / "out" / "verify"
(VER / "frames").mkdir(parents=True, exist_ok=True)
sb = json.loads((RM / "src" / "storyboard.json").read_text())
AN = RUN / "analysis"
load = lambda n: json.loads((AN / n).read_text()) if (AN / n).exists() else {}  # noqa: E731
media, shots_doc, alignment = load("media.json"), load("shots.json"), load("alignment.json")

fails, warns, report = [], [], {}


def check(name, ok, detail, hard=True):
    report.setdefault(name, []).append({"pass": bool(ok), "detail": detail})
    if not ok:
        (fails if hard else warns).append(f"{name}: {detail}")


if not RENDER.exists():
    raise SystemExit(f"no render at {RENDER} — run `npm run render` in {RM}")

info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(RENDER)],
                                 capture_output=True, text=True, check=True).stdout)
v = next(s for s in info["streams"] if s["codec_type"] == "video")
n, d = (int(x) for x in v["avg_frame_rate"].split("/"))
fps = n / d
frame = 1 / sb["fps"]
# The video stream, not the container: AAC encoder padding makes the container a frame or two longer.
dur = float(v.get("duration") or info["format"]["duration"])

# --- format + duration
check("format", (int(v["width"]), int(v["height"])) == (sb["width"], sb["height"]), f"{v['width']}×{v['height']} vs storyboard {sb['width']}×{sb['height']}")
check("format", abs(fps - sb["fps"]) < 0.01, f"{fps:.3f} fps vs storyboard {sb['fps']}")
check("duration", abs(dur - sb["duration"]) <= 2 * frame + 1e-3, f"{dur:.3f} s vs storyboard {sb['duration']} s (±2 frames)")
if media:
    check("duration", abs(dur - media["duration"]) <= 2 * frame + 1e-3, f"{dur:.3f} s vs reference {media['duration']} s (±2 frames)", hard=False)


def grab(t, dest):
    # -ss returns the first frame at or after the time; seek just before the
    # frame that is showing at t so a grab never slips into the next shot.
    seek = max(0.0, (int(t * sb["fps"] + 1e-6) - 0.25) / sb["fps"])
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{seek:.4f}", "-i", str(RENDER), "-frames:v", "1", "-q:v", "2", str(dest)], check=True)
    return Image.open(dest).convert("RGB")


# --- structure: mirrored shots start where the reference cuts were
ref_shots = {s["shot"]: s for s in shots_doc.get("shots", [])}
for s in sb["shots"]:
    r = ref_shots.get(s.get("ref"))
    if r and r["start"] > 0:
        check("structure", abs(s["start"] - r["start"]) <= 2 * frame + 1e-3, f"shot {s['id']} starts {s['start']:.3f} s; reference shot {r['shot']} cut at {r['start']:.3f} s", hard=False)

# --- legibility


def lum(rgb):
    c = np.asarray(rgb, dtype=float) / 255
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * c[..., 0] + 0.7152 * c[..., 1] + 0.0722 * c[..., 2]


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


H, W = sb["height"], sb["width"]
for s in sb["shots"]:
    for li, layer in enumerate(s["layers"]):
        if layer["type"] != "text":
            continue
        e = layer.get("enter") or {}
        settle = min(s["start"] + (e.get("at", 0) + e.get("duration", 0.35)) + 0.1, s["end"] - frame)
        img = grab(settle, VER / "frames" / f"{s['id']}-text{li}.jpg")
        b = layer["box"]
        crop = np.asarray(img.crop((int(b["x"] / 100 * W), int(b["y"] / 100 * H), int((b["x"] + b["w"]) / 100 * W), int((b["y"] + b["h"]) / 100 * H))), dtype=float).reshape(-1, 3)
        tc = np.array(hex_rgb(layer["style"]["color"]), dtype=float)
        dist = np.linalg.norm(crop - tc, axis=1)
        text_px = (dist < 40).mean()
        bg = np.median(crop[dist > 80], axis=0) if (dist > 80).any() else crop.mean(0)
        l1, l2 = sorted([float(lum(tc)), float(lum(bg))], reverse=True)
        ratio = (l1 + 0.05) / (l2 + 0.05)
        size = layer["style"]["sizePct"]
        need = 3.0 if size >= 4 else 4.5
        label = f"“{layer['text'][:30]}” in {s['id']} at {settle:.2f} s"
        check("legibility", text_px > 0.005, f"{label}: {text_px:.1%} of the box is text-coloured (found)")
        check("legibility", size >= 2.5, f"{label}: size {size}% of height (≥ 2.5)")
        check("legibility", ratio >= need, f"{label}: contrast {ratio:.1f}:1 vs background #{''.join(f'{int(c):02x}' for c in bg)} (needs {need}:1)")

# --- beat sync
has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
cues = (sb.get("audio") or {}).get("cues", [])
if cues or (sb.get("audio") or {}).get("bed"):
    check("beat sync", has_audio, "render has an audio stream")
if has_audio and (cues or alignment.get("cut_hits")):
    import librosa

    wav = VER / "render.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(RENDER), "-vn", "-ac", "1", "-ar", "22050", str(wav)], check=True)
    y, sr = librosa.load(wav, sr=22050)
    pad = np.zeros(int(0.5 * sr), dtype=y.dtype)
    envl = librosa.onset.onset_strength(y=np.concatenate([pad, y]), sr=sr)
    onsets = librosa.frames_to_time(librosa.onset.onset_detect(onset_envelope=envl, sr=sr, backtrack=True), sr=sr) - 0.5
    tol = 1.5 * frame
    def nearest(t):
        return float(onsets[np.argmin(np.abs(onsets - t))]) if len(onsets) else None

    def offset(near, t):
        return "none" if near is None else f"{near:.3f} s ({(near - t) * 1000:+.0f} ms)"

    for c in cues:
        near = nearest(c["t"])
        label = c.get("label", c["src"])
        check("beat sync", near is not None and abs(near - c["t"]) <= tol, f"cue {label} at {c['t']:.3f} s → onset {offset(near, c['t'])}")
    by_ref = {s.get("ref"): s for s in sb["shots"]}
    for h in alignment.get("cut_hits", []):
        if h["onset_t"] is None or h["cut"] not in by_ref:
            continue
        shot = by_ref[h["cut"]]
        near = nearest(shot["start"])
        check("beat sync", near is not None and abs(near - shot["start"]) <= tol,
              f"reference cut {h['cut']} was on a hit; render shot {shot['id']} at {shot['start']:.3f} s → onset {offset(near, shot['start'])}")
    wav.unlink(missing_ok=True)

# --- originality (risk #6) and the side-by-side sheet


def dhash(img, size=8):
    g = np.asarray(img.convert("L").resize((size + 1, size)), dtype=float)
    return (g[:, 1:] > g[:, :-1]).flatten()


pairs = []
ref_mids = {k: Image.open(RUN / r["frames"]["mid"]).convert("RGB") for k, r in ref_shots.items()}
ref_hashes = {k: dhash(im) for k, im in ref_mids.items()}
for s in sb["shots"]:
    mid = grab((s["start"] + s["end"]) / 2, VER / "frames" / f"{s['id']}-mid.jpg")
    hsh = dhash(mid)
    closest = min(((int((hsh != rh).sum()), k) for k, rh in ref_hashes.items()), default=(64, None))
    check("originality", closest[0] > 10, f"shot {s['id']} vs reference shot {closest[1]}: {closest[0]}/64 bits differ (≤ 10 = near-duplicate)")
    pairs.append((s, ref_mids.get(s.get("ref")), mid))

th = 480
cells = []
for s, ref, mine in pairs:
    row = [im.resize((round(im.width * th / im.height), th)) if im else Image.new("RGB", (270, th), (40, 40, 40)) for im in (ref, mine)]
    cells.append((s, row))
cw = max([sum(im.width for im in row) + 24 for _, row in cells] + [900])
sheet = Image.new("RGB", (cw, len(cells) * (th + 40) + 10), (18, 18, 18))
draw = ImageDraw.Draw(sheet)
try:
    font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 18)
except OSError:
    font = ImageFont.load_default()
for i, (s, row) in enumerate(cells):
    y = 10 + i * (th + 40)
    draw.text((8, y), f"{s['id']}  {s['start']:.2f}–{s['end']:.2f} s   left: reference shot {s.get('ref', '—')}   right: render", fill=(230, 230, 230), font=font)
    x = 8
    for im in row:
        sheet.paste(im, (x, y + 28))
        x += im.width + 8
sheet.save(VER / "compare.jpg", quality=85)

(RM / "out" / "verify.json").write_text(json.dumps({"pass": not fails, "failures": fails, "warnings": warns, "checks": report}, indent=2))
total = sum(len(v) for v in report.values())
print(f"{total - len(fails) - len(warns)}/{total} checks pass; {len(fails)} fail, {len(warns)} warn → remotion/out/verify.json, verify/compare.jpg")
for f in fails:
    print(f"  FAIL {f}")
for w in warns:
    print(f"  warn {w}")
sys.exit(1 if fails else 0)
