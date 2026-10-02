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
onset_frames = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=HOP, backtrack=True)
onsets = librosa.frames_to_time(onset_frames, sr=sr, hop_length=HOP) - PAD
strength = env[onset_frames] / (env.max() or 1)
keep = onsets >= -0.02
onsets, strength = np.clip(onsets[keep], 0, None), strength[keep]

# beat_track marks energy peaks; onsets are backtracked to where the sound
# starts. Snap each beat to an onset within 60 ms so beats share that reference
# (otherwise they run ~20 ms late and skew the beat grid align.py tests cuts on).
if len(onsets):
    near = onsets[np.abs(onsets[None, :] - beats[:, None]).argmin(axis=1)] if len(beats) else beats
    beats = np.where(np.abs(near - beats) <= 0.06, near, beats)

# Tempo: a line fitted through the beat times averages out the 23 ms hop
# quantisation. Beat trackers often lock to half or double time on short clips
# (risk #5), so report all three; align.py says which one the cuts follow.
if len(beats) > 2:
    tempo = 60 / float(np.polyfit(np.arange(len(beats)), beats, 1)[0])
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
    "onsets": [{"t": round(float(t), 3), "strength": round(float(s), 2)} for t, s in zip(onsets, strength)],
    "loudness_peaks": peaks,
    "silences": silences,
    "method": "librosa beat_track / onset_detect (backtracked) / RMS; times in seconds from the start of the reference",
}, indent=2))
print(f"tempo ≈ {tempo:.1f} bpm (candidates {candidates}), {len(beats)} beats, {len(onsets)} onsets, "
      f"{len(peaks)} peaks, {len(silences)} silences")
