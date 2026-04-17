import asyncio
import json
import re
from typing import Dict, List
from pathlib import Path
import os
import xml.etree.ElementTree as ET
from collections import defaultdict

from google import genai
from google.genai import types


# =========================================================
# GEMINI SETUP
# =========================================================

def get_gemini_api_key() -> str:
    try:
        import streamlit as st
        if "GEMINI_API_KEY" in st.secrets:
            return st.secrets["GEMINI_API_KEY"]
    except Exception:
        pass

    from dotenv import load_dotenv
    root_dir = Path(__file__).resolve().parents[1]
    env_path = root_dir / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not found.")
    return key


API_KEY = get_gemini_api_key()
MODEL_NAME = "gemini-3.1-pro-preview"
client = genai.Client(api_key=API_KEY)

# =========================================================
# SEMAPHORE -- lazy init to avoid Streamlit event loop conflict
# =========================================================

_semaphore: asyncio.Semaphore | None = None

def get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    loop = asyncio.get_event_loop()
    if _semaphore is None or _semaphore._loop is not loop:
        _semaphore = asyncio.Semaphore(5)
    return _semaphore


# =========================================================
# GEMINI JSON CALL
# =========================================================

async def gemini_json_call(system_prompt: str, user_prompt: str, temperature: float = 0.3) -> dict:
    async with get_semaphore():
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=MODEL_NAME,
                contents=[system_prompt, user_prompt],
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    response_mime_type="application/json",
                ),
            )
            return json.loads(response.text)
        except Exception as e:
            print("Gemini JSON error:", e)
            return {}


# =========================================================
# FDX PARSER
# =========================================================

def parse_fdx_to_canonical(fdx_path: str) -> dict:
    tree = ET.parse(fdx_path)
    root = tree.getroot()

    screenplay = {"scenes": []}
    current_scene = None
    scene_counter = 1
    heading_count = defaultdict(int)

    for para in root.iter("Paragraph"):
        p_type = para.attrib.get("Type", "").strip()
        text_node = para.find("Text")
        text = (text_node.text or "").strip() if text_node is not None else ""

        if not text:
            continue

        if p_type == "Scene Heading":
            heading_norm = text.upper()
            heading_count[heading_norm] += 1

            scene_id = f"S{scene_counter:03}_{heading_count[heading_norm]}"
            scene_counter += 1

            if current_scene:
                screenplay["scenes"].append(current_scene)

            current_scene = {
                "scene_id": scene_id,
                "heading": text,
                "elements": [],
                "full_text": text,
            }

        else:
            if current_scene:
                current_scene["elements"].append({"type": p_type, "text": text})
                current_scene["full_text"] += "\n" + text

    if current_scene:
        screenplay["scenes"].append(current_scene)

    return screenplay


# =========================================================
# PHASE 1: INGESTION & STORY BREAKING
# =========================================================

async def phase1_story_breaking(canonical: dict, notes: str = "") -> dict:
    """
    Analyzes the raw screenplay to extract narrative DNA:
    - Controlling idea / central theme
    - Character architecture (Want vs Need, fatal flaw)
    - Logline
    """

    # Build a condensed version of the screenplay for analysis
    scene_texts = []
    for s in canonical["scenes"]:
        clean = re.sub(r'\s+', ' ', s["full_text"]).strip()
        scene_texts.append(clean)

    condensed = "\n---\n".join(scene_texts)

    system_prompt = (
        "You are an elite story analyst and screenplay development executive. "
        "You break stories the way Aaron Sorkin or Greta Gerwig would in a writers' room. "
        "Analyze the raw material and extract the core narrative DNA. "
        "Every great script is an argument -- identify the controlling idea that governs "
        "character choices and visual motifs. Return ONLY JSON."
    )

    user_prompt = f"""
RAW SCREENPLAY MATERIAL:
{condensed}

WRITER'S NOTES:
{notes if notes else "None provided."}

Analyze this material and return JSON:
{{
  "controlling_idea": "The central thematic argument of the story -- what is this screenplay trying to prove or explore?",
  "core_theme": "The one-word or short-phrase theme (e.g., 'redemption', 'the cost of ambition')",
  "character_architecture": {{
    "protagonist": "Name or description of the protagonist",
    "conscious_want": "What the protagonist openly pursues",
    "unconscious_need": "What the protagonist actually needs but doesn't realize",
    "fatal_flaw": "The internal weakness that must be overcome",
    "antagonist_force": "The primary opposing force (character, system, or internal)"
  }},
  "logline": "A tight, compelling logline that serves as the story's North Star (1-2 sentences)",
  "tone_profile": "The emotional and stylistic tone of the piece",
  "genre_alignment": "Primary and secondary genre",
  "stakes_progression": "How the stakes escalate across the story"
}}
"""

    return await gemini_json_call(system_prompt, user_prompt, temperature=0.4)


# =========================================================
# PHASE 2: STRUCTURAL BLUEPRINT
# =========================================================

async def phase2_structural_blueprint(story_dna: dict, canonical: dict, notes: str = "") -> dict:
    """
    Constructs the structural framework:
    - Beat sheet (Save the Cat / 8-Sequence hybrid)
    - Detailed step outline (scene-by-scene roadmap without dialogue)
    """

    # Provide condensed scene info for structural reference
    scene_refs = []
    for s in canonical["scenes"]:
        scene_refs.append({
            "heading": s["heading"],
            "text_preview": re.sub(r'\s+', ' ', s["full_text"]).strip()[:300]
        })

    system_prompt = (
        "You are an elite screenplay architect. "
        "You structure stories using proven frameworks like the 15-point Save the Cat model "
        "and Frank Daniel's 8-Sequence approach. "
        "Your job is to map the major turning points and then expand them into a richly detailed "
        "scene-by-scene step outline. "
        "The step outline is the crucial bridge between structure and drafting -- it locks in "
        "pacing and momentum before a single line of dialogue is written. "
        "Break away from the source material's exact scene structure and design a clean, "
        "original beat structure. Never use scene IDs or alphanumeric scene references. "
        "Return ONLY JSON."
    )

    user_prompt = f"""
STORY DNA (from Phase 1 analysis):
{json.dumps(story_dna, indent=2)}

REFERENCE MATERIAL (source scenes for context, DO NOT copy structure):
{json.dumps(scene_refs[:30], indent=2)}

WRITER'S NOTES:
{notes if notes else "None provided."}

Create a structural blueprint in two parts:

PART 1 - BEAT SHEET: Map the major turning points using a hybrid Save the Cat / 8-Sequence framework.
Include: Opening Image, Theme Stated, Setup, Catalyst/Inciting Incident, Debate, Break into Two,
B-Story, Fun and Games/Promise of the Premise, Midpoint, Bad Guys Close In, All Is Lost,
Dark Night of the Soul, Break into Three, Finale, Final Image.

PART 2 - STEP OUTLINE: Expand the beat sheet into 35-45 detailed scene-by-scene steps.
Each step should note the primary action, conflict, and emotional shift WITHOUT writing dialogue.
This is the roadmap that ensures pacing and momentum are locked before drafting.

Rules:
- NO scene IDs or scene codes of any kind
- Fresh structure independent of reference scenes
- Chronological progression
- Each step must be actionable and dramatically specific
- Every character arc from the NOTES must be reflected
- HARD LIMIT: Never more than 2 consecutive steps in the same location

Return JSON:
{{
  "beat_sheet": {{
    "opening_image": "",
    "theme_stated": "",
    "setup": "",
    "catalyst": "",
    "debate": "",
    "break_into_two": "",
    "b_story": "",
    "fun_and_games": "",
    "midpoint": "",
    "bad_guys_close_in": "",
    "all_is_lost": "",
    "dark_night_of_the_soul": "",
    "break_into_three": "",
    "finale": "",
    "final_image": ""
  }},
  "step_outline": [
    {{
      "step_number": 1,
      "title": "",
      "beat_sheet_phase": "Which beat sheet phase this falls under",
      "act": "Act I / Act II / Act III",
      "location": "INT./EXT. LOCATION - TIME",
      "action": "What happens -- the primary dramatic action",
      "conflict": "What opposes the character in this scene",
      "emotional_shift": "How the emotional state changes from start to end of scene",
      "story_function": "Why this scene must exist in the screenplay"
    }}
  ],
  "total_steps": 0
}}
"""

    return await gemini_json_call(system_prompt, user_prompt, temperature=0.4)


# =========================================================
# MAIN: PHASES 1-2 COMBINED
# =========================================================

async def story_breaking_agent(fdx_path: str, notes: str = "") -> dict:
    """
    Runs the first two phases of the pipeline:
    Phase 1: Ingestion & Story Breaking
    Phase 2: Structural Blueprint
    """

    # Parse the FDX
    canonical = parse_fdx_to_canonical(fdx_path)

    # Phase 1: Extract narrative DNA
    story_dna = await phase1_story_breaking(canonical, notes)

    # Phase 2: Build structural blueprint from the DNA
    blueprint = await phase2_structural_blueprint(story_dna, canonical, notes)

    return {
        "story_dna": story_dna,
        "blueprint": blueprint,
        "canonical": canonical
    }
