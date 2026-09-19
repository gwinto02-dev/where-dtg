import json, argparse, os
from .utils import WORK, ensure_dirs, synth_speech

def main():
    ensure_dirs()
    settings=json.loads(open("config/settings.json", encoding="utf-8").read())
    ep=json.loads((WORK/"episode.json").read_text())
    text="\n\n".join(s["narration"] for s in ep["scenes"])
    out=WORK/"narration.mp3"
    synth_speech(text, out, settings["tts_voice"], settings["tts_rate"])
    print(out)

if __name__=="__main__":
    main()
