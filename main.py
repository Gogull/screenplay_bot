import streamlit as st
import asyncio
import tempfile
import json

from services.notes_to_json import notes_docx_to_change_plan
from services.rewrite_to_fountain import (
    rewrite_fdx_with_plan,
    parse_fdx_to_canonical
)
from services.drive import (
    list_files,
    download_file
)

# -------------------------------------------------
# DRIVE FOLDER IDS
# -------------------------------------------------
SCREENPLAYS_FOLDER_ID = "1d0F56K_cKtV-_Km00ieXlnCSGVFTH4ub"
NOTES_FOLDER_ID = "1d9B_GZbgOUdOhamN1ypBNlvAbEMO37II"

st.set_page_config(layout="wide")
st.title("🎬 Screenplay Conversion")

# -------------------------------------------------
# CLEAR CACHE ONLY ON FULL PAGE REFRESH
# -------------------------------------------------
if "initialized" not in st.session_state:
    st.cache_data.clear()
    st.session_state.initialized = True


# -------------------------------------------------
# CACHED DRIVE CALLS (NO TTL)
# -------------------------------------------------
@st.cache_data(show_spinner=False)
def cached_list_files(folder_id):
    return list_files(folder_id)


@st.cache_data(show_spinner=False)
def cached_download_file(file_id):
    return download_file(file_id)


@st.cache_data(show_spinner=False)
def cached_parse_fdx(path):
    return parse_fdx_to_canonical(path)


# -------------------------------------------------
# SESSION STATE INIT
# -------------------------------------------------
if "fountain_text" not in st.session_state:
    st.session_state.fountain_text = None

if "output_name" not in st.session_state:
    st.session_state.output_name = None

if "change_plan" not in st.session_state:
    st.session_state.change_plan = None


# -------------------------------------------------
# LOAD SCREENPLAYS
# -------------------------------------------------
screenplays = cached_list_files(SCREENPLAYS_FOLDER_ID)

if not screenplays:
    st.error("No screenplays found in Drive.")
    st.stop()

screenplay_name = st.selectbox(
    "Select screenplay",
    [f["name"] for f in screenplays]
)

screenplay_file = next(
    f for f in screenplays if f["name"] == screenplay_name
)

with tempfile.NamedTemporaryFile(delete=False, suffix=".fdx") as tmp_fdx:
    tmp_fdx.write(cached_download_file(screenplay_file["id"]))
    fdx_path = tmp_fdx.name


# -------------------------------------------------
# SCENE DETECTION
# -------------------------------------------------
canonical = cached_parse_fdx(fdx_path)
total_scenes = len(canonical.get("scenes", []))

st.info(f"🎞️ Detected **{total_scenes} scenes**")


# -------------------------------------------------
# SCENE RANGE
# -------------------------------------------------
apply_mode = st.radio(
    "Apply notes to:",
    ["Entire Screenplay", "Specific Scene Range"]
)

start_scene = None
end_scene = None

if apply_mode == "Specific Scene Range":
    col1, col2 = st.columns(2)

    start_scene = col1.number_input(
        "Start scene",
        min_value=1,
        max_value=total_scenes,
        value=1
    )

    end_scene = col2.number_input(
        "End scene",
        min_value=start_scene,
        max_value=total_scenes,
        value=min(start_scene + 5, total_scenes)
    )


# -------------------------------------------------
# LOAD NOTES
# -------------------------------------------------
notes_files = cached_list_files(NOTES_FOLDER_ID)

if not notes_files:
    st.error("No notes found in Drive.")
    st.stop()

notes_name = st.selectbox(
    "Select revision notes",
    [f["name"] for f in notes_files]
)

notes_file = next(
    f for f in notes_files if f["name"] == notes_name
)

with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp_notes:
    tmp_notes.write(cached_download_file(notes_file["id"]))
    notes_path = tmp_notes.name


# -------------------------------------------------
# SAFE ASYNC RUNNER
# -------------------------------------------------
def run_async_task(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(coro)
    loop.close()
    return result


# -------------------------------------------------
# CONVERT BUTTON
# -------------------------------------------------
if st.button("🚀 Convert"):

    progress_bar = st.progress(0)
    status_text = st.empty()

    # Step 1: Parse notes → change plan
    status_text.text("Step 1/3: Parsing notes...")
    change_plan = notes_docx_to_change_plan(notes_path)
    st.session_state.change_plan = change_plan
    progress_bar.progress(33)

    # Step 2: Rewrite screenplay
    status_text.text("Step 2/3: Rewriting screenplay...")

    if start_scene and end_scene:
        suffix = f"_S{start_scene:03}-S{end_scene:03}"
    else:
        suffix = "_FULL"

    output_name = screenplay_name.replace(
        ".fdx",
        f"{suffix}.fountain"
    )

    result = run_async_task(
        rewrite_fdx_with_plan(
            fdx_path,
            change_plan,
            start_scene=start_scene,
            end_scene=end_scene
        )
    )

    st.session_state.fountain_text = result.get("fountain_text")
    st.session_state.output_name = output_name

    progress_bar.progress(66)

    # Step 3: Finalize
    status_text.text("Step 3/3: Finalizing output...")
    progress_bar.progress(100)
    status_text.text("✅ Conversion Complete")


# -------------------------------------------------
# DOWNLOAD OUTPUT
# -------------------------------------------------
if st.session_state.fountain_text:

    st.download_button(
        "⬇️ Download Screenplay (Fountain)",
        data=st.session_state.fountain_text,
        file_name=st.session_state.output_name,
        mime="text/plain"
    )


# -------------------------------------------------
# DOWNLOAD CHANGE INSTRUCTIONS JSON
# -------------------------------------------------
if st.session_state.change_plan:

    json_data = json.dumps(
        st.session_state.change_plan,
        indent=2
    )

    st.download_button(
        "⬇️ Download Change Instructions (JSON)",
        data=json_data,
        file_name="change_instructions.json",
        mime="application/json"
    )

