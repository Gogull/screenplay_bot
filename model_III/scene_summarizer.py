import asyncio
import json
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
        raise RuntimeError("❌ GEMINI_API_KEY not found.")
    return key


API_KEY = get_gemini_api_key()
MODEL_NAME = "gemini-2.5-flash"
client = genai.Client(api_key=API_KEY)

SEMAPHORE = asyncio.Semaphore(5)


# =========================================================
# GEMINI JSON CALL
# =========================================================

async def gemini_json_call(system_prompt: str, user_prompt: str, temperature: float = 0.3) -> dict:
    async with SEMAPHORE:
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
# ACT ASSIGNMENT
# =========================================================

def assign_acts(screenplay: dict):
    total = len(screenplay["scenes"])

    for i, scene in enumerate(screenplay["scenes"]):
        ratio = i / max(total, 1)

        if ratio < 0.25:
            scene["act"] = "Act I"
        elif ratio < 0.75:
            scene["act"] = "Act II"
        else:
            scene["act"] = "Act III"


# =========================================================
# SCENE SUMMARY
# =========================================================

async def summarize_scene(scene: dict) -> Dict:

    system_prompt = "You are a professional screenplay analyst. Return ONLY JSON."

    user_prompt = f"""
Return JSON:

{{
  "scene_id": "{scene['scene_id']}",
  "heading": "{scene['heading']}",
  "act": "{scene.get('act','')}",
  "summary": "",
  "purpose": "",
  "conflict": "",
  "characters": [],
  "emotional_beat": ""
}}

SCENE:
{scene["full_text"]}
"""

    result = await gemini_json_call(system_prompt, user_prompt)

    if not result:
        result = {
            "scene_id": scene["scene_id"],
            "heading": scene["heading"],
            "act": scene.get("act", ""),
            "summary": "",
            "purpose": "",
            "conflict": "",
            "characters": [],
            "emotional_beat": "",
        }

    result["full_text"] = scene["full_text"]
    return result


# =========================================================
# GLOBAL SUMMARY
# =========================================================

async def generate_global_summary(scene_summaries: List[dict]) -> dict:

    system_prompt = "You are a screenplay architect. Return ONLY JSON."

    user_prompt = f"""
SCENE SUMMARIES:
{json.dumps(scene_summaries, indent=2)}

Return JSON:
{{
  "logline": "",
  "core_theme": "",
  "protagonist_arc": "",
  "antagonist_force": "",
  "stakes_progression": "",
  "global_goal": "",
  "act_structure": {{
    "act1_turn": "",
    "midpoint": "",
    "act2_low_point": "",
    "climax": ""
  }},
  "tone_profile": "",
  "genre_alignment": ""
}}
"""

    return await gemini_json_call(system_prompt, user_prompt)


# =========================================================
# ACT PLAN
# =========================================================

async def generate_act_plan(acts, global_summary, notes):

    system_prompt = "You are a screenplay architect. Return ONLY JSON."

    user_prompt = f"""
GLOBAL SUMMARY:
{json.dumps(global_summary, indent=2)}

ACTS:
{json.dumps(acts, indent=2)}

NOTES:
{notes}

Return JSON:
{{
  "act_level_strategy": [],
  "structural_adjustments": [],
  "character_arc_adjustments": []
}}
"""

    return await gemini_json_call(system_prompt, user_prompt)


# =========================================================
# BEAT SHEET (CORE FIX)
# =========================================================

async def generate_beat_sheet(global_summary, act_plan, scene_summaries, notes):

    system_prompt = (
        "You are an elite screenplay architect. "
        "Break away from scene numbers and design a clean beat structure."
    )

    user_prompt = f"""
GLOBAL SUMMARY:
{json.dumps(global_summary, indent=2)}

ACT PLAN:
{json.dumps(act_plan, indent=2)}

REFERENCE SCENES (DO NOT reuse structure):
{json.dumps(scene_summaries, indent=2)}

NOTES:
{notes}

Create a flexible beat sheet (35–45 beats, target ~40).

Rules:
- NO scene IDs
- Fresh structure
- Chronological beats
- Each beat must be actionable

Return JSON:
{{
  "total_beats": 0,
  "beats": [
    {{
      "beat_number": <sequential integer starting from 1>,
      "title": "",
      "description": "",
      "act": "",
      "emotional_goal": "",
      "story_function": ""
    }}
  ]
}}
"""

    return await gemini_json_call(system_prompt, user_prompt)


# =========================================================
# MAIN ARCHITECT
# =========================================================

async def architect_agent(fdx_path: str, notes: str = "") -> dict:

    canonical = parse_fdx_to_canonical(fdx_path)
    assign_acts(canonical)

    # Scene summaries
    scene_results = await asyncio.gather(
        *[summarize_scene(s) for s in canonical["scenes"]]
    )

    # Global summary
    global_summary = await generate_global_summary(scene_results)

    # Group acts
    acts = defaultdict(list)
    for s in scene_results:
        acts[s["act"]].append({
            "summary": s["summary"]
        })

    # Act plan
    act_plan = await generate_act_plan(acts, global_summary, notes)

    # 🔥 NEW CORE STEP
    beat_sheet = await generate_beat_sheet(
        global_summary,
        act_plan,
        scene_results,
        notes
    )

    return {
        "global_summary": global_summary,
        "act_level_plan": act_plan,
        "beat_sheet": beat_sheet,  # ✅ CORE OUTPUT
        "scene_summaries": scene_results,  # reference only
        "scenes": canonical["scenes"]      # raw only
    }