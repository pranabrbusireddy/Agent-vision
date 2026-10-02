# Agent Vision

A [Claude Code](https://claude.com/claude-code) skill that **watches a reference video for you**. Paste an Instagram reel link (or give it a local video file), and Agent Vision:

1. **downloads it anonymously.** No login, no cookies, ever.
2. **measures it locally:** cuts, frames, colours, tempo, beats, the hits each cut lands on, and speech if you allow it.
3. **writes a production brief** where every claim cites a timestamp or frame, and anything unclear is marked *unverified*. **You approve it** before anything is built.
4. **builds an original video in the same format** with [Remotion](https://www.remotion.dev): your assets, generated licence-free audio, the reference's structure and timing.
5. **verifies the render numerically:** duration, cut timing, text legibility, audio sync, and that no shot is a near-copy of the reference.

It copies the **format** (pacing, motion, type treatment), never the footage, logos or music.

## Requirements

- macOS or Linux (developed and tested on macOS, Apple Silicon)
- [Claude Code](https://claude.com/claude-code)
- `ffmpeg` and `ffprobe` (e.g. `brew install ffmpeg`)
- Python 3.10+ (tested on 3.11)
- Node.js 18+ (for Remotion)

## Install

```bash
git clone <this-repo-url> ~/.claude/skills/agent-vision
cd ~/.claude/skills/agent-vision
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
bash scripts/preflight.sh        # checks every tool; fix anything marked MISS
```

Start a new Claude Code session; `agent-vision` appears in the skills list.

## Use

From the project folder where you want the video made (put your logo, colours, screen recordings and copy in `./assets`):

```
/agent-vision https://www.instagram.com/reel/<id>/
```

or just paste a reel link and ask Claude to make a launch video in the same format. Each run lives in `./agent-vision-runs/<run-id>/` (`input/`, `analysis/`, `brief.md`, `storyboard.md`, `remotion/out/final.mp4`).

If a reel needs a login to view, Agent Vision stops and asks you for a **screen recording** from your phone instead (iPhone and Android 11+ can record the app's sound). It will never ask for your cookies, tokens or password.

Speech transcription downloads a Whisper model (~1.6 GB) the first time; you're asked first and can skip it.

## Test

```bash
bash scripts/selftest.sh            # known-answer test: download, analysis, failure cases, brief gate
bash scripts/selftest.sh --render   # plus a Remotion build, render and verify (slower)
```

## Known issues

- **Audio sync in Remotion renders:** Remotion's AAC encode currently puts audio about 43 ms late. Until the template is fixed, render the video with `--muted`, render the audio with `--codec=wav`, then mux with `ffmpeg -i video.mp4 -i audio.wav -c:v copy -c:a aac out.mp4`.
- `verify.py`'s beat-sync check uses librosa onsets, which can misreport sync by ±30–40 ms at 60 fps. A sample-accurate check is planned.

## Licences

- Agent Vision: MIT (see `LICENSE`).
- **Remotion has its own licence.** It's free for individuals and small companies, and larger companies need a paid licence. Check [remotion.dev/license](https://www.remotion.dev/license) before rendering.
- You're responsible for only analysing videos you're allowed to use as a reference, and for following the platform's terms.
