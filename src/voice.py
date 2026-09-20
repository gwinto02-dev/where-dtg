import json, subprocess
from pathlib import Path
from .utils import WORK, ensure_dirs, synth_speech, save_json, run


def probe_duration(path):
    x = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        text=True,
    )
    return float(x.strip())


def main():
    ensure_dirs()
    settings = json.loads(open("config/settings.json", encoding="utf-8").read())
    ep = json.loads((WORK / "episode.json").read_text())

    # Narrate each scene to its own file so we know exactly where each
    # scene starts/ends in the final audio track. This is what lets
    # render.py show clips that were actually fetched FOR that scene while
    # that scene's narration is playing, instead of pulling from a pool of
    # every clip across the whole episode with no timing relationship to
    # what's being said.
    scene_files = []
    timings = []
    cursor = 0.0

    for s in ep["scenes"]:
        out = WORK / f"narration_scene_{s['id']:03d}.mp3"
        synth_speech(s["narration"], out, settings["tts_voice"], settings["tts_rate"])
        d = probe_duration(out)
        timings.append({
            "scene_id": s["id"],
            "start": cursor,
            "end": cursor + d,
            "duration": d,
        })
        cursor += d
        scene_files.append(out)

    concat_list = WORK / "narration_concat.txt"
    concat_list.write_text("".join(f"file '{p.resolve()}'\n" for p in scene_files))

    out_path = WORK / "narration.mp3"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(out_path)])

    save_json(WORK / "scene_timings.json", timings)

    print(out_path)
    print("Total narration duration:", cursor)
    print("Scene timings saved:", WORK / "scene_timings.json")


if __name__ == "__main__":
    main()
