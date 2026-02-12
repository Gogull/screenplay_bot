import os
import json
from pathlib import Path

from google import genai
from google.genai import types
from docx import Document


# ---------------------------------
# LOAD API KEY
# ---------------------------------
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

    raise RuntimeError(
        "❌ GEMINI_API_KEY not found.\n"
        "Set it in Streamlit secrets or in a .env file."
    )


# ---------------------------------
# CONFIG
# ---------------------------------
API_KEY = get_gemini_api_key()
MODEL_NAME = "gemini-2.5-pro"
client = genai.Client(api_key=API_KEY)


# ---------------------------------
# STRONGER SYSTEM PROMPT
# ---------------------------------
SYSTEM_PROMPT = """
You are a professional screenplay development analyst.

You will receive a document containing revision notes for a screenplay.

Your task:
1. Extract actionable changes.
2. Convert them into structured JSON.
3. Classify where they apply.

Output format:

{
  "scene_level_changes": [
    {
      "description": "Clear, specific instruction",
      "placement": "Act I | Act II | Act III | Entire Screenplay | Specific Scene"
    }
  ]
}

Rules:

- Convert high-level thematic notes into actionable instructions.
- Preserve original intent of notes.
- Do NOT rewrite scenes.
- Do NOT invent new story content.
- If a note applies to the whole script, use "Entire Screenplay".
- If unclear but broad, default to "Entire Screenplay".
- Keep descriptions concise but precise.
- Output ONLY valid JSON.
"""


# ---------------------------------
# HELPERS
# ---------------------------------
def read_docx(file_path: str) -> str:
    doc = Document(file_path)
    return "\n".join(
        p.text.strip() for p in doc.paragraphs if p.text.strip()
    )


def normalize_placement(text: str) -> str:
    if not text:
        return "Entire Screenplay"

    t = text.lower()

    if "act i" in t:
        return "Act I"
    if "act ii" in t:
        return "Act II"
    if "act iii" in t:
        return "Act III"
    if "entire" in t or "whole" in t or "throughout" in t:
        return "Entire Screenplay"
    if "scene" in t:
        return "Specific Scene"

    # Fallback
    return "Entire Screenplay"


# ---------------------------------
# MAIN FUNCTION
# ---------------------------------
def notes_docx_to_change_plan(notes_path: str) -> dict:
    notes_text = read_docx(notes_path)

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[SYSTEM_PROMPT, notes_text],
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json"
        )
    )

    # -------------------------------
    # Parse JSON safely
    # -------------------------------
    try:
        plan = json.loads(response.text)
    except Exception:
        raise ValueError("Gemini returned invalid JSON for change plan.")

    if not isinstance(plan, dict):
        raise ValueError("Change plan must be a JSON object.")

    if "scene_level_changes" not in plan:
        plan["scene_level_changes"] = []

    if not isinstance(plan["scene_level_changes"], list):
        raise ValueError("scene_level_changes must be a list.")

    cleaned_changes = []

    for idx, change in enumerate(plan["scene_level_changes"], start=1):
        if not isinstance(change, dict):
            continue

        description = change.get("description", "").strip()
        placement = normalize_placement(change.get("placement", ""))

        if not description:
            continue

        cleaned_changes.append({
            "change_id": f"C{idx}",
            "description": description,
            "placement": placement
        })

    return {
        "scene_level_changes": cleaned_changes
    }
