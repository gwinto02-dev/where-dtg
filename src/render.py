import json, subprocess, random
from pathlib import Path
from .utils import WORK, OUTPUT, ensure_dirs, run

def duration(p):
    x=subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration",
                               "-of","default=noprint_wrappers=1:nokey=1",str(p)],text=True)
    return float(x.strip())

class ClipScheduler:
    """
    Picks which manifest entry to use next without the old fixed
    round-robin (manifest[i % len(manifest)]), which played the same clips
    in the same order every lap -- with a small manifest that meant the
    same footage reappearing every 10-15 seconds like clockwork.

    Instead: shuffle the whole manifest into a "deck", hand out clips from
    the deck one at a time, and only reshuffle a fresh deck once every clip
    has been used once. That guarantees no clip repeats until everything
    else has had a turn, and the order is different each lap so repeats
    don't fall into a predictable pattern. It also avoids the same clip
    landing back-to-back across a reshuffle boundary.
    """
    def __init__(self, manifest, rng):
        self.manifest = manifest
        self.rng = rng
        self.deck = []
        self.last_index = None

    def _reshuffle(self):
        self.deck = list(range(len(self.manifest)))
        self.rng.shuffle(self.deck)
        # Avoid the same clip playing twice in a row across the boundary
        # between one exhausted deck and the freshly shuffled next one.
        if self.last_index is not None and len(self.deck) > 1 and self.deck[0] == self.last_index:
            swap_at = self.rng.randint(1, len(self.deck) - 1)
            self.deck[0], self.deck[swap_at] = self.deck[swap_at], self.deck[0]

    def next(self):
        if not self.deck:
            self._reshuffle()
        idx = self.deck.pop(0)
        self.last_index = idx
        return self.manifest[idx]

def main():
    ensure_dirs()
    settings=json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
    manifest=json.loads((WORK/"media_manifest.json").read_text())
    timings=json.loads((WORK/"scene_timings.json").read_text())
    narration=WORK/"narration.mp3"
    narration_dur=duration(narration)

    timing_sum=sum(t["duration"] for t in timings)
    if abs(timing_sum - narration_dur) > 0.5:
        print(f"WARNING: scene_timings.json total ({timing_sum:.2f}s) doesn't match "
              f"narration.mp3 duration ({narration_dur:.2f}s) -- concat may have dropped "
              f"or added time. Clip/narration sync could drift.")

    min_cut=float(settings.get("visual_change_min_seconds",2.5))
    max_cut=float(settings.get("visual_change_max_seconds",5.0))

    # Group clips by the scene they were actually fetched for. This is the
    # fix for clips not matching what the narration is saying: previously
    # every scene's clips were pooled together and shuffled across the
    # WHOLE episode with no relationship to timing, so a scene-3 clip could
    # easily play during scene 1's narration. Now each scene's time window
    # (from scene_timings.json, built from the real per-scene narration
    # audio) only draws from that scene's own manifest entries.
    by_scene={}
    for m in manifest:
        by_scene.setdefault(m["scene_id"], []).append(m)

    rng=random.Random(42)
    src_durations={}

    def src_duration(path):
        if path not in src_durations:
            src_durations[path]=duration(path)
        return src_durations[path]

    def render_cut(m, cut, out):
        if m["type"]=="video":
            sdur=src_duration(m["file"])
            if sdur>cut:
                ss=rng.uniform(0, sdur-cut)
                cmd=["ffmpeg","-y","-ss",f"{ss:.2f}","-i",m["file"],"-t",f"{cut:.2f}"]
            else:
                # Source is shorter than the cut we need -- loop it, but
                # still pick a random start point within the source so a
                # reused short clip doesn't always begin at frame 0.
                ss=rng.uniform(0, sdur) if sdur>0 else 0.0
                cmd=["ffmpeg","-y","-stream_loop","-1","-ss",f"{ss:.2f}","-i",m["file"],"-t",f"{cut:.2f}"]
            cmd += ["-vf",
                    "scale=1920:1080:force_original_aspect_ratio=decrease,"
                    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=25",
                    "-an","-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p",
                    "-fps_mode","cfr",str(out)]
            run(cmd)
        else:
            frames=max(1, round(cut*25))
            run(["ffmpeg","-y","-loop","1","-i",m["file"],"-t",f"{cut:.2f}","-vf",
                 "scale=1920:1080:force_original_aspect_ratio=decrease,"
                 "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
                 f"zoompan=z='min(zoom+0.0008,1.08)':d={frames}:s=1920x1080:fps=25",
                 "-an","-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p",
                 "-fps_mode","cfr",str(out)])

    clips=[]
    i=0

    for t in timings:
        scene_id=t["scene_id"]
        scene_target=t["duration"]
        pool=by_scene.get(scene_id)

        if not pool:
            # Shouldn't happen (visuals.py guarantees >=1 entry per scene),
            # but fall back to the full manifest rather than crash the
            # render if a scene somehow has zero clips.
            print(f"WARNING: no clips found for scene {scene_id} -- borrowing from the full pool.")
            pool=manifest

        scheduler=ClipScheduler(pool, rng)
        scene_total=0.0

        # Cover this scene's exact narration window with clips drawn only
        # from this scene's own pool, trimming the final cut so the scene's
        # visual runway lines up with its narration length instead of
        # drifting into the next scene's audio.
        while scene_total < scene_target:
            m=scheduler.next()
            remaining=scene_target - scene_total
            cut=rng.uniform(min_cut, max_cut)
            if remaining <= max_cut:
                cut=remaining if remaining > 0.15 else max(remaining, 0.15)
            out=WORK/f"clip_{i:03d}.mp4"
            render_cut(m, cut, out)
            clips.append(out)
            scene_total+=cut
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
