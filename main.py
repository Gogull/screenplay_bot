import re
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
# DUPLICATE HEADING REMOVER
# =============================

def normalize_heading(heading: str) -> str:
    """
    Strip time-of-day qualifiers and normalize punctuation so that
    headings referring to the same location compare as equal regardless
    of em-dash vs hyphen, CONTINUOUS vs DAY, trailing spaces, etc.
    """
    # Normalize em-dash and en-dash to regular hyphen
    heading = heading.replace('\u2013', '-').replace('\u2014', '-')
    # Remove time qualifiers including CONTINUOUS
    heading = re.sub(
        r'\s*[-]\s*(DAY|NIGHT|DAWN|DUSK|CONTINUOUS|LATER|MOMENTS LATER|'
        r'MORNING|AFTERNOON|EVENING|SAME TIME|SAME|INTERCUT)\b.*',
        '',
        heading,
        flags=re.IGNORECASE
    )
    return heading.strip().upper()


def remove_duplicate_headings(parts: list) -> str:
    """
    When joining beat segments, remove a scene heading from the start
    of a segment if it refers to the same location as the last scene
    heading of the previous segment.

    Legitimate time-shift transitions (NIGHT -> DAY, DAY -> LATER) are
    preserved because normalize_heading strips the qualifier before
    comparing — if the base location is the same the heading is a
    duplicate; if the base location differs it is kept.
    """
    scene_heading_pattern = re.compile(
        r'^(INT\.|EXT\.)[^\n]+', re.IGNORECASE | re.MULTILINE
    )

    def get_last_heading(text: str) -> str:
        matches = scene_heading_pattern.findall(text)
        return normalize_heading(matches[-1]) if matches else ""

    def get_first_heading(text: str) -> str:
        match = scene_heading_pattern.search(text)
        return normalize_heading(match.group(0)) if match else ""

    def strip_opening_heading(text: str) -> str:
        """Remove the first scene heading line and trailing blank lines."""
        text = scene_heading_pattern.sub('', text, count=1)
        return text.lstrip('\n')

    merged = []
    for i, part in enumerate(parts):
        if not part.strip():
            continue
        if i == 0:
            merged.append(part)
            continue

        last_heading = get_last_heading(merged[-1])
        first_heading = get_first_heading(part)

        if last_heading and first_heading and last_heading == first_heading:
            part = strip_opening_heading(part)

        merged.append(part)

    return "\n\n".join(merged)


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

            BATCH_SIZE = 5
            screenplay_parts = [""] * len(beats)

            async def write_single_beat(i, beat):
                progress_text.text(f"Writing beat {i+1}/{len(beats)}...")
                prev_beat = beats[i - 1] if i > 0 else None
                next_beat = beats[i + 1] if i < len(beats) - 1 else None
                return await retry_async(
                    write_beat_agent,
                    current_beat=beat,
                    previous_beat=prev_beat,
                    next_beat=next_beat,
                    global_summary=global_summary,
                    act_level_plan=act_plan,
                    reference_scenes=scene_refs[:6]
                )

            for batch_start in range(0, len(beats), BATCH_SIZE):
                batch_indices = range(
                    batch_start,
                    min(batch_start + BATCH_SIZE, len(beats))
                )
                batch_results = await asyncio.gather(
                    *[write_single_beat(i, beats[i]) for i in batch_indices]
                )
                for idx, result in zip(batch_indices, batch_results):
                    screenplay_parts[idx] = result.get("screenplay_segment", "")

            # Join segments, removing duplicate consecutive scene headings
            # that appear at beat boundaries
            final_script = remove_duplicate_headings(screenplay_parts)

            progress_text.text("🎉 Screenplay complete.")

            return architect_data, final_script

        # Fix: use asyncio.new_event_loop() to avoid Streamlit
        # reuse conflict with the semaphore's bound event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            architect_data, final_script = loop.run_until_complete(run_pipeline())
        finally:
            loop.close()

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