import os
import json
import time
import random
import argparse

from google import genai
from google.genai import types

from .utils import WORK, save_json, ensure_dirs


MODEL = "gemini-3.6-flash"

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

NARRATION VOICE — EXPLAINING STYLE:
- Write as a knowledgeable narrator explaining something fascinating to a
  curious friend, not as a formal reporter or a dry textbook.
- Use plain, conversational words over technical or corporate ones
  ("gets shredded", not "undergoes mechanical decommissioning").
- Address the viewer directly ("you") where natural, especially when
  connecting a stage of the process back to something they've done.
- Explain the "why" behind a step, not just the "what" — a fact lands
  better when the viewer understands the reason it happens.
- Use short, punchy sentences to land a reveal, then a slightly longer
  sentence to unpack it. Vary rhythm — avoid a string of same-length
  sentences.

ENGAGEMENT — MAKE IT INTERESTING:
- Every scene should either answer a question the viewer is already
  asking themselves, or plant a new one to pull them into the next scene.
- End most scenes on a small hook, twist, or open thread rather than a
  neat, closed statement — something the next scene will resolve.
- Use concrete, relatable comparisons to make scale or numbers feel real
  (e.g. relate a volume to something everyday-sized) instead of stating
  a bare figure.
- Highlight the counterintuitive part of each stage — what most people
  would assume happens versus what actually happens — this contrast is
  the engine of the episode, not just the opening hook.
- Give at least one scene a genuine "wait, what?" moment: a detail that
  reframes what the viewer thought they understood so far.

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


def generate_with_retry(client, prompt, attempts=6):

    for attempt in range(1, attempts + 1):

        try:

            print(
                f"Gemini request attempt "
                f"{attempt}/{attempts} using {MODEL}"
            )

            response = client.models.generate_content(
                model=MODEL,
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

            print(
                f"Gemini request failed on attempt "
                f"{attempt}: {error_text}"
            )

            # Retry transient server/capacity errors.
            if (
                "503" not in error_text
                and "UNAVAILABLE" not in error_text
                and "429" not in error_text
                and "RESOURCE_EXHAUSTED" not in error_text
                and "500" not in error_text
                and "INTERNAL" not in error_text
            ):
                raise

            if attempt == attempts:
                raise

            # Exponential backoff + jitter.
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


def main(topic):

    ensure_dirs()

    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        raise SystemExit(
            "Missing GEMINI_API_KEY"
        )

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