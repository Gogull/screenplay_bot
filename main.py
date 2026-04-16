import re
import streamlit as st
import tempfile
import asyncio
import json
from pathlib import Path
from services.drive import list_files, download_file
from model_IV.scene_summarizer import story_breaking_agent
from model_IV.scene_writer_agent import (
    phase3_scene_construction,
    phase4_dialogue_pass,
    phase5_visual_polish,
    phase6_industry_compile,
)
from docx import Document


# =============================
# Drive Folder IDs
# =============================
SCREENPLAYS_FOLDER_ID = "1d0F56K_cKtV-_Km00ieXlnCSGVFTH4ub"
NOTES_FOLDER_ID = "1d9B_GZbgOUdOhamN1ypBNlvAbEMO37II"

st.set_page_config(layout="wide")
st.title("Screenplay Pipeline")


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
# HEADING NORMALIZER
# =============================

def normalize_heading(heading: str) -> str:
    heading = heading.replace('\u2013', '-').replace('\u2014', '-')
    heading = re.sub(r'\s*\(.*?\)\s*$', '', heading)
    heading = re.sub(
        r'\s*[-]\s*(DAY|NIGHT|DAWN|DUSK|CONTINUOUS|LATER|MOMENTS LATER|'
        r'MORNING|AFTERNOON|EVENING|SAME TIME|SAME|INTERCUT)\b.*',
        '',
        heading,
        flags=re.IGNORECASE
    )
    return heading.strip().upper()


# =============================
# WITHIN-SEGMENT DUPLICATE REMOVER
# =============================

def remove_internal_duplicate_headings(text: str) -> str:
    lines = text.split('\n')
    result = []
    last_heading = None
    for line in lines:
        if re.match(r'^(INT\.|EXT\.)', line.strip(), re.IGNORECASE):
            current = normalize_heading(line.strip())
            if current == last_heading:
                continue
            last_heading = current
        result.append(line)
    return '\n'.join(result)


# =============================
# CROSS-SEGMENT DUPLICATE REMOVER
# =============================

def remove_duplicate_headings(parts: list) -> str:
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
        text = scene_heading_pattern.sub('', text, count=1)
        return text.lstrip('\n')

    merged = []
    last_seen_heading = ""

    for i, part in enumerate(parts):
        if not part.strip():
            continue

        part = remove_internal_duplicate_headings(part)
        first_heading = get_first_heading(part)

        if i == 0:
            merged.append(part)
            last_seen_heading = get_last_heading(part) or first_heading
            continue

        if last_seen_heading and first_heading and last_seen_heading == first_heading:
            part = strip_opening_heading(part)

        new_last = get_last_heading(part)
        if new_last:
            last_seen_heading = new_last
        elif first_heading:
            last_seen_heading = first_heading

        merged.append(part)

    return "\n\n".join(merged)


# =============================
# MAIN 6-PHASE PIPELINE
# =============================
if st.button("Rewrite Screenplay"):

    with tempfile.NamedTemporaryFile(delete=False, suffix=".fdx") as tmp_fdx:
        tmp_fdx.write(download_file(screenplay_file["id"]))
        fdx_path = tmp_fdx.name

    progress_text = st.empty()
    progress_bar = st.progress(0)

    with st.spinner("Running 6-phase pipeline..."):

        async def run_pipeline():

            # =============================================
            # PHASES 1-2: STORY BREAKING + BLUEPRINT
            # =============================================
            progress_text.text("Phase 1-2: Breaking story and building structural blueprint...")
            progress_bar.progress(5)

            phase12_data = await retry_async(
                story_breaking_agent,
                fdx_path,
                notes=notes_text
            )

            story_dna = phase12_data["story_dna"]
            blueprint = phase12_data["blueprint"]
            steps = blueprint.get("step_outline", [])

            progress_text.text(f"Phases 1-2 complete. {len(steps)} steps in outline.")
            progress_bar.progress(20)

            # =============================================
            # PHASE 3: SCENE CONSTRUCTION (First Draft)
            # =============================================
            progress_text.text("Phase 3: Constructing first draft scenes...")

            BATCH_SIZE = 5
            screenplay_parts = [""] * len(steps)

            async def write_single_step(i, step):
                progress_text.text(f"Phase 3: Writing step {i+1}/{len(steps)}...")
                prev_step = steps[i - 1] if i > 0 else None
                next_step = steps[i + 1] if i < len(steps) - 1 else None
                return await retry_async(
                    phase3_scene_construction,
                    step=step,
                    story_dna=story_dna,
                    prev_step=prev_step,
                    next_step=next_step,
                )

            for batch_start in range(0, len(steps), BATCH_SIZE):
                batch_indices = range(
                    batch_start,
                    min(batch_start + BATCH_SIZE, len(steps))
                )
                batch_results = await asyncio.gather(
                    *[write_single_step(i, steps[i]) for i in batch_indices]
                )
                for idx, result in zip(batch_indices, batch_results):
                    screenplay_parts[idx] = result

            # Join and deduplicate headings
            first_draft = remove_duplicate_headings(screenplay_parts)

            progress_text.text("Phase 3 complete: First draft assembled.")
            progress_bar.progress(50)

            # =============================================
            # PHASE 4: SUBTEXT & DIALOGUE PASS
            # =============================================
            progress_text.text("Phase 4: Polishing dialogue for subtext and naturalism...")

            polished_dialogue = await retry_async(
                phase4_dialogue_pass,
                screenplay_text=first_draft,
                story_dna=story_dna,
            )

            if not polished_dialogue.strip():
                polished_dialogue = first_draft

            progress_text.text("Phase 4 complete: Dialogue polished.")
            progress_bar.progress(70)

            # =============================================
            # PHASE 5: VISUAL & ATMOSPHERIC POLISH
            # =============================================
            progress_text.text("Phase 5: Polishing action lines and visual atmosphere...")

            polished_visual = await retry_async(
                phase5_visual_polish,
                screenplay_text=polished_dialogue,
                story_dna=story_dna,
            )

            if not polished_visual.strip():
                polished_visual = polished_dialogue

            progress_text.text("Phase 5 complete: Visuals polished.")
            progress_bar.progress(85)

            # =============================================
            # PHASE 6: INDUSTRY COMPILER
            # =============================================
            progress_text.text("Phase 6: Final formatting pass...")

            final_script = await retry_async(
                phase6_industry_compile,
                screenplay_text=polished_visual,
            )

            if not final_script.strip():
                final_script = polished_visual

            progress_text.text("Pipeline complete.")
            progress_bar.progress(100)

            return phase12_data, final_script

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            phase12_data, final_script = loop.run_until_complete(run_pipeline())
        finally:
            loop.close()

        st.session_state["phase12"] = phase12_data
        st.session_state["final_script"] = final_script


# =============================
# OUTPUT SECTION
# =============================
if "phase12" in st.session_state:

    data = st.session_state["phase12"]

    blueprint_json = json.dumps({
        "story_dna": data.get("story_dna"),
        "blueprint": data.get("blueprint"),
    }, indent=2)

    st.success("6-phase pipeline completed.")

    col1, col2 = st.columns(2)

    with col1:
        st.download_button(
            label="Download Story DNA + Blueprint (JSON)",
            data=blueprint_json,
            file_name=f"{Path(screenplay_name).stem}_blueprint.json",
            mime="application/json",
            use_container_width=True
        )

    with col2:
        st.download_button(
            label="Download Screenplay (Fountain)",
            data=st.session_state["final_script"],
            file_name=f"{Path(screenplay_name).stem}_final.fountain",
            mime="text/plain",
            use_container_width=True
        )
