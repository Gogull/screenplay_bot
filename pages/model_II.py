import streamlit as st
import tempfile
import asyncio
import json
from pathlib import Path
from services.drive import list_files, download_file
from model_II.scene_summarizer import architect_agent
from model_II.scene_writer_agent import rewrite_scene_agent
from docx import Document


# =============================
# Drive Folder IDs
# =============================
SCREENPLAYS_FOLDER_ID = "1d0F56K_cKtV-_Km00ieXlnCSGVFTH4ub"
NOTES_FOLDER_ID = "1d9B_GZbgOUdOhamN1ypBNlvAbEMO37II"

st.set_page_config(layout="wide")
st.title("📖 Screenplay Architect + Rewrite System")


# =============================
# Cache Drive Lists
# =============================
# =============================
# Load Drive Files (NO CACHE)
# =============================
screenplays = list_files(SCREENPLAYS_FOLDER_ID)
notes_files = list_files(NOTES_FOLDER_ID)

# =============================
# Select Screenplay
# =============================
screenplay_name = st.selectbox(
    "Select screenplay",
    [f["name"] for f in screenplays]
)
screenplay_file = next(f for f in screenplays if f["name"] == screenplay_name)


# =============================
# Select Notes (Optional)
# =============================
notes_name = st.selectbox(
    "Select notes (optional)",
    ["None"] + [f["name"] for f in notes_files]
)

notes_text = ""
if notes_name != "None":
    notes_file = next(f for f in notes_files if f["name"] == notes_name)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp_docx:
        tmp_docx.write(download_file(notes_file["id"]))
        doc_path = tmp_docx.name
    doc = Document(doc_path)
    notes_text = "\n".join([p.text for p in doc.paragraphs])


# =============================
# Retry Wrapper
# =============================
async def retry_async(func, *args, retries=5, delay=2, backoff=2, **kwargs):
    current_delay = delay
    for attempt in range(retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            msg = str(e)
            if "503" in msg or "high demand" in msg.lower() or "unavailable" in msg.lower():
                if attempt < retries - 1:
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
                    continue
            raise e


# =============================
# Generate Architect Plan
# =============================
if st.button("🧠 Generate Architect Action Plan"):

    with tempfile.NamedTemporaryFile(delete=False, suffix=".fdx") as tmp_fdx:
        tmp_fdx.write(download_file(screenplay_file["id"]))
        fdx_path = tmp_fdx.name

    progress_text = st.empty()

    with st.spinner("Running architect pipeline..."):

        async def run_pipeline():
            progress_text.text("Running architect agent...")
            result = await retry_async(
                architect_agent,
                fdx_path,
                notes=notes_text
            )
            progress_text.text("Architect plan ready.")
            return result

        st.session_state["full_pipeline_result"] = asyncio.run(run_pipeline())


# =============================
# Architect Plan UI (CLEAN)
# =============================
if "full_pipeline_result" in st.session_state:

    data = st.session_state["full_pipeline_result"]

    architect_json = json.dumps({
        "global_summary": data.get("global_summary"),
        "act_level_plan": data.get("act_level_plan"),
        "scene_level_plan": data.get("scene_level_plan")
    }, indent=2)

    st.success("Architect action plan generated.")

    # Download Button (Primary)
    st.download_button(
        label="⬇️ Download Architect Action Plan (JSON)",
        data=architect_json,
        file_name=f"{Path(screenplay_name).stem}_architect_plan.json",
        mime="application/json",
        use_container_width=True
    )

    # Expandable Preview (Optional)
    with st.expander("Preview Architect Plan"):
        st.json(json.loads(architect_json))


# =============================
# Rewrite Full Screenplay
# =============================
if st.button("✍️ Rewrite Full Screenplay") and "full_pipeline_result" in st.session_state:

    data = st.session_state["full_pipeline_result"]

    scenes = data["scenes"]
    scene_summaries = data["scene_summaries"]
    global_summary = data["global_summary"]
    act_level_plan = data.get("act_level_plan", {})
    scene_level_plan = data.get("scene_level_plan", [])

    rewritten_scenes = []
    progress_text = st.empty()

    with st.spinner("Rewriting scenes..."):

        async def run_rewrite():
            updated_global = global_summary

            for i, scene_summary in enumerate(scene_summaries):

                progress_text.text(f"Rewriting scene {i+1}/{len(scene_summaries)}...")

                prev_summary = scene_summaries[i-1] if i > 0 else None
                next_summary = scene_summaries[i+1] if i < len(scene_summaries)-1 else None
                scene_plan = scene_level_plan[i] if i < len(scene_level_plan) else {}

                rewrite_result = await retry_async(
                    rewrite_scene_agent,
                    scene_full_text=scenes[i]["full_text"],
                    previous_summary=prev_summary,
                    current_summary=scene_summary,
                    next_summary=next_summary,
                    global_summary=updated_global,
                    act_level_plan=act_level_plan,
                    scene_level_plan=scene_plan
                )

                rewritten_scenes.append(rewrite_result["updated_scene_text"])

                scene_summaries[i] = rewrite_result.get(
                    "updated_scene_summary",
                    scene_summary
                )

                updated_global = rewrite_result.get(
                    "updated_global_summary",
                    updated_global
                )

            return "\n\n".join(rewritten_scenes), updated_global

        fountain_text, updated_global_summary = asyncio.run(run_rewrite())

        st.session_state["full_rewrite_result"] = {
            "fountain_text": fountain_text,
            "scene_summaries": scene_summaries,
            "global_summary": updated_global_summary
        }


# =============================
# Download Rewritten Screenplay
# =============================
if "full_rewrite_result" in st.session_state:

    fountain_text = st.session_state["full_rewrite_result"]["fountain_text"]

    st.success("Rewrite complete.")

    st.download_button(
        label="⬇️ Download Rewritten Screenplay (Fountain)",
        data=fountain_text,
        file_name=f"{Path(screenplay_name).stem}_rewritten.fountain",
        mime="text/plain",
        use_container_width=True
    )