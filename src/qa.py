import subprocess, json
from pathlib import Path
from .utils import OUTPUT, WORK

def probe(p):
    return subprocess.check_output(["ffprobe","-v","error","-show_entries",
        "format=duration:stream=codec_type","-of","json",str(p)],text=True)

BANNED_HOOK_LEADINS = [
    "imagine", "somewhere in", "have you ever", "what if",
    "picture this", "did you know", "in a world where",
]

def check_hook_style():
    episode_path = WORK / "episode.json"
    if not episode_path.exists():
        print("QA hook check: episode.json not found, skipping")
        return

    data = json.loads(episode_path.read_text(encoding="utf-8"))
    hook = (data.get("hook") or "").strip()
    scenes = data.get("scenes") or []
    scene1_narration = (scenes[0].get("narration") or "").strip() if scenes else ""

    for label, text in (("hook", hook), ("scene 1 narration", scene1_narration)):
        if not text:
            continue
        lowered = text.lower()
        for phrase in BANNED_HOOK_LEADINS:
            if lowered.startswith(phrase):
                print(f"QA WARNING: {label} opens with a framed lead-in ('{phrase}...') "
                      f"instead of a cold open. Consider regenerating.")
                break

def main():
    p=OUTPUT/"episode.mp4"
    if not p.exists(): raise SystemExit("No output video")
    data=json.loads(probe(p))
    d=float(data["format"]["duration"])
    streams=[x["codec_type"] for x in data["streams"]]
    print(f"QA duration={d:.1f}s streams={streams}")
    if d < 360: raise SystemExit("FAIL: video shorter than 6 minutes")
    if "video" not in streams or "audio" not in streams: raise SystemExit("FAIL: missing A/V stream")
    check_hook_style()
    print("QA PASS")

if __name__=="__main__":
    main()
