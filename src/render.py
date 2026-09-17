import json, subprocess, random
from pathlib import Path
from .utils import WORK, OUTPUT, ensure_dirs, run

def duration(p):
    x=subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration",
                               "-of","default=noprint_wrappers=1:nokey=1",str(p)],text=True)
    return float(x.strip())

class ClipScheduler:
    """
    Uses every downloaded clip AT MOST ONCE per episode. It shuffles the
    manifest into a "deck" and hands out clips from it one at a time,
    stretching each clip's on-screen duration (see stretch_min/stretch_max
    in main()) so the available unique footage is made to cover the whole
    narration length without needing to repeat anything.

    Repeating a clip only happens if the deck runs out AND stretching
    still isn't enough to cover the narration -- i.e. there truly isn't
    enough unique footage for the episode length. That case reshuffles and
    starts reusing clips, but prints a clear warning so it's obvious in
    the logs (fix by raising CLIPS_PER_SCENE / CANDIDATE_POOL in
    visuals.py, or adding PIXABAY_API_KEY, rather than silently repeating).
    """
    def __init__(self, manifest, rng):
        self.manifest = manifest
        self.rng = rng
        self.deck = []
        self.last_index = None
        self.exhausted_once = False
        self.repeat_warned = False
        self._reshuffle()

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
            # Every unique clip has now been used once. Only reshuffle
            # (i.e. start repeating) if we still need more footage -- the
            # caller stops calling next() once the narration is covered,
            # so reaching this point means stretching wasn't enough either.
            if not self.repeat_warned:
                print("WARNING: ran out of unique clips even after stretching "
                      "clip durations -- reusing footage for the remainder of "
                      "the episode. Raise CLIPS_PER_SCENE / CANDIDATE_POOL in "
                      "visuals.py, or set PIXABAY_API_KEY, to avoid this.")
                self.repeat_warned = True
            self.exhausted_once = True
            self._reshuffle()
        idx = self.deck.pop(0)
        self.last_index = idx
        return self.manifest[idx]

    def unique_remaining(self):
        """How many not-yet-repeated clips are left in the current deck."""
        return 0 if self.exhausted_once else len(self.deck)

def main():
    ensure_dirs()
    settings=json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
    manifest=json.loads((WORK/"media_manifest.json").read_text())
    narration=WORK/"narration.mp3"
    narration_dur=duration(narration)

    min_cut=float(settings.get("visual_change_min_seconds",2.5))
    max_cut=float(settings.get("visual_change_max_seconds",5.0))

    # V1 fix (kept): previously each of the N scene clips was shown exactly
    # once for a fixed 5s, capping total video length at N*5s no matter how
    # long the narration was.
    #
    # V3 fix: each downloaded clip is now used AT MOST ONCE per episode.
    # Instead of cycling back through the manifest, we stretch every
    # clip's on-screen duration (below) so the unique footage we actually
    # downloaded covers the full narration length. Repeats only happen as
    # a last resort if there truly isn't enough unique footage even after
    # stretching -- see ClipScheduler.
    rng=random.Random(42)

    # Stretch per-clip duration so the unique clips we actually downloaded
    # can cover the full narration length without repeating any of them.
    # Only fall back to the configured min/max (and eventually to reusing
    # clips, inside ClipScheduler) if there genuinely isn't enough unique
    # footage even at the stretch ceiling below.
    STRETCH_CEILING = 12.0
    unique_total = len(manifest)
    if unique_total == 0:
        raise RuntimeError("media_manifest.json is empty -- nothing to render.")

    target_total = narration_dur + max_cut
    avg_needed = target_total / unique_total
    if avg_needed > max_cut:
        stretched_max = min(STRETCH_CEILING, avg_needed * 1.15)
        stretched_min = min(stretched_max, max(min_cut, avg_needed * 0.85))
        print(f"{unique_total} unique clips for a {narration_dur:.0f}s narration -- "
              f"stretching per-clip duration to {stretched_min:.1f}-{stretched_max:.1f}s "
              f"so no clip has to repeat.")
        min_cut, max_cut = stretched_min, stretched_max

    scheduler=ClipScheduler(manifest, rng)
    src_durations={}

    def src_duration(path):
        if path not in src_durations:
            src_durations[path]=duration(path)
        return src_durations[path]

    clips=[]
    total=0.0
    i=0
    while total < narration_dur + max_cut:
        m=scheduler.next()
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
