"""Tkinter GUI for the beets music manager.

Layout follows the project mockup: a left panel with "Create Library"
and "Import music" folder pickers, a scrollable center library view
(large album art with the track list beneath each album), and a right
"Info" panel showing the selected item's metadata.
"""

import io
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

import backend

ALBUM_ART_SIZE = 150
INFO_ART_SIZE = 220
SELECT_BG = "#cfe4f7"
EMPTY_MESSAGE = "No music yet — click Import Music to get started"


def _load_art(source, size):
    """Load album art (file path or raw bytes) scaled to fit size x size."""
    if not source:
        return None
    try:
        if isinstance(source, bytes):
            img = Image.open(io.BytesIO(source))
        else:
            img = Image.open(source)
        img.thumbnail((size, size), Image.LANCZOS)
        return ImageTk.PhotoImage(img)
    except OSError:
        return None


def _placeholder_art(size):
    """Gray square used when an album has no art."""
    return ImageTk.PhotoImage(Image.new("RGB", (size, size), "#b8c4d0"))


class FirstRunDialog(tk.Toplevel):
    """Modal dialog shown when no library is configured yet.

    ``self.result`` is "ok" once a library location was saved, or
    "cancelled" if the user backed out.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Welcome to Beets GUI")
        self.result = "cancelled"
        self.resizable(False, False)

        body = ttk.Frame(self, padding=20)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            body,
            text="Beets needs a folder for your music library.",
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor=tk.W)
        ttk.Label(
            body,
            text=(
                "Create a new library in an empty folder, or select the\n"
                "folder of an existing beets library."
            ),
        ).pack(anchor=tk.W, pady=(6, 14))

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X)
        ttk.Button(
            buttons, text="Create New Library…",
            command=lambda: self._choose("Choose an empty folder for your new library"),
        ).pack(side=tk.LEFT)
        ttk.Button(
            buttons, text="Select Existing Library…",
            command=lambda: self._choose("Select your existing beets library folder"),
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.transient(parent)
        self.grab_set()

    def _choose(self, title):
        path = filedialog.askdirectory(parent=self, title=title)
        if not path:
            return  # user cancelled the picker; dialog stays open
        try:
            backend.set_library_location(path)
        except backend.BackendError as exc:
            # e.g. unwritable folder (story 1d): stay open for a retry
            messagebox.showerror("Cannot use that folder", str(exc), parent=self)
            return
        self.result = "ok"
        self.destroy()


def ensure_library_configured(root):
    """Show the first-run dialog until a library exists. False = user quit."""
    while True:
        try:
            directory = backend.get_library_directory()
        except backend.BackendError as exc:
            messagebox.showerror("Beets GUI", str(exc))
            return False
        if directory and os.path.isdir(directory):
            return True
        dialog = FirstRunDialog(root)
        root.wait_window(dialog)
        if dialog.result == "cancelled":
            messagebox.showinfo(
                "Beets GUI",
                "A music library is required to continue. Exiting.",
            )
            return False


class BeetsGuiApp:
    def __init__(self, root):
        self.root = root
        root.title("Beets GUI")
        root.geometry("900x500")
        root.minsize(760, 400)

        self._import_queue = queue.Queue()
        self._art_refs = []        # PhotoImages shown in the album list
        self._info_art = None      # large art PhotoImage reference
        self._art_placeholder = _placeholder_art(ALBUM_ART_SIZE)
        self._selected_widget = None
        self._default_row_bg = None

        self._build_left_panel()
        self._build_center_panel()
        self._build_info_panel()

        self.refresh_library()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_left_panel(self):
        panel = ttk.Frame(self.root, padding=12)
        panel.pack(side=tk.LEFT, fill=tk.Y)

        ttk.Label(panel, text="Create Library",
                  font=("TkDefaultFont", 10, "bold")).pack(anchor=tk.W)
        ttk.Button(panel, text="Choose folder…",
                   command=self.on_change_library).pack(anchor=tk.W, pady=(4, 18))

        ttk.Label(panel, text="Import music",
                  font=("TkDefaultFont", 10, "bold")).pack(anchor=tk.W)
        self.import_button = ttk.Button(panel, text="Choose folder…",
                                        command=self.on_import)
        self.import_button.pack(anchor=tk.W, pady=(4, 18))

        self.status_var = tk.StringVar(value="")
        ttk.Label(panel, textvariable=self.status_var, wraplength=160,
                  foreground="#444444").pack(side=tk.BOTTOM, anchor=tk.W)

    def _build_center_panel(self):
        panel = ttk.Frame(self.root, padding=(0, 12, 0, 12))
        panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(panel, highlightthickness=0, background="white")
        self.canvas_scroll = ttk.Scrollbar(panel, orient=tk.VERTICAL,
                                           command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.canvas_scroll.set)

        self.albums_frame = tk.Frame(self.canvas, background="white")
        self._albums_window = self.canvas.create_window(
            (0, 0), window=self.albums_frame, anchor="nw")
        self.albums_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        # keep the inner frame as wide as the canvas
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._albums_window, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self.empty_label = ttk.Label(panel, text=EMPTY_MESSAGE,
                                     font=("TkDefaultFont", 11),
                                     foreground="#666666", anchor=tk.CENTER)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas_scroll.pack(side=tk.LEFT, fill=tk.Y)

    def _build_info_panel(self):
        panel = ttk.Frame(self.root, padding=12, width=280)
        panel.pack(side=tk.LEFT, fill=tk.Y)
        panel.pack_propagate(False)

        ttk.Label(panel, text="Info",
                  font=("TkDefaultFont", 14, "bold")).pack(anchor=tk.W)

        self.art_label = ttk.Label(panel)
        self.art_label.pack(anchor=tk.W, pady=(8, 8))

        self.info_text = tk.Text(panel, wrap=tk.WORD, height=12, width=34,
                                 relief=tk.FLAT, background=self.root["background"])
        self.info_text.pack(fill=tk.BOTH, expand=True)
        self.info_text.tag_configure("label", font=("TkDefaultFont", 9, "bold"))
        self.info_text.configure(state=tk.DISABLED)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    # ------------------------------------------------------------------
    # Library view (Requirement 3)
    # ------------------------------------------------------------------

    def refresh_library(self):
        for child in self.albums_frame.winfo_children():
            child.destroy()
        self._art_refs.clear()
        self._selected_widget = None
        self._show_info([], art_source=None)

        try:
            albums = backend.get_albums()
            singletons = backend.get_singletons()
        except backend.BackendError as exc:
            messagebox.showerror("Could not load library", str(exc))
            return

        if not albums and not singletons:
            self.canvas.pack_forget()
            self.canvas_scroll.pack_forget()
            self.empty_label.pack(fill=tk.BOTH, expand=True)
            return

        self.empty_label.pack_forget()
        if not self.canvas.winfo_ismapped():
            self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            self.canvas_scroll.pack(side=tk.LEFT, fill=tk.Y)

        for album in albums:
            self._add_album_block(album)

        if singletons:
            self._add_singletons_block(singletons)

        self.canvas.yview_moveto(0)

    def _add_album_block(self, album):
        block = tk.Frame(self.albums_frame, background="white",
                         padx=16, pady=12)
        block.pack(fill=tk.X, anchor=tk.W)

        art_img = _load_art(album["art"], ALBUM_ART_SIZE) or self._art_placeholder
        self._art_refs.append(art_img)

        art = tk.Label(block, image=art_img, background="white",
                       cursor="hand2")
        art.pack(anchor=tk.W)

        year = f" ({album['year']})" if album["year"] else ""
        header = tk.Label(
            block, text=f"{album['albumartist']} — {album['album']}{year}",
            font=("TkDefaultFont", 11, "bold"), background="white",
            anchor=tk.W, cursor="hand2", padx=4)
        header.pack(fill=tk.X, pady=(6, 2))

        def select_album(_event, widget=header, album_id=album["id"],
                         art_source=album["art"]):
            self._select(widget, backend.get_album_info, album_id, art_source)

        art.bind("<Button-1>", select_album)
        header.bind("<Button-1>", select_album)

        for track in backend.get_album_tracks(album["id"]):
            num = f"{track['track']}. " if track["track"] else ""
            row = tk.Label(block, text=f"{num}{track['title']}",
                           background="white", anchor=tk.W,
                           cursor="hand2", padx=16)
            row.pack(fill=tk.X, pady=1)
            row.bind(
                "<Button-1>",
                lambda _e, widget=row, item_id=track["id"],
                       art_source=album["art"]:
                self._select(widget, backend.get_track_info, item_id, art_source))

    def _add_singletons_block(self, singletons):
        block = tk.Frame(self.albums_frame, background="white",
                         padx=16, pady=12)
        block.pack(fill=tk.X, anchor=tk.W)

        tk.Label(block, text="Singletons (no album)",
                 font=("TkDefaultFont", 11, "bold"), background="white",
                 anchor=tk.W, padx=4).pack(fill=tk.X, pady=(0, 2))

        for track in singletons:
            row = tk.Label(block, text=f"{track['title']} — {track['artist']}",
                           background="white", anchor=tk.W,
                           cursor="hand2", padx=16)
            row.pack(fill=tk.X, pady=1)
            row.bind(
                "<Button-1>",
                lambda _e, widget=row, item_id=track["id"],
                       art_source=track["art"]:
                self._select(widget, backend.get_track_info, item_id, art_source))

    def _select(self, widget, info_fn, obj_id, art_source):
        try:
            info = info_fn(obj_id)
        except backend.BackendError as exc:
            messagebox.showerror("Could not load metadata", str(exc))
            return
        if self._selected_widget is not None and \
                self._selected_widget.winfo_exists():
            self._selected_widget.configure(background="white")
        widget.configure(background=SELECT_BG)
        self._selected_widget = widget
        self._show_info(info, art_source)

    def _show_info(self, info, art_source):
        self._info_art = _load_art(art_source, INFO_ART_SIZE)
        self.art_label.configure(image=self._info_art or "")

        self.info_text.configure(state=tk.NORMAL)
        self.info_text.delete("1.0", tk.END)
        for label, value in info:
            self.info_text.insert(tk.END, f"{label}: ", "label")
            self.info_text.insert(tk.END, f"{value}\n")
        self.info_text.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # Create/select library (Requirement 1)
    # ------------------------------------------------------------------

    def on_change_library(self):
        path = filedialog.askdirectory(
            parent=self.root, title="Choose a folder for your music library")
        if not path:
            return
        try:
            backend.set_library_location(path)
        except backend.BackendError as exc:
            messagebox.showerror("Cannot use that folder", str(exc),
                                 parent=self.root)
            return
        self.status_var.set(f"Library set to:\n{path}")
        self.refresh_library()

    # ------------------------------------------------------------------
    # Import music (Requirement 2)
    # ------------------------------------------------------------------

    def on_import(self):
        folder = filedialog.askdirectory(
            parent=self.root, title="Choose a folder of music to import")
        if not folder:
            return  # story 2c: cancelled, nothing happens

        self.import_button.configure(state=tk.DISABLED)
        self.status_var.set("Importing…\nThis may take a while.")
        threading.Thread(target=self._import_worker, args=(folder,),
                         daemon=True).start()
        self.root.after(200, self._poll_import)

    def _import_worker(self, folder):
        try:
            added = backend.run_import(folder)
            self._import_queue.put(("ok", added))
        except backend.BackendError as exc:
            self._import_queue.put(("error", str(exc)))

    def _poll_import(self):
        try:
            kind, payload = self._import_queue.get_nowait()
        except queue.Empty:
            self.root.after(200, self._poll_import)
            return

        self.import_button.configure(state=tk.NORMAL)
        if kind == "error":
            self.status_var.set("Import failed.")
            messagebox.showerror("Import failed", payload, parent=self.root)
        elif payload == 0:
            # story 2b: nothing importable found (or all candidates skipped)
            self.status_var.set("No importable music was found.")
            messagebox.showinfo(
                "Nothing imported",
                "No importable music was found in that folder.\n"
                "The library is unchanged.",
                parent=self.root,
            )
        else:
            self.status_var.set(f"Imported {payload} track(s).")
            self.refresh_library()
