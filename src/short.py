import json
import random
import subprocess
import textwrap
from pathlib import Path

from .utils import WORK, OUTPUT, ensure_dirs, run, synth_speech_with_words

# Common locations for a bundled font across GitHub Actions ubuntu-latest
# runners and most desktop Linux installs. The first one found is used for
# the title/CTA text overlays and, when present, named explicitly for the
# caption styling too so it doesn't silently fall back to whatever libass
# picks by default.
FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVu Sans"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "Liberation Sans"),
    ("/usr/share/fonts/truetype/freefont/FreeSansBold.ttf", "FreeSans"),
]


def duration(p):
    x = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
        text=True,
    )
    return float(x.strip())


def find_font():
    for path, family in FONT_CANDIDATES:
        if Path(path).exists():
            return path, family
    print("WARNING: no bundled font found -- short video will render without "
          "the burned-in title/CTA text, and captions will use libass's default font. "
          "Install fonts-dejavu-core (or similar) for consistent styling.")
    return None, None


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
        pool = [m for m in manifest if m["scene_id"] in wanted_scene_ids]
    if not pool:
        pool = list(manifest)
    return pool


def escape_drawtext(text):
    # ffmpeg drawtext needs these characters escaped inside a filtergraph.
    return (
        text.replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "\u2019")
            .replace("%", "\\%")
    )


def wrap_title(title, frame_width=1080, start_fontsize=64, min_fontsize=38, max_lines=2, margin_ratio=0.84):
    """
    ffmpeg's drawtext does not auto-wrap, so a long title just runs off both
    edges of the frame. This wraps to at most `max_lines` lines, shrinking
    the font size first, and only truncates with an ellipsis as a last
    resort if it still doesn't fit at the minimum readable size.
    Returns (wrapped_text_with_real_newlines, fontsize).
    """
    fontsize = start_fontsize
    while fontsize >= min_fontsize:
        avg_char_w = fontsize * 0.58  # rough glyph-width estimate for a bold sans font
        max_chars = max(6, int((frame_width * margin_ratio) / avg_char_w))
        lines = textwrap.wrap(title, max_chars)
        if len(lines) <= max_lines:
            return "\n".join(lines), fontsize
        fontsize -= 4

    avg_char_w = min_fontsize * 0.58
    max_chars = max(6, int((frame_width * margin_ratio) / avg_char_w))
    lines = textwrap.wrap(title, max_chars)[:max_lines]
    if lines:
        last = lines[-1]
        lines[-1] = (last[:-3].rstrip() + "...") if len(last) > 3 else last
    return "\n".join(lines), min_fontsize


def build_caption_chunks(words, max_words=4, max_duration=2.0):
    """
    Groups edge-tts's per-word boundary events into short caption cues
    (a handful of words each) instead of either one giant subtitle for the
    whole narration or a new cue flashing on every single word. Breaks
    early on sentence-ending punctuation so cues land on natural pauses.
    """
    chunks = []
    current = []

    def flush():
        if current:
            chunks.append({
                "text": " ".join(w["text"] for w in current),
                "start": current[0]["start"],
                "end": current[-1]["end"],
            })

    for w in words:
        current.append(w)
        ends_sentence = w["text"].strip().endswith((".", "?", "!"))
        span = current[-1]["end"] - current[0]["start"]
        if len(current) >= max_words or span >= max_duration or ends_sentence:
            flush()
            current = []
    flush()
    return chunks


def format_srt_time(t):
    if t < 0:
        t = 0
    total_ms = int(round(t * 1000))
    h, rem = divmod(total_ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(chunks, path, uppercase=True):
    lines = []
    for i, c in enumerate(chunks, start=1):
        text = c["text"].upper() if uppercase else c["text"]
        lines.append(str(i))
        lines.append(f"{format_srt_time(c['start'])} --> {format_srt_time(c['end'])}")
        lines.append(text)
        lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def escape_subtitles_path(path):
    p = str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return p


def build_short(ep, manifest, settings):
    ensure_dirs()

    short_text = (ep.get("short_script") or ep.get("hook") or "").strip()
    if not short_text:
        raise RuntimeError("No short_script or hook available -- cannot build a short video.")

    narration = WORK / "narration_short.mp3"
    words = synth_speech_with_words(
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
    needed = narration_dur + cta_seconds
    if visuals_dur < needed:
        extended = WORK / "short_visuals_extended.mp4"
        run(["ffmpeg", "-y", "-i", str(silent), "-vf",
             f"tpad=stop_mode=clone:stop_duration={needed - visuals_dur:.2f}",
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(extended)])
        silent = extended
        visuals_dur = needed

    # --- Captions -----------------------------------------------------
    caption_chunks = build_caption_chunks(words) if words else []
    srt_path = WORK / "short_captions.srt"
    if caption_chunks:
        write_srt(caption_chunks, srt_path, uppercase=settings.get("short_caption_uppercase", True))
    else:
        print("WARNING: no word-boundary timing came back from edge-tts -- short video will have no captions.")

    cta_text = settings.get("short_cta_text", "Full story linked below")
    title = ep.get("working_title", "")
    font_path, font_family = find_font()
    cta_start = max(0.0, visuals_dur - cta_seconds)
    title_seconds = min(3.5, narration_dur)  # don't outlast a very short narration

    with_narration = WORK / "short_with_audio.mp4"
    run(["ffmpeg", "-y", "-i", str(silent), "-i", str(narration),
         "-map", "0:v:0", "-map", "1:a:0", "-shortest",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(with_narration)])

    final = OUTPUT / "short.mp4"
    filters = []

    if font_path:
        title_text, title_fontsize = wrap_title(title)
        title_fade = 0.4
        title_alpha = (
            f"if(lt(t,{title_seconds - title_fade:.2f}),1,"
            f"if(lt(t,{title_seconds:.2f}),({title_seconds:.2f}-t)/{title_fade},0))"
        )
        filters.append(
            f"drawtext=fontfile={font_path}:text='{escape_drawtext(title_text)}':"
            f"fontcolor=white:fontsize={title_fontsize}:line_spacing=10:"
            "box=1:boxcolor=black@0.55:boxborderw=22:"
            "borderw=2:bordercolor=black@0.6:"
            f"x=(w-text_w)/2:y=190:alpha='{title_alpha}':"
            f"enable='lte(t,{title_seconds:.2f})'"
        )

        cta_fade = 0.4
        cta_alpha = (
            f"if(lt(t,{cta_start:.2f}),0,"
            f"if(lt(t,{cta_start + cta_fade:.2f}),(t-{cta_start:.2f})/{cta_fade},1))"
        )
        filters.append(
            f"drawtext=fontfile={font_path}:text='{escape_drawtext(cta_text)}':"
            "fontcolor=white:fontsize=52:"
            "box=1:boxcolor=black@0.6:boxborderw=20:"
            "borderw=2:bordercolor=black@0.6:"
            f"x=(w-text_w)/2:y=h-340:alpha='{cta_alpha}':"
            f"enable='gte(t,{cta_start:.2f})'"
        )
    else:
        title_fontsize = None

    if caption_chunks:
        style_parts = [
            "FontSize=62",
            "PrimaryColour=&H00FFFFFF",
            "OutlineColour=&H00000000",
            "BackColour=&H80000000",
            "BorderStyle=1",
            "Outline=3",
            "Shadow=1",
            "Bold=1",
            "Alignment=2",
            "MarginV=560",
            "MarginL=60",
            "MarginR=60",
        ]
        if font_family:
            style_parts.insert(0, f"FontName={font_family}")
        force_style = ",".join(style_parts)
        filters.append(
            f"subtitles='{escape_subtitles_path(srt_path)}':force_style='{force_style}'"
        )

    if filters:
        vf = ",".join(filters)
        run(["ffmpeg", "-y", "-i", str(with_narration), "-vf", vf,
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-c:a", "copy", str(final)])
    else:
        run(["ffmpeg", "-y", "-i", str(with_narration), "-c", "copy", str(final)])

    print("SHORT:", final)
    print("Short duration:", duration(final))
    print("Captions:", len(caption_chunks), "cues" if caption_chunks else "(none)")
    return final


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
