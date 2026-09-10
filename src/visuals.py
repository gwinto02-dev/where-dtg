import os, json, time, random
import requests
from pathlib import Path

from google import genai
from google.genai import types

from .utils import WORK, ensure_dirs, save_json

BASE = "https://api.pexels.com/v1"
MODEL = "gemini-3.6-flash"

CLIPS_PER_SCENE = 3      # how many clips to actually download per scene
CANDIDATE_POOL = 12      # how many keyword-matched candidates to show Gemini for relevance judging


def search_videos(q, key):
    h = {"Authorization": key}
    r = requests.get(f"{BASE}/videos/search", headers=h, params={
        "query": q, "per_page": 8, "orientation": "landscape", "size": "medium"
    }, timeout=30)
    r.raise_for_status()
    return r.json().get("videos", [])


def search_photos(q, key):
    h = {"Authorization": key}
    r = requests.get(f"{BASE}/search", headers=h, params={
        "query": q, "per_page": 8, "orientation": "landscape"
    }, timeout=30)
    r.raise_for_status()
    return r.json().get("photos", [])


def best_video(v):
    files = [x for x in v.get("video_files", []) if x.get("width", 0) >= 1280]
    return sorted(files, key=lambda x: (x.get("width", 0), x.get("height", 0)), reverse=True)[0] if files else None


def fetch_thumbnail_bytes(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.content


def clean_json(text):
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
        text = text.replace("json", "", 1).strip()
    return text


def judge_relevance(client, topic, scene, candidates, attempts=4):
    """
    candidates: list of dicts {index, thumb_bytes, query}
    Returns a list of candidate indices Gemini considers genuinely relevant,
    ordered by how well they match (best first).
    """
    parts = [types.Part.from_text(text=(
        f'Episode topic: "{topic}"\n'
        f'Scene narration: "{scene["narration"]}"\n\n'
        f"Below are {len(candidates)} candidate stock-footage thumbnails, numbered 0 to "
        f"{len(candidates) - 1} in the order they appear. Each was returned by a keyword "
        f"search and may or may not actually depict the episode's subject.\n\n"
        f"Look at each thumbnail and decide whether it plausibly shows something "
        f"relevant to the topic and this scene's narration (the actual object/process, "
        f"or a directly related close-up/action shot) -- not just a loose keyword match "
        f"like a generic warehouse, landfill, or unrelated hands-doing-something shot.\n\n"
        f"Return ONLY valid JSON, no other text:\n"
        f'{{"relevant": [list of candidate index numbers, best match first]}}\n'
        f'If none are relevant, return {{"relevant": []}}.'
    ))]

    for i, c in enumerate(candidates):
        parts.append(types.Part.from_text(text=f"Candidate {i} (query: \"{c['query']}\"):"))
        parts.append(types.Part.from_bytes(data=c["thumb_bytes"], mime_type="image/jpeg"))

    for attempt in range(1, attempts + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=[types.Content(role="user", parts=parts)],
                config=types.GenerateContentConfig(temperature=0.2)
            )
            text = clean_json(response.text or "")
            data = json.loads(text)
            relevant = data.get("relevant", [])
            return [i for i in relevant if isinstance(i, int) and 0 <= i < len(candidates)]
        except Exception as e:
            error_text = str(e)
            print(f"Relevance check failed on attempt {attempt}: {error_text}")
            if attempt == attempts:
                # If Gemini vision keeps failing, fall back to trusting the
                # keyword search order rather than blocking the whole build.
                print("Falling back to keyword order for this scene.")
                return list(range(len(candidates)))
            time.sleep(min(20, (2 ** (attempt - 1)) * 3) + random.uniform(0, 2))


def main():
    ensure_dirs()
    pexels_key = os.environ.get("PEXELS_API_KEY")
    if not pexels_key:
        raise SystemExit("Missing PEXELS_API_KEY")

    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        raise SystemExit("Missing GEMINI_API_KEY")

    client = genai.Client(api_key=gemini_key)

    ep = json.loads((WORK / "episode.json").read_text())
    topic = ep.get("working_title", "")
    media = WORK / "media"
    media.mkdir(exist_ok=True)
    manifest = []
    seen_video_ids = set()  # avoid downloading the exact same Pexels clip twice

    for scene in ep["scenes"]:
        # Step 1: gather a keyword-matched candidate pool across all of the
        # scene's visual_queries (previously we downloaded straight off this
        # list -- that's how quarry/landfill/"scooping beans" clips got in,
        # since Pexels keyword search is fuzzy and tags by generic action
        # rather than by subject).
        candidates = []
        for q in scene["visual_queries"]:
            if len(candidates) >= CANDIDATE_POOL:
                break
            try:
                vids = search_videos(q, pexels_key)
            except Exception as e:
                print(f"Pexels search failed for query '{q}': {e}")
                continue
            for v in vids:
                if len(candidates) >= CANDIDATE_POOL:
                    break
                if v.get("id") in seen_video_ids:
                    continue
                f = best_video(v)
                thumb_url = v.get("image")
                if not f or not thumb_url:
                    continue
                try:
                    thumb_bytes = fetch_thumbnail_bytes(thumb_url)
                except Exception as e:
                    print(f"Could not fetch thumbnail for candidate: {e}")
                    continue
                candidates.append({
                    "video": v, "file_info": f, "query": q, "thumb_bytes": thumb_bytes
                })

        # Step 2: ask Gemini vision which candidates actually depict
        # something relevant to the topic/scene, ranked best-first.
        found = []
        if candidates:
            order = judge_relevance(client, topic, scene, candidates)
            for idx in order:
                if len(found) >= CLIPS_PER_SCENE:
                    break
                c = candidates[idx]
                vid_id = c["video"].get("id")
                found.append((c["video"], c["file_info"], c["query"]))
                seen_video_ids.add(vid_id)

        if found:
            for idx, (v, f, q) in enumerate(found):
                url = f["link"]
                out = media / f"scene_{scene['id']:03d}_{idx}.mp4"
                with requests.get(url, stream=True, timeout=60) as rr:
                    rr.raise_for_status()
                    with open(out, "wb") as fh:
                        for chunk in rr.iter_content(1024 * 1024):
                            if chunk:
                                fh.write(chunk)
                manifest.append({
                    "scene_id": scene["id"], "type": "video", "query": q,
                    "file": str(out), "pexels_url": v.get("url"),
                    "photographer": v.get("user", {}).get("name", ""),
                    "relevance_checked": True
                })
        else:
            # No video candidate cleared the relevance bar -- fall back to a
            # plain photo search rather than failing the whole build.
            photos = search_photos(scene["visual_queries"][0], pexels_key)
            if not photos:
                raise RuntimeError(f"No visual found for scene {scene['id']}")
            p = photos[0]
            url = p["src"]["large2x"]
            out = media / f"scene_{scene['id']:03d}.jpg"
            rr = requests.get(url, timeout=60)
            rr.raise_for_status()
            out.write_bytes(rr.content)
            manifest.append({
                "scene_id": scene["id"], "type": "photo", "query": scene["visual_queries"][0],
                "file": str(out), "pexels_url": p.get("url"),
                "photographer": p.get("photographer", ""),
                "relevance_checked": False
            })

    save_json(WORK / "media_manifest.json", manifest)
    print("Downloaded", len(manifest), "visuals")


if __name__ == "__main__":
    main()
