import os
import xml.etree.ElementTree as ET
from pathlib import Path
from docx import Document
from google import genai
from google.genai import types

# ==========================
# CONFIG
# ==========================

MODEL_NAME = "gemini-2.5-pro"
MIN_LENGTH_RATIO = 0.85
MAX_OUTPUT_TOKENS_PER_SCENE = 8000
MIN_SCENE_WORDS = 20


# ==========================
# GEMINI CLIENT
# ==========================

def get_gemini_api_key() -> str:
    try:
        import streamlit as st
        if "GEMINI_API_KEY" in st.secrets:
            return st.secrets["GEMINI_API_KEY"]
    except Exception:
        pass

    from dotenv import load_dotenv
    load_dotenv()
    key = os.getenv("GEMINI_API_KEY")

    if not key:
        raise RuntimeError("❌ GEMINI_API_KEY not found.")

    return key


client = genai.Client(api_key=get_gemini_api_key())


# ==========================
# READ DOCX
# ==========================

def read_docx(file_path: str) -> str:
    doc = Document(file_path)
    return "\n".join(
        p.text.strip()
        for p in doc.paragraphs
        if p.text.strip()
    )


# ==========================
# FDX → SCENES (FOUNTAIN)
# ==========================

def fdx_to_fountain_scenes(fdx_path: str):
    tree = ET.parse(fdx_path)
    root = tree.getroot()

    scenes = []
    current_scene = []
    first_heading_skipped = False

    for para in root.iter("Paragraph"):
        p_type = para.attrib.get("Type", "").strip()
        text_node = para.find("Text")

        if text_node is None:
            continue

        text = (text_node.text or "").strip()
        if not text:
            continue

        if p_type == "Scene Heading":
            if not first_heading_skipped:
                first_heading_skipped = True
                continue

            if current_scene:
                scene_text = "\n".join(current_scene).strip()

                if len(scene_text.split()) < MIN_SCENE_WORDS and scenes:
                    scenes[-1] += "\n\n" + scene_text
                else:
                    scenes.append(scene_text)

                current_scene = []

            current_scene.append(text.upper())
            current_scene.append("")
            continue

        # Convert formatting
        if p_type == "Character":
            current_scene.append(text.upper())
        elif p_type == "Parenthetical":
            current_scene.append(f"({text})")
        else:
            current_scene.append(text)

        current_scene.append("")

    if current_scene:
        scene_text = "\n".join(current_scene).strip()
        if len(scene_text.split()) < MIN_SCENE_WORDS and scenes:
            scenes[-1] += "\n\n" + scene_text
        else:
            scenes.append(scene_text)

    return scenes


# ==========================
# SCENE REWRITE
# ==========================

def rewrite_scene(scene_text: str, notes_text: str, retry=1):

    system_prompt = """
You are a professional screenplay rewrite engine.

Rewrite the provided scene based on the development notes.

STRICT RULES:
- Preserve Fountain formatting exactly.
- Maintain approximately the same length (+/- 5%).
- Rewrite ALL dialogue and action.
- Do NOT summarize.
- Do NOT condense.
- Do NOT remove content unless explicitly required.
- Output ONLY the rewritten scene.
"""

    user_prompt = f"""
DEVELOPMENT NOTES:
{notes_text}

--------------------------------
SCENE TO REWRITE (FOUNTAIN):
{scene_text}
"""

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[system_prompt, user_prompt],
        config=types.GenerateContentConfig(
            temperature=0.6,
            max_output_tokens=MAX_OUTPUT_TOKENS_PER_SCENE
        )
    )

    rewritten = response.text.strip()

    original_wc = len(scene_text.split())
    rewritten_wc = len(rewritten.split())

    if rewritten_wc < original_wc * MIN_LENGTH_RATIO and retry > 0:
        return rewrite_scene(scene_text, notes_text, retry=retry - 1)

    return rewritten


# ==========================
# MAIN REWRITE FUNCTION
# ==========================

def rewrite_fdx_scene_by_scene(
    fdx_path: str,
    notes_path: str,
    start_scene: int | None = None,
    end_scene: int | None = None,
):

    scenes = fdx_to_fountain_scenes(fdx_path)
    notes_text = read_docx(notes_path)

    rewritten_scenes = []

    for i, scene in enumerate(scenes, start=1):

        if start_scene and i < start_scene:
            rewritten_scenes.append(scene)
            continue

        if end_scene and i > end_scene:
            rewritten_scenes.append(scene)
            continue

        rewritten = rewrite_scene(scene, notes_text)
        rewritten_scenes.append(rewritten)

    final_script = "\n\n".join(rewritten_scenes)

    return final_script
