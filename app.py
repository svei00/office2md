"""Office2MD — convert Office docs, PDFs, and technical screenshots to Markdown.

Built on Microsoft's markitdown. Runs locally or self-hosted (Docker / DietPi),
so files never leave your network. Screenshots are transcribed with a vision
LLM (Ollama or any OpenAI-compatible endpoint) when one is configured.
"""

import io
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import streamlit as st
from markitdown import MarkItDown

from sheets import LeanSheetConverter

DOCUMENT_TYPES = [
    "docx", "pptx", "xlsx", "xlsm", "xls", "pdf",
    "csv", "json", "xml", "html", "htm", "epub", "msg", "zip", "txt",
]
IMAGE_TYPES = ["png", "jpg", "jpeg"]
# formats markitdown can't read; LibreOffice (if installed) upgrades them first
LEGACY_UPGRADE = {
    ".doc": "docx", ".rtf": "docx", ".odt": "docx",
    ".ppt": "pptx", ".odp": "pptx",
    ".ods": "xlsx",
}
SHEET_MODES = {
    "Auto (recommended)": "auto",
    "Markdown table": "table",
    "Cell list (A1: value)": "cells",
    "CSV": "csv",
}

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


def find_soffice() -> str | None:
    """LibreOffice binary: on PATH, or the default Windows install location."""
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    win = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    candidate = win / "LibreOffice" / "program" / "soffice.exe"
    return str(candidate) if candidate.exists() else None


SOFFICE = find_soffice()


# ---------------------------------------------------------------- sidebar ---
st.sidebar.title("📝 Office2MD")
st.sidebar.caption("Documents → Markdown via Microsoft markitdown")

st.sidebar.subheader("Token-lean output")
sheet_mode = SHEET_MODES[
    st.sidebar.selectbox(
        "Excel / CSV layout",
        list(SHEET_MODES),
        help="Auto: title/form cells as 'A1: value' lines, data blocks as a "
        "compact table. Cell list suits forms and invoices; CSV is the "
        "densest for big data tables.",
    )
]
sheet_formulas = st.sidebar.checkbox(
    "Show Excel formulas next to values",
    help="e.g. '150 (=B8*C8)'. Formulas with no saved value are always shown.",
)
sheet_max_rows = st.sidebar.number_input(
    "Max data rows per sheet (0 = all)",
    min_value=0,
    value=0,
    step=50,
    help="Cap huge sheets when the AI only needs the structure and a sample.",
)
compact = st.sidebar.checkbox(
    "Compact Markdown",
    value=True,
    help="Strip table padding, blank-line runs and embedded-image stubs.",
)
if not SOFFICE:
    st.sidebar.caption(
        "Install LibreOffice to also convert legacy .doc/.ppt and "
        "OpenDocument/RTF files."
    )

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
    md = MarkItDown(enable_plugins=False)
    md.register_converter(LeanSheetConverter())  # replaces the NaN-padded tables
    return md


def convert_kwargs() -> dict:
    """All per-run options passed through markitdown to the converters."""
    return {
        **build_llm_kwargs(),
        "sheet_mode": sheet_mode,
        "sheet_formulas": sheet_formulas,
        "sheet_max_rows": sheet_max_rows,
    }


def compact_markdown(text: str) -> str:
    """Drop formatting that costs tokens but carries no content."""
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith("```"):
            in_fence = not in_fence
        elif not in_fence and line.startswith("|") and line.endswith("|"):
            line = "|" + "|".join(c.strip() for c in line[1:-1].split("|")) + "|"
            if re.fullmatch(r"\|(?::?-+:?\|)+", line):
                line = re.sub(r"-+", "---", line)
            # markitdown emits an empty header row for header-less tables:
            # promote the first data row instead
            if (
                len(out) >= 2
                and re.fullmatch(r"\|+", out[-2])
                and re.fullmatch(r"\|(?:-+\|)+", out[-1])
            ):
                out[-2] = line
                continue
        out.append(line)
    text = "\n".join(out)
    text = re.sub(r"!\[([^\]]*)\]\(data:[^)]*\)", r"[image \1]", text)
    text = re.sub(r"\[image \]", "[image]", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def upgrade_legacy(path: Path, workdir: Path) -> Path:
    """Convert .doc/.ppt/ODF/RTF to the modern format with LibreOffice."""
    target = LEGACY_UPGRADE[path.suffix.lower()]
    if not SOFFICE:
        raise RuntimeError(
            f"{path.suffix} needs LibreOffice installed to convert it "
            f"(or re-save the file as .{target})."
        )
    subprocess.run(
        [
            SOFFICE,
            "--headless",
            # private profile, so parallel runs don't fight over the user's one
            f"-env:UserInstallation={(workdir / 'lo-profile').as_uri()}",
            "--convert-to", target,
            "--outdir", str(workdir),
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=300,
    )
    out = workdir / f"{path.stem}.{target}"
    if not out.exists():
        raise RuntimeError(f"LibreOffice could not convert {path.name}")
    return out


def convert_upload(uploaded_file, kwargs: dict) -> str:
    """Convert one uploaded file to Markdown text."""
    with tempfile.TemporaryDirectory() as tmp:
        # markitdown infers the format from the extension, so keep the name
        path = Path(tmp) / Path(uploaded_file.name).name
        path.write_bytes(uploaded_file.getbuffer())
        if path.suffix.lower() in LEGACY_UPGRADE:
            path = upgrade_legacy(path, Path(tmp))
        text = get_converter().convert(str(path), **kwargs).text_content
    return compact_markdown(text) if compact else text


def token_estimate(text: str) -> str:
    return f"≈{len(text) // 4:,} tokens"


# -------------------------------------------------------------------- UI ----
tab_files, tab_url = st.tabs(["📁 Files", "🔗 URL"])

with tab_files:
    accepted = DOCUMENT_TYPES + (IMAGE_TYPES if llm_enabled else [])
    if SOFFICE:
        accepted += [ext.lstrip(".") for ext in LEGACY_UPGRADE]
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
        kwargs = convert_kwargs()
        progress = st.progress(0.0)
        for i, up in enumerate(uploads):
            with st.spinner(f"Converting {up.name}…"):
                try:
                    results[up.name] = convert_upload(up, kwargs)
                except Exception as exc:  # surface per-file errors, keep going
                    st.error(f"**{up.name}** failed: {exc}")
            progress.progress((i + 1) / len(uploads))
        progress.empty()
        st.session_state["results"] = results

    results = st.session_state.get("results", {})
    stems = [Path(n).stem for n in results]
    # "report.docx" + "report.xlsx" would both become report.md in the ZIP
    md_names = {
        n: f"{Path(n).stem}.md" if stems.count(Path(n).stem) == 1 else f"{n}.md"
        for n in results
    }
    for name, text in results.items():
        md_name = md_names[name]
        label = f"✅ {name} → {md_name} ({token_estimate(text)})"
        with st.expander(label, expanded=len(uploads or []) == 1):
            st.download_button(
                f"⬇️ Download {md_name}",
                text,
                file_name=md_name,
                mime="text/markdown",
                key=f"dl_{name}",
            )
            st.code(text, language="markdown")

    if len(results) > 1:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, text in results.items():
                zf.writestr(md_names[name], text)
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
                text = get_converter().convert(url, **convert_kwargs()).text_content
                if compact:
                    text = compact_markdown(text)
                st.download_button(
                    f"⬇️ Download Markdown ({token_estimate(text)})",
                    text,
                    file_name="page.md",
                    mime="text/markdown",
                )
                st.code(text, language="markdown")
            except Exception as exc:
                st.error(f"Conversion failed: {exc}")
