import argparse, subprocess, sys
from .utils import ROOT

def step(module, args):
    cmd=[sys.executable,"-m",module]+args
    print("\n==>", " ".join(cmd))
    subprocess.run(cmd,cwd=ROOT,check=True)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--topic",required=True)
    a=ap.parse_args()
    step("src.research",["--topic",a.topic])
    step("src.visuals",[])
    step("src.voice",[])
    step("src.render",[])
    step("src.short",[])
    step("src.qa",[])
    print("\nDONE. Check output/episode.mp4 and output/short.mp4")

if __name__=="__main__":
    main()
