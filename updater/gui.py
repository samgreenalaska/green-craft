"""Tkinter front end for first-run setup and uninstall.

tkinter because it ships with Python and so costs nothing extra in the packaged exe.
A console window is intimidating to someone who just wants to play Minecraft, and it
also makes a normal progress message look like an error.
"""
import queue
import threading
import tkinter as tk
from tkinter import ttk

BG = "#1f2430"
FG = "#e6e6e6"
MUTED = "#9aa4b2"
ACCENT = "#3f9142"


def _style(root):
    root.configure(bg=BG)
    s = ttk.Style(root)
    try:
        s.theme_use("clam")
    except tk.TclError:
        pass
    s.configure("TFrame", background=BG)
    s.configure("TLabel", background=BG, foreground=FG)
    s.configure("Muted.TLabel", background=BG, foreground=MUTED)
    s.configure("Title.TLabel", background=BG, foreground=FG, font=("Segoe UI", 18, "bold"))
    s.configure("TCheckbutton", background=BG, foreground=FG)
    s.map("TCheckbutton", background=[("active", BG)], foreground=[("active", FG)])
    s.configure("Accent.TButton", background=ACCENT, foreground="white",
                borderwidth=0, focusthickness=0, padding=(18, 9))
    s.map("Accent.TButton", background=[("active", "#4aa64e"), ("disabled", "#3a4150")])
    s.configure("TButton", padding=(14, 7))
    s.configure("TProgressbar", background=ACCENT, troughcolor="#2a3040", borderwidth=0)


def _icon_path():
    """The .ico beside this module, or inside the PyInstaller bundle at runtime."""
    import os
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "icon.ico")


def _centre(win, w, h):
    win.update_idletasks()
    x = (win.winfo_screenwidth() - w) // 2
    y = (win.winfo_screenheight() - h) // 3
    win.geometry(f"{w}x{h}+{x}+{y}")


class SetupWindow:
    """Options screen, then a live progress log. `work` is called on a worker thread
    with (options, log) and must not touch tkinter directly."""

    def __init__(self, work, title="GreenCraft Setup", uninstall_work=None,
                 world_count=0, installed=False):
        self.work = work
        self.uninstall_work = uninstall_work
        self.world_count = world_count
        self.installed = installed
        self.show_uninstall = uninstall_work is not None
        self.q = queue.Queue()
        self.result = None
        self.launch_requested = False

        self.root = tk.Tk()
        self.root.title(title)
        _style(self.root)
        _centre(self.root, 560, 480)
        self.root.minsize(520, 440)
        try:
            self.root.iconbitmap(_icon_path())
        except Exception:
            pass

        self.frame = ttk.Frame(self.root, padding=24)
        self.frame.pack(fill="both", expand=True)
        self._build_options()

    # ---------------------------------------------------------------- options
    def _build_options(self):
        f = self.frame
        ttk.Label(f, text="GreenCraft", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, text="Modded Minecraft, set up for you.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 16))

        ttk.Label(f, text="This will set up:", style="TLabel").pack(anchor="w")
        for line in (
            "Tailscale, so you can reach the server",
            "Prism Launcher and Minecraft",
            "The server's mods and shader pack",
        ):
            ttk.Label(f, text=f"   •  {line}", style="Muted.TLabel").pack(anchor="w")

        ttk.Separator(f).pack(fill="x", pady=14)

        ttk.Label(f, text="Paste the invite link you were sent:",
                  style="TLabel").pack(anchor="w")
        self.v_invite = tk.StringVar(value="")
        e = ttk.Entry(f, textvariable=self.v_invite)
        e.pack(fill="x", pady=(4, 2))
        ttk.Label(f, text="Opens automatically at the right moment. Leave blank if you\n"
                          "have already accepted it.",
                  style="Muted.TLabel").pack(anchor="w")

        ttk.Separator(f).pack(fill="x", pady=14)

        self.v_desktop = tk.BooleanVar(value=True)
        self.v_startmenu = tk.BooleanVar(value=False)
        self.v_experimental = tk.BooleanVar(value=False)

        ttk.Checkbutton(f, text="Create a desktop shortcut",
                        variable=self.v_desktop).pack(anchor="w", pady=2)
        ttk.Checkbutton(f, text="Add to the Start Menu",
                        variable=self.v_startmenu).pack(anchor="w", pady=2)
        ttk.Checkbutton(f, text="Also set up the Experimental channel (not recommended)",
                        variable=self.v_experimental).pack(anchor="w", pady=(10, 0))
        ttk.Label(f, text="       For testing upcoming updates. Leave this off unless\n"
                          "       you have been asked to try something.",
                  style="Muted.TLabel").pack(anchor="w")

        row = ttk.Frame(f)
        row.pack(side="bottom", fill="x", pady=(20, 0))
        ttk.Button(row, text="Cancel", command=self.root.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(row, text="Install", style="Accent.TButton",
                   command=self._start).pack(side="right")
        if self.show_uninstall:
            ttk.Button(row, text="Uninstall...",
                       command=self._open_uninstall).pack(side="left")

    # --------------------------------------------------------------- progress
    def _build_progress(self):
        for w in self.frame.winfo_children():
            w.destroy()
        f = self.frame
        ttk.Label(f, text="Setting up", style="Title.TLabel").pack(anchor="w")
        self.status = ttk.Label(f, text="Starting...", style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(2, 12))

        self.bar = ttk.Progressbar(f, mode="indeterminate")
        self.bar.pack(fill="x")
        self.bar.start(12)

        box = tk.Text(f, height=12, bg="#151922", fg=MUTED, relief="flat",
                      font=("Consolas", 9), wrap="word", insertbackground=FG)
        box.pack(fill="both", expand=True, pady=(14, 0))
        box.configure(state="disabled")
        self.box = box

        self.btnrow = ttk.Frame(f)
        self.btnrow.pack(side="bottom", fill="x", pady=(14, 0))
        self.done_btn = ttk.Button(self.btnrow, text="Close", style="Accent.TButton",
                                   command=self.root.destroy, state="disabled")
        self.done_btn.pack(side="right")

    def _append(self, text):
        self.box.configure(state="normal")
        self.box.insert("end", text + "\n")
        self.box.see("end")
        self.box.configure(state="disabled")

    def _start(self):
        opts = {
            "desktop": self.v_desktop.get(),
            "start_menu": self.v_startmenu.get(),
            "experimental": self.v_experimental.get(),
            "invite": self.v_invite.get().strip(),
        }
        self._build_progress()
        threading.Thread(target=self._run, args=(opts,), daemon=True).start()
        self.root.after(80, self._drain)

    def _run(self, opts):
        def log(msg=""):
            self.q.put(("log", str(msg)))
        try:
            self.result = self.work(opts, log)
            self.q.put(("done", None))
        except Exception as e:
            self.q.put(("log", ""))
            self.q.put(("log", f"Setup failed: {e}"))
            self.q.put(("fail", str(e)))

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append(payload)
                    if payload.strip():
                        self.status.configure(text=payload.strip()[:70])
                elif kind == "done":
                    self.bar.stop()
                    self.bar.configure(mode="determinate", value=100)
                    self.status.configure(text="Starting Minecraft...")
                    # Setup finishes by opening the game and getting out of the way.
                    # A "Play" button here would just be one more thing to click.
                    self.launch_requested = True
                    self.root.after(900, self.root.destroy)
                    return
                elif kind == "fail":
                    self.bar.stop()
                    self.bar.configure(mode="determinate", value=0)
                    self.status.configure(text="Setup did not finish.")
                    self.done_btn.configure(state="normal")
                    return
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    # -------------------------------------------------------------- uninstall
    def _open_uninstall(self):
        for w in self.frame.winfo_children():
            w.destroy()
        f = self.frame
        ttk.Label(f, text="Uninstall", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, text="Choose what to remove.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 14))

        self.u_all = tk.BooleanVar(value=True)
        self.u_greencraft = tk.BooleanVar(value=True)
        self.u_prism = tk.BooleanVar(value=True)
        self.u_tailscale = tk.BooleanVar(value=True)
        self.u_keep_worlds = tk.BooleanVar(value=True)

        ttk.Checkbutton(f, text="Remove everything", variable=self.u_all,
                        command=self._toggle_all).pack(anchor="w")
        ttk.Separator(f).pack(fill="x", pady=(10, 12))

        ttk.Checkbutton(f, text="GreenCraft  (shortcuts, settings, and the Minecraft instance)",
                        variable=self.u_greencraft,
                        command=self._sync_all).pack(anchor="w", pady=2)
        ttk.Label(f, text="       Nothing else removes the instance — uninstalling Prism\n"
                          "       leaves it in place.",
                  style="Muted.TLabel").pack(anchor="w")

        # Only shown when there is actually something to lose. On a multiplayer-only
        # install the saves folder is empty, and an extra scary checkbox helps nobody.
        if self.world_count:
            ttk.Checkbutton(
                f,
                text=f"       Keep my {self.world_count} saved world"
                     f"{'' if self.world_count == 1 else 's'} "
                     f"(moved to Documents\\GreenCraft worlds)",
                variable=self.u_keep_worlds).pack(anchor="w", pady=(4, 0))

        ttk.Checkbutton(f, text="Prism Launcher", variable=self.u_prism,
                        command=self._sync_all).pack(anchor="w", pady=(10, 2))
        ttk.Checkbutton(f, text="Tailscale", variable=self.u_tailscale,
                        command=self._sync_all).pack(anchor="w", pady=2)
        ttk.Label(f, text="       Other things may rely on these. Windows will ask for\n"
                          "       permission before removing Tailscale.",
                  style="Muted.TLabel").pack(anchor="w")

        row = ttk.Frame(f)
        row.pack(side="bottom", fill="x", pady=(18, 0))
        ttk.Button(row, text="Back", command=self._back_to_options).pack(side="left")
        ttk.Button(row, text="Cancel", command=self.root.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(row, text="Remove", style="Accent.TButton",
                   command=self._start_uninstall).pack(side="right")

    def _toggle_all(self):
        v = self.u_all.get()
        for var in (self.u_greencraft, self.u_prism, self.u_tailscale):
            var.set(v)

    def _sync_all(self):
        """Untick "Remove everything" the moment a component is unticked, so the
        header never claims more than the boxes below it."""
        parts = (self.u_greencraft.get(), self.u_prism.get(), self.u_tailscale.get())
        self.u_all.set(all(parts))

    def _back_to_options(self):
        for w in self.frame.winfo_children():
            w.destroy()
        self._build_options()

    def _start_uninstall(self):
        opts = {
            "greencraft": self.u_greencraft.get(),
            "prism": self.u_prism.get(),
            "tailscale": self.u_tailscale.get(),
            "keep_worlds": self.u_keep_worlds.get(),
        }
        if not any(opts.values()):
            return
        self._build_progress()
        self.status.configure(text="Removing...")

        def run():
            def log(msg=""):
                self.q.put(("log", str(msg)))
            try:
                self.uninstall_work(opts, log)
                self.q.put(("uninstalled", None))
            except Exception as e:
                self.q.put(("log", f"Uninstall failed: {e}"))
                self.q.put(("fail", None))

        threading.Thread(target=run, daemon=True).start()
        self.root.after(80, self._drain_uninstall)

    def _drain_uninstall(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append(payload)
                    if payload.strip():
                        self.status.configure(text=payload.strip()[:70])
                else:
                    self.bar.stop()
                    self.bar.configure(mode="determinate",
                                       value=100 if kind == "uninstalled" else 0)
                    self.status.configure(
                        text="Removed." if kind == "uninstalled" else "Did not finish.")
                    self.done_btn.configure(state="normal", text="Close")
                    return
        except queue.Empty:
            pass
        self.root.after(80, self._drain_uninstall)

    def run(self):
        self.root.mainloop()
        return self.result, self.launch_requested


class ProgressWindow:
    """Small window for the routine sync-then-launch path.

    The shortcuts run the packaged exe with --noconsole, so without this a friend
    clicking GreenCraft would see nothing at all for however long the update takes.
    Closes itself when the work succeeds; stays open with the log if it fails, which
    is the only time the detail is worth reading.
    """

    def __init__(self, work, title="GreenCraft"):
        self.work = work
        self.q = queue.Queue()
        self.result = None
        self.failed = False

        self.root = tk.Tk()
        self.root.title(title)
        _style(self.root)
        _centre(self.root, 460, 220)
        self.root.resizable(False, False)

        f = ttk.Frame(self.root, padding=20)
        f.pack(fill="both", expand=True)
        self.frame = f

        ttk.Label(f, text="GreenCraft", style="Title.TLabel").pack(anchor="w")
        self.status = ttk.Label(f, text="Checking for updates...", style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(4, 14))
        self.bar = ttk.Progressbar(f, mode="indeterminate")
        self.bar.pack(fill="x")
        self.bar.start(12)
        self.detail = ttk.Label(f, text="", style="Muted.TLabel", wraplength=400)
        self.detail.pack(anchor="w", pady=(12, 0))

        self.lines = []

    def _expand_on_failure(self):
        self.bar.stop()
        self.bar.pack_forget()
        self.status.configure(text="Something went wrong")
        box = tk.Text(self.frame, height=8, bg="#151922", fg=MUTED, relief="flat",
                      font=("Consolas", 9), wrap="word")
        box.pack(fill="both", expand=True, pady=(4, 0))
        box.insert("end", "\n".join(self.lines[-40:]))
        box.configure(state="disabled")
        ttk.Button(self.frame, text="Close", style="Accent.TButton",
                   command=self.root.destroy).pack(side="bottom", pady=(12, 0))
        self.root.geometry("560x420")

    def _run(self):
        def log(msg=""):
            self.q.put(("log", str(msg)))
        try:
            self.result = self.work(log)
            self.q.put(("done", None))
        except Exception as e:
            self.q.put(("log", f"{type(e).__name__}: {e}"))
            self.q.put(("fail", None))

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.lines.append(payload)
                    if payload.strip():
                        self.detail.configure(text=payload.strip()[:90])
                elif kind == "done":
                    self.root.destroy()
                    return
                elif kind == "fail":
                    self.failed = True
                    self._expand_on_failure()
                    return
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def run(self):
        threading.Thread(target=self._run, daemon=True).start()
        self.root.after(80, self._drain)
        self.root.mainloop()
        return self.result, self.failed


class UninstallWindow:
    def __init__(self, worlds, work):
        self.work = work
        self.q = queue.Queue()
        self.root = tk.Tk()
        self.root.title("Uninstall GreenCraft")
        _style(self.root)
        _centre(self.root, 520, 340)

        f = ttk.Frame(self.root, padding=24)
        f.pack(fill="both", expand=True)
        self.frame = f

        ttk.Label(f, text="Uninstall GreenCraft", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, text="Shortcuts and the download cache will be removed.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 14))

        self.v_data = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Also delete the Minecraft instance",
                        variable=self.v_data).pack(anchor="w")
        warn = (f"       This permanently deletes {worlds} saved world"
                f"{'' if worlds == 1 else 's'}."
                if worlds else
                "       No saved worlds were found, so nothing irreplaceable is here.")
        ttk.Label(f, text=warn, style="Muted.TLabel").pack(anchor="w")

        ttk.Label(f, text="Prism Launcher and Tailscale are left installed.",
                  style="Muted.TLabel").pack(anchor="w", pady=(14, 0))

        row = ttk.Frame(f)
        row.pack(side="bottom", fill="x", pady=(18, 0))
        ttk.Button(row, text="Cancel", command=self.root.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(row, text="Uninstall", style="Accent.TButton",
                   command=self._go).pack(side="right")

    def _go(self):
        remove = self.v_data.get()
        for w in self.frame.winfo_children():
            w.destroy()
        box = tk.Text(self.frame, height=12, bg="#151922", fg=MUTED, relief="flat",
                      font=("Consolas", 9), wrap="word")
        box.pack(fill="both", expand=True)
        box.configure(state="disabled")
        self.box = box
        btn = ttk.Button(self.frame, text="Close", style="Accent.TButton",
                         command=self.root.destroy, state="disabled")
        btn.pack(side="bottom", pady=(12, 0))
        self.btn = btn

        def log(msg=""):
            self.q.put(str(msg))

        def run():
            try:
                self.work(remove, log)
            except Exception as e:
                log(f"Uninstall failed: {e}")
            self.q.put(None)

        threading.Thread(target=run, daemon=True).start()
        self.root.after(80, self._drain)

    def _drain(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg is None:
                    self.btn.configure(state="normal")
                    return
                self.box.configure(state="normal")
                self.box.insert("end", msg + "\n")
                self.box.see("end")
                self.box.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def run(self):
        self.root.mainloop()
