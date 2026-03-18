import asyncio
import json
import re
from google.genai import types
from model_III.scene_summarizer import client, MODEL_NAME, SEMAPHORE


# =========================================================
# JSON SAFETY HELPERS
# =========================================================

def repair_common_json_issues(text: str) -> str:
    text = text.replace("```json", "").replace("```", "")
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)
    return text.strip()


def safe_json_parse(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
    return {}


# =========================================================
# BEAT WRITER AGENT (NEW CORE)
# =========================================================

async def write_beat_agent(
    current_beat: dict,
    previous_beat: dict | None,
    next_beat: dict | None,
    global_summary: dict,
    act_level_plan: dict | None,
    reference_scenes: list | None = None,
) -> dict:

    async with SEMAPHORE:

        def compact_json(d):
            return json.dumps(d, separators=(",", ":")) if d else "None"

        system_instruction = (
            "You are a professional feature film screenwriter. "
            "You write original screenplay content from story beats. "
            "You DO NOT reference scene IDs or previous script structure. "
            "You produce clean, industry-standard Fountain format. "
            "No placeholders. No broken lines. No meta text. "
            "Return ONLY valid JSON."
        )

        user_prompt = f"""
================ STORY CORE ================

GLOBAL SUMMARY:
{compact_json(global_summary)}

ACT STRATEGY:
{compact_json(act_level_plan)}

=============== CURRENT BEAT ===============

CURRENT BEAT:
{compact_json(current_beat)}

PREVIOUS BEAT:
{compact_json(previous_beat)}

NEXT BEAT:
{compact_json(next_beat)}

=============== OPTIONAL REFERENCE ===============

REFERENCE SCENES (for tone only, DO NOT reuse structure):
{compact_json(reference_scenes)}

=============== TASK ===============

Write a screenplay segment that fulfills THIS BEAT.

Requirements:

1. Fully express the beat dramatically (not summary).
2. Create natural scene boundaries (you decide scene breaks).
3. Maintain continuity with previous beat.
4. Set up next beat if applicable.
5. Strong character action + emotion (no exposition dumps).
6. NO scene IDs like S001 anywhere.
7. NO broken transitions like "WITH" or fragments.
8. NO backend leakage like "(S0261)".

=============== FORMAT RULES ===============

- Use proper screenplay format:
  INT./EXT. LOCATION – TIME
  Character names in CAPS
  Dialogue properly formatted
  Action lines clean and readable

=============== OUTPUT ===============

Return ONLY JSON:

{{
  "screenplay_segment": "Fountain formatted text",
  "beat_summary": {{
    "what_happens": "",
    "character_progress": "",
    "conflict": "",
    "setup_payoff": ""
  }},
  "continuity_notes": {{
    "links_to_previous": "",
    "sets_up_next": ""
  }}
}}
"""

        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=MODEL_NAME,
                contents=[
                    types.Content(
                        role="user",
                        parts=[types.Part(text=user_prompt)]
                    )
                ],
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.7,
                    response_mime_type="application/json"
                ),
            )

            raw_text = repair_common_json_issues(response.text)
            parsed = safe_json_parse(raw_text)

            if not isinstance(parsed, dict) or "screenplay_segment" not in parsed:
                raise ValueError("Invalid JSON structure")

            return parsed

        except Exception as e:
            print("Beat writer error:", e)

            return {
                "screenplay_segment": "",
                "beat_summary": current_beat,
                "continuity_notes": {}
            }