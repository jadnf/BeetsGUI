"""Entry point for the Beets GUI."""

import tkinter as tk

import gui


def main():
    root = tk.Tk()
    root.withdraw()  # hide the main window during first-run setup

    if not gui.ensure_library_configured(root):
        root.destroy()
        return

    root.deiconify()
    gui.BeetsGuiApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
