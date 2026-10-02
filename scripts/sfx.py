"""Generated audio for the recreation: a beat bed at the reference tempo and a
small kit of cues (hit, whoosh, riser, click, pop). Pure numpy synthesis with a
fixed seed — no samples, no downloads, no third-party licence, same bytes every run.

    .venv/bin/python scripts/sfx.py <out-dir> --bpm 120 --duration 15 [--offset 0.0]

--offset shifts the bed's first beat (match the reference's beat phase from
analysis/audio.json so the cuts you mirror land on the bed's kicks).
Writes 48 kHz 16-bit stereo WAVs.
"""
import argparse
import wave
from pathlib import Path

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--bpm", type=float, required=True)
ap.add_argument("--duration", type=float, required=True)
ap.add_argument("--offset", type=float, default=0.0)
args = ap.parse_args()

SR = 48_000
OUT = Path(args.out)
OUT.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(7)


def t_axis(sec):
    return np.arange(int(sec * SR)) / SR


def env(n, attack=0.002, decay=0.2):
    t = np.arange(n) / SR
    a = np.clip(t / attack, 0, 1)
    return a * np.exp(-t / decay)


def write(name, mono, peak_db=-1.5):
    x = mono / (np.max(np.abs(mono)) or 1) * 10 ** (peak_db / 20)
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    stereo = np.repeat(pcm[:, None], 2, axis=1)
    with wave.open(str(OUT / name), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())


def kick(sec=0.35):
    t = t_axis(sec)
    f = 45 + 110 * np.exp(-t / 0.03)  # pitch drop
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(len(t), 0.001, 0.12)


def hat(sec=0.06):
    n = int(sec * SR)
    noise = rng.standard_normal(n)
    hp = noise - np.concatenate([[0], noise[:-1]])  # crude high-pass
    return hp * env(n, 0.0005, 0.015) * 0.25


def lowpassed_noise(n, cutoff_start, cutoff_end):
    noise = rng.standard_normal(n)
    out = np.empty(n)
    y = 0.0
    cut = np.linspace(cutoff_start, cutoff_end, n)
    a = 1 - np.exp(-2 * np.pi * cut / SR)
    for i in range(n):
        y += a[i] * (noise[i] - y)
        out[i] = y
    return out


# --- bed: kick on every beat, hat on the off-beats, a soft sub pad underneath.
period = 60 / args.bpm
bed = np.zeros(int(args.duration * SR))
k, h = kick(), hat()
t = args.offset
while t < args.duration:
    i = int(t * SR)
    bed[i:i + len(k)] += k[: len(bed) - i]
    j = int((t + period / 2) * SR)
    if j < len(bed):
        bed[j:j + len(h)] += h[: len(bed) - j]
    t += period
pad_t = t_axis(args.duration)
bed += 0.08 * np.sin(2 * np.pi * 55 * pad_t) * (0.6 + 0.4 * np.sin(2 * np.pi * pad_t / (period * 8)))
fade = np.minimum(1, np.minimum(pad_t / 0.05, (args.duration - pad_t) / 0.4))
write("bed.wav", bed * fade, peak_db=-3)

# --- cues
write("hit.wav", kick(0.5) * 1.0 + lowpassed_noise(int(0.5 * SR), 4000, 300) * env(int(0.5 * SR), 0.001, 0.08) * 0.6)
n = int(0.45 * SR)
write("whoosh.wav", lowpassed_noise(n, 300, 6000) * np.sin(np.linspace(0, np.pi, n)) ** 2)
n = int(1.5 * SR)
rt = t_axis(1.5)
riser = lowpassed_noise(n, 200, 9000) * np.linspace(0, 1, n) ** 2 + 0.3 * np.sin(2 * np.pi * np.cumsum(200 + 600 * rt / 1.5) / SR) * np.linspace(0, 1, n)
write("riser.wav", riser)
n = int(0.03 * SR)
write("click.wav", np.sin(2 * np.pi * 2400 * t_axis(0.03)) * env(n, 0.0002, 0.005))
n = int(0.12 * SR)
pt = t_axis(0.12)
write("pop.wav", np.sin(2 * np.pi * np.cumsum(900 - 500 * pt / 0.12) / SR) * env(n, 0.001, 0.03))
print(f"wrote bed.wav ({args.bpm} bpm, {args.duration} s, offset {args.offset} s), hit, whoosh, riser, click, pop → {OUT}")
