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
