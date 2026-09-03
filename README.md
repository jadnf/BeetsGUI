# Beets GUI

A simple tkinter front end for the [beets](https://beets.io) music manager.
It handles the config editing and command running that beets normally
requires on the command line:

- **Create/select a library** — picks the music folder and writes the
  `directory:` and `library:` keys into beets' `config.yaml` for you
  (all other config keys are preserved).
- **Import music** — runs a non-interactive, quiet-mode `beets import`
  on a folder you choose and reports how many tracks were added.
- **View library** — browse albums (with cover art) and their tracks;
  click any album or track to see its metadata in the Info panel.

## Setup

```
pip install -r requirements.txt
```

Tkinter ships with the standard Python installer on Windows.

## Run

```
python main.py
```

On first launch (or whenever no library is configured) you'll be asked
to create a new library or select an existing one. After that, use
**Import music** to add a folder of audio files, and click albums or
tracks to inspect their metadata.

## Files

- `main.py` — entry point
- `gui.py` — tkinter interface (first-run dialog, library view, info panel)
- `backend.py` — all beets interaction: config editing, import execution,
  and read-only library queries
