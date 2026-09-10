import subprocess, json
from pathlib import Path
from .utils import OUTPUT

def probe(p):
    return subprocess.check_output(["ffprobe","-v","error","-show_entries",
        "format=duration:stream=codec_type","-of","json",str(p)],text=True)

def main():
    p=OUTPUT/"episode.mp4"
    if not p.exists(): raise SystemExit("No output video")
    data=json.loads(probe(p))
    d=float(data["format"]["duration"])
    streams=[x["codec_type"] for x in data["streams"]]
    print(f"QA duration={d:.1f}s streams={streams}")
    if d < 360: raise SystemExit("FAIL: video shorter than 6 minutes")
    if "video" not in streams or "audio" not in streams: raise SystemExit("FAIL: missing A/V stream")
    print("QA PASS")

if __name__=="__main__":
    main()
