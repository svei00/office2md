# Office2MD 📝

Self-hosted web app that converts Office documents, PDFs, and technical
screenshots to clean Markdown — powered by [Microsoft markitdown](https://github.com/microsoft/markitdown)
with a [Streamlit](https://streamlit.io) GUI.

**Why self-hosted?** Your files never leave your network. Run it on your PC or
24/7 on a small home server (DietPi / Raspberry Pi), convert huge files, and
feed clean Markdown to your LLM of choice to save tokens.

## Features

- **Documents**: Word, PowerPoint, Excel (.xlsx/.xlsm/.xls), PDF, CSV, JSON,
  XML, HTML, EPub, Outlook .msg, ZIP, plain text
- **Legacy / OpenDocument** (.doc, .ppt, .rtf, .odt, .odp, .ods): converted
  automatically when [LibreOffice](https://www.libreoffice.org) is installed
- **Token-lean spreadsheets**: cells are read directly instead of through
  markitdown's pandas table, so there's no `NaN` padding, no `Unnamed: 3`
  headers, no empty rows/columns, dates as `2024-01-05`, and formulas
  without a saved value aren't lost (see below)
- **Compact Markdown**: strips table padding, blank-line runs and embedded
  image stubs; each result shows an approximate token count
- **Screenshots / images** (PNG, JPG): transcribed to Markdown by a vision
  LLM — works with **Ollama (free, local)** or any OpenAI-compatible API.
  Great for technical screenshots (forms, tables, error dialogs).
- **URLs**: web pages and YouTube transcripts
- Batch conversion with per-file download or one ZIP
- Up to 2 GB per file (configurable in `.streamlit/config.toml`)

## Spreadsheet layouts (sidebar → Token-lean output)

| Layout | Best for | Example |
| --- | --- | --- |
| **Auto** (default) | most sheets | title/form cells as lines, the data block below as a table |
| Markdown table | clean data tables | `\|Item\|Qty\|Price\|` |
| Cell list | forms, invoices, sparse sheets | `A3: Date \| B3: 2024-01-05` |
| CSV | huge data tables (densest) | `Item,Qty,Price` |

An invoice-style sheet in Auto mode comes out like:

```text
## Invoice [A1:E12]
A1: ACME Corp | E1: Invoice #1234
A3: Date | B3: 2024-01-05

|Item|Qty|Price|Total|
|---|---|---|---|
|Widget|3|50|150|
|||Total|179.99|
```

Options: show formulas next to values (`150 (=B8*C8)`), and cap rows per
sheet when the AI only needs the structure and a sample. On a 100k-row test
sheet the output was ~40% smaller and 4× faster than stock markitdown.

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

For legacy .doc/.ppt support inside Docker, build with LibreOffice (adds
several hundred MB): `docker compose build --build-arg WITH_LIBREOFFICE=1`.

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
- Legacy .doc/.ppt need LibreOffice; without it they're hidden from the
  uploader. On Windows the default install path is detected automatically.
- Scanned (image-only) PDFs are not OCR'd by markitdown; export them through
  the screenshot path or pre-OCR them.

## License

MIT — markitdown itself is MIT-licensed by Microsoft.
