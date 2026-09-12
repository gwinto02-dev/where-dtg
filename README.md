# Where Does It Go? — YouTube Documentary Automation (V1)

A GitHub Actions-friendly pipeline for producing 6–12 minute faceless documentary videos.

## Pipeline

Topic → Research/Script → Scene Plan → Pexels + Pixabay video/photo search (Gemini-judged relevance) → TTS → FFmpeg edit (shuffled, no-repeat-until-exhausted clip scheduling) → QA → MP4

The default workflow does **not** publish publicly. YouTube upload is intentionally left as an optional private/scheduled stage.

## V1 goals

- 8–10 minute default target
- Real stock video first; photos as fallback
- Visual change every ~2–5 seconds
- Documentary-style narration
- Simple motion on photos
- Background music optional
- Automatic source manifest
- Basic duration/black-frame/audio QA
- GitHub Actions compatible

## Required secrets

- `GEMINI_API_KEY` — script/research generation, and vision-based relevance judging for every candidate clip
- `PEXELS_API_KEY` — stock video/photo search
- `PIXABAY_API_KEY` — second free stock video/photo source (recommended, not strictly required — the pipeline runs on Pexels alone if this is unset, but a second source means fewer irrelevant-clip fallbacks and fewer repeated clips)

Optional:
- `YOUTUBE_CLIENT_ID`
- `YOUTUBE_CLIENT_SECRET`
- `YOUTUBE_REFRESH_TOKEN`

## Local test

Install FFmpeg first, then:

```bash
pip install -r requirements.txt
python -m src.pipeline --topic "Where Does Your Old Phone Actually Go?"
```

Output is written to `output/`.

## GitHub Actions

The workflow can be triggered manually with a topic. Public repositories can use standard GitHub-hosted runners without Actions minute charges; private GitHub Free repositories have a monthly included allowance. See GitHub's current billing documentation before scaling production workloads.

## Important

This is V1. It deliberately prefers reliable, attributable stock sources instead of scraping random YouTube videos. Pexels provides an API for photos and videos and asks developers to provide attribution/linking where possible.
