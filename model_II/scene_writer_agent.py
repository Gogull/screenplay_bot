import asyncio
import json
import re
from google.genai import types
from model_II.scene_summarizer import client, MODEL_NAME, SEMAPHORE


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
# WRITER AGENT – REWRITE SCENE
# =========================================================

async def rewrite_scene_agent(
    scene_full_text: str,
    previous_summary: dict | None,
    current_summary: dict,
    next_summary: dict | None,
    global_summary: dict,
    act_level_plan: dict | None,
    scene_level_plan: dict | None,
) -> dict:

    async with SEMAPHORE:

        def compact_json(d):
            return json.dumps(d, separators=(",", ":")) if d else "None"

        system_instruction = (
            "You are a professional feature film screenwriter and story editor. "
            "You rewrite scenes with structural precision, emotional continuity, "
            "and clean Fountain formatting. "
            "You strictly return valid JSON only. No markdown. No commentary."
        )

        user_prompt = f"""
================ STORY CONTEXT ================

GLOBAL STORY SUMMARY:
{compact_json(global_summary)}

ACT-LEVEL PLAN:
{compact_json(act_level_plan)}

SCENE-SPECIFIC PLAN:
{compact_json(scene_level_plan)}

=============== CONTINUITY ===============

PREVIOUS SCENE SUMMARY:
{compact_json(previous_summary)}

CURRENT SCENE SUMMARY:
{compact_json(current_summary)}

NEXT SCENE SUMMARY:
{compact_json(next_summary)}

=============== ORIGINAL SCENE ===============

{scene_full_text}

=============== TASK ===============

1. Apply act-level structural adjustments if relevant.
2. Apply scene-specific action items precisely.
3. Preserve character psychology continuity.
4. Maintain tone and thematic alignment.
5. Strengthen dramatic tension where possible.
6. Do not add unrelated content.

=============== FORMAT ===============

Return ONLY valid JSON:

{{
  "updated_scene_text": "Full rewritten scene in Fountain format",
  "updated_scene_summary": {{
      "scene_goal": "",
      "character_arc_progress": "",
      "conflict_shift": "",
      "notes": ""
  }},
  "updated_global_summary": {{
      "story_direction_shift": "",
      "act_progression_status": "",
      "new_setups_or_payoffs": "",
      "continuity_notes": ""
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
                    temperature=0.6,
                    response_mime_type="application/json"
                ),
            )

            raw_text = repair_common_json_issues(response.text)
            parsed = safe_json_parse(raw_text)

            if not isinstance(parsed, dict) or "updated_scene_text" not in parsed:
                raise ValueError("Invalid JSON structure")

            return parsed

        except Exception as e:
            print("Rewrite agent error:", e)

            return {
                "updated_scene_text": scene_full_text,
                "updated_scene_summary": current_summary,
                "updated_global_summary": global_summary,
            }