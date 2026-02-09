import asyncio
import json
import difflib
import xml.etree.ElementTree as ET
from copy import deepcopy
from typing import Optional
from pathlib import Path
import os

from google import genai
from google.genai import types


# =========================================================
# LOAD GEMINI API KEY (Streamlit → .env)
# =========================================================
def get_gemini_api_key() -> str:
    # 1️⃣ Streamlit secrets (Cloud)
    try:
        import streamlit as st
        if "GEMINI_API_KEY" in st.secrets:
            return st.secrets["GEMINI_API_KEY"]
    except Exception:
        pass

    # 2️⃣ .env (local dev)
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

    # 3️⃣ Fail fast
    raise RuntimeError(
        "❌ GEMINI_API_KEY not found. "
        "Set it in Streamlit secrets or .env"
    )


# =========================================================
# CONFIG
# =========================================================
API_KEY = get_gemini_api_key()
MODEL_NAME = "gemini-2.5-pro"

client = genai.Client(api_key=API_KEY)

# =========================================================
# FDX → CANONICAL JSON
# =========================================================
def parse_fdx_to_canonical(fdx_path: str) -> dict:
    print(f"📄 Parsing FDX: {fdx_path}")

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

    print(f"🎞️ Scenes detected: {len(screenplay['scenes'])}")
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
# SCENE TARGETING
# =========================================================
def scene_needs_rewrite(scene: dict, change_plan: dict) -> bool:
    for change in change_plan.get("scene_level_changes", []):
        placement = change.get("placement", "").upper()
        if scene["act"].upper() in placement:
            return True
    return False


# =========================================================
# GEMINI SCENE REWRITE
# =========================================================
async def rewrite_scene_with_gemini(scene: dict, change_plan: dict) -> dict:
    system_prompt = """
You are a professional screenplay editor.

You will receive:
- A list of paragraph texts from ONE scene
- A screenplay change plan

Rules:
- Return a JSON list of strings
- SAME length as input
- Modify text only where relevant
- Do NOT add, remove, or reorder items
- Output JSON only
"""

    text_list = [el["text"] for el in scene["elements"]]

    user_prompt = f"""
CHANGE PLAN:
{json.dumps(change_plan, indent=2)}

SCENE TEXT LIST:
{json.dumps(text_list, indent=2)}
"""

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=MODEL_NAME,
        contents=[system_prompt, user_prompt],
        config=types.GenerateContentConfig(
            temperature=0.3,
            response_mime_type="application/json"
        )
    )

    new_texts = json.loads(response.text)
    updated_scene = deepcopy(scene)

    for i, el in enumerate(updated_scene["elements"]):
        if i < len(new_texts) and isinstance(new_texts[i], str):
            el["text"] = new_texts[i]

    return updated_scene


# =========================================================
# DIFF GENERATION
# =========================================================
def diff_scene(original: dict, rewritten: dict) -> list:
    diffs = []

    for i, (o, r) in enumerate(zip(original["elements"], rewritten["elements"])):
        if o["text"] != r["text"]:
            diffs.append({
                "element_index": i,
                "type": o.get("type", "Action"),
                "before": o["text"],
                "after": r["text"],
                "unified_diff": list(
                    difflib.unified_diff(
                        o["text"].splitlines(),
                        r["text"].splitlines(),
                        lineterm=""
                    )
                )
            })

    return diffs


# =========================================================
# CANONICAL JSON → FOUNTAIN
# =========================================================
def screenplay_to_fountain(screenplay: dict) -> str:
    lines = []

    for scene in screenplay["scenes"]:
        lines.append(scene["heading"].upper())
        lines.append("")

        for el in scene["elements"]:
            t = el.get("type", "Action")
            text = el["text"].strip()

            if not text:
                continue

            if t == "Action":
                lines.append(text)
            elif t == "Character":
                lines.append(text.upper())
            elif t == "Dialogue":
                lines.append(text)
            elif t == "Parenthetical":
                lines.append(f"({text})")
            else:
                lines.append(text)

            lines.append("")

        lines.append("")

    return "\n".join(lines).strip() + "\n"


# =========================================================
# MAIN ENTRY
# =========================================================
async def rewrite_fdx_with_plan(
    fdx_path: str,
    change_plan: dict,
    output_path: str = None,
    start_scene: Optional[int] = None,
    end_scene: Optional[int] = None
):
    canonical = parse_fdx_to_canonical(fdx_path)
    assign_acts(canonical)

    updated = deepcopy(canonical)
    total_scenes = len(updated["scenes"])

    diff_report = {
        "fdx_file": fdx_path,
        "start_scene": start_scene,
        "end_scene": end_scene,
        "scenes_changed": []
    }

    if start_scene and end_scene:
        start_idx = max(start_scene - 1, 0)
        end_idx = min(end_scene - 1, total_scenes - 1)
        use_act_filter = False
    else:
        start_idx = 0
        end_idx = total_scenes - 1
        use_act_filter = True

    tasks = []

    for i, scene in enumerate(updated["scenes"]):
        if not (start_idx <= i <= end_idx):
            continue

        if not use_act_filter or scene_needs_rewrite(scene, change_plan):
            tasks.append((i, rewrite_scene_with_gemini(scene, change_plan)))

    if tasks:
        results = await asyncio.gather(
            *[task for _, task in tasks],
            return_exceptions=True
        )

        for (idx, _), result in zip(tasks, results):
            if isinstance(result, Exception):
                continue

            original = canonical["scenes"][idx]
            rewritten = result

            diffs = diff_scene(original, rewritten)

            if diffs:
                diff_report["scenes_changed"].append({
                    "scene_index": idx + 1,
                    "scene_id": original["scene_id"],
                    "heading": original["heading"],
                    "diffs": diffs
                })

            updated["scenes"][idx] = rewritten

    fountain_text = screenplay_to_fountain(updated)

    return {
        "fountain_text": fountain_text,
        "diff_report": diff_report
    }
