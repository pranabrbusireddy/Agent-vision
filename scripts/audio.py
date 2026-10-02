"""Phase 3: measured audio events — tempo, beats, onsets, loudness peaks, silences.

    .venv/bin/python scripts/audio.py <run-dir>

Reads input/reference.mp4, writes analysis/audio.wav (16 kHz mono, reused by
transcribe.py) and analysis/audio.json. No interpretation and no shot mapping
here (align.py does that). If there is no audio stream, or it is silent,
audio.json says so with "available": false — silence is reported, never filled in.
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

RUN = Path(sys.argv[1]).resolve()
SRC = RUN / "input" / "reference.mp4"
AN = RUN / "analysis"
AN.mkdir(parents=True, exist_ok=True)
OUT = AN / "audio.json"


def unavailable(reason):
    OUT.write_text(json.dumps({"available": False, "reason": reason}, indent=2))
    print(f"audio unavailable: {reason}")
    sys.exit(0)


has_stream = subprocess.run(
    ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(SRC)],
    capture_output=True, text=True,
).stdout.strip()
if not has_stream:
    unavailable("the reference has no audio stream")

vol = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(SRC), "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"],
                     capture_output=True, text=True).stderr
mean_db = next((float(l.split(":")[1].split()[0]) for l in vol.splitlines() if "mean_volume" in l and "inf" not in l), float("-inf"))
if mean_db < -60:
    unavailable(f"the audio stream is silent (mean {mean_db} dB)")

wav = AN / "audio.wav"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(SRC), "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)

import librosa  # noqa: E402  (slow import; only when there is audio)

SR, HOP = 22050, 512
y, sr = librosa.load(wav, sr=SR, mono=True)
duration = len(y) / sr
# Onset detection can't fire on the very first frame (nothing to rise from), so
# a hit at 0.00 s would be lost. Analyse with 0.5 s of silence in front, shift back.
PAD = 0.5
y_pad = np.concatenate([np.zeros(int(PAD * sr), dtype=y.dtype), y])

tempo_est, beat_frames = librosa.beat.beat_track(y=y_pad, sr=sr, hop_length=HOP, units="frames")
beats = librosa.frames_to_time(beat_frames, sr=sr, hop_length=HOP) - PAD
beats = beats[beats >= 0]

env = librosa.onset.onset_strength(y=y_pad, sr=sr, hop_length=HOP)
onset_frames = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=HOP, backtrack=False)
peaks = librosa.frames_to_time(onset_frames, sr=sr, hop_length=HOP)  # in y_pad time

# Refine each detected onset at sample level. librosa's backtrack lands on the
# previous energy minimum, which in dense audio can be the tail of an earlier
# sound (±30–50 ms). Instead, on a 12 ms TRAILING-max amplitude envelope (it
# rises on the attack sample and never dips inside a low-pitched sound, unlike a
# short moving average): the onset is the last crossing of baseline + 30 % of
# the rise before the sound's level; strength is that absolute amplitude jump,
# so a loud kick outranks a bright click.
# Unclear attacks (jump below 10 % of the median jump, or no crossing) keep
# librosa's time and are flagged refined=false; nothing moves outside the window.
from scipy.ndimage import maximum_filter1d  # noqa: E402

N_ENV = int(0.012 * sr)
amp = maximum_filter1d(np.abs(y_pad), size=N_ENV, origin=(N_ENV - 1) // 2)
cands = []
for tp in peaks:
    lo, hi = max(0, int((tp - 0.08) * sr)), min(len(amp), int((tp + 0.03) * sr))
    if hi - lo < 4:
        cands.append((tp, 0.0, False))
        continue
    seg = amp[lo:hi]
    p0 = max(0, int((tp - 0.01) * sr) - lo)
    k = p0 + int(np.argmax(seg[p0:]))           # the sound's level, at/after the detected peak
    base, level = float(seg[: k + 1].min()), float(seg[k])
    below = np.where(seg[: k + 1] < base + 0.3 * (level - base))[0]
    if level - base <= 0 or not len(below):
        cands.append((tp, max(level - base, 0.0), False))
    else:
        cands.append(((lo + int(below[-1]) + 1) / sr, level - base, True))
jumps = np.array([c[1] for c in cands]) if cands else np.zeros(0)
floor = 0.1 * float(np.median(jumps[jumps > 0])) if np.any(jumps > 0) else 0.0
refined, jump, is_ref = [], [], []
for (t_ref, j, ok), tp in zip(cands, peaks):
    ok = ok and j >= floor
    refined.append(t_ref if ok else tp)
    jump.append(j)
    is_ref.append(ok)
onsets = np.array(refined) - PAD
strength = np.array(jump) / (max(jump) if jump and max(jump) > 0 else 1)
is_ref = np.array(is_ref, dtype=bool)
keep = onsets >= -0.02
onsets, strength, is_ref = np.clip(onsets[keep], 0, None), strength[keep], is_ref[keep]
# Two detections can refine to the same attack; keep the stronger.
if len(onsets):
    order = np.argsort(onsets)
    onsets, strength, is_ref = onsets[order], strength[order], is_ref[order]
    dedup = [0]
    for j in range(1, len(onsets)):
        if onsets[j] - onsets[dedup[-1]] < 0.01:
            if strength[j] > strength[dedup[-1]]:
                dedup[-1] = j
        else:
            dedup.append(j)
    onsets, strength, is_ref = onsets[dedup], strength[dedup], is_ref[dedup]

# beat_track can mark a kick's energy peak (up to ~70 ms after its attack) or
# lock onto a bright click/hi-hat near it (spectral flux favours high
# frequencies). Snap each beat to the STRONGEST attack (amplitude jump) within
# ±120 ms, so beats share the attack reference whichever side the tracker erred.
SNAP_BEFORE, SNAP_AFTER = 0.12, 0.12
if len(onsets) and len(beats):
    snapped = []
    for b in beats:
        win = np.where((onsets >= b - SNAP_BEFORE) & (onsets <= b + SNAP_AFTER))[0]
        snapped.append(onsets[win[np.argmax(strength[win])]] if len(win) else b)
    beats = np.array(snapped)
    # Second pass, grid-consistent: fit period and phase over ALL first-pass beats,
    # then snap each grid-predicted beat to the strongest attack within ±60 ms.
    # One accent between beats (or a decoy the tracker locked onto) can then only
    # move its own beat if it sits on the grid.
    if len(beats) > 3:
        period = float(np.median(np.diff(beats)))
        if period > 0:
            idx = np.round((beats - beats[0]) / period)
            slope, icpt = np.polyfit(idx, beats, 1)
            phase_ang = np.angle(np.mean(np.exp(1j * 2 * np.pi * (beats - icpt) / slope)))
            icpt += phase_ang / (2 * np.pi) * slope
            n0 = int(np.ceil((0 - icpt) / slope))
            grid = icpt + slope * np.arange(n0, int((duration - icpt) / slope) + 1)
            win = min(0.06, slope / 4)
            second = []
            for g in grid:
                cand = np.where(np.abs(onsets - g) <= win)[0]
                if len(cand):
                    second.append(onsets[cand[np.argmax(strength[cand])]])
            if len(second) >= len(beats) // 2:
                beats = np.array(second)

# Tempo: a line fitted through the beat times averages out the 23 ms hop
# quantisation. Beat trackers often lock to half or double time on short clips
# (risk #5), so report all three; align.py says which one the cuts follow.
if len(beats) > 2:
    # Index each beat by its grid position (gaps where a beat had no clear attack
    # count as skipped beats, not as a slower tempo).
    step = float(np.median(np.diff(beats)))
    idx = np.round((beats - beats[0]) / step) if step > 0 else np.arange(len(beats))
    tempo = 60 / float(np.polyfit(idx, beats, 1)[0])
else:
    tempo = float(np.atleast_1d(tempo_est)[0])
candidates = [round(tempo / 2, 1), round(tempo, 1), round(tempo * 2, 1)]

rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
rms_db = librosa.amplitude_to_db(rms, ref=np.max(rms) or 1.0)
times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=HOP)

peaks = []  # local maxima within 6 dB of the loudest point, ≥ 0.25 s apart
for i in np.argsort(rms_db)[::-1]:
    if rms_db[i] < -6:
        break
    if all(abs(times[i] - p) >= 0.25 for p in peaks):
        peaks.append(round(float(times[i]), 3))
peaks.sort()

silences, start = [], None  # ≥ 0.3 s more than 40 dB below the peak
for t, db in zip(times, rms_db):
    if db < -40 and start is None:
        start = t
    elif db >= -40 and start is not None:
        if t - start >= 0.3:
            silences.append([round(float(start), 2), round(float(t), 2)])
        start = None
if start is not None and duration - start >= 0.3:
    silences.append([round(float(start), 2), round(duration, 2)])

OUT.write_text(json.dumps({
    "available": True,
    "duration": round(duration, 3),
    "mean_db": mean_db,
    "tempo_bpm": round(tempo, 1),
    "tempo_candidates_bpm": candidates,
    "beats": [round(float(t), 3) for t in beats],
    "onsets": [{"t": round(float(t), 3), "strength": round(float(s), 3), "refined": bool(r)} for t, s, r in zip(onsets, strength, is_ref)],
    "onsets_refined": f"{int(is_ref.sum())}/{len(is_ref)}",
    "loudness_peaks": peaks,
    "silences": silences,
    "method": "librosa beat_track / onset_detect; onsets refined on a 12 ms trailing-max envelope (last 30 % crossing before the level; refined=false keeps librosa's time when the attack is unclear); onset strength = amplitude jump, normalised to the track max; beats snapped to the strongest attack within ±120 ms, then re-snapped on a fitted grid (strongest attack within ±60 ms of each grid beat); RMS peaks/silences; times in seconds from the start of the reference",
}, indent=2))
print(f"tempo ≈ {tempo:.1f} bpm (candidates {candidates}), {len(beats)} beats, {len(onsets)} onsets, "
      f"{len(peaks)} peaks, {len(silences)} silences")
