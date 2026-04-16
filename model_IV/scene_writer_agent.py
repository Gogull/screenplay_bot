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
    text = re.sub(r'[\(\[]?S\d{3,4}[\)\]]?', '', text)
    text = re.sub(r'^\s*(WITH|AND|BUT|OR|THEN)\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# =========================================================
# GEMINI TEXT CALL (for polish passes that return plain text)
# =========================================================

async def gemini_text_call(system_prompt: str, user_prompt: str, temperature: float = 0.5) -> str:
    async with get_semaphore():
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
                    system_instruction=system_prompt,
                    temperature=temperature,
                ),
            )
            return response.text.strip()
        except Exception as e:
            print("Gemini text call error:", e)
            return ""


# =========================================================
# PHASE 3: SCENE CONSTRUCTION ENGINE (First Draft)
# =========================================================

async def phase3_scene_construction(
    step: dict,
    story_dna: dict,
    prev_step: dict | None,
    next_step: dict | None,
) -> str:
    """
    Drafts a single scene/step using the G.O.D.D. formula:
    Goal, Obstacle, Danger, Direction.
    Arrives late, leaves early.
    """

    async with get_semaphore():

        def compact_json(d):
            return json.dumps(d, separators=(",", ":")) if d else "None"

        system_instruction = (
            "You are a professional feature film screenwriter. "
            "You write original screenplay content from step outlines. "
            "You produce clean, industry-standard Fountain format. "
            "DRAMATIC PRINCIPLES: "
            "1. Every scene must pass the G.O.D.D. test: "
            "   - GOAL: What does the character want in this scene? "
            "   - OBSTACLE: What stands in their way? "
            "   - DANGER: What are the stakes of failure? "
            "   - DIRECTION: How does the outcome propel the story into the next scene? "
            "2. ARRIVE LATE, LEAVE EARLY: Cut out pleasantries and exposition. "
            "   Drop the reader into the middle of conflict. Cut away the moment "
            "   the scene's dramatic question is answered. "
            "3. Drive character through action and subtext, not exposition dumps. "
            "ABSOLUTE FORMAT RULES: "
            "- Never write scene IDs like S001, S026, or any alphanumeric codes. "
            "- Every scene heading must be fully formed: INT./EXT. LOCATION - TIME. "
            "- Every line of dialogue must be preceded by a character name in CAPS. "
            "- Action lines must be clean, visual, present tense. "
            "- Character names must be spelled consistently. "
            "Output ONLY the raw Fountain-formatted screenplay text. No JSON. No markdown fences. No preamble."
        )

        user_prompt = f"""
================ STORY DNA ================

CONTROLLING IDEA: {story_dna.get("controlling_idea", "")}
THEME: {story_dna.get("core_theme", "")}
LOGLINE: {story_dna.get("logline", "")}
TONE: {story_dna.get("tone_profile", "")}

CHARACTER:
{compact_json(story_dna.get("character_architecture", {}))}

=============== CURRENT STEP ===============

STEP {step.get("step_number", "")}: {step.get("title", "")}
ACT: {step.get("act", "")}
BEAT PHASE: {step.get("beat_sheet_phase", "")}
LOCATION: {step.get("location", "")}
ACTION: {step.get("action", "")}
CONFLICT: {step.get("conflict", "")}
EMOTIONAL SHIFT: {step.get("emotional_shift", "")}
STORY FUNCTION: {step.get("story_function", "")}

PREVIOUS STEP: {compact_json(prev_step)}
NEXT STEP: {compact_json(next_step)}

=============== TASK ===============

Write a complete screenplay scene that fully dramatizes this step.

- Apply the G.O.D.D. formula (Goal, Obstacle, Danger, Direction)
- Arrive late into the scene, leave early
- Maintain continuity with the previous step
- Set up the next step organically
- Write full dialogue and action, not summaries
- If continuing from the same location as the previous step, do NOT repeat the scene heading

=============== FORMAT ===============

INT./EXT. LOCATION - TIME

Action line describing what we see.

CHARACTER NAME
Dialogue spoken by the character.

Write the scene now:
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
                ),
            )

            raw = response.text.strip()
            raw = raw.replace("```fountain", "").replace("```", "").strip()
            return scrub_screenplay_output(raw)

        except Exception as e:
            print("Phase 3 scene construction error:", e)
            return ""


# =========================================================
# PHASE 4: SUBTEXT & DIALOGUE PASS
# =========================================================

async def phase4_dialogue_pass(screenplay_text: str, story_dna: dict) -> str:
    """
    Sweeps through the drafted text focusing only on dialogue:
    - Flags and rewrites on-the-nose dialogue with subtext
    - Breaks theatrical monologues into naturalistic fragments
    - Adds interruptions, unfinished sentences, messy cadence
    - Uses parentheticals and (beats) sparingly
    """

    system_prompt = (
        "You are a dialogue specialist and script doctor. "
        "Your ONLY job is to polish dialogue. Do NOT change action lines, scene headings, "
        "or story structure. Preserve the exact same scenes in the exact same order. "
        "RULES: "
        "1. SUBTEXT INJECTION: Flag any on-the-nose dialogue where characters state exactly "
        "what they think or feel. Rewrite so the true meaning is buried under the surface. "
        "Characters should deflect, lie, or use mundane topics to wage emotional warfare. "
        "2. NATURALISTIC RHYTHM: Break up long theatrical monologues into fragments. "
        "Sentences left unfinished, characters interrupt each other, dialogue mimics the "
        "authentic, sometimes messy cadence of native speakers. "
        "3. STRATEGIC PAUSES: Use parentheticals and (beat) sparingly -- only where a shift "
        "in subtext demands a specific rhythm. "
        "4. Keep dialogue succinct and dialogue-driven. "
        "Output the COMPLETE screenplay with polished dialogue. "
        "Maintain Fountain format exactly. No JSON. No markdown fences. No commentary."
    )

    user_prompt = f"""
STORY CONTEXT:
Theme: {story_dna.get("core_theme", "")}
Controlling Idea: {story_dna.get("controlling_idea", "")}
Protagonist Flaw: {story_dna.get("character_architecture", {}).get("fatal_flaw", "")}

SCREENPLAY TO POLISH:

{screenplay_text}

Return the complete screenplay with dialogue polished for subtext and naturalism.
"""

    return await gemini_text_call(system_prompt, user_prompt, temperature=0.6)


# =========================================================
# PHASE 5: VISUAL & ATMOSPHERIC POLISH
# =========================================================

async def phase5_visual_polish(screenplay_text: str, story_dna: dict) -> str:
    """
    Strips unfilmable prose and enhances visual storytelling:
    - Trims action lines to 1-4 line punchy blocks
    - Replaces "is-ing" with strong active verbs
    - Removes internal thoughts, replaces with filmable behavior
    - Refines imagery to match thematic tone
    """

    system_prompt = (
        "You are a visual storytelling specialist and action line editor. "
        "Screenplays are visual blueprints, not novels. "
        "Your ONLY job is to polish action lines and visual descriptions. "
        "Do NOT change dialogue, scene headings, or story structure. "
        "RULES: "
        "1. ACTION LINE ECONOMY: Trim all action lines into punchy 1-to-4 line blocks "
        "to create a fast, breathless read. Use strong active verbs in present tense. "
        "Eliminate 'is-ing' phrasing (e.g., 'John is running' becomes 'John sprints'). "
        "2. VISUAL STORYTELLING: Remove internal character thoughts ('She thinks about her past') "
        "and replace with filmable behaviors ('She traces the rim of the rusted locket'). "
        "3. THEMATIC TONE: Refine imagery, setting descriptions, and visual motifs to organically "
        "build the atmosphere the story requires. "
        "4. Preserve all dialogue exactly as written. "
        "Output the COMPLETE screenplay with polished visuals. "
        "Maintain Fountain format exactly. No JSON. No markdown fences. No commentary."
    )

    user_prompt = f"""
STORY CONTEXT:
Tone: {story_dna.get("tone_profile", "")}
Theme: {story_dna.get("core_theme", "")}
Genre: {story_dna.get("genre_alignment", "")}

SCREENPLAY TO POLISH:

{screenplay_text}

Return the complete screenplay with action lines polished for visual economy and atmosphere.
"""

    return await gemini_text_call(system_prompt, user_prompt, temperature=0.5)


# =========================================================
# PHASE 6: INDUSTRY COMPILER
# =========================================================

async def phase6_industry_compile(screenplay_text: str) -> str:
    """
    Final formatting pass for industry standards:
    - Ensures proper Fountain format throughout
    - Checks scene headings (sluglines), character names, transitions
    - Validates (V.O.), (O.S.), (CONT'D) extensions
    - Ensures consistent spacing and formatting
    """

    system_prompt = (
        "You are a screenplay formatting expert and script supervisor. "
        "Your ONLY job is to ensure the screenplay meets strict industry formatting standards. "
        "Do NOT change any content, dialogue, or story elements. "
        "FORMATTING RULES: "
        "1. Scene Headings (Sluglines): Must be INT. or EXT. (or INT./EXT.), followed by "
        "LOCATION - TIME OF DAY. All caps. "
        "2. Action lines: Present tense, no ALL CAPS except for character introductions. "
        "3. Character names: ALL CAPS above dialogue, consistent spelling throughout. "
        "4. Dialogue: Properly indented under character name. "
        "5. Parentheticals: Lowercase in parentheses on their own line between character name and dialogue. "
        "6. Transitions: CUT TO:, SMASH CUT TO:, FADE IN:, FADE OUT. -- right-aligned, used sparingly. "
        "7. Extensions: (V.O.) for voice over, (O.S.) for off screen, (CONT'D) for continued dialogue. "
        "8. No orphaned lines, no broken formatting. "
        "9. Consistent blank line spacing between elements. "
        "Output the COMPLETE screenplay with perfect formatting. "
        "No JSON. No markdown fences. No commentary. Just the clean Fountain screenplay."
    )

    user_prompt = f"""
SCREENPLAY TO FORMAT:

{screenplay_text}

Return the complete screenplay with perfect industry-standard Fountain formatting.
"""

    result = await gemini_text_call(system_prompt, user_prompt, temperature=0.2)
    result = result.replace("```fountain", "").replace("```", "").strip()
    return scrub_screenplay_output(result)
