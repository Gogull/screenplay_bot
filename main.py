import streamlit as st
import asyncio
import tempfile

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
SCREENPLAYS_FOLDER_ID = "1WIvj-3_tQO0Bhw228EwZ35Sh6j9e8VuU"
NOTES_FOLDER_ID = "1de-v2V4ZvFcm4qEhL0P3M-LC0jby5l8T"

st.set_page_config(layout="wide")
st.title("🎬 Screenplay Conversion")

# -------------------------------------------------
# SESSION STATE
# -------------------------------------------------
if "last_diff" not in st.session_state:
    st.session_state.last_diff = None

if "fountain_text" not in st.session_state:
    st.session_state.fountain_text = None

if "output_name" not in st.session_state:
    st.session_state.output_name = None

# -------------------------------------------------
# SELECT SCREENPLAY
# -------------------------------------------------
screenplays = list_files(SCREENPLAYS_FOLDER_ID)

if not screenplays:
    st.error("No screenplays found in Drive.")
    st.stop()

screenplay_name = st.selectbox(
    "Select screenplay",
    [f["name"] for f in screenplays]
)

screenplay_file = next(f for f in screenplays if f["name"] == screenplay_name)

with tempfile.NamedTemporaryFile(delete=False, suffix=".fdx") as tmp_fdx:
    tmp_fdx.write(download_file(screenplay_file["id"]))
    fdx_path = tmp_fdx.name

# -------------------------------------------------
# SCENE DETECTION
# -------------------------------------------------
canonical = parse_fdx_to_canonical(fdx_path)
total_scenes = len(canonical["scenes"])
st.info(f"🎞️ Detected **{total_scenes} scenes**")

# -------------------------------------------------
# SCENE RANGE
# -------------------------------------------------
apply_mode = st.radio(
    "Apply notes to:",
    ["Entire Screenplay", "Specific Scene Range"]
)

start_scene = end_scene = None

if apply_mode == "Specific Scene Range":
    col1, col2 = st.columns(2)

    start_scene = col1.number_input(
        "Start scene",
        1,
        total_scenes,
        1
    )

    end_scene = col2.number_input(
        "End scene",
        start_scene,
        total_scenes,
        min(start_scene + 5, total_scenes)
    )

# -------------------------------------------------
# SELECT NOTES
# -------------------------------------------------
notes_files = list_files(NOTES_FOLDER_ID)

if not notes_files:
    st.error("No notes found in Drive.")
    st.stop()

notes_name = st.selectbox(
    "Select revision notes",
    [f["name"] for f in notes_files]
)

notes_file = next(f for f in notes_files if f["name"] == notes_name)

with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp_notes:
    tmp_notes.write(download_file(notes_file["id"]))
    notes_path = tmp_notes.name

# -------------------------------------------------
# CONVERT
# -------------------------------------------------
if st.button("🚀 Convert"):
    with st.spinner("Processing screenplay…"):
        change_plan = notes_docx_to_change_plan(notes_path)

        suffix = (
            f"_S{start_scene:03}-S{end_scene:03}"
            if start_scene and end_scene
            else "_FULL"
        )

        output_name = screenplay_name.replace(".fdx", f"{suffix}.fountain")

        result = asyncio.run(
            rewrite_fdx_with_plan(
                fdx_path,
                change_plan,
                output_path=":memory:",
                start_scene=start_scene,
                end_scene=end_scene
            )
        )

        st.session_state.fountain_text = result["fountain_text"]
        st.session_state.output_name = output_name
        st.session_state.last_diff = result.get("diff_report")

    st.success("✅ Conversion complete")

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
# HUMAN-READABLE CHANGE SUMMARY
# -------------------------------------------------
if st.session_state.last_diff:
    st.divider()
    st.subheader("✏️ Changes Applied")

    diff = st.session_state.last_diff
    scenes = diff.get("scenes_changed", [])

    if not scenes:
        st.info("No changes were made.")
    else:
        for scene in scenes:
            scene_id = scene.get("scene_id", "Unknown Scene")
            heading = scene.get("heading", "")

            st.markdown(f"## 🎬 {scene_id} — {heading}")

            for d in scene.get("diffs", []):
                change_type = d.get("type", "Change")
                before = d.get("before", "").strip()
                after = d.get("after", "").strip()

                st.markdown(f"**{change_type} updated**")

                col1, col2 = st.columns(2)

                with col1:
                    st.markdown("❌ **Before**")
                    st.markdown(
                        f"""
                        <div style="
                            background-color:#2b2b2b;
                            padding:10px;
                            border-radius:6px;
                            font-family:monospace;
                            white-space:pre-wrap;
                        ">
                        {before}
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

                with col2:
                    st.markdown("✅ **After**")
                    st.markdown(
                        f"""
                        <div style="
                            background-color:#1f3b2c;
                            padding:10px;
                            border-radius:6px;
                            font-family:monospace;
                            white-space:pre-wrap;
                        ">
                        {after}
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

                st.markdown("")

            st.markdown("---")
