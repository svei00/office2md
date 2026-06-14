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


# ------------------------------------------------------ ollama detection ---
def ollama_native_root(base_url: str) -> str:
    """Turn an OpenAI-compatible base URL into Ollama's native API root.

    e.g. http://localhost:11434/v1 -> http://localhost:11434
    """
    root = base_url.strip().rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return root.rstrip("/")


@st.cache_data(show_spinner=False, ttl=300)
def list_ollama_vision_models(base_url: str) -> list[str]:
    """Return installed Ollama models that advertise the 'vision' capability.

    Raises on connection failure so the caller can fall back to manual entry.
    """
    import requests

    root = ollama_native_root(base_url)
    tags = requests.get(f"{root}/api/tags", timeout=4).json()
    vision: list[str] = []
    for m in tags.get("models", []):
        name = m.get("name")
        if not name:
            continue
        try:
            info = requests.post(
                f"{root}/api/show", json={"model": name}, timeout=6
            ).json()
        except Exception:
            continue  # skip a model we can't introspect
        if "vision" in (info.get("capabilities") or []):
            vision.append(name)
    return sorted(vision)


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
if llm_enabled and st.sidebar.button("🔄 Detect vision models from Ollama"):
    list_ollama_vision_models.clear()  # bust the cache, re-query

default_model = os.environ.get("LLM_MODEL", "")
detected: list[str] = []
detect_error: str | None = None
if llm_enabled:
    try:
        detected = list_ollama_vision_models(llm_base_url)
    except Exception as exc:
        detect_error = str(exc)

if detected:
    # keep an env-provided default selected if it's actually vision-capable
    index = detected.index(default_model) if default_model in detected else 0
    llm_model = st.sidebar.selectbox(
        "Vision model (auto-detected)",
        detected,
        index=index,
        disabled=not llm_enabled,
        help="Only Ollama models reporting the 'vision' capability are listed.",
    )
else:
    if llm_enabled and detect_error:
        st.sidebar.caption(
            "⚠️ Couldn't auto-detect from Ollama "
            f"({detect_error}). Type the model name, or use an OpenAI model."
        )
    llm_model = st.sidebar.text_input(
        "Vision model",
        value=default_model or "qwen2.5vl:7b",
        disabled=not llm_enabled,
        help="Vision-capable model, e.g. qwen2.5vl:7b, llava, gpt-4o-mini.",
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
