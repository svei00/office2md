# Office2MD 📝

Self-hosted web app that converts Office documents, PDFs, and technical
screenshots to clean Markdown — powered by [Microsoft markitdown](https://github.com/microsoft/markitdown)
with a [Streamlit](https://streamlit.io) GUI.

**Why self-hosted?** Your files never leave your network. Run it on your PC or
24/7 on a small home server (DietPi / Raspberry Pi), convert huge files, and
feed clean Markdown to your LLM of choice to save tokens.

## Features

- **Documents**: Word, PowerPoint, Excel (modern & legacy), PDF, CSV, JSON,
  XML, HTML, EPub, Outlook .msg, ZIP, plain text
- **Screenshots / images** (PNG, JPG): transcribed to Markdown by a vision
  LLM — works with **Ollama (free, local)** or any OpenAI-compatible API.
  Great for technical screenshots (forms, tables, error dialogs).
- **URLs**: web pages and YouTube transcripts
- Batch conversion with per-file download or one ZIP
- Up to 2 GB per file (configurable in `.streamlit/config.toml`)

## Run locally (Windows/Mac/Linux)

```bash
pip install -r requirements.txt
streamlit run app.py
```

Opens at <http://localhost:8501>.

## Run with Docker

```bash
docker compose up -d --build
```

Then open <http://localhost:8501> (or `http://<server-ip>:8501` from another
device).

## Deploy on DietPi (24/7)

1. Install Docker on the DietPi box: `dietpi-software` → install **Docker**
   and **Docker Compose**.
2. Clone and start:

   ```bash
   git clone https://github.com/YOURUSER/office2md.git
   cd office2md
   docker compose up -d --build
   ```

3. Open `http://<dietpi-ip>:8501` from any device on your LAN.
4. **Remote access**: don't port-forward. Install [Tailscale](https://tailscale.com)
   on the DietPi and your devices — then use the Tailscale IP from anywhere,
   encrypted, with no exposed ports.

## Screenshot transcription with Ollama (free, private)

Vision models are too heavy for a small ARM board, so run Ollama on your
main PC and let the app (wherever it runs) call it over the network:

1. Install [Ollama](https://ollama.com) on your PC and pull a vision model:

   ```bash
   ollama pull qwen2.5vl:7b
   ```

2. Make Ollama listen on the network (Windows): set the environment variable
   `OLLAMA_HOST=0.0.0.0` (System Properties → Environment Variables), then
   restart Ollama.
3. In the app sidebar, enable **screenshot/image transcription** and set the
   base URL to your PC's IP, e.g. `http://192.168.1.50:11434/v1`.

   Or pre-configure it for Docker in a `.env` file next to
   `docker-compose.yml`:

   ```env
   LLM_BASE_URL=http://192.168.1.50:11434/v1
   LLM_MODEL=qwen2.5vl:7b
   LLM_API_KEY=ollama
   ```

To use OpenAI instead: base URL `https://api.openai.com/v1`, model
`gpt-4o-mini`, and your API key.

## Notes

- Bare images **without** a vision LLM only yield EXIF metadata — that's a
  markitdown limitation, which is why the app hides image upload until a
  vision endpoint is configured.
- Scanned (image-only) PDFs are not OCR'd by markitdown; export them through
  the screenshot path or pre-OCR them.

## License

MIT — markitdown itself is MIT-licensed by Microsoft.
