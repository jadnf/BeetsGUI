"""Backend layer between the tkinter GUI and beets.

Handles the three technical stories from the project doc:
- Config editing (Req. 1 T3): read/write config.yaml, touching only the
  ``directory:`` and ``library:`` keys and preserving everything else.
- Import execution (Req. 2 T2): run a non-interactive quiet-mode import
  and report the number of items added.
- Library queries (Req. 3 T2): read-only access to albums, tracks, and
  metadata via beets' Library API.
"""

import os
import subprocess
import sys
import tempfile

import beets
import mediafile
import yaml
from beets import library

DB_FILENAME = "musiclibrary.db"

# Timeout for a single import run; autotagging large folders can be slow.
IMPORT_TIMEOUT_SECONDS = 60 * 30


class BackendError(Exception):
    """A backend operation failed. The message is safe to show the user."""


# ---------------------------------------------------------------------------
# Config management (Requirement 1)
# ---------------------------------------------------------------------------

def get_config_path():
    """Path to the beets config.yaml (may not exist yet)."""
    return str(beets.config.user_config_path())


def _read_config_file(config_path):
    """Parse config.yaml into a dict. Missing or empty file -> {}."""
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise BackendError(
            f"Could not read the beets config file at {config_path}:\n{exc}"
        )
    except yaml.YAMLError as exc:
        raise BackendError(
            f"The beets config file at {config_path} is not valid YAML:\n{exc}"
        )
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise BackendError(
            f"The beets config file at {config_path} has an unexpected format."
        )
    return data


def get_library_directory():
    """The configured music directory, or None if not configured."""
    data = _read_config_file(get_config_path())
    directory = data.get("directory")
    if directory:
        return os.path.expanduser(str(directory))
    return None


def get_db_path():
    """Path of the library database, or None if no library is configured."""
    data = _read_config_file(get_config_path())
    db_path = data.get("library")
    if db_path:
        return os.path.expanduser(str(db_path))
    directory = data.get("directory")
    if directory:
        return os.path.join(os.path.expanduser(str(directory)), DB_FILENAME)
    return None


def directory_is_writable(path):
    """True if we can create files inside ``path``."""
    if not os.path.isdir(path):
        return False
    try:
        fd, probe = tempfile.mkstemp(prefix=".beetsgui-", dir=path)
        os.close(fd)
        os.remove(probe)
    except OSError:
        return False
    return True


def set_library_location(directory):
    """Point beets at ``directory`` by updating config.yaml.

    Only the ``directory:`` and ``library:`` keys are changed; all other
    keys are preserved. The original file is left untouched if anything
    goes wrong (the new config is written to a temp file first).
    """
    directory = os.path.abspath(directory)
    if not os.path.isdir(directory):
        raise BackendError(f"'{directory}' is not a folder.")
    if not directory_is_writable(directory):
        raise BackendError(
            f"The folder '{directory}' is not writable.\n"
            "Please choose a different folder."
        )

    config_path = get_config_path()
    data = _read_config_file(config_path)  # raises before we touch the file
    data["directory"] = directory
    data["library"] = os.path.join(directory, DB_FILENAME)

    tmp_path = config_path + ".tmp"
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path, config_path)
    except OSError as exc:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise BackendError(f"Could not write the beets config file:\n{exc}")


# ---------------------------------------------------------------------------
# Library queries (Requirement 3) -- read-only
# ---------------------------------------------------------------------------

def _open_library():
    db_path = get_db_path()
    if not db_path:
        raise BackendError("No library is configured yet.")
    try:
        # beets 2.x stores item paths relative to the music directory, so
        # the configured directory must be passed for paths to resolve.
        return library.Library(db_path, directory=get_library_directory())
    except Exception as exc:  # sqlite/beets errors
        raise BackendError(f"Could not open the beets library database:\n{exc}")


def _decode_path(path):
    if path is None:
        return None
    if isinstance(path, bytes):
        return os.fsdecode(path)
    return str(path)


def _embedded_art(item_path):
    """Cover art bytes embedded in an audio file's tags, or None."""
    if not item_path:
        return None
    try:
        return mediafile.MediaFile(item_path).art
    except (mediafile.UnreadableFileError, OSError):
        return None


def _album_art_source(album):
    """Art for an album: a file path (str), raw bytes, or None.

    Prefers the cover file beets tracks via ``artpath``; falls back to
    art embedded in the first track's tags (common when no fetchart
    plugin is used).
    """
    artpath = _decode_path(album.artpath)
    if artpath and os.path.exists(artpath):
        return artpath
    for item in album.items():
        return _embedded_art(_decode_path(item.path))
    return None


def get_albums():
    """All albums as dicts: id, album, albumartist, year, art.

    ``art`` is a file path (str), raw image bytes, or None.
    """
    lib = _open_library()
    albums = []
    for album in lib.albums():
        albums.append({
            "id": album.id,
            "album": album.album or "Unknown Album",
            "albumartist": album.albumartist or "Unknown Artist",
            "year": album.year,
            "art": _album_art_source(album),
        })
    albums.sort(key=lambda a: (a["albumartist"].lower(), a["album"].lower()))
    return albums


def get_singletons():
    """Tracks with no album, as dicts: id, title, artist, art."""
    lib = _open_library()
    singles = []
    for item in lib.items("singleton:true"):
        path = _decode_path(item.path)
        singles.append({
            "id": item.id,
            "title": item.title or path,
            "artist": item.artist or "Unknown Artist",
            "art": _embedded_art(path),
        })
    singles.sort(key=lambda t: t["title"].lower())
    return singles


def get_album_tracks(album_id):
    """Tracks of an album as dicts: id, track, title."""
    lib = _open_library()
    album = lib.get_album(album_id)
    if album is None:
        return []
    tracks = []
    for item in album.items():
        tracks.append({
            "id": item.id,
            "track": item.track or 0,
            "title": item.title or _decode_path(item.path),
        })
    tracks.sort(key=lambda t: t["track"])
    return tracks


def _get_genre(obj):
    """Genre display string; beets 2.x stores a ``genres`` list."""
    genres = obj.get("genres")
    if genres:
        return ", ".join(str(g) for g in genres)
    return obj.get("genre")


def _format_length(seconds):
    if not seconds:
        return None
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def get_album_info(album_id):
    """Metadata for the Info panel: list of (label, value) pairs."""
    lib = _open_library()
    album = lib.get_album(album_id)
    if album is None:
        raise BackendError("That album is no longer in the library.")
    track_count = len(list(album.items()))
    # .get() instead of attribute access: some fields (e.g. genre) are
    # flexible in beets 2.x and raise AttributeError when unset.
    fields = [
        ("Album", album.get("album")),
        ("Album Artist", album.get("albumartist")),
        ("Year", album.get("year")),
        ("Genre", _get_genre(album)),
        ("Label", album.get("label")),
        ("Country", album.get("country")),
        ("Media", album.get("media")),
        ("Tracks", track_count),
    ]
    return [(label, value) for label, value in fields if value]


def get_track_info(item_id):
    """Metadata for the Info panel: list of (label, value) pairs."""
    lib = _open_library()
    item = lib.get_item(item_id)
    if item is None:
        raise BackendError("That track is no longer in the library.")
    bitrate = item.get("bitrate")
    fields = [
        ("Title", item.get("title")),
        ("Artist", item.get("artist")),
        ("Album", item.get("album")),
        ("Track", item.get("track")),
        ("Year", item.get("year")),
        ("Genre", _get_genre(item)),
        ("Length", _format_length(item.get("length"))),
        ("Bitrate", f"{bitrate // 1000} kbps" if bitrate else None),
        ("Format", item.get("format")),
        ("File", _decode_path(item.get("path"))),
    ]
    return [(label, value) for label, value in fields if value]


def get_track_album_id(item_id):
    """Album id for a track, or None for singletons."""
    lib = _open_library()
    item = lib.get_item(item_id)
    if item is None:
        return None
    return item.album_id


def count_items():
    """Number of tracks currently in the library database."""
    lib = _open_library()
    return sum(1 for _ in lib.items())


# ---------------------------------------------------------------------------
# Import execution (Requirement 2)
# ---------------------------------------------------------------------------

def run_import(folder):
    """Import ``folder`` into the library, non-interactively.

    Returns the number of tracks added (0 means nothing importable was
    found or every candidate was skipped). Raises BackendError on failure.
    Blocking -- the GUI runs this on a worker thread.
    """
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise BackendError(f"'{folder}' is not a folder.")
    if get_db_path() is None:
        raise BackendError("Set up a library before importing music.")

    before = count_items()

    # Quiet mode (-q) never prompts: it auto-applies strong matches and
    # falls back to the configured quiet_fallback (default: skip).
    cmd = [sys.executable, "-m", "beets", "import", "-q", folder]
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=IMPORT_TIMEOUT_SECONDS,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired:
        raise BackendError("The import timed out. Try a smaller folder.")
    except OSError as exc:
        raise BackendError(f"Could not start the beets importer:\n{exc}")

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise BackendError(
            "The beets import failed:\n" + (detail[-1500:] or "unknown error")
        )

    return count_items() - before
