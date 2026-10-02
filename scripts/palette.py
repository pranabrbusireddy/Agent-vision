"""Dominant colours per shot, measured (k-means over the shot's middle frame).

    .venv/bin/python scripts/palette.py <run-dir> [--k 5]

Reads analysis/shots.json, writes analysis/palette.json: for each shot, k colours
as hex with their share of the frame, darkest first. These are measurements; the
brief still has to say which colour plays background, accent or text, citing the frame.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

ap = argparse.ArgumentParser()
ap.add_argument("run")
ap.add_argument("--k", type=int, default=5)
args = ap.parse_args()
RUN = Path(args.run).resolve()
AN = RUN / "analysis"


def palette(path, k):
    im = Image.open(path).convert("RGB")
    im.thumbnail((160, 160))
    px = np.asarray(im, dtype=np.float32).reshape(-1, 3)
    rng = np.random.default_rng(0)  # fixed seed: same frame, same palette
    centers = px[rng.choice(len(px), size=min(k, len(px)), replace=False)]
    for _ in range(25):
        labels = np.argmin(((px[:, None, :] - centers[None]) ** 2).sum(-1), axis=1)
        new = np.array([px[labels == i].mean(0) if np.any(labels == i) else centers[i] for i in range(len(centers))])
        if np.allclose(new, centers, atol=0.5):
            break
        centers = new
    shares = np.bincount(labels, minlength=len(centers)) / len(px)
    return [
        {"hex": "#{:02x}{:02x}{:02x}".format(*np.clip(centers[i].round(), 0, 255).astype(int)), "share": round(float(shares[i]), 3)}
        for i in np.argsort(centers.sum(1)) if shares[i] > 0.01
    ]


shots = json.loads((AN / "shots.json").read_text())["shots"]
items = [{"shot": s["shot"], "frame": s["frames"]["mid"], "colors": palette(RUN / s["frames"]["mid"], args.k)} for s in shots]
(AN / "palette.json").write_text(json.dumps({"k": args.k, "method": "k-means on the mid-shot frame", "shots": items}, indent=2))
print(f"palette.json: {len(items)} shots")
