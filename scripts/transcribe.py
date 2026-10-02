"""Phase 3: speech, if any.

    .venv/bin/python scripts/transcribe.py <run-dir> [--model NAME]
    .venv/bin/python scripts/transcribe.py <run-dir> --skip "<reason>"

--skip records that speech was deliberately not analysed (e.g. the user declined
the model download). validate_brief.py then rejects any speech quote in the brief.

Reads analysis/audio.wav (from audio.py), writes analysis/speech.json.
Engine: mlx-whisper on Apple Silicon, else faster-whisper (both open source).
Whisper invents words over music ("Thank you."), so segments it isn't sure of
are kept but marked "unverified" rather than dropped or trusted.
"""
import argparse
import json
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("run")
ap.add_argument("--model", help="override the model (mlx repo id or faster-whisper size)")
ap.add_argument("--skip", metavar="REASON", help="don't transcribe; record why")
args = ap.parse_args()
RUN = Path(args.run).resolve()
AN = RUN / "analysis"
OUT = AN / "speech.json"

if args.skip is not None:
    reason = args.skip.strip() or "skipped"
    AN.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"available": False, "reason": f"speech not analysed: {reason}"}, indent=2))
    print(f"speech: not analysed ({reason})")
    raise SystemExit(0)
wav = AN / "audio.wav"

audio = json.loads((AN / "audio.json").read_text()) if (AN / "audio.json").exists() else {}
if not audio.get("available") or not wav.exists():
    OUT.write_text(json.dumps({"available": False, "reason": audio.get("reason", "run audio.py first")}, indent=2))
    print("speech: skipped (no audio)")
    raise SystemExit(0)


def doubtful(no_speech, logprob):
    return no_speech > 0.5 or logprob < -1.0


segments, engine, language = [], None, None
try:
    import mlx_whisper

    engine = args.model or "mlx-community/whisper-large-v3-turbo"
    res = mlx_whisper.transcribe(str(wav), path_or_hf_repo=engine, condition_on_previous_text=False)
    language = res.get("language")
    for s in res.get("segments", []):
        segments.append((s["start"], s["end"], s["text"], doubtful(s.get("no_speech_prob", 0), s.get("avg_logprob", 0))))
except ImportError:
    from faster_whisper import WhisperModel

    engine = args.model or "small"
    model = WhisperModel(engine, compute_type="int8")
    segs, info = model.transcribe(str(wav), vad_filter=True)
    language = info.language
    for s in segs:
        segments.append((s.start, s.end, s.text, doubtful(s.no_speech_prob, s.avg_logprob)))

out = {
    "available": True,
    "engine": engine,
    "language": language,
    "segments": [
        {"start": round(a, 2), "end": round(b, 2), "text": t.strip(), "confidence": "unverified" if d else "medium"}
        for a, b, t, d in segments if t.strip()
    ],
}
OUT.write_text(json.dumps(out, indent=2))
sure = sum(s["confidence"] != "unverified" for s in out["segments"])
print(f"speech: {len(out['segments'])} segment(s), {sure} confident ({engine}, {language})")
