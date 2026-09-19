import json
import random
import subprocess
from pathlib import Path

from .utils import WORK, OUTPUT, ensure_dirs, run, synth_speech

# Common locations for a bundled font across GitHub Actions ubuntu-latest
# runners and most desktop Linux installs. The first one found is used for
# the CTA text overlay; if none exist, the short is still built, just
# without burned-in text (see build_short()).
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


def duration(p):
    x = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
        text=True,
    )
    return float(x.strip())


def find_font():
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    print("WARNING: no bundled font found -- short video will render without "
          "the burned-in CTA text. Install fonts-dejavu-core (or similar) to enable it.")
    return None


def pick_short_clips(manifest, scenes_to_use=2):
    """
    Short videos borrow footage already downloaded for the long-form video
    (no extra API calls) -- prioritizing the earliest scenes, since those
    match the hook/curiosity beat the short-form script is built around.
    Video clips are preferred over photos for a punchier short.
    """
    wanted_scene_ids = sorted({m["scene_id"] for m in manifest})[:scenes_to_use]
    pool = [m for m in manifest if m["scene_id"] in wanted_scene_ids and m["type"] == "video"]
    if not pool:
        # Fall back to whatever's available for those scenes (photos included).
        pool = [m for m in manifest if m["scene_id"] in wanted_scene_ids]
    if not pool:
        # Last resort: use anything in the whole manifest.
        pool = list(manifest)
    return pool


def build_short(ep, manifest, settings):
    ensure_dirs()

    short_text = (ep.get("short_script") or ep.get("hook") or "").strip()
    if not short_text:
        raise RuntimeError("No short_script or hook available -- cannot build a short video.")

    narration = WORK / "narration_short.mp3"
    synth_speech(
        short_text,
        narration,
        settings["tts_voice"],
        settings.get("short_tts_rate", settings.get("tts_rate", "+0%")),
    )
    narration_dur = duration(narration)

    min_cut = float(settings.get("short_cut_min_seconds", 0.8))
    max_cut = float(settings.get("short_cut_max_seconds", 1.8))
    cta_seconds = 2.5  # tail window reserved for the CTA card

    pool = pick_short_clips(manifest)
    rng = random.Random(7)
    rng.shuffle(pool)

    src_durations = {}

    def src_duration(path):
        if path not in src_durations:
            src_durations[path] = duration(path)
        return src_durations[path]

    clips = []
    total = 0.0
    i = 0
    target = narration_dur + cta_seconds

    while total < target:
        m = pool[i % len(pool)]
        cut = rng.uniform(min_cut, max_cut)
        out = WORK / f"short_clip_{i:03d}.mp4"

        # Vertical crop: scale so the shortest dimension fills 1080x1920,
        # then center-crop the overflow -- keeps subjects framed rather
        # than letterboxed like the horizontal long-form video.
        vf_vertical = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,fps=25"
        )

        if m["type"] == "video":
            sdur = src_duration(m["file"])
            if sdur > cut:
                ss = rng.uniform(0, sdur - cut)
                cmd = ["ffmpeg", "-y", "-ss", f"{ss:.2f}", "-i", m["file"], "-t", f"{cut:.2f}"]
            else:
                ss = rng.uniform(0, sdur) if sdur > 0 else 0.0
                cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-ss", f"{ss:.2f}", "-i", m["file"], "-t", f"{cut:.2f}"]
            cmd += ["-vf", vf_vertical, "-an", "-c:v", "libx264", "-preset", "veryfast",
                    "-pix_fmt", "yuv420p", "-fps_mode", "cfr", str(out)]
            run(cmd)
        else:
            frames = max(1, round(cut * 25))
            run(["ffmpeg", "-y", "-loop", "1", "-i", m["file"], "-t", f"{cut:.2f}", "-vf",
                 "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                 f"zoompan=z='min(zoom+0.0015,1.12)':d={frames}:s=1080x1920:fps=25",
                 "-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                 "-fps_mode", "cfr", str(out)])

        clips.append(out)
        total += cut
        i += 1

    concat = WORK / "short_concat.txt"
    concat.write_text("".join(f"file '{p.resolve()}'\n" for p in clips))
    silent = WORK / "short_visuals.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(silent)])

    visuals_dur = duration(silent)

    # Freeze the last frame to cover the CTA card if the visuals ran short
    # of narration_dur + cta_seconds (short cut lengths can under/overshoot).
    needed = narration_dur + cta_seconds
    if visuals_dur < needed:
        extended = WORK / "short_visuals_extended.mp4"
        run(["ffmpeg", "-y", "-i", str(silent), "-vf",
             f"tpad=stop_mode=clone:stop_duration={needed - visuals_dur:.2f}",
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(extended)])
        silent = extended
        visuals_dur = needed

    cta_text = settings.get("short_cta_text", "Full story linked below")
    title = ep.get("working_title", "")
    font = find_font()
    cta_start = max(0.0, visuals_dur - cta_seconds)

    with_narration = WORK / "short_with_audio.mp4"
    run(["ffmpeg", "-y", "-i", str(silent), "-i", str(narration),
         "-map", "0:v:0", "-map", "1:a:0", "-shortest",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(with_narration)])

    final = OUTPUT / "short.mp4"

    if font:
        draw_title = (
            f"drawtext=fontfile={font}:text='{escape_drawtext(title)}':"
            "fontcolor=white:fontsize=54:borderw=3:bordercolor=black@0.7:"
            "x=(w-text_w)/2:y=120"
        )
        draw_cta = (
            f"drawtext=fontfile={font}:text='{escape_drawtext(cta_text)}':"
            "fontcolor=white:fontsize=58:borderw=4:bordercolor=black@0.8:"
            f"x=(w-text_w)/2:y=h-260:enable='gte(t,{cta_start:.2f})'"
        )
        vf = f"{draw_title},{draw_cta}"
        run(["ffmpeg", "-y", "-i", str(with_narration), "-vf", vf,
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-c:a", "copy", str(final)])
    else:
        run(["ffmpeg", "-y", "-i", str(with_narration), "-c", "copy", str(final)])

    print("SHORT:", final)
    print("Short duration:", duration(final))
    return final


def escape_drawtext(text):
    # ffmpeg drawtext needs these characters escaped inside a filtergraph.
    return (
        text.replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "\u2019")  # swap straight quotes for a safe apostrophe
    )


def main():
    ensure_dirs()
    settings = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))

    if not settings.get("shorts_enabled", True):
        print("shorts_enabled is false in config/settings.json -- skipping short video.")
        return

    ep = json.loads((WORK / "episode.json").read_text(encoding="utf-8"))
    manifest = json.loads((WORK / "media_manifest.json").read_text(encoding="utf-8"))

    build_short(ep, manifest, settings)


if __name__ == "__main__":
    main()
