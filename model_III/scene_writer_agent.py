import asyncio
import json
import re
from google.genai import types
from model_III.scene_summarizer import client, MODEL_NAME, get_semaphore


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
# OUTPUT SCRUBBER
# =========================================================

def scrub_screenplay_output(text: str) -> str:
    # Remove scene ID codes like (S001), S001, [S0261], (S0261) etc.
    text = re.sub(r'[\(\[]?S\d{3,4}[\)\]]?', '', text)
    # Remove orphaned single transition words on their own line
    text = re.sub(r'^\s*(WITH|AND|BUT|OR|THEN)\s*$', '', text, flags=re.MULTILINE)
    # Collapse multiple blank lines into max two
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# =========================================================
# REFERENCE SCENE SANITIZER
# =========================================================

def sanitize_reference_scenes(reference_scenes: list) -> list:
    """
    Strip all structural/ID fields from reference scenes.
    Only pass tone-relevant fields to the writer to prevent
    scene ID bleed-through and structural pattern-matching.
    """
    if not reference_scenes:
        return []

    safe = []
    for s in reference_scenes:
        safe.append({
            "summary":        s.get("summary", ""),
            "emotional_beat": s.get("emotional_beat", ""),
            "conflict":       s.get("conflict", ""),
            "purpose":        s.get("purpose", ""),
        })
    return safe


# =========================================================
# BEAT WRITER AGENT
# =========================================================

async def write_beat_agent(
    current_beat: dict,
    previous_beat: dict | None,
    next_beat: dict | None,
    global_summary: dict,
    act_level_plan: dict | None,
    reference_scenes: list | None = None,
) -> dict:

    async with get_semaphore():

        def compact_json(d):
            return json.dumps(d, separators=(",", ":")) if d else "None"

        # Sanitize reference scenes before they reach the prompt
        safe_references = sanitize_reference_scenes(reference_scenes or [])

        system_instruction = (
            "You are a professional feature film screenwriter. "
            "You write original screenplay content from story beats. "
            "You produce clean, industry-standard Fountain format. "
            "ABSOLUTE RULES — violating any of these invalidates your entire response: "
            "1. Never write scene IDs like S001, S026, (S0261) or any alphanumeric scene codes anywhere in your output. "
            "2. Never write orphaned transition words like 'WITH', 'AND', 'BUT', 'OR', 'THEN' on their own line. "
            "3. Never write placeholder text, ellipsis stand-ins, or meta-commentary. "
            "4. Never reference or reuse structure, headings, or IDs from the reference scenes. "
            "5. Every scene heading must be fully formed: INT./EXT. LOCATION - TIME. "
            "6. Every line of dialogue must be preceded by a character name in CAPS. "
            "7. Action lines must be clean, visual, and free of parenthetical asides. "
            "8. If your segment ends at the same location as the next beat, do NOT repeat "
            "the scene heading. Use CONTINUOUS or simply continue the action. "
            "Never open two consecutive scenes with identical INT./EXT. headings. "
            "9. Character names in dialogue headers must be spelled consistently throughout. "
            "Never introduce a new spelling of an existing character name. "
            "Return ONLY valid JSON. No markdown fences. No preamble."
            "10.Never introduce named props, codes, or specific data (numbers, words, phrases) that contradict what was established in the previous beat. "
            "If the previous beat established specific details, carry them forward exactly"
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

REFERENCE SCENES (TONE AND EMOTION ONLY — never reuse IDs, headings, or structure):
{compact_json(safe_references)}

=============== TASK ===============

Write a screenplay segment that fully dramatizes THIS BEAT.

Requirements:

1. Fully express the beat dramatically — scene, action, dialogue. Not summary.
2. Decide your own scene breaks naturally based on story logic.
3. Maintain continuity with the previous beat.
4. Set up the next beat organically if applicable.
5. Drive character through action and subtext — no exposition dumps.

HARD FORMAT VIOLATIONS TO AVOID:
- NO scene ID codes (S001, S026, (S0261), etc.) anywhere in the text.
- NO orphaned single words on their own line (WITH, AND, BUT, THEN, OR).
- NO incomplete scene headings.
- NO placeholder lines or TODO comments.
- NO meta-text referencing the beat structure.
- NO duplicate scene headings for the same location in consecutive beats.
- NO inconsistent character name spellings across the segment.

=============== FORMAT RULES ===============

Proper Fountain format:

  INT./EXT. LOCATION - TIME

  Action line describing what we see.

  CHARACTER NAME
  Dialogue spoken by the character.

  CHARACTER NAME (CONT'D)
  Continuation if interrupted by action.

=============== OUTPUT ===============

Return ONLY this JSON structure with no additional text:

{{
  "screenplay_segment": "Full Fountain formatted screenplay text here",
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

            # Scrub any residual artifacts from the screenplay segment
            parsed["screenplay_segment"] = scrub_screenplay_output(
                parsed["screenplay_segment"]
            )

            return parsed

        except Exception as e:
            print("Beat writer error:", e)

            return {
                "screenplay_segment": "",
                "beat_summary": current_beat,
                "continuity_notes": {}
            }