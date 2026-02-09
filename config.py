import os

def get_gemini_api_key() -> str:
    # 1️⃣ Streamlit Cloud
    try:
        import streamlit as st
        if "GEMINI_API_KEY" in st.secrets:
            return st.secrets["GEMINI_API_KEY"]
    except Exception:
        pass

    # 2️⃣ Local (.env or system env)
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        return api_key

    raise RuntimeError(
        "GEMINI_API_KEY not found in Streamlit secrets or environment variables"
    )
