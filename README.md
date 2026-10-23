# lecture-agent

Drop lecture slides, your own notes and transcripts into a folder. The agent reads them,
asks Claude to turn them into structured study notes, then writes the result to
**Obsidian** (a markdown file in your vault) and **Notion** (a page in a database).
Relevant slide diagrams are embedded, and a few verified reference links are added in small text.

```
inbox/<UNIT>/<LECTURE>/   ->  extract text  ->  Claude  ->  Obsidian .md  +  Notion page
```

## Folder structure

```
lecture-agent/
  config.yaml              settings (models, Notion property names, vault subfolder)
  .env                     secrets (copy from .env.example)
  inbox/
    COMP5xxx/              one folder per unit
      week01/              one folder per lecture: put ALL files for that lecture here
        slides.pptx
        notes.md
        transcript.vtt
        whiteboard.jpg
  state/state.json         what has been processed (created automatically)
  state/figures/           rendered slide images (created automatically)
  src/lecture_agent/
    main.py                CLI (check, watch, scan, process, init-notion)
    watcher.py             watches inbox, debounces, triggers processing
    pipeline.py            extract -> LLM -> write, with hash based dedupe
    llm.py, prompts.py     Claude calls (structured output via tool use)
    extractors/            pptx, pdf, vtt/srt/txt, md, images
    figures.py             finds diagram slides, renders them to images
    references.py          web-search backed reference links (URLs are verified)
    writers/               obsidian_writer.py, notion_writer.py, md_to_notion.py
  tests/
```

Supported inputs: `.pptx`, `.pdf`, `.vtt`, `.srt`, `.txt`, `.md`, `.png/.jpg/.webp/.gif`.
A text file with "transcript" in its name is treated as a transcript; other `.txt/.md` files are treated as your notes.
Old `.ppt` is not supported (save as `.pptx` or export to PDF).

## Diagrams and references

**Diagrams.** For each `.pptx`, slides that look like they hold a diagram (pictures, charts, SmartArt,
grouped shapes, connectors, several shapes) are rendered to images and shown to Claude as small thumbnails.
Claude embeds only the ones that genuinely help (flowcharts, architectures, graphs), placing each right after the
text it illustrates with a short caption such as "Figure: four step pipeline (Slide 5)". Text-only slides,
title slides and logos are skipped. PDFs work the same way per page, and uploaded images (whiteboard photos) can be embedded too.

- Best results need **LibreOffice** (`sudo apt install libreoffice-impress`, `brew install --cask libreoffice`, or the Windows installer).
  Without it the agent falls back to extracting pictures embedded in the slides, which misses diagrams drawn with shapes or charts.
- Obsidian: images are copied to `<vault>/Uni/<UNIT>/attachments/<lecture>/` and embedded with `![[...|700]]`.
- Notion: images are uploaded through Notion's File Upload API and the caption becomes the image caption.
  Uploads are limited to about 5 MB per file on free workspaces (larger images are shrunk automatically).

**References.** Claude lists the topics where an outside source would really help (thinly covered or foundational topics).
A separate step then searches the web for each topic (free DuckDuckGo search by default, no key) and keeps the best
result, preferring Wikipedia, universities, official docs and arXiv over forums and blogs. Links come from real search
results, so there are no invented URLs. Links that already appear in your slides or notes are included too.
References go in a "References" section at the end.

**Small font.** Captions and references use `<small>`, which renders as small text in Obsidian.
Notion has no font sizes, so the same text is shown in grey (captions use Notion's built-in small grey caption style).

Tune or turn off in `config.yaml` (`figures:` and `references:`). Each candidate figure sent to Claude costs roughly
1 to 1.5k input tokens, so lower `max_candidates` if you want cheaper runs.

## Free options

Everything except the model is free (Obsidian, Notion free plan, LibreOffice, DuckDuckGo search). For the model, set `llm.provider` in `config.yaml`:

| provider | cost | needs | notes |
|---|---|---|---|
| `gemini` (default) | free tier, no billing | free key from aistudio.google.com/apikey | sees slide images, huge context, rate limited (the agent waits and retries on 429). Google may use free tier inputs to improve its products, so avoid anything confidential. Check aistudio.google.com/rate-limit for what your key gets. |
| `ollama` | free, offline | Ollama installed and a pulled model | private, no limits, but needs a decent computer. Small context, so long transcripts are condensed in chunks. Cannot see images unless you pick a vision model and set `llm.vision: true`. |
| `anthropic` | paid | `ANTHROPIC_API_KEY` | best quality, forced structured output |

Gemini model names change often. The default `gemini-flash-latest` is an alias for the current Flash model. If you get a
"model not found" or "no free quota" error, set `llm.model` to a Flash model that AI Studio shows for your key.

Ollama setup: install from ollama.com, run `ollama pull llama3.1:8b` (or any current instruction model, optionally a vision one),
set `provider: ollama` and `model: <that name>`. Ollama's default context window is small, so raise it for long lectures
(for example set the environment variable `OLLAMA_CONTEXT_LENGTH=16384` before `ollama serve`).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                                   # then fill it in
```

1. **Model (free)**: get a Gemini API key at https://aistudio.google.com/apikey (no billing needed) and put it in `GEMINI_API_KEY`. See "Free options" below for Ollama (fully local) or Anthropic (paid).
2. **Obsidian**: set `OBSIDIAN_VAULT_PATH` to your vault folder. Notes land in `<vault>/Uni/<UNIT>/`.
3. **Notion**:
   - Create an internal integration at https://www.notion.so/my-integrations and copy the token into `NOTION_TOKEN`.
   - Create (or pick) a page, open its `...` menu, then **Connections** and add your integration.
   - Run `lecture-agent init-notion <that page's id>` to create the database, and paste the printed id into `NOTION_DATABASE_ID`.
     (Or use your own database and match the property names in `config.yaml`.)
4. Check everything: `lecture-agent check`

Only want one output? Set `obsidian.enabled` or `notion.enabled` to `false` in `config.yaml`.

## Usage

```bash
lecture-agent watch                       # leave running; drop files into inbox/UNIT/LECTURE/
lecture-agent scan                        # process everything in the inbox once
lecture-agent process inbox/COMP5xxx/week03
lecture-agent scan --force                # regenerate even if inputs are unchanged
```

In `watch` mode a lecture is processed once its files have not changed for `debounce_seconds`
(default 20), so you can drop several files in without it firing early.

Re-running is safe: inputs are hashed, unchanged lectures are skipped, the same Obsidian file is
overwritten, and the old Notion page is archived when a new version is created.

## Notes and limits

- Notion API: max 100 blocks per request (handled), 2000 chars per text run (handled). Obsidian `[[wikilinks]]` become plain text in Notion.
- Very long transcripts (over `max_input_chars`) are condensed chunk by chunk with the cheaper `condense_model` first.
- The model is told to use only the provided material and to flag unclear parts. Skim the notes before relying on them for exams.
- Gemini free tier is limited per minute and per day. A lecture is normally one request (two or three for very long transcripts), so a few lectures a day is fine.
- Hidden slides are skipped. Always check that embedded diagrams match their captions.
- Run `pytest` to verify the converter, extractors and pipeline (LLM is mocked in tests).
