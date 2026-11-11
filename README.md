# Script Tool

Native Python desktop screenplay application for macOS.

## Features

- **Search:** Import PDF, TXT, or Fountain screenplays; extract scenes; search sluglines and scene text.
- **Keepers:** Save reference scenes, reorder, annotate, and rename characters in the saved copy.
- **Writing:** Draft an original screenplay alongside keeper references; export Fountain and basic PDF.
- **Projects:** Local autosave and portable JSON project files.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## macOS build

The GitHub Actions workflow `.github/workflows/mac-build.yml` packages a macOS application with PyInstaller. Download the artifact from a successful workflow run under **Actions**. Builds are not signed or notarized.

## Limitations

Scanned PDFs need OCR and are not supported. PDF extraction and screenplay pagination are basic, and semantic natural-language search is not yet implemented.

The earlier HTML prototype remains in `index.html`; the native Python application is `main.py`.
