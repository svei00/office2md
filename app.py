"""Office2MD — convert Office docs, PDFs, and technical screenshots to Markdown.

Built on Microsoft's markitdown. Runs locally or self-hosted (Docker / DietPi),
so files never leave your network. Screenshots are transcribed with a vision
LLM (Ollama or any OpenAI-compatible endpoint) when one is configured.
"""

import io
import os
import tempfile
import zipfile
from pathlib import Path

import streamlit as st
from markitdown import MarkItDown

DOCUMENT_TYPES = [
    "docx", "doc", "pptx", "ppt", "xlsx", "xls", "pdf",
    "csv", "json", "xml", "html", "htm", "epub", "msg", "zip", "txt",
]
IMAGE_TYPES = ["png", "jpg", "jpeg"]

SCREENSHOT_PROMPT = (
    "Transcribe this screenshot into Markdown. Extract ALL visible text exactly "
    "as written, in the original language. Preserve structure: use Markdown "
    "tables for tabular data, headings for window/section titles, and lists for "
    "menus or options. Mention UI elements (buttons, fields, tabs) inline only "
    "when needed to understand the text. Do not summarize or omit anything."
)

st.set_page_config(page_title="Office2MD", page_icon="📝", layout="wide")


# ---------------------------------------------------------------- sidebar ---
st.sidebar.title("📝 Office2MD")
st.sidebar.caption("Documents → Markdown via Microsoft markitdown")

st.sidebar.subheader("Image transcription (vision LLM)")
llm_enabled = st.sidebar.toggle(
    "Enable screenshot/image transcription",
    value=bool(os.environ.get("LLM_BASE_URL") or os.environ.get("LLM_API_KEY")),
    help="Needs a vision model: Ollama (free, local) or an OpenAI-compatible API.",
)
llm_base_url = st.sidebar.text_input(
    "API base URL",
    value=os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1"),
    disabled=not llm_enabled,
    help="Ollama default: http://localhost:11434/v1 — from Docker on another "
    "machine, use that machine's LAN/Tailscale IP.",
)
llm_model = st.sidebar.text_input(
    "Vision model",
    value=os.environ.get("LLM_MODEL", "qwen2.5vl:7b"),
    disabled=not llm_enabled,
    help="Must be a vision-capable model, e.g. qwen2.5vl:7b, llava, gpt-4o-mini.",
)
llm_api_key = st.sidebar.text_input(
    "API key",
    value=os.environ.get("LLM_API_KEY", "ollama"),
    type="password",
    disabled=not llm_enabled,
    help="Ollama ignores the key (any value works). Required for OpenAI etc.",
)


def build_llm_kwargs() -> dict:
    """Conversion kwargs for markitdown; empty when transcription is off."""
    if not llm_enabled:
        return {}
    from openai import OpenAI

    client = OpenAI(base_url=llm_base_url, api_key=llm_api_key or "ollama")
    return {
        "llm_client": client,
        "llm_model": llm_model,
        "llm_prompt": SCREENSHOT_PROMPT,
    }


# ------------------------------------------------------------- conversion ---
@st.cache_resource
def get_converter() -> MarkItDown:
    return MarkItDown(enable_plugins=False)


def convert_upload(uploaded_file, llm_kwargs: dict) -> str:
    """Convert one uploaded file to Markdown text."""
    suffix = Path(uploaded_file.name).suffix
    # markitdown infers the format from the extension, so keep it on the temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.getbuffer())
        tmp_path = tmp.name
    try:
        result = get_converter().convert(tmp_path, **llm_kwargs)
        return result.text_content
    finally:
        os.unlink(tmp_path)


# -------------------------------------------------------------------- UI ----
tab_files, tab_url = st.tabs(["📁 Files", "🔗 URL"])

with tab_files:
    accepted = DOCUMENT_TYPES + (IMAGE_TYPES if llm_enabled else [])
    if not llm_enabled:
        st.info(
            "Images (PNG/JPG) are hidden because transcription is off — a bare "
            "screenshot only yields metadata. Enable the vision LLM in the "
            "sidebar to transcribe screenshots.",
            icon="ℹ️",
        )
    uploads = st.file_uploader(
        "Drop files here (multiple allowed)",
        type=accepted,
        accept_multiple_files=True,
    )

    if uploads and st.button("Convert to Markdown", type="primary"):
        results: dict[str, str] = {}
        llm_kwargs = build_llm_kwargs()
        progress = st.progress(0.0)
        for i, up in enumerate(uploads):
            with st.spinner(f"Converting {up.name}…"):
                try:
                    results[up.name] = convert_upload(up, llm_kwargs)
                except Exception as exc:  # surface per-file errors, keep going
                    st.error(f"**{up.name}** failed: {exc}")
            progress.progress((i + 1) / len(uploads))
        progress.empty()
        st.session_state["results"] = results

    for name, text in st.session_state.get("results", {}).items():
        md_name = f"{Path(name).stem}.md"
        with st.expander(f"✅ {name} → {md_name}", expanded=len(uploads or []) == 1):
            st.download_button(
                f"⬇️ Download {md_name}",
                text,
                file_name=md_name,
                mime="text/markdown",
                key=f"dl_{name}",
            )
            st.code(text, language="markdown")

    results = st.session_state.get("results", {})
    if len(results) > 1:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, text in results.items():
                zf.writestr(f"{Path(name).stem}.md", text)
        st.download_button(
            "⬇️ Download all as ZIP",
            buf.getvalue(),
            file_name="markdown_export.zip",
            mime="application/zip",
        )

with tab_url:
    url = st.text_input("Web page or YouTube URL")
    if url and st.button("Convert URL"):
        with st.spinner("Fetching and converting…"):
            try:
                result = get_converter().convert(url, **build_llm_kwargs())
                st.download_button(
                    "⬇️ Download Markdown",
                    result.text_content,
                    file_name="page.md",
                    mime="text/markdown",
                )
                st.code(result.text_content, language="markdown")
            except Exception as exc:
                st.error(f"Conversion failed: {exc}")
