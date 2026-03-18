import streamlit as st
import tempfile
import asyncio
import json
from pathlib import Path
from services.drive import list_files, download_file
from model_III.scene_summarizer import architect_agent
from model_III.scene_writer_agent import write_beat_agent
from docx import Document


# =============================
# Drive Folder IDs
# =============================
SCREENPLAYS_FOLDER_ID = "1d0F56K_cKtV-_Km00ieXlnCSGVFTH4ub"
NOTES_FOLDER_ID = "1d9B_GZbgOUdOhamN1ypBNlvAbEMO37II"

st.set_page_config(layout="wide")
st.title("📖 Screenplay Architect + Beat-Based Rewrite System")


# =============================
# Load Drive Files
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
# Select Notes
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
        doc = Document(tmp_docx.name)
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
            if "503" in msg or "high demand" in msg.lower():
                if attempt < retries - 1:
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
                    continue
            raise e


# =============================
# MAIN PIPELINE BUTTON
# =============================
if st.button("🚀 Rewrite Screenplay"):

    with tempfile.NamedTemporaryFile(delete=False, suffix=".fdx") as tmp_fdx:
        tmp_fdx.write(download_file(screenplay_file["id"]))
        fdx_path = tmp_fdx.name

    progress_text = st.empty()

    with st.spinner("Running full pipeline..."):

        async def run_pipeline():

            # STEP 1: ARCHITECT
            progress_text.text("🧠 Generating architecture...")

            architect_data = await retry_async(
                architect_agent,
                fdx_path,
                notes=notes_text
            )

            progress_text.text("✅ Architecture ready.")

            # STEP 2: REWRITE
            progress_text.text("✍️ Writing screenplay...")

            beats = architect_data["beat_sheet"].get("beats", [])
            global_summary = architect_data["global_summary"]
            act_plan = architect_data.get("act_level_plan", {})
            scene_refs = architect_data.get("scene_summaries", [])

            screenplay_parts = []

            for i, beat in enumerate(beats):

                progress_text.text(f"Writing beat {i+1}/{len(beats)}...")

                prev_beat = beats[i - 1] if i > 0 else None
                next_beat = beats[i + 1] if i < len(beats) - 1 else None

                result = await retry_async(
                    write_beat_agent,
                    current_beat=beat,
                    previous_beat=prev_beat,
                    next_beat=next_beat,
                    global_summary=global_summary,
                    act_level_plan=act_plan,
                    reference_scenes=scene_refs[:3]
                )

                screenplay_parts.append(result["screenplay_segment"])

            final_script = "\n\n".join(screenplay_parts)

            progress_text.text("🎉 Screenplay complete.")

            return architect_data, final_script

        architect_data, final_script = asyncio.run(run_pipeline())

        # Store results
        st.session_state["architect"] = architect_data
        st.session_state["final_script"] = final_script


# =============================
# OUTPUT SECTION
# =============================
if "architect" in st.session_state:

    data = st.session_state["architect"]

    architect_json = json.dumps({
        "global_summary": data.get("global_summary"),
        "act_level_plan": data.get("act_level_plan"),
        "beat_sheet": data.get("beat_sheet")
    }, indent=2)

    st.success("✅ Full pipeline completed.")

    col1, col2 = st.columns(2)

    with col1:
        st.download_button(
            label="⬇️ Download Architect Plan",
            data=architect_json,
            file_name=f"{Path(screenplay_name).stem}_architect.json",
            mime="application/json",
            use_container_width=True
        )

    with col2:
        st.download_button(
            label="⬇️ Download Screenplay (Fountain)",
            data=st.session_state["final_script"],
            file_name=f"{Path(screenplay_name).stem}_final.fountain",
            mime="text/plain",
            use_container_width=True
        )