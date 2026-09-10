import json, subprocess, random
from pathlib import Path
from .utils import WORK, OUTPUT, ensure_dirs, run

def duration(p):
    x=subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration",
                               "-of","default=noprint_wrappers=1:nokey=1",str(p)],text=True)
    return float(x.strip())

def main():
    ensure_dirs()
    settings=json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
    manifest=json.loads((WORK/"media_manifest.json").read_text())
    narration=WORK/"narration.mp3"
    narration_dur=duration(narration)

    min_cut=float(settings.get("visual_change_min_seconds",2.5))
    max_cut=float(settings.get("visual_change_max_seconds",5.0))

    # V1 fix: previously each of the N scene clips was shown exactly once
    # for a fixed 5s, capping total video length at N*5s no matter how
    # long the narration was. With only 9 scenes that's ~45s max, so any
    # narration over ~45s got silently truncated by -shortest in the final
    # mux -- which is why QA kept failing on "video shorter than 6 minutes".
    #
    # Now we cycle back through the downloaded visuals, cutting each use to
    # a randomized length within [min_cut, max_cut] (per settings.json),
    # until the visual runway covers the full narration length. Reused
    # clips get a randomized start offset (instead of always replaying
    # from frame 0) so repeats look less identical.
    rng=random.Random(42)
    src_durations={}

    def src_duration(path):
        if path not in src_durations:
            src_durations[path]=duration(path)
        return src_durations[path]

    clips=[]
    total=0.0
    i=0
    while total < narration_dur + max_cut:
        m=manifest[i % len(manifest)]
        cut=rng.uniform(min_cut, max_cut)
        out=WORK/f"clip_{i:03d}.mp4"

        if m["type"]=="video":
            sdur=src_duration(m["file"])
            if sdur>cut:
                ss=rng.uniform(0, sdur-cut)
                cmd=["ffmpeg","-y","-ss",f"{ss:.2f}","-i",m["file"],"-t",f"{cut:.2f}"]
            else:
                # Source is shorter than the cut we need -- loop it, but
                # still pick a random start point within the source so a
                # reused short clip doesn't always begin at frame 0 (which
                # made repeats look identical when their cut length also
                # happened to land close together).
                ss=rng.uniform(0, sdur) if sdur>0 else 0.0
                cmd=["ffmpeg","-y","-stream_loop","-1","-ss",f"{ss:.2f}","-i",m["file"],"-t",f"{cut:.2f}"]
            cmd += ["-vf",
                    "scale=1920:-2:force_original_aspect_ratio=decrease,"
                    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=25",
                    "-an","-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p",
                    "-fps_mode","cfr",str(out)]
            run(cmd)
        else:
            frames=max(1, round(cut*25))
            run(["ffmpeg","-y","-loop","1","-i",m["file"],"-t",f"{cut:.2f}","-vf",
                 "scale=1920:-2:force_original_aspect_ratio=decrease,"
                 "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
                 f"zoompan=z='min(zoom+0.0008,1.08)':d={frames}:s=1920x1080:fps=25",
                 "-an","-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p",
                 "-fps_mode","cfr",str(out)])

        clips.append(out)
        total+=cut
        i+=1

    concat=WORK/"concat.txt"
    concat.write_text("".join(f"file '{p.resolve()}'\n" for p in clips))
    silent=WORK/"visuals.mp4"
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat),"-c","copy",str(silent)])
    final=OUTPUT/"episode.mp4"
    run(["ffmpeg","-y","-i",str(silent),"-i",str(narration),"-map","0:v:0","-map","1:a:0",
         "-shortest","-c:v","copy","-c:a","aac","-b:a","192k",str(final)])
    print("FINAL:", final)
    print("Duration:", duration(final))

if __name__=="__main__":
    main()
