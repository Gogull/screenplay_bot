import asyncio
import json
import xml.etree.ElementTree as ET
from copy import deepcopy
from typing import Optional, List, Dict
from pathlib import Path
import os

from google import genai
from google.genai import types


# =========================================================
# LOAD GEMINI API KEY
# =========================================================
def get_gemini_api_key() -> str:
    try:
        import streamlit as st
        if "GEMINI_API_KEY" in st.secrets:
            return st.secrets["GEMINI_API_KEY"]
    except Exception:
        pass

    try:
        from dotenv import load_dotenv
        root_dir = Path(__file__).resolve().parents[1]
        env_path = root_dir / ".env"
        if env_path.exists():
            load_dotenv(env_path)

        key = os.getenv("GEMINI_API_KEY")
        if key:
            return key
    except Exception:
        pass

    raise RuntimeError("❌ GEMINI_API_KEY not found.")


API_KEY = get_gemini_api_key()
MODEL_NAME = "gemini-2.5-pro"
client = genai.Client(api_key=API_KEY)


# =========================================================
# FDX → CANONICAL JSON
# =========================================================
def parse_fdx_to_canonical(fdx_path: str) -> dict:
    tree = ET.parse(fdx_path)
    root = tree.getroot()

    screenplay = {"scenes": []}
    current_scene = None
    scene_counter = 1

    for para in root.iter("Paragraph"):
        p_type = para.attrib.get("Type", "").strip()
        text_node = para.find("Text")
        text = (text_node.text or "").strip() if text_node is not None else ""

        if not text:
            continue

        if p_type == "Scene Heading":
            if current_scene:
                screenplay["scenes"].append(current_scene)

            current_scene = {
                "scene_id": f"S{scene_counter:03}",
                "heading": text,
                "elements": []
            }
            scene_counter += 1
        else:
            if current_scene:
                current_scene["elements"].append({
                    "type": p_type,
                    "text": text
                })

    if current_scene:
        screenplay["scenes"].append(current_scene)

    return screenplay


# =========================================================
# CONVERT CANONICAL → FOUNTAIN
# =========================================================
def screenplay_to_fountain(screenplay: dict) -> str:
    lines = []

    for scene in screenplay["scenes"]:
        lines.append(scene["heading"].upper())
        lines.append("")

        for el in scene["elements"]:
            t = el.get("type", "Action")
            text = el["text"].strip()

            if t == "Character":
                lines.append(text.upper())
            elif t == "Parenthetical":
                lines.append(f"({text})")
            else:
                lines.append(text)

            lines.append("")

        lines.append("")

    return "\n".join(lines).strip()


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
# FILTER CHANGES PER SCENE
# =========================================================
def get_relevant_changes_for_scene(scene: dict, change_plan: dict) -> List[Dict]:
    relevant = []

    for change in change_plan.get("scene_level_changes", []):
        placement = change.get("placement", "").upper()

        if placement == "ENTIRE SCREENPLAY":
            relevant.append(change)
        elif scene.get("act", "").upper() in placement:
            relevant.append(change)

    return relevant


# =========================================================
# FOUNTAIN PARSER
# =========================================================
def parse_fountain_scene(fountain_text: str, original_scene_id: str) -> dict:
    lines = fountain_text.split("\n")

    scene = {
        "scene_id": original_scene_id,
        "heading": lines[0].strip(),
        "elements": []
    }

    current_type = "Action"

    for raw_line in lines[1:]:
        line = raw_line.strip()
        if not line:
            continue

        if line.isupper() and not line.startswith("("):
            current_type = "Character"
            scene["elements"].append({"type": "Character", "text": line})
        elif line.startswith("(") and line.endswith(")"):
            scene["elements"].append({
                "type": "Parenthetical",
                "text": line[1:-1]
            })
        else:
            if current_type == "Character":
                scene["elements"].append({
                    "type": "Dialogue",
                    "text": line
                })
            else:
                scene["elements"].append({
                    "type": "Action",
                    "text": line
                })

    return scene


# =========================================================
# GEMINI REWRITE WITH SCENE-LEVEL SUMMARY
# =========================================================
async def rewrite_scene_with_gemini(scene: dict, relevant_changes: List[Dict]):

    if not relevant_changes:
        return scene, None

    scene_fountain = screenplay_to_fountain({"scenes": [scene]})

    system_prompt = """
You are a professional screenplay rewrite engine.

Rules:
- Preserve Fountain formatting.
- Return the rewritten scene.
- After the scene, add this delimiter:

===SUMMARY===

- Then provide a concise 1-3 sentence summary of what changed at scene level.
- Do NOT explain line-by-line.
"""

    user_prompt = f"""
CHANGE INSTRUCTIONS:
{json.dumps(relevant_changes, indent=2)}

SCENE:
{scene_fountain}
"""

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=MODEL_NAME,
        contents=[system_prompt, user_prompt],
        config=types.GenerateContentConfig(temperature=0.6),
    )

    rewritten_text = response.text.strip()

    if "===SUMMARY===" in rewritten_text:
        scene_part, summary_part = rewritten_text.split("===SUMMARY===")
        summary = summary_part.strip()
    else:
        scene_part = rewritten_text
        summary = "Scene rewritten based on provided notes."

    rewritten_scene = parse_fountain_scene(
        scene_part.strip(),
        original_scene_id=scene["scene_id"]
    )

    return rewritten_scene, summary


# =========================================================
# MAIN ENTRY
# =========================================================
async def rewrite_fdx_with_plan(
    fdx_path: str,
    change_plan: dict,
    start_scene: Optional[int] = None,
    end_scene: Optional[int] = None
):

    canonical = parse_fdx_to_canonical(fdx_path)
    assign_acts(canonical)

    updated = deepcopy(canonical)

    diff_report = {
        "fdx_file": fdx_path,
        "scenes_changed": []
    }

    tasks = []

    for i, scene in enumerate(updated["scenes"]):
        scene_number = i + 1

        if start_scene is not None and scene_number < start_scene:
            continue
        if end_scene is not None and scene_number > end_scene:
            continue

        relevant_changes = get_relevant_changes_for_scene(scene, change_plan)

        if relevant_changes:
            tasks.append((i, relevant_changes))

    if tasks:
        results = await asyncio.gather(
            *[
                rewrite_scene_with_gemini(updated["scenes"][i], changes)
                for i, changes in tasks
            ]
        )

        for (idx, relevant_changes), (rewritten, summary) in zip(tasks, results):

            original = canonical["scenes"][idx]

            diff_report["scenes_changed"].append({
                "scene_index": idx + 1,
                "scene_id": original["scene_id"],
                "heading": original["heading"],
                "applied_change_ids": [
                    c["change_id"] for c in relevant_changes
                ],
                "change_summary": summary
            })

            updated["scenes"][idx] = rewritten

    return {
        "fountain_text": screenplay_to_fountain(updated),
        "diff_report": diff_report
    }
