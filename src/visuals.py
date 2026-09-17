import os, json, time, random
import requests
from pathlib import Path

from google import genai
from google.genai import types

from .utils import WORK, ensure_dirs, save_json

PEXELS_BASE = "https://api.pexels.com/v1"
PIXABAY_BASE = "https://pixabay.com/api/"
MODEL = "gemini-3.6-flash"

CLIPS_PER_SCENE = 6       # how many clips to actually download per scene
CANDIDATE_POOL = 30       # keyword-matched candidates to show Gemini for relevance judging
PHOTO_FALLBACK_POOL = 16  # smaller pool used only if zero videos clear the bar


# ---------------------------------------------------------------------------
# Pexels
# ---------------------------------------------------------------------------

def search_pexels_videos(q, key):
    h = {"Authorization": key}
    r = requests.get(f"{PEXELS_BASE}/videos/search", headers=h, params={
        "query": q, "per_page": 40, "orientation": "landscape", "size": "medium"
    }, timeout=30)
    r.raise_for_status()
    return r.json().get("videos", [])


def search_pexels_photos(q, key):
    h = {"Authorization": key}
    r = requests.get(f"{PEXELS_BASE}/search", headers=h, params={
        "query": q, "per_page": 40, "orientation": "landscape"
    }, timeout=30)
    r.raise_for_status()
    return r.json().get("photos", [])


def pexels_best_video_file(v):
    files = [x for x in v.get("video_files", []) if x.get("width", 0) >= 1280]
    files = sorted(files, key=lambda x: (x.get("width", 0), x.get("height", 0)), reverse=True)
    return files[0] if files else None


# ---------------------------------------------------------------------------
# Pixabay -- a second free, keyless-quota source. Doubles real candidate
# supply per query, which is what actually fixes both problems: Gemini gets
# more genuine matches to choose from (fewer forced falls into "nothing
# relevant"), and the bigger pool per episode means render.py has to repeat
# clips less often.
# ---------------------------------------------------------------------------

def search_pixabay_videos(q, key):
    r = requests.get(f"{PIXABAY_BASE}videos/", params={
        "key": key, "q": q, "per_page": 50, "safesearch": "true"
    }, timeout=30)
    r.raise_for_status()
    return r.json().get("hits", [])


def search_pixabay_photos(q, key):
    r = requests.get(PIXABAY_BASE, params={
        "key": key, "q": q, "image_type": "photo", "per_page": 50, "safesearch": "true"
    }, timeout=30)
    r.raise_for_status()
    return r.json().get("hits", [])


def pixabay_best_video_file(hit):
    videos = hit.get("videos", {})
    for size in ("large", "medium", "small", "tiny"):
        f = videos.get(size)
        if f and f.get("url"):
            return f
    return None


def pixabay_video_thumb_url(hit):
    pic = hit.get("picture_id")
    return f"https://i.vimeocdn.com/video/{pic}_640x360.jpg" if pic else None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def fetch_bytes(url):
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


def gather_candidates(scene, pexels_key, pixabay_key, seen_ids, pool_size, media_kind):
    """
    Pull keyword-matched candidates from Pexels and (if configured) Pixabay
    across ALL of the scene's visual_queries, up to pool_size, deduped
    against seen_ids (namespaced per-source so ids never collide).
    Returns normalized candidate dicts:
      {source, id, download_url, thumb_bytes, query, page_url, author}
    """
    candidates = []

    for q in scene["visual_queries"]:
        if len(candidates) >= pool_size:
            break

        # Pexels
        try:
            items = search_pexels_videos(q, pexels_key) if media_kind == "video" else search_pexels_photos(q, pexels_key)
        except Exception as e:
            print(f"Pexels {media_kind} search failed for '{q}': {e}")
            items = []
        for it in items:
            if len(candidates) >= pool_size:
                break
            uid = f"pexels:{it.get('id')}"
            if uid in seen_ids:
                continue
            if media_kind == "video":
                f = pexels_best_video_file(it)
                thumb_url = it.get("image")
                download_url = f["link"] if f else None
                page_url = it.get("url")
                author = it.get("user", {}).get("name", "")
            else:
                download_url = it.get("src", {}).get("large2x") or it.get("src", {}).get("large")
                thumb_url = it.get("src", {}).get("medium") or download_url
                page_url = it.get("url")
                author = it.get("photographer", "")
            if not download_url or not thumb_url:
                continue
            try:
                thumb_bytes = fetch_bytes(thumb_url)
            except Exception as e:
                print(f"Could not fetch thumbnail (pexels): {e}")
                continue
            candidates.append({
                "source": "pexels", "id": uid, "download_url": download_url,
                "thumb_bytes": thumb_bytes, "query": q, "page_url": page_url, "author": author
            })

        # Pixabay (skipped cleanly if no key configured)
        if pixabay_key:
            try:
                items = search_pixabay_videos(q, pixabay_key) if media_kind == "video" else search_pixabay_photos(q, pixabay_key)
            except Exception as e:
                print(f"Pixabay {media_kind} search failed for '{q}': {e}")
                items = []
            for it in items:
                if len(candidates) >= pool_size:
                    break
                uid = f"pixabay:{it.get('id')}"
                if uid in seen_ids:
                    continue
                if media_kind == "video":
                    f = pixabay_best_video_file(it)
                    download_url = f["url"] if f else None
                    thumb_url = pixabay_video_thumb_url(it)
                else:
                    download_url = it.get("largeImageURL") or it.get("webformatURL")
                    thumb_url = it.get("webformatURL") or download_url
                page_url = it.get("pageURL")
                author = it.get("user", "")
                if not download_url or not thumb_url:
                    continue
                try:
                    thumb_bytes = fetch_bytes(thumb_url)
                except Exception as e:
                    print(f"Could not fetch thumbnail (pixabay): {e}")
                    continue
                candidates.append({
                    "source": "pixabay", "id": uid, "download_url": download_url,
                    "thumb_bytes": thumb_bytes, "query": q, "page_url": page_url, "author": author
                })

    return candidates


def judge_relevance(client, topic, scene, candidates, attempts=4):
    """
    Returns a list of candidate indices Gemini considers genuinely relevant,
    best match first. An empty list means "none of these are actually
    relevant" -- callers must respect that instead of grabbing something
    anyway (that silent override was the original bug).
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
                # Gemini itself kept erroring (not "found nothing relevant" --
                # actually failing). Fall back to keyword order so a transient
                # API problem doesn't block the whole build.
                print("Gemini relevance check kept erroring -- falling back to keyword order for this scene.")
                return list(range(len(candidates)))
            time.sleep(min(20, (2 ** (attempt - 1)) * 3) + random.uniform(0, 2))


def main():
    ensure_dirs()
    pexels_key = os.environ.get("PEXELS_API_KEY")
    if not pexels_key:
        raise SystemExit("Missing PEXELS_API_KEY")

    pixabay_key = os.environ.get("PIXABAY_API_KEY")
    if not pixabay_key:
        print("PIXABAY_API_KEY not set -- running on Pexels only. Setting it (still free) "
              "widens the candidate pool and reduces both irrelevant fallbacks and clip repeats.")

    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        raise SystemExit("Missing GEMINI_API_KEY")

    client = genai.Client(api_key=gemini_key)

    ep = json.loads((WORK / "episode.json").read_text())
    topic = ep.get("working_title", "")
    media = WORK / "media"
    media.mkdir(exist_ok=True)
    manifest = []
    seen_ids = set()  # namespaced per-source, e.g. "pexels:123", "pixabay:456"

    for scene in ep["scenes"]:
        found = []  # list of (candidate, media_kind, relevance_checked)

        # Step 1: video candidates across Pexels + Pixabay, judged by Gemini.
        video_candidates = gather_candidates(scene, pexels_key, pixabay_key, seen_ids, CANDIDATE_POOL, "video")
        if video_candidates:
            order = judge_relevance(client, topic, scene, video_candidates)
            for idx in order:
                if len(found) >= CLIPS_PER_SCENE:
                    break
                c = video_candidates[idx]
                found.append((c, "video", True))
                seen_ids.add(c["id"])

        # Step 2: if nothing relevant came back from video search, try
        # photos -- through the SAME relevance judge, not blindly. This is
        # the actual fix for "garbage dump / random warehouse" clips: the
        # old code took search_photos(...)[0] with no relevance check at all
        # whenever video search came up empty.
        if not found:
            photo_candidates = gather_candidates(scene, pexels_key, pixabay_key, seen_ids, PHOTO_FALLBACK_POOL, "photo")
            if photo_candidates:
                order = judge_relevance(client, topic, scene, photo_candidates)
                for idx in order:
                    if len(found) >= 1:  # one relevant photo is enough for this scene
                        break
                    c = photo_candidates[idx]
                    found.append((c, "photo", True))
                    seen_ids.add(c["id"])
        else:
            photo_candidates = []

        # Step 3: absolute last resort -- nothing cleared the relevance bar
        # in either videos or photos. Rather than silently injecting an
        # unrelated clip, take the single best keyword match we have (video
        # preferred) and flag it clearly (relevance_checked=False) so it's
        # easy to spot in media_manifest.json and swap by hand if needed.
        if not found:
            fallback_pool = video_candidates or photo_candidates
            fallback_kind = "video" if video_candidates else "photo"
            if fallback_pool:
                c = fallback_pool[0]
                print(f"WARNING: scene {scene['id']} -- no candidate cleared the relevance "
                      f"check. Using best keyword match as a flagged fallback "
                      f"({c['source']}, query='{c['query']}'). Check media_manifest.json.")
                found.append((c, fallback_kind, False))
                seen_ids.add(c["id"])
            else:
                raise RuntimeError(f"No visual candidates at all for scene {scene['id']} -- "
                                    f"check your visual_queries / API keys.")

        for idx, (c, kind, checked) in enumerate(found):
            ext = "mp4" if kind == "video" else "jpg"
            out = media / f"scene_{scene['id']:03d}_{idx}.{ext}"
            with requests.get(c["download_url"], stream=True, timeout=60) as rr:
                rr.raise_for_status()
                with open(out, "wb") as fh:
                    for chunk in rr.iter_content(1024 * 1024):
                        if chunk:
                            fh.write(chunk)
            manifest.append({
                "scene_id": scene["id"], "type": kind, "query": c["query"],
                "file": str(out), "source": c["source"], "source_url": c["page_url"],
                "author": c["author"], "relevance_checked": checked
            })

    save_json(WORK / "media_manifest.json", manifest)
    print("Downloaded", len(manifest), "visuals")


if __name__ == "__main__":
    main()
