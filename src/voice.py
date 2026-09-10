import json, asyncio, argparse, os
import edge_tts
from .utils import WORK, ensure_dirs

async def make(text, out, voice, rate):
    communicate=edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(str(out))

def main():
    ensure_dirs()
    settings=json.loads(open("config/settings.json", encoding="utf-8").read())
    ep=json.loads((WORK/"episode.json").read_text())
    text="\n\n".join(s["narration"] for s in ep["scenes"])
    out=WORK/"narration.mp3"
    asyncio.run(make(text,out,settings["tts_voice"],settings["tts_rate"]))
    print(out)

if __name__=="__main__":
    main()
