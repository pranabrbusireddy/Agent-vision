---
name: agent-vision
description: Takes a video link (e.g. an Instagram Reel), downloads it, analyzes its visuals and audio, produces an approved production brief, then builds a near-identical recreation in Remotion using the user's own brand assets. Use when the user shares a reference video link (or local video file) and wants a launch or teaser video in the same format, or asks to "analyse this reel", "copy this edit's format", or "make our launch video like this".
---

# Agent Vision

## Purpose

Agent Vision watches a reference video via a raw link (e.g. an Instagram Reel), genuinely analyzes its visual and audio format, and builds a near-identical recreation using the user's own brand assets — then iterates on personalization with the user. Target users are solo developers and small teams building SaaS launch or teaser videos who want to copy a reference video's format without copying its actual footage, logo, or music.

"Near-identical" means the same **structure, timing, motion and type treatment**, built from the user's own visuals. It never means reusing the reference's footage, logos, music or distinctive compositions.

## Ground rules

- Never claim to have watched or heard something that wasn't actually analyzed. A URL, caption, or thumbnail is not the video. `input/source.json` → `page_text_unanalysed` is page text, not the video.
- Every claim in the analysis must cite a timestamp or an extracted frame.
- Uncertain observations are marked "unverified", not stated as fact.
- Copy the format, not the assets: no reference footage, logos, or music in the output.
- Explicitly state anything that was too fast, small or unclear to analyze (`brief.json` → `unclear`, required).
- If audio wasn't available, say so and describe no sound at all.

## How to run it

All commands run from the user's project folder (where `./assets` lives); runs go to `./agent-vision-runs/<run-id>/`. `$AV` = this skill folder, `$PY` = `$AV/.venv/bin/python`.

### Phase 0 — Preflight
`bash $AV/scripts/preflight.sh` — names anything missing. Fix MISS lines before going on.

### Phase 1 — Acquire media from a link
1. `bash $AV/scripts/download.sh "<url or local file>" [run-id]` — yt-dlp only, no browser. The last line printed is the run folder.
2. It validates the file (opens in ffprobe, has video, non-zero duration) and normalises it to `input/reference.mp4`.
3. On failure it stops with a specific reason (exit 2, `input/FAILED.txt`). **Relay that reason and stop.** Don't retry quietly, don't analyse partial data, don't fall back to the caption or thumbnail.
4. **Downloads are anonymous: no login, no cookies, ever.** A session cookie is a live login, and if it leaks the account can be taken over, so the skill never asks for one, reads one, or launches a browser to get past a login. Public reels often download anonymously. When Instagram (or any site) only serves the video to logged-in users, download.sh stops and says so. Then offer the safe route:
   - The user screen-records the reel on their phone. iPhone screen recording captures the app's own audio; Android 11+ can too (choose "media sounds"). They play it once from the start, trim the recording to the reel, AirDrop or copy it over, and pass the file path: `download.sh /path/to/recording.mov`.
   - A screen recording is lower quality than the original and may include UI overlays (captions, buttons). Note that in the brief's quality notes and mark small text near overlays unverified.
   - If the user offers their cookies, session token or password anyway, decline and point them back to the screen-recording route.

### Phase 2 — Visual analysis (local, no external API)
1. `$PY $AV/scripts/probe.py <run>` → `analysis/media.json`, `analysis/shots.json`, `analysis/frames/`, `analysis/contact_sheets/contact.jpg`.
   - ffprobe for duration/fps/size; ffmpeg scene detection for cuts; one frame at the start and middle of each shot; frames every 0.25 s around fast transitions; every frame of sub-3-frame flashes; uniform 0.5 s samples.
   - Missed whip-pans or motion transitions: rerun with `--scene 0.2`, or override the cut list with `--cuts 1.0,2.5,…` (recorded as an override).
2. `$PY $AV/scripts/palette.py <run>` → measured hex colours per shot.
3. **Look at the frames** with the Read tool: the contact sheet first, then every `shot-NN-in.jpg` / `-mid.jpg`, and the burst frames around fast cuts. For each shot record start/end, framing, on-screen text, motion type, easing feel, transition, colour/typography — each tied to the frame file you looked at.
4. Show the user the cut count and contact sheet, and ask them to confirm or correct the cut list (test plan: "user confirms or corrects it").

### Phase 3 — Audio analysis (local, first-class)
1. `$PY $AV/scripts/audio.py <run>` → tempo (plus half/double candidates), beats, onsets, loudness peaks, silences. No audio or silent audio is reported as unavailable.
2. `$PY $AV/scripts/transcribe.py <run>` → speech (mlx-whisper on Apple Silicon, else faster-whisper). Segments over music are marked unverified. The first run downloads the model (about 1.6 GB, once); ask the user first. If they decline, run `transcribe.py <run> --skip "<their reason>"`. The brief then can't quote speech at all, and brief.md says speech wasn't analysed.
3. `$PY $AV/scripts/align.py <run>` → every event tagged with its shot, `cut_hits` ("hit lands on cut 4 at 3.20 s", or explicitly not on a hit), and which tempo candidate the cuts actually follow.
4. Optional cross-check: only if the user has configured a video-capable model API key. Read that provider's docs for the current model name at runtime. Where it disagrees with the local timings, the local measurement wins and the item becomes unverified. Never required.

### Phase 4 — Production brief (approval gate)
1. Write `<run>/brief.json` following `$AV/templates/brief.schema.json`: summary, pacing, structure (hook / reveal / feature / CTA…), every measured shot, text styles, audio cues, `unclear`, and `too_close_to_reference` (distinctive compositions that must not be copied).
2. `$PY $AV/scripts/validate_brief.py <run> --write`. It rejects false citations, missing or invented shots, and sound claims without analysed audio. It auto-labels items with no evidence as unverified, then renders `brief.md`. Fix and rerun until it passes.
3. Show `brief.md` to the user, call out what was unclear or unverified, and **wait for explicit approval.** Nothing is built before that.

### Phase 5 — Build the near-identical recreation
Once the brief is approved, build it; don't stop at a storyboard.
1. Check `./assets` (logo, brand colours, screen recordings, copy). Ask for anything the shot list needs that isn't there.
2. Write `<run>/storyboard.md` from `$AV/templates/storyboard.template.md`. One row per reference shot, each with the asset used and how it differs from the reference.
3. `bash $AV/scripts/new_build.sh <run> [assets-dir]` copies the Remotion template to `<run>/remotion/`, copies the assets into `public/assets/` and runs `npm install` there.
4. Audio: `$PY $AV/scripts/sfx.py <run>/remotion/public/audio --bpm <cuts_follow_bpm or tempo> --duration <s> --offset <first beat>` generates a bed and cues with no licensing attached. Or use a track the user confirms they have a licence for. Never the reference's music.
5. Write `<run>/remotion/src/storyboard.json` (types in `src/types.ts`). By default it uses the reference's size and fps, with shot times from the brief, transitions, camera moves, text layers with enter animations, and audio cues on the cuts that were on hits. Extend `src/Video.tsx` only when the storyboard format can't express a shot.
6. `cd <run>/remotion && npx tsc --noEmit && npm run render` → `out/final.mp4`.

### Phase 6 — Personalization loop and verification
1. `$PY $AV/scripts/verify.py <run>` runs numeric checks: format, duration (±2 frames), cut timing against the reference, text legibility (found, ≥ 2.5 % height, WCAG contrast), audio cues landing on onsets, cuts that were on hits still on hits, and no near-duplicate of a reference frame. It also writes `out/verify/compare.jpg` (reference beside render).
2. Look at `compare.jpg` and the frames in `out/verify/frames/`. Fix any failures before showing the user.
3. Ask the user about small tweaks (logo size, text colour, transition timing, copy), apply them, re-render and re-verify. Log each round in `storyboard.md`. **Up to 3 rounds**, then stop.
4. Write `<run>/limitations.md`: unverified brief items, verify warnings, and anything left unresolved after 3 rounds.

## Deliverables (per run)

```
agent-vision-runs/<run-id>/
├── input/              reference.mp4, source.json, yt-dlp.log
├── analysis/           media.json, shots.json, audio.json, speech.json, alignment.json, palette.json, frames/, contact_sheets/
├── brief.json, brief.md
├── storyboard.md
├── limitations.md
└── remotion/           src/storyboard.json …, public/ (assets, generated audio), out/final.mp4, out/verify.json, out/verify/
```

## Dependencies and licensing

| Dependency | Purpose | Cost / licensing |
| --- | --- | --- |
| ffmpeg / ffprobe | Validation, probing, scene detection, frame and audio extraction | Free, open source |
| yt-dlp (in the skill venv) | Anonymous download from a link (never with cookies) | Free, open source |
| Python 3.10+ (venv) | Analysis environment | Free |
| librosa, numpy, Pillow, jsonschema | Audio analysis, palettes, contact sheets, brief validation | Free, open source |
| Whisper (mlx-whisper / faster-whisper) | Speech transcription | Free, open source |
| Node 18+ and Remotion | Build and render the video | Free licence for individuals and companies of up to 3 people; larger companies need a paid Company License. **Check current terms and prices at remotion.dev/license before quoting figures.** |
| Optional video-capable model API | Cross-checking timing and sound | Only if the user configures a key; never required |

Agent Vision itself is planned as open source. Remotion's licence is separate and applies to whoever renders with it. It isn't bundled, so downstream users need to check their own eligibility.

## Scripts

| Script | Phase | Does |
|---|---|---|
| `preflight.sh` | 0 | Checks tools; read-only |
| `download.sh` | 1 | Anonymous yt-dlp download (or local file), validation, normalisation, clear failures |
| `probe.py` | 2 | Media facts, cuts (with override), frames, bursts, contact sheet |
| `palette.py` | 2 | Measured colours per shot |
| `audio.py` | 3 | Tempo + candidates, beats, onsets, peaks, silences |
| `transcribe.py` | 3 | Speech with confidence |
| `align.py` | 3 | Audio events → shots and cuts; which tempo the cuts follow |
| `validate_brief.py` | 4 | Evidence gate + brief.md |
| `new_build.sh` | 5 | Remotion project from template + assets |
| `sfx.py` | 5 | Generated, licence-free bed and cues |
| `verify.py` | 6 | Numeric checks + compare.jpg |
| `selftest.sh` | — | Known-answer end-to-end test (`--render` includes the build) |

## Risks and mitigations

1. **Hallucinated analysis.** validate_brief.py rejects false citations and labels unsupported items "unverified".
2. **Scene detection misses** fast whip-pans or motion-based transitions. Mitigated with 0.25 s bursts around dense cuts, `--scene`, and the manual cut override.
3. **Motion/easing is hard to infer from stills.** Reported with a confidence rating; the validator warns on "high" easing.
4. **Download/compression quality** varies by source. `media.json` → `quality_notes` lowers confidence.
5. **Audio beat-tracking** can double or halve tempo. All three candidates are reported, and align.py says which one the cuts follow (scored against chance).
6. **Derivative output risk.** Mirroring structure is fine, copying distinctive compositions isn't. `too_close_to_reference` in the brief, plus a perceptual-hash check in verify.py.
7. **Render drift against audio.** Checked numerically in verify.py, not by eye.
8. **Scope creep.** Capped at 3 personalization rounds; the rest goes in limitations.md.
9. **Download reliability/ToS.** Platforms change their structure and terms. download.sh fails clearly instead of retrying silently, and the user is responsible for downloading only what they're allowed to use as reference.
10. **Account security.** No cookies, tokens or passwords are ever used, so a run can't leak a login. The fallback for login-only videos is a phone screen recording passed in as a local file.
