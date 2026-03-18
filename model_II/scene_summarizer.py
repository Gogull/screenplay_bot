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

# Global concurrency limiter (shared across agents)
SEMAPHORE = asyncio.Semaphore(5)


# =========================================================
# GEMINI JSON CALL WRAPPER
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
                current_scene["elements"].append(
                    {"type": p_type, "text": text}
                )
                current_scene["full_text"] += "\n" + text

    if current_scene:
        screenplay["scenes"].append(current_scene)

    return screenplay


# =========================================================
# ACT ASSIGNMENT (Simple 3-Act Split)
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

    system_prompt = (
        "You are a professional screenplay analyst. "
        "Return ONLY valid JSON."
    )

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
# GLOBAL STORY SUMMARY
# =========================================================

async def generate_global_summary(scene_summaries: List[dict]) -> dict:

    system_prompt = (
        "You are a professional screenplay architect. "
        "Return ONLY valid JSON."
    )

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
# ACT LEVEL PLAN
# =========================================================

async def generate_act_plan(
    acts: dict,
    global_summary: dict,
    notes: str
):

    system_prompt = (
        "You are a screenplay architect. "
        "Return ONLY valid JSON."
    )

    user_prompt = f"""
GLOBAL SUMMARY:
{json.dumps(global_summary, indent=2)}

ACT BREAKDOWN:
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
# SCENE LEVEL PLAN
# =========================================================

async def generate_scene_plan(
    scene_summary: dict,
    global_summary: dict,
    act_strategy: dict,
    notes: str
):

    system_prompt = (
        "You are a screenplay architect. "
        "Return ONLY valid JSON."
    )

    user_prompt = f"""
GLOBAL STORY DIRECTION:
{json.dumps(global_summary, indent=2)}

ACT STRATEGY:
{json.dumps(act_strategy, indent=2)}

SCENE:
{json.dumps(scene_summary, indent=2)}

NOTES:
{notes}

Return JSON:
{{
  "scene_id": "{scene_summary['scene_id']}",
  "action_items": []
}}
"""

    return await gemini_json_call(system_prompt, user_prompt)


# =========================================================
# MASTER ARCHITECT AGENT
# =========================================================

async def architect_agent(
    fdx_path: str,
    notes: str = ""
) -> dict:

    canonical = parse_fdx_to_canonical(fdx_path)
    assign_acts(canonical)

    # ---- Scene Summaries ----
    scene_tasks = [
        summarize_scene(scene)
        for scene in canonical["scenes"]
    ]
    scene_results = await asyncio.gather(*scene_tasks)

    # ---- Global Summary ----
    global_summary = await generate_global_summary(scene_results)

    # ---- Group By Act ----
    acts = defaultdict(list)
    for scene in scene_results:
        acts[scene["act"]].append({
            "scene_id": scene["scene_id"],
            "summary": scene["summary"]
        })

    # ---- Act-Level Strategy ----
    act_level_plan = await generate_act_plan(
        acts,
        global_summary,
        notes
    )

    # ---- Scene-Level Plans ----
    scene_tasks = [
        generate_scene_plan(
            scene,
            global_summary,
            act_level_plan,
            notes
        )
        for scene in scene_results
    ]

    scene_level_plan = await asyncio.gather(*scene_tasks)

    return {
        "global_summary": global_summary,
        "scene_summaries": scene_results,
        "act_level_plan": act_level_plan,
        "scene_level_plan": scene_level_plan,
        "scenes": canonical["scenes"]
    }