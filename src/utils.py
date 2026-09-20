from pathlib import Path
import json, subprocess, re, asyncio

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
OUTPUT = ROOT / "output"

def ensure_dirs():
    WORK.mkdir(exist_ok=True)
    OUTPUT.mkdir(exist_ok=True)

def save_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def run(cmd):
    print("$", " ".join(map(str, cmd)))
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(p.stdout[-5000:])
    if p.returncode:
        raise RuntimeError(f"Command failed: {p.returncode}")
    return p.stdout

def clean_filename(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:100]

async def _synth(text, out, voice, rate):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(str(out))

def synth_speech(text, out, voice, rate):
    """Shared narration TTS helper used by both the long-form and short-form
    pipelines, so both narrate in the same voice via edge-tts."""
    asyncio.run(_synth(text, out, voice, rate))

async def _synth_with_words(text, out, voice, rate):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    words = []
    with open(out, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # offset/duration come back in 100-nanosecond ticks.
                start = chunk["offset"] / 1e7
                dur = chunk["duration"] / 1e7
                words.append({
                    "text": chunk["text"],
                    "start": start,
                    "end": start + dur,
                })
    return words

def synth_speech_with_words(text, out, voice, rate):
    """Like synth_speech, but also returns word-level timing
    ([{text, start, end}, ...] in seconds) captured straight from edge-tts's
    own boundary events -- so burned-in captions are frame-accurate to the
    actual audio with no separate transcription/alignment step needed."""
    return asyncio.run(_synth_with_words(text, out, voice, rate))
