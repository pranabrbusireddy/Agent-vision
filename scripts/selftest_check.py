"""Known-answer checks for selftest.sh.

    selftest_check.py analysis   <truth.json> <run>     analysis numbers vs ground truth
    selftest_check.py brief      <run> <silent-run>     the brief gate accepts/labels/rejects correctly
    selftest_check.py storyboard <run>                  writes a storyboard mirroring the reference
"""
import copy
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = str(HERE.parent / ".venv" / "bin" / "python")
fails = []


def check(ok, msg):
    print(f"  {'PASS' if ok else 'FAIL'}  {msg}")
    if not ok:
        fails.append(msg)


def load(run, name):
    return json.loads((Path(run) / "analysis" / name).read_text())


mode = sys.argv[1]

if mode == "analysis":
    truth, run = json.loads(Path(sys.argv[2]).read_text()), Path(sys.argv[3])
    media, shots, audio, align = load(run, "media.json"), load(run, "shots.json"), load(run, "audio.json"), load(run, "alignment.json")
    frame = 1 / media["fps"]
    check(abs(media["duration"] - truth["duration"]) <= 0.05, f"duration {media['duration']} s vs {truth['duration']} s")
    check(media["fps"] == 30 and media["size"] == [720, 1280], f"probe: {media['fps']} fps, {media['size']}")
    for c in truth["cuts"]:
        near = min(shots["cuts"], key=lambda f: abs(f - c), default=None)
        check(near is not None and abs(near - c) <= frame + 1e-3, f"cut {c:5.2f} s → {near} (±1 frame)")
    check(len(shots["cuts"]) == len(truth["cuts"]), f"no false cuts ({len(shots['cuts'])} found, {len(truth['cuts'])} real)")
    fast = [s["shot"] for s in shots["shots"] if s["fast"]]
    check(fast == [4], f"fast shot detected: {fast} (expect [4], the 6-frame shot)")
    check(bool(shots["burst_frames"]), f"burst frames around the fast shot: {len(shots['burst_frames'])}")
    check((run / shots["contact_sheet"]).exists(), "contact sheet written")

    onsets = [o["t"] for o in audio["onsets"]]
    beeps = [round(k * truth["beat"], 3) for k in range(int(truth["duration"] / truth["beat"]))]
    errs = [min((o - b for o in onsets), key=abs) for b in beeps]
    check(all(abs(e) <= 0.03 for e in errs), f"every beep found within ±30 ms: {sum(abs(e) <= 0.03 for e in errs)}/{len(beeps)}, worst {max(map(abs, errs)) * 1000:.0f} ms (incl. the one at 0.00 s)")
    check(abs(audio["tempo_bpm"] - 120) <= 1.5, f"tempo {audio['tempo_bpm']} bpm (truth 120); candidates {audio['tempo_candidates_bpm']}")
    on_beat = {c for c in truth["cuts"] if abs(c / truth["beat"] - round(c / truth["beat"])) < 1e-6}
    for h in align["cut_hits"]:
        c = min(truth["cuts"], key=lambda t: abs(t - h["cut_t"]))
        check((h["onset_t"] is not None) == (c in on_beat), f"cut {c:5.2f} s is {'on' if c in on_beat else 'off'} the beat → reported {'hit' if h['onset_t'] is not None else 'no hit'}")
    check(align["tempo"]["cuts_follow_bpm"] == 120.0, f"cuts follow {align['tempo']['cuts_follow_bpm']} bpm (truth 120, not the half/double candidates)")
    sp = load(run, "speech.json")
    check(sp.get("available") and all(s["confidence"] == "unverified" for s in sp["segments"]),
          f"speech over beeps: {len(sp.get('segments', []))} segment(s), all unverified or none")

elif mode == "brief":
    run, silent = Path(sys.argv[2]), Path(sys.argv[3])

    def make_brief(r, with_audio):
        media, shots = load(r, "media.json"), load(r, "shots.json")
        lens = [s["duration"] for s in shots["shots"]]
        ev = lambda s: {"t": [s["start"]], "frames": [s["frames"]["in"], s["frames"]["mid"]]}  # noqa: E731
        b = {
            "reference": {"source": "selftest", "duration": media["duration"], "fps": media["fps"], "size": media["size"]},
            "summary": "Synthetic test pattern with hard cuts.",
            "pacing": {"shots": len(lens), "avg_shot_s": round(sum(lens) / len(lens), 3), "fastest_shot_s": min(lens), "slowest_shot_s": max(lens),
                       "evidence": {"source": "analysis/shots.json", "t": shots["cuts"]}, "confidence": "high"},
            "structure": [{"section": "other", "start": 0, "end": media["duration"], "shots": [s["shot"] for s in shots["shots"]],
                           "purpose": "test pattern", "evidence": {"t": [0]}, "confidence": "high"}],
            "shots": [{
                "shot": s["shot"], "start": s["start"], "end": s["end"], "framing": "full-frame test pattern", "content_type": "abstract",
                "motion": {"type": "static", "speed": "still", "evidence": ev(s), "confidence": "high"},
                "transition_in": {"type": "hard cut", "evidence": ev(s), "confidence": "high"},
                "evidence": ev(s), "confidence": "high"} for s in shots["shots"]],
            "text_styles": [],
            "audio": {"available": False, "reason": "no audio"},
            "unclear": [{"what": "shot 4 content", "t": [4.2], "why": "6 frames long"}],
        }
        if with_audio:
            b["audio"] = {"available": True, "tempo_bpm": 120, "character": "880 Hz beeps",
                          "cues": [{"t": 1.0, "kind": "beat", "description": "beep", "on_cut": 2,
                                    "evidence": {"t": [1.0], "source": "analysis/alignment.json"}, "confidence": "high"}]}
        return b

    def run_validator(r, brief, write=False):
        (r / "brief.json").write_text(json.dumps(brief, indent=2))
        p = subprocess.run([PY, str(HERE / "validate_brief.py"), str(r)] + (["--write"] if write else []), capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    good = make_brief(run, True)
    code, out = run_validator(run, good, write=True)
    check(code == 0 and (run / "brief.md").exists(), f"valid brief passes and renders brief.md ({out.strip().splitlines()[0]})")

    b = copy.deepcopy(good)
    del b["shots"][2]["motion"]["evidence"]
    code, out = run_validator(run, b, write=True)
    after = json.loads((run / "brief.json").read_text())["shots"][2]["motion"]
    check(code == 0 and after["confidence"] == "unverified" and after.get("auto_unverified"), "item with no evidence is auto-labelled unverified")

    b = copy.deepcopy(good)
    b["shots"][0]["evidence"]["frames"] = ["analysis/frames/made-up.jpg"]
    code, out = run_validator(run, b)
    check(code == 1 and "doesn't exist" in out, "citing a frame that doesn't exist is rejected")

    b = copy.deepcopy(good)
    b["shots"][0]["evidence"]["t"] = [99.0]
    code, out = run_validator(run, b)
    check(code == 1 and "outside the reference" in out, "citing a time outside the reference is rejected")

    b = copy.deepcopy(good)
    b["shots"].pop()
    code, out = run_validator(run, b)
    check(code == 1 and "missing from the brief" in out, "dropping a measured shot is rejected")

    b = make_brief(silent, True)
    code, out = run_validator(silent, b)
    check(code == 1 and "describes sound" in out, "describing sound on an audio-less reference is rejected")

    # ---- speech gate (F1/F2): quotes only from a transcript, never when speech was skipped
    sp_path = run / "analysis" / "speech.json"
    original = sp_path.read_text()
    p = subprocess.run([PY, str(HERE / "transcribe.py"), str(run), "--skip", "declined by user"], capture_output=True, text=True)
    check(p.returncode == 0 and json.loads(sp_path.read_text()) == {"available": False, "reason": "speech not analysed: declined by user"},
          "transcribe.py --skip writes {available: false, reason}")
    p = subprocess.run([PY, str(HERE / "align.py"), str(run)], capture_output=True, text=True)
    check(p.returncode == 0 and "speech" not in json.loads((run / "analysis" / "alignment.json").read_text()),
          "align.py accepts the --skip output (no speech section)")

    b = copy.deepcopy(good)
    b["audio"]["speech"] = [{"start": 0.5, "end": 2.0, "text": "Ship faster with Acme", "confidence": "medium"}]
    code, out = run_validator(run, b)
    check(code == 1 and "speech wasn't analysed" in out and "declined by user" in out, "speech skipped + invented quote is rejected, reason cited")

    b = copy.deepcopy(good)
    b["summary"] = "Bold captions: \u201cSHIP IT\u201d on shot 3, then \"NOW\" on the CTA."
    code, out = run_validator(run, b)
    check(code == 0 and "may describe speech" not in out, "speech skipped + quoted on-screen text: passes, no speech warning")

    b = copy.deepcopy(good)
    b["summary"] = "A voiceover says the product is fast."
    code, out = run_validator(run, b)
    check(code == 0 and "may describe speech" in out, "speech skipped + 'voiceover says' in summary: warned")

    sp_path.write_text(json.dumps({"available": True, "engine": "test", "language": "en", "segments": [
        {"start": 1.0, "end": 2.5, "text": "Ship it today.", "confidence": "medium"}]}))
    # Cites evidence, so the auto-label doesn't downgrade it before the confidence check.
    quote = {"start": 1.2, "end": 2.0, "text": "ship it TODAY", "confidence": "medium",
             "evidence": {"t": [1.2], "source": "analysis/speech.json"}}
    for label, change, want in [
        ("exact quote (case/punctuation-insensitive) passes", {}, 0),
        ("altered quote is rejected", {"text": "ship it tomorrow"}, 1),
        ("time-shifted quote is rejected", {"start": 8.0, "end": 9.0}, 1),
        ("quote more confident than its transcript segment is rejected", {"confidence": "high"}, 1),
        ("word fragment 'hip' (from 'ship') is rejected", {"text": "hip"}, 1),
        ("cross-word fragment 'p it to' is rejected", {"text": "p it to"}, 1),
        ("whole-word sub-quote 'it today' passes", {"text": "it today"}, 0),
        ("quote claiming 0–14 s for 1.0–2.5 s of speech is rejected", {"text": "today", "start": 0.0, "end": 14.0}, 1),
    ]:
        b = copy.deepcopy(good)
        b["audio"]["speech"] = [{**quote, **change}]
        code, out = run_validator(run, b)
        check(code == want, f"speech analysed: {label}")
    sp_path.write_text(original)

    run_validator(run, good, write=True)  # leave a clean brief behind for the build step

elif mode == "storyboard":
    # A recreation that mirrors the reference's cut points, with the bed on its
    # beat grid and a hit on each cut that was on a hit in the reference.
    run = Path(sys.argv[2])
    media, shots, align = load(run, "media.json"), load(run, "shots.json"), load(run, "alignment.json")
    bgs = ["#141413", "#f97316", "#232320", "#fbfaf8"]
    fg = {"#141413": "#fbfaf8", "#f97316": "#141413", "#232320": "#fb923c", "#fbfaf8": "#141413"}
    sb_shots = []
    for s in shots["shots"]:
        bg = bgs[(s["shot"] - 1) % len(bgs)]
        layers = [{"type": "text", "text": f"Feature {s['shot']}", "box": {"x": 5, "y": 42, "w": 90, "h": 16},
                   "style": {"sizePct": 6, "color": fg[bg], "weight": 800, "case": "upper"},
                   "enter": {"type": "rise", "duration": min(0.3, s["duration"] / 3), "easing": "snap"}}]
        if s["shot"] == 1:
            layers.append({"type": "logo", "src": "assets/logo.png", "box": {"x": 40, "y": 25, "w": 20, "h": 12},
                           "enter": {"type": "pop", "duration": 0.3}})
        sb_shots.append({"id": f"s{s['shot']:02d}", "ref": s["shot"], "start": s["start"], "end": s["end"], "background": bg,
                         "transitionIn": {"type": "cut"}, "camera": {"type": "push-in", "amount": 0.05}, "layers": layers})
    cues = [{"t": h["cut_t"], "src": "audio/hit.wav", "label": f"hit on cut {h['cut']}"} for h in align["cut_hits"] if h["onset_t"] is not None]
    sb = {"width": media["size"][0], "height": media["size"][1], "fps": round(media["fps"]), "duration": media["duration"],
          "audio": {"bed": "audio/bed.wav", "bedVolume": 0.5, "cues": cues}, "shots": sb_shots}
    (run / "remotion" / "src" / "storyboard.json").write_text(json.dumps(sb, indent=2))
    print(f"  storyboard: {len(sb_shots)} shots mirroring the reference cuts, {len(cues)} hit cue(s)")

sys.exit(1 if fails else 0)
