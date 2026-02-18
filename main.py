import streamlit as st
import tempfile

from services.scene_rewriter import rewrite_fdx_scene_by_scene
from services.rewrite_to_fountain import parse_fdx_to_canonical
from services.drive import list_files, download_file

# =============================
# Folders
# =============================
SCREENPLAYS_FOLDER_ID = "1d0F56K_cKtV-_Km00ieXlnCSGVFTH4ub"
NOTES_FOLDER_ID = "1d9B_GZbgOUdOhamN1ypBNlvAbEMO37II"

st.set_page_config(layout="wide")
st.title("🎬 Screenplay Rewriter (Scene-by-Scene)")

# =============================
# Load Files from Drive
# =============================
screenplays = list_files(SCREENPLAYS_FOLDER_ID)
notes_files = list_files(NOTES_FOLDER_ID)

screenplay_name = st.selectbox(
    "Select screenplay",
    [f["name"] for f in screenplays]
)

notes_name = st.selectbox(
    "Select revision notes",
    [f["name"] for f in notes_files]
)

screenplay_file = next(f for f in screenplays if f["name"] == screenplay_name)
notes_file = next(f for f in notes_files if f["name"] == notes_name)

# =============================
# Parse FDX to canonical
# =============================
with tempfile.NamedTemporaryFile(delete=False, suffix=".fdx") as tmp_fdx:
    tmp_fdx.write(download_file(screenplay_file["id"]))
    fdx_path = tmp_fdx.name

canonical = parse_fdx_to_canonical(fdx_path)
total_scenes = len(canonical.get("scenes", []))
st.info(f"🎞️ Detected **{total_scenes} scenes**")

# =============================
# Scene Range Selection
# =============================
apply_mode = st.radio(
    "Apply notes to:",
    ["Entire Screenplay", "Specific Scene Range"]
)

start_scene = None
end_scene = None

if apply_mode == "Specific Scene Range":
    scene_options = [f"{i+1}: {scene['heading']}" for i, scene in enumerate(canonical["scenes"])]

    col1, col2 = st.columns(2)

    start_scene_idx = col1.selectbox(
        "Start scene",
        options=list(range(total_scenes)),
        format_func=lambda i: scene_options[i]
    )
    end_scene_idx = col2.selectbox(
        "End scene",
        options=list(range(start_scene_idx, total_scenes)),
        format_func=lambda i: scene_options[i]
    )

    start_scene = start_scene_idx + 1
    end_scene = end_scene_idx + 1

    st.markdown(f"**Selected range:** Scene {start_scene} → Scene {end_scene}")

# =============================
# Convert Button
# =============================
if st.button("🚀 Rewrite Screenplay"):

    with tempfile.NamedTemporaryFile(delete=False, suffix=".fdx") as tmp_fdx:
        tmp_fdx.write(download_file(screenplay_file["id"]))
        fdx_path = tmp_fdx.name

    with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp_notes:
        tmp_notes.write(download_file(notes_file["id"]))
        notes_path = tmp_notes.name

    with st.spinner("Rewriting scenes..."):
        final_script = rewrite_fdx_scene_by_scene(
            fdx_path,
            notes_path,
            start_scene=start_scene,
            end_scene=end_scene
        )

    output_name = screenplay_name.replace(".fdx", "_REWRITTEN.fountain")

    st.success("✅ Rewrite complete!")

    st.download_button(
        "⬇️ Download Rewritten Screenplay",
        data=final_script,
        file_name=output_name,
        mime="text/plain"
    )
