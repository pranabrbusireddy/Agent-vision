"""Phase 3: map audio events onto shots and cuts.

    .venv/bin/python scripts/align.py <run-dir>

Reads analysis/shots.json, audio.json, speech.json; writes analysis/alignment.json:
  - every beat / onset / peak tagged with its shot, and with a cut when it lands
    within 1.5 frames of one (the precision editors sync hits to)
  - cut_hits: for each cut, the onset on it, or an explicit "not on a hit"
    (with a "near" onset noted when one is within min(3 frames, 60 ms))
  - which tempo candidate the cuts follow (risk #5: half/double-time)
  - speech segments tagged with the shots they span
"""
import json
import sys
from pathlib import Path

import numpy as np

RUN = Path(sys.argv[1]).resolve()
AN = RUN / "analysis"
load = lambda name: json.loads((AN / name).read_text()) if (AN / name).exists() else {}  # noqa: E731
media, shots_doc, audio, speech = load("media.json"), load("shots.json"), load("audio.json"), load("speech.json")
if not shots_doc:
    raise SystemExit("run probe.py first")

cuts, shots = shots_doc["cuts"], shots_doc["shots"]
fps = media["fps"]
TOL = 1.5 / fps
# "near": informational only, capped so it stays meaningful at low frame rates.
NEAR = min(3 / fps, 0.06)


def shot_at(t):
    return next((s["shot"] for s in shots if s["start"] <= t < s["end"]), shots[-1]["shot"])


def tag(t, **extra):
    out = {"t": t, "shot": shot_at(t), **extra}
    if cuts:
        d, n = min((abs(t - c), i) for i, c in enumerate(cuts, start=2))  # cut n opens shot n
        if d <= TOL:
            out["on_cut"] = n
            out["offset_ms"] = round((t - cuts[n - 2]) * 1000)
    return out


result = {"tolerance_ms": round(TOL * 1000), "cuts": cuts, "audio_available": bool(audio.get("available"))}

if audio.get("available"):
    onsets = audio["onsets"]
    result["beats"] = [tag(t) for t in audio["beats"]]
    result["onsets"] = [tag(o["t"], strength=o["strength"]) for o in onsets]
    result["loudness_peaks"] = [tag(t) for t in audio["loudness_peaks"]]

    hits = []
    for n, c in enumerate(cuts, start=2):
        near = min(onsets, key=lambda o: abs(o["t"] - c), default=None)
        if near and abs(near["t"] - c) <= TOL:
            hits.append({"cut": n, "cut_t": c, "onset_t": near["t"], "offset_ms": round((near["t"] - c) * 1000), "strength": near["strength"]})
        else:
            h = {"cut": n, "cut_t": c, "onset_t": None, "note": f"no onset within {round(TOL * 1000)} ms — not cut on a hit"}
            if near and abs(near["t"] - c) <= NEAR:
                h["near"] = {"onset_t": near["t"], "offset_ms": round((near["t"] - c) * 1000)}
            hits.append(h)
    result["cut_hits"] = hits
    on = [h for h in hits if h["onset_t"] is not None]
    result["cuts_on_hits"] = f"{len(on)}/{len(hits)}"
    result["cuts_near_hits"] = f"{sum('near' in h for h in hits)}/{len(hits)}"
    result["near_tolerance_ms"] = round(NEAR * 1000)
    # Signed sound-minus-cut offset over hit + near cuts: a consistent lead/lag
    # (e.g. cuts always ~1.6 frames before the attack) is clearer than a count
    # that flips on a couple of ms at the hit/near boundary.
    offs = [h["offset_ms"] for h in hits if h["onset_t"] is not None] + [h["near"]["offset_ms"] for h in hits if "near" in h]
    if offs:
        q1, med, q3 = np.percentile(offs, [25, 50, 75])
        result["hit_offset_ms"] = {"median": round(float(med)), "iqr": [round(float(q1)), round(float(q3))], "n": len(offs),
                                   "note": "attack minus cut over hit + near cuts; positive = sound after the cut"}

    # Which tempo do the cuts follow? Count cuts within 1 frame of each
    # candidate's beat grid (phase from the first beat), minus the count expected
    # by pure chance — otherwise the finest grid always wins.
    if audio["beats"] and cuts:
        beats_arr = np.array(audio["beats"])
        grid_tol = 1 / fps
        scores = []
        for bpm in audio["tempo_candidates_bpm"]:
            period = 60 / bpm
            # Grid phase: circular mean of all beats (attack-snapped by audio.py), not just the first one.
            ang = beats_arr / period * 2 * np.pi
            phase = (np.angle(np.mean(np.exp(1j * ang))) / (2 * np.pi)) % 1 * period
            on_grid = int(sum(min((c - phase) % period, period - (c - phase) % period) <= grid_tol + 1e-6 for c in cuts))
            chance = len(cuts) * min(1.0, 2 * grid_tol / period)
            scores.append({"bpm": bpm, "cuts_on_grid": on_grid, "above_chance": round(on_grid - chance, 2)})
        best = max(scores, key=lambda s: (s["above_chance"], s["bpm"] == audio["tempo_bpm"]))
        if best["above_chance"] <= 0:
            best = {"bpm": None, "cuts_on_grid": 0}
        # Signed offset from each cut to its nearest beat: a consistent lead/lag shows
        # up as a tight IQR away from 0, which the on-grid count alone would miss.
        offs = np.array([(beats_arr[np.argmin(np.abs(beats_arr - c))] - c) * 1000 for c in cuts])
        q1, med, q3 = np.percentile(offs, [25, 50, 75])
        result["tempo"] = {
            "estimate_bpm": audio["tempo_bpm"],
            "candidates": scores,
            "cuts_follow_bpm": best["bpm"],
            "cut_beat_offset_ms": {"median": round(float(med)), "iqr": [round(float(q1)), round(float(q3))],
                                   "note": "beat minus cut, nearest beat per cut; beats are attack-snapped"},
            "note": "cuts_follow_bpm: the candidate whose beat grid holds the most cuts beyond chance (±1 frame); null = the edit isn't on a beat grid",
        }
else:
    result["reason"] = audio.get("reason", "audio.py not run")

if speech.get("available"):
    result["speech"] = [
        {**s, "shots": sorted({shot_at(s["start"]), shot_at(max(s["start"], s["end"] - 1e-3))})} for s in speech["segments"]
    ]

(AN / "alignment.json").write_text(json.dumps(result, indent=2))
if audio.get("available"):
    t = result.get("tempo", {})
    ho = result.get("hit_offset_ms", {})
    print(f"{result['cuts_on_hits']} cuts on a hit (±{result['tolerance_ms']} ms), {result['cuts_near_hits']} near (±{result['near_tolerance_ms']} ms), "
          f"hit offset median {ho.get('median')} ms IQR {ho.get('iqr')}; cuts follow "
          f"{t.get('cuts_follow_bpm')} bpm of candidates {[c['bpm'] for c in t.get('candidates', [])]}")
else:
    print(f"no audio to align: {result['reason']}")
