"""Phase 4 gate: check brief.json against the evidence, then render brief.md.

    .venv/bin/python scripts/validate_brief.py <run-dir> [--write]

Rejects (exit 1):
  - structure that doesn't match templates/brief.schema.json
  - a cited frame that doesn't exist, or a cited time outside the reference
  - brief shots that don't match the measured shots in analysis/shots.json
  - any claim about sound when analysis/audio.json says audio wasn't available
Auto-labels (risk #1):
  - any item with no timestamp and no frame → confidence "unverified", auto_unverified: true
Warns:
  - easing marked "high" (easing is inferred from stills)
  - pacing numbers that disagree with the measured shots

--write saves the auto-labelled brief.json and renders brief.md. Without it the
check is dry-run (exit code still reports pass/fail).
"""
import argparse
import json
from pathlib import Path

import jsonschema

SKILL = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("run")
ap.add_argument("--write", action="store_true")
args = ap.parse_args()
RUN = Path(args.run).resolve()
AN = RUN / "analysis"

brief = json.loads((RUN / "brief.json").read_text())
schema = json.loads((SKILL / "templates" / "brief.schema.json").read_text())
media = json.loads((AN / "media.json").read_text())
shots_doc = json.loads((AN / "shots.json").read_text())
audio = json.loads((AN / "audio.json").read_text()) if (AN / "audio.json").exists() else {"available": False, "reason": "audio.py not run"}

errors, warnings, auto = [], [], []

for e in sorted(jsonschema.Draft202012Validator(schema).iter_errors(brief), key=lambda e: list(e.path)):
    errors.append(f"schema: {'/'.join(map(str, e.path)) or '(root)'}: {e.message}")
if errors:
    print("REJECTED — fix the structure first:\n  " + "\n  ".join(errors[:30]))
    raise SystemExit(1)

duration, frame = media["duration"], 1 / media["fps"]


def walk(node, path):
    """Yield (path, item) for every object that carries a confidence."""
    if isinstance(node, dict):
        if "confidence" in node:
            yield path, node
        for k, v in node.items():
            yield from walk(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}[{i}]")


items = list(walk(brief, ""))
for path, item in items:
    ev = item.get("evidence") or {}
    ts, frames = ev.get("t") or [], ev.get("frames") or []
    if not ts and not frames:
        if item["confidence"] != "unverified" or not item.get("auto_unverified"):
            auto.append(path)
        item["confidence"] = "unverified"
        item["auto_unverified"] = True
    for f in frames:
        if not (RUN / f).exists():
            errors.append(f"{path}: cites {f}, which doesn't exist")
    for t in ts:
        if not 0 <= t <= duration + frame:
            errors.append(f"{path}: cites t={t}, outside the reference (0–{duration} s)")

# Shots must be the measured shots.
measured = {s["shot"]: s for s in shots_doc["shots"]}
described = {s["shot"]: s for s in brief["shots"]}
for n, m in measured.items():
    d = described.get(n)
    if not d:
        errors.append(f"shot {n} ({m['start']}–{m['end']} s) is measured but missing from the brief")
    elif abs(d["start"] - m["start"]) > frame * 1.01 or abs(d["end"] - m["end"]) > frame * 1.01:
        errors.append(f"shot {n}: brief says {d['start']}–{d['end']} s, measured {m['start']}–{m['end']} s")
for n in described.keys() - measured.keys():
    errors.append(f"shot {n} is in the brief but not in analysis/shots.json (add it via a cut override, don't invent it)")

# Never claim to have heard what wasn't analysed.
if not audio.get("available"):
    a = brief["audio"]
    sound = [c for c in a.get("cues", []) if c["kind"] != "silence"] + a.get("speech", [])
    if a.get("available") or sound or a.get("tempo_bpm") or a.get("character"):
        errors.append(f"brief describes sound, but analysis/audio.json says audio wasn't available ({audio.get('reason')})")

for path, item in items:
    if path.endswith("/motion") and item.get("easing") and item["confidence"] == "high":
        warnings.append(f"{path}: easing marked high — it's inferred from stills; medium at most")
lengths = [s["duration"] for s in shots_doc["shots"]]
p = brief["pacing"]
if p["shots"] != len(lengths) or abs(p["avg_shot_s"] - sum(lengths) / len(lengths)) > 0.05:
    warnings.append(f"pacing says {p['shots']} shots / avg {p['avg_shot_s']} s; measured {len(lengths)} / {sum(lengths) / len(lengths):.2f} s")

counts = {c: sum(i["confidence"] == c for _, i in items) for c in ("high", "medium", "unverified")}
print(f"{len(items)} items: {counts['high']} high, {counts['medium']} medium, {counts['unverified']} unverified "
      f"({len(auto)} auto-labelled for missing evidence)")
for a in auto:
    print(f"  auto-unverified: {a}")
for w in warnings:
    print(f"  warn: {w}")
if errors:
    print("REJECTED:\n  " + "\n  ".join(errors))
    raise SystemExit(1)


# ------------------------------------------------------------------ brief.md

def cite(ev):
    ev = ev or {}
    parts = [f"{t:.2f}s" for t in ev.get("t", [])] + [f"[{Path(f).name}]({f})" for f in ev.get("frames", [])]
    if ev.get("source"):
        parts.append(f"`{ev['source']}`")
    return ", ".join(parts) or "—"


CONF = {"high": "high", "medium": "medium", "unverified": "**unverified**"}


def md():
    b, r = brief, brief["reference"]
    out = [f"# Production brief", "",
           f"Reference: `{r['source']}` · {r['duration']:.2f} s · {r['fps']:g} fps · {r['size'][0]}×{r['size'][1]}", ""]
    for q in r.get("quality_notes", []):
        out.append(f"> Input quality: {q}")
    out += ["", b["summary"], "",
            f"Confidence: {counts['high']} high · {counts['medium']} medium · {counts['unverified']} unverified. "
            "Every row cites a time or frame; open the frame to check it.", "",
            "## Pacing", "",
            f"{p['shots']} shots, average {p['avg_shot_s']:.2f} s (fastest {p['fastest_shot_s']:.2f} s, slowest {p['slowest_shot_s']:.2f} s). "
            f"{p.get('notes', '')} — {CONF[p['confidence']]}, {cite(p['evidence'])}", "",
            "## Structure", "", "| Section | Time | Shots | Purpose | Conf. | Evidence |", "|---|---|---|---|---|---|"]
    for s in b["structure"]:
        out.append(f"| {s['section']} | {s['start']:.2f}–{s['end']:.2f} s | {', '.join(map(str, s['shots']))} | {s['purpose']} | {CONF[s['confidence']]} | {cite(s['evidence'])} |")
    out += ["", "## Shot timeline", "", "| # | Time | Framing | Motion | Transition in | Text | Conf. | Evidence |", "|---|---|---|---|---|---|---|---|"]
    for s in b["shots"]:
        m, tr = s["motion"], s["transition_in"]
        motion = f"{m['type']}{', ' + m['speed'] if m.get('speed') else ''}{', ' + m['easing'] if m.get('easing') else ''} ({CONF[m['confidence']]})"
        tr_len = f" {tr['duration_s']:.2f}s" if tr.get("duration_s") else ""
        tr_audio = f" — {tr['on_audio']}" if tr.get("on_audio") else ""
        trans = f"{tr['type']}{tr_len}{tr_audio} ({CONF[tr['confidence']]})"
        text = "<br>".join(f"“{t['text']}” [{t['style']}]" for t in s.get("on_screen_text", [])) or "—"
        out.append(f"| {s['shot']} | {s['start']:.2f}–{s['end']:.2f} | {s['content_type']}: {s['framing']} | {motion} | {trans} | {text} | {CONF[s['confidence']]} | {cite(s['evidence'])} |")
    out += ["", "## Text styles", "", "| Style | Feel | Weight / case | Size | Colour | Animation | Conf. | Evidence |", "|---|---|---|---|---|---|---|---|"]
    for t in b["text_styles"]:
        out.append(f"| {t['name']} | {t['font_feel']} | {t['weight']} / {t['case']} | {t['size_pct_of_height']:g}% of height | `{t['colour']}` | {t['animation']} | {CONF[t['confidence']]} | {cite(t['evidence'])} |")
    a = b["audio"]
    out += ["", "## Audio", ""]
    if not a["available"]:
        out.append(f"**Not analysed:** {a.get('reason', 'no audio')}. Nothing in this brief describes sound.")
    else:
        out.append(f"Tempo ≈ {a.get('tempo_bpm', '?')} bpm; cuts follow {a.get('cuts_follow_bpm') or 'no beat grid'}. {a.get('character', '')}")
        out += ["", "| Time | Kind | Cue | On cut | Conf. | Evidence |", "|---|---|---|---|---|---|"]
        for c in a.get("cues", []):
            out.append(f"| {c['t']:.2f} s | {c['kind']} | {c['description']} | {c.get('on_cut', '—')} | {CONF[c['confidence']]} | {cite(c['evidence'])} |")
        if a.get("speech"):
            out += ["", "Speech:", ""] + [f"- {s['start']:.2f}–{s['end']:.2f} s: “{s['text']}” ({CONF[s['confidence']]})" for s in a["speech"]]
    out += ["", "## Too fast or unclear to analyse", ""]
    out += [f"- {u['what']}{' at ' + ', '.join(f'{t:.2f} s' for t in u['t']) if u.get('t') else ''}: {u['why']}" for u in b["unclear"]] or ["- Nothing flagged."]
    if b.get("too_close_to_reference"):
        out += ["", "## Do not copy (too distinctive)", ""] + [f"- Shot {x['shot']}: {x['what']}" for x in b["too_close_to_reference"]]
    if warnings:
        out += ["", "## Validator warnings", ""] + [f"- {w}" for w in warnings]
    return "\n".join(out) + "\n"


if args.write:
    (RUN / "brief.json").write_text(json.dumps(brief, indent=2))
    (RUN / "brief.md").write_text(md())
    print("PASS — brief.json updated, brief.md written")
else:
    print("PASS (dry run; --write to save labels and render brief.md)")
