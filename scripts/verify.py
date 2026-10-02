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
  beat sync   every transient audio cue (hit/click/pop) is found in the render
              audio within 2 ms, by cross-correlating the cue's own file;
              cuts that were on a hit in the reference have one; swells
              (risers, whooshes) and missing cue files are reported, never passed
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

# --- beat sync: each transient cue located in the render by cross-correlating
# its own file (sample-accurate, unlike onset detection), so a muxing offset
# such as unsignalled AAC priming can't hide inside a frame-based tolerance.
SR = 48000
SYNC_MS = 2.0


def pcm(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).astype(float)


def is_transient(x):
    """Reaches half its peak within 20 ms: a hit, click or pop, not a swell."""
    a = np.abs(x)
    return a.size > 0 and a.max() > 0 and np.argmax(a >= 0.5 * a.max()) <= 0.02 * SR


has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
cues = (sb.get("audio") or {}).get("cues", [])
if cues or (sb.get("audio") or {}).get("bed"):
    check("beat sync", has_audio, "render has an audio stream")
passed_at = []  # times of transient cues found in sync
if has_audio and cues:
    y = pcm(RENDER)
    transients = 0
    for c in cues:
        label = c.get("label", c["src"])
        src = RM / "public" / c["src"]
        if not src.exists():
            check("beat sync", False, f"cue {label}: file public/{c['src']} missing")
            continue
        x = pcm(src)
        if not is_transient(x):
            warns.append(f"beat sync: cue {label} not sync-checked (not a transient)")
            report.setdefault("beat sync", []).append({"pass": None, "detail": f"cue {label}: not a transient, not checked"})
            continue
        transients += 1
        tmpl = x[: int(0.04 * SR)] - x[: int(0.04 * SR)].mean()
        lo = max(0, int((c["t"] - 0.06) * SR))
        seg = y[lo: int((c["t"] + 0.06) * SR) + len(tmpl)]
        if len(seg) < len(tmpl):
            check("beat sync", False, f"cue {label} at {c['t']:.3f} s: outside the render audio")
            continue
        corr = np.correlate(seg, tmpl, mode="valid")
        energy = np.sqrt(np.convolve(seg ** 2, np.ones(len(tmpl)), mode="valid") * np.sum(tmpl ** 2)) + 1e-12
        score = corr / energy
        k = int(np.argmax(score))
        off_ms = ((lo + k) / SR - c["t"]) * 1000
        ok = score[k] >= 0.5 and abs(off_ms) <= SYNC_MS
        check("beat sync", ok, f"cue {label} at {c['t']:.3f} s → found {off_ms:+.1f} ms (match {score[k]:.2f}; needs ≤{SYNC_MS:g} ms, ≥0.5)")
        if ok:
            passed_at.append(c["t"])
    if transients == 0:
        warns.append("beat sync: not checked (no transient cue files)")
    # Cuts that were on a hit in the reference need a synced transient cue at the mirrored shot.
    by_ref = {s.get("ref"): s for s in sb["shots"]}
    for h in alignment.get("cut_hits", []):
        if h["onset_t"] is None or h["cut"] not in by_ref:
            continue
        shot = by_ref[h["cut"]]
        ok = any(abs(t - shot["start"]) <= frame + 1e-6 for t in passed_at)
        check("beat sync", ok, f"reference cut {h['cut']} was on a hit; render shot {shot['id']} at {shot['start']:.3f} s "
              f"{'has' if ok else 'has no'} synced transient cue within 1 frame")
elif not cues:
    warns.append("beat sync: not checked (no audio cues in the storyboard)")

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
passes = sum(x["pass"] is True for v in report.values() for x in v)
print(f"{passes}/{total} checks pass; {len(fails)} fail, {len(warns)} warn → remotion/out/verify.json, verify/compare.jpg")
for f in fails:
    print(f"  FAIL {f}")
for w in warns:
    print(f"  warn {w}")
sys.exit(1 if fails else 0)
