import os
import json
import time
import random
import argparse

from google import genai
from google.genai import types

from .utils import WORK, save_json, ensure_dirs

# Conservative words-per-minute estimate for edge-tts narration at a
# slightly-slowed rate. Used only to make sure Gemini's script is long
# enough to clear the QA minimum-duration check -- deliberately a bit
# low/conservative so we ask for MORE words than strictly needed rather
# than risk falling short again.
WORDS_PER_MINUTE = 140

EXPAND_PROMPT = """The documentary script below is shorter than required.

Current total narration word count: {current_words}
Required minimum word count: {min_words}

Here is the current episode JSON:

{episode_json}

Revise it to reach AT LEAST {min_words} spoken words total across all
scenes, while:
- Keeping the existing "working_title", "hook", and "short_script" fields
  unchanged unless a small tweak is needed for consistency.
- Keeping the same factual claims and tone -- do not invent new
  statistics, companies, or quotes.
- Expanding depth/detail on the existing scenes and/or adding additional
  scenes (each scene still 120-170 words) to reach the target -- whichever
  serves the story better.
- Following the exact same JSON schema as the input.

Return ONLY the revised, valid JSON object -- nothing else.
"""


# All models currently on Gemini's free tier (Pro models are paid-only as of
# 2026), ordered from highest known daily quota to lowest so the script
# always tries the roomiest bucket first.
MODEL_FALLBACKS = [
    # Flash-Lite family — ~500 free requests/day
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash-lite-preview",
    "gemini-2.0-flash-lite",
    # Standard Flash family — ~20 free requests/day, tried last
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-preview",
    "gemini-2.0-flash",
]

PROMPT = """You are the research and documentary producer for a YouTube channel called
"Where Does It Go?".

The episode follows the hidden journey of an ordinary object or process.

Topic: {topic}

Create a factual, engaging 6–12 minute documentary plan.

HOOK STYLE — COLD OPEN (applies to "hook" and scene 1's narration):
- Open inside a single, concrete, real moment tied directly to {topic} —
  a specific person, place, or action, happening right now. No framing,
  no throat-clearing, no "Somewhere in..." or "Imagine..." lead-ins.
  Just the scene, stated plainly, as if it's already in progress.
- Do NOT open with a question, a statistic, or a general statement about
  the topic. Save the big-picture framing for after the scene has landed.
- Delay the pivot to the viewer ("you", "your [object]") for 2–3
  sentences after the opening scene — let the scene breathe first, then
  connect it back to something the viewer personally owns or does.
- The scene must be something visual_queries can actually depict with
  real stock footage — avoid abstract or unfilmable moments.
- End the hook on a concrete promise of what the episode will follow,
  not a vague tease.

SHORT-FORM HOOK (applies to "short_script" only):
- Write a separate, self-contained 20–40 word script for a 15–25 second
  YouTube Shorts / vertical teaser about the SAME topic.
- It must work as a stand-alone curiosity hook: open on the same kind of
  concrete, real moment as the main hook, but punchier and faster —
  no setup, no context, straight into the scene.
- Build to a specific, concrete question or reveal-in-progress about
  {topic} — then STOP just before the answer. The viewer must feel like
  they're missing the actual explanation.
- End with a short, natural line that points at the full video without
  sounding like an ad — e.g. referencing "the full story", "how it
  actually happens", or similar. Do not say "link in bio" or "swipe up"
  or any platform-specific mechanic — keep it timeless.
- Do NOT reuse the main hook's exact wording. It should feel like its own
  tight, vertical-video moment, not a trimmed copy of the long hook.

IMPORTANT:
- Do not invent statistics, companies, locations, quotes, or claims.
- Clearly flag claims that require external verification.
- Make the narration sound natural and human.
- Avoid generic AI-style wording.
- Build curiosity throughout the episode.
- Each scene must have specific visual requirements.
- Prefer real-world footage opportunities.
- Avoid repetitive explanations.
- visual_queries must be concrete, literal stock-footage search terms tied
  directly to the object/topic in {topic} — e.g. "smartphone repair
  technician workbench", "circuit board close up", "phone disassembly".
  Do NOT use abstract or thematic terms like "environmental impact" or
  "consumerism" — vague queries return generic, unrelated stock footage
  (e.g. quarries, landfills, stock imagery of "stone" or "garbage") that
  has nothing to do with the actual object in this episode.

Return ONLY valid JSON:

{{
  "working_title": "...",
  "hook": "...",
  "short_script": "20-40 word standalone vertical-video hook, ending on a cliffhanger that points to the full video",
  "summary": "...",
  "scenes": [
    {{
      "id": 1,
      "narration": "120-170 words maximum",
      "visual_queries": [
        "specific footage search query",
        "another specific footage query",
        "another specific footage query"
      ],
      "visual_type": "video",
      "on_screen_text": "",
      "fact_notes": [
        "claims that need verification"
      ]
    }}
  ]
}}

Target approximately 1100–1350 spoken words total.
"""


def clean_json(text):
    text = text.strip()

    if text.startswith("```"):
        parts = text.split("```")

        if len(parts) >= 3:
            text = parts[1]

        text = text.replace("json", "", 1).strip()

    return text


def generate_with_retry(client, prompt, models=None, attempts_per_model=3):

    models = models or MODEL_FALLBACKS
    last_error = None

    for model_index, model in enumerate(models):

        for attempt in range(1, attempts_per_model + 1):

            try:

                print(
                    f"Gemini request attempt "
                    f"{attempt}/{attempts_per_model} using {model}"
                )

                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=1.0
                    )
                )

                if not response.text:
                    raise RuntimeError(
                        "Gemini returned an empty response."
                    )

                return response.text

            except Exception as e:

                error_text = str(e)
                last_error = e

                print(
                    f"Gemini request failed on attempt "
                    f"{attempt} ({model}): {error_text}"
                )

                is_daily_quota = (
                    "RESOURCE_EXHAUSTED" in error_text
                    and "PerDay" in error_text
                )

                is_transient = (
                    "503" in error_text
                    or "UNAVAILABLE" in error_text
                    or "429" in error_text
                    or "RESOURCE_EXHAUSTED" in error_text
                    or "500" in error_text
                    or "INTERNAL" in error_text
                )

                if not is_transient:
                    raise

                # Daily quota exhausted for this model — no point retrying
                # it, move straight to the next model in the fallback list.
                if is_daily_quota:
                    print(
                        f"Daily quota exhausted for {model}. "
                        f"Falling back to next model..."
                    )
                    break

                if attempt == attempts_per_model:
                    # Out of retries for this model — try the next one.
                    break

                # Exponential backoff + jitter for transient errors.
                delay = min(
                    60,
                    (2 ** (attempt - 1)) * 5
                )

                jitter = random.uniform(0, 3)

                wait_time = delay + jitter

                print(
                    f"Transient Gemini error. "
                    f"Waiting {wait_time:.1f} seconds before retry..."
                )

                time.sleep(wait_time)

    # All models exhausted.
    raise last_error


def total_words(data):
    return sum(len(s.get("narration", "").split()) for s in data.get("scenes", []))


def expand_if_short(client, data, min_minutes, max_expand_attempts=2):
    min_words = int(min_minutes * WORDS_PER_MINUTE)

    for attempt in range(1, max_expand_attempts + 1):
        current = total_words(data)

        if current >= min_words:
            return data

        print(
            f"Script is short: {current} words (need at least {min_words} "
            f"for a {min_minutes}-minute minimum). Requesting an expansion "
            f"(attempt {attempt}/{max_expand_attempts})..."
        )

        expand_prompt = EXPAND_PROMPT.format(
            current_words=current,
            min_words=min_words,
            episode_json=json.dumps(data, ensure_ascii=False, indent=2),
        )

        try:
            text = generate_with_retry(client, expand_prompt, attempts_per_model=2)
            text = clean_json(text)
            expanded = json.loads(text)

            if "scenes" in expanded and expanded["scenes"]:
                data = expanded
            else:
                print("Expansion response was missing scenes -- keeping previous version.")
                break

        except Exception as e:
            print(f"Expansion attempt failed, keeping current script: {e}")
            break

    final_words = total_words(data)
    if final_words < min_words:
        print(
            f"WARNING: script is still only {final_words} words after "
            f"expansion attempts (target {min_words}+). The rendered video "
            f"may come in under your configured min_minutes -- consider "
            f"lowering min_minutes in config/settings.json, or re-running."
        )

    return data


def main(topic):

    ensure_dirs()

    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        raise SystemExit(
            "Missing GEMINI_API_KEY"
        )

    settings = json.loads(open("config/settings.json", encoding="utf-8").read())
    min_minutes = settings.get("min_minutes", 6)

    client = genai.Client(
        api_key=api_key
    )

    prompt = PROMPT.format(
        topic=topic
    )

    text = generate_with_retry(
        client,
        prompt
    )

    text = clean_json(text)

    try:

        data = json.loads(text)

    except json.JSONDecodeError as e:

        print("Gemini returned invalid JSON.")
        print("Raw response:")
        print(text)

        raise RuntimeError(
            f"Could not parse Gemini JSON: {e}"
        )

    if "scenes" not in data:
        raise RuntimeError(
            "Gemini response does not contain scenes."
        )

    if not data["scenes"]:
        raise RuntimeError(
            "Gemini returned zero scenes."
        )

    if not (data.get("short_script") or "").strip():
        print("Gemini did not return a short_script -- falling back to the main hook for the short video.")
        data["short_script"] = data.get("hook", "")

    data = expand_if_short(client, data, min_minutes)

    save_json(
        WORK / "episode.json",
        data
    )

    print()
    print("===================================")
    print("RESEARCH COMPLETE")
    print("===================================")
    print("Title:", data.get("working_title"))
    print("Scenes:", len(data["scenes"]))
    print("Total narration words:", total_words(data))
    print("Output:", WORK / "episode.json")
    print("===================================")


if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--topic",
        required=True
    )

    args = parser.parse_args()

    main(args.topic)