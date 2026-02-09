import os
import json
from pathlib import Path

from google import genai
from google.genai import types
from docx import Document

# ---------------------------------
# LOAD API KEY (Streamlit → .env)
# ---------------------------------
def get_gemini_api_key() -> str:
    # 1️⃣ Try Streamlit secrets
    try:
        import streamlit as st
        if "GEMINI_API_KEY" in st.secrets:
            return st.secrets["GEMINI_API_KEY"]
    except Exception:
        # Streamlit not available (local scripts, tests, etc.)
        pass

    # 2️⃣ Load .env from project root (one level above /services)
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
# SYSTEM PROMPT
# ---------------------------------
SYSTEM_PROMPT = """
You are a screenplay development analyst.

Your task:
- Read screenplay revision notes
- Extract requested changes
- Convert them into structured JSON

Rules:
- Do NOT rewrite screenplay scenes
- Do NOT invent new story content
- Do NOT explain your reasoning
- Output ONLY valid JSON
"""

# ---------------------------------
# HELPERS
# ---------------------------------
def read_docx(file_path: str) -> str:
    doc = Document(file_path)
    return "\n".join(
        p.text.strip() for p in doc.paragraphs if p.text.strip()
    )


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

    return json.loads(response.text)
