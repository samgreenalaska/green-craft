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
    """A path to icon.ico that Tk can hold open safely.

    Not the copy inside sys._MEIPASS. Tk keeps the .ico open for the window's lifetime,
    and a onefile build cannot delete its temp directory while a file in it is held --
    which is what produced "Failed to remove temporary directory: ...\\_MEInnnnn" on
    exit. Copy it out to our own folder once and point Tk at that instead.
    """
    import os
    import shutil
    import sys

    src = os.path.join(
        getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "icon.ico")
    if not getattr(sys, "frozen", False):
        return src

    dest_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "GreenCraft")
    dest = os.path.join(dest_dir, "icon.ico")
    try:
        if not os.path.exists(dest) or os.path.getsize(dest) != os.path.getsize(src):
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copyfile(src, dest)
        return dest
    except OSError:
        return src


class SmoothBar:
    """A determinate bar that eases toward its target instead of jumping.

    Steps report in bursts (a hash check per mod is a few milliseconds), and a bar
    that leaps 40% then sits still reads as stuck. Easing makes it read as moving.
    It never goes backwards.
    """

    def __init__(self, parent, pct_label=None):
        self.bar = ttk.Progressbar(parent, mode="determinate", maximum=100)
        self.pct = pct_label
        self.target = 0.0
        self.shown = 0.0

    def set(self, pct):
        self.target = max(self.target, min(float(pct), 100.0))

    def tick(self):
        gap = self.target - self.shown
        if gap > 0.05:
            self.shown += max(gap * 0.25, 0.05)
        else:
            self.shown = self.target
        self.bar.configure(value=self.shown)
        if self.pct is not None:
            self.pct.configure(text=f"{int(self.shown)}%")


def _centre(win, w, h):
    win.update_idletasks()
    x = (win.winfo_screenwidth() - w) // 2
    y = (win.winfo_screenheight() - h) // 3
    win.geometry(f"{w}x{h}+{x}+{y}")


class SetupWindow:
    """Options screen, then a live progress log. `work` is called on a worker thread
    with (options, log) and must not touch tkinter directly."""

    def __init__(self, work, title="GreenCraft Setup", uninstall_work=None,
                 world_count=0, installed=False, show_tailscale=True):
        self.work = work
        self.show_tailscale = show_tailscale
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
        _centre(self.root, 580, 620)
        self.root.minsize(560, 560)
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

        # Pack the buttons FIRST, anchored to the bottom, so they always get their
        # space. tkinter allocates in packing order, so building content first meant
        # adding the invite field squeezed the row down to unlabelled slivers.
        row = ttk.Frame(f)
        row.pack(side="bottom", fill="x", pady=(20, 0))
        ttk.Button(row, text="Cancel", command=self.root.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(row, text="Install", style="Accent.TButton",
                   command=self._start).pack(side="right")
        if self.show_uninstall:
            ttk.Button(row, text="Uninstall...",
                       command=self._open_uninstall).pack(side="left")

        try:
            import version as _v
            ver = _v.VERSION
        except Exception:
            ver = "?"
        head = ttk.Frame(f)
        head.pack(fill="x")
        ttk.Label(head, text="GreenCraft", style="Title.TLabel").pack(side="left", anchor="w")
        # Version on screen, not just in the log -- a bug report that says "0.1.0" is
        # worth far more than one that says "the latest one, I think".
        ttk.Label(head, text=f"v{ver}", style="Muted.TLabel").pack(side="left",
                                                                   anchor="s", padx=(8, 0),
                                                                   pady=(0, 4))
        ttk.Label(f, text="Modded Minecraft, set up for you.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 16))

        ttk.Label(f, text="This will set up:", style="TLabel").pack(anchor="w")
        lines = ["Prism Launcher and Minecraft", "The server's mods and shader pack"]
        if self.show_tailscale:
            lines.insert(0, "Tailscale, so you can reach the server")
        for line in lines:
            ttk.Label(f, text=f"   •  {line}", style="Muted.TLabel").pack(anchor="w")

        ttk.Separator(f).pack(fill="x", pady=14)

        self.v_invite = tk.StringVar(value="")
        if self.show_tailscale:
            ttk.Label(f, text="Paste the invite link you were sent:",
                      style="TLabel").pack(anchor="w")
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
        self.v_tidy = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Delete this installer when finished",
                        variable=self.v_tidy).pack(anchor="w", pady=2)

        ttk.Checkbutton(f, text="Also set up the Experimental channel (not recommended)",
                        variable=self.v_experimental).pack(anchor="w", pady=(10, 0))
        ttk.Label(f, text="       For testing upcoming updates. Leave this off unless\n"
                          "       you have been asked to try something.",
                  style="Muted.TLabel").pack(anchor="w")

    # --------------------------------------------------------------- progress
    def _build_progress(self, determinate=True):
        for w in self.frame.winfo_children():
            w.destroy()
        f = self.frame
        ttk.Label(f, text="Setting up", style="Title.TLabel").pack(anchor="w")
        line = ttk.Frame(f)
        line.pack(fill="x", pady=(2, 12))
        self.status = ttk.Label(line, text="Starting...", style="Muted.TLabel")
        self.status.pack(side="left")

        # Determinate for setup, which reports named steps; uninstall does not, so it
        # keeps the bouncing bar rather than one that sits at 0%.
        if determinate:
            pct = ttk.Label(line, text="0%", style="Muted.TLabel")
            pct.pack(side="right")
            self.smooth = SmoothBar(f, pct)
            self.bar = self.smooth.bar
        else:
            self.smooth = None
            self.bar = ttk.Progressbar(f, mode="indeterminate")
            self.bar.start(12)
        self.bar.pack(fill="x")

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
        # Appears only if a sign-in link is logged. Sitting on a spinning bar with no
        # way to reach or copy the URL is what made the Tailscale hang unrecoverable.
        self._link = None
        self.link_btn = ttk.Button(self.btnrow, text="Copy sign-in link",
                                   command=self._copy_link)
        self.open_btn = ttk.Button(self.btnrow, text="Open sign-in page",
                                   command=self._open_link)

    def _copy_link(self):
        if self._link:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._link)
            self.link_btn.configure(text="Copied")

    def _open_link(self):
        if self._link:
            import webbrowser
            webbrowser.open(self._link)

    def _note_link(self, text):
        """Surface a sign-in URL as buttons the moment it is logged."""
        t = text.strip()
        if self._link or not t.startswith("https://"):
            return
        self._link = t
        self.link_btn.pack(side="left")
        self.open_btn.pack(side="left", padx=(8, 0))

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
            "tidy_download": self.v_tidy.get(),
        }
        self._build_progress()
        threading.Thread(target=self._run, args=(opts,), daemon=True).start()
        self.root.after(80, self._drain)

    def _run(self, opts):
        import progress

        def log(msg=""):
            self.q.put(("log", str(msg)))
        steps = progress.Steps(lambda label, pct: self.q.put(("step", (label, pct))))
        try:
            self.result = self.work(opts, log, steps)
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
                    self._note_link(payload)
                elif kind == "step":
                    label, pct = payload
                    if label:
                        self.status.configure(text=label[:70])
                    if pct is not None:
                        self.smooth.set(pct)
                elif kind == "done":
                    self.smooth.set(100)
                    self.smooth.shown = 100
                    self.smooth.tick()
                    self.status.configure(text="Starting Minecraft...")
                    # Setup finishes by opening the game and getting out of the way.
                    # A "Play" button here would just be one more thing to click.
                    self.launch_requested = True
                    self.root.after(900, self.root.destroy)
                    return
                elif kind == "fail":
                    self.status.configure(text="Setup did not finish.")
                    self.done_btn.configure(state="normal")
                    return
        except queue.Empty:
            pass
        self.smooth.tick()
        self.root.after(50, self._drain)

    # -------------------------------------------------------------- uninstall
    def _open_uninstall(self):
        for w in self.frame.winfo_children():
            w.destroy()
        f = self.frame

        # Buttons first so they always keep their space -- same reason as the options
        # screen, and this one grows by a whole checkbox when saved worlds exist.
        row = ttk.Frame(f)
        row.pack(side="bottom", fill="x", pady=(18, 0))
        ttk.Button(row, text="Back", command=self._back_to_options).pack(side="left")
        ttk.Button(row, text="Cancel", command=self.root.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(row, text="Remove", style="Accent.TButton",
                   command=self._start_uninstall).pack(side="right")

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
        self._build_progress(determinate=False)
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
                    self._note_link(payload)
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

    `work(log, steps)` runs on a worker thread and reports named steps through
    `steps` (progress.Steps), which fill the bar up to `launch_from` percent.

    `wait`, if given, is called on the GUI thread with the work's result once it
    succeeds. It starts the game and returns an object whose poll() is None while the
    window should stay up, or a reason string to close it (see gamewatch.LaunchWatch),
    or returns None itself to close straight away. The rest of the bar belongs to that
    wait, in two phases: Prism getting ready, then Minecraft loading.
    """

    # Seconds in the Prism phase before warning that a first launch downloads the game.
    SLOW_AFTER = 15
    # Share of the launch part of the bar given to Prism before the game JVM starts.
    PRISM_SHARE = 0.3

    def __init__(self, work, title="GreenCraft", wait=None, launch_from=80):
        self.work = work
        self.wait = wait
        self.launch_from = launch_from
        self.watch = None
        self.outcome = None
        self.q = queue.Queue()
        self.result = None
        self.failed = False
        self.phase = None
        self.phase_start = 0.0

        self.root = tk.Tk()
        self.root.title(title)
        _style(self.root)
        _centre(self.root, 460, 230)
        self.root.resizable(False, False)
        try:
            self.root.iconbitmap(_icon_path())
        except Exception:
            pass

        f = ttk.Frame(self.root, padding=20)
        f.pack(fill="both", expand=True)
        self.frame = f

        ttk.Label(f, text="GreenCraft", style="Title.TLabel").pack(anchor="w")
        line = ttk.Frame(f)
        line.pack(fill="x", pady=(4, 10))
        self.status = ttk.Label(line, text="Checking for updates...", style="TLabel")
        self.status.pack(side="left")
        self.pct = ttk.Label(line, text="0%", style="Muted.TLabel")
        self.pct.pack(side="right")
        self.smooth = SmoothBar(f, self.pct)
        self.bar = self.smooth.bar
        self.bar.pack(fill="x")
        self.detail = ttk.Label(f, text="", style="Muted.TLabel", wraplength=420)
        self.detail.pack(anchor="w", pady=(10, 0))

        self.lines = []

    def _expand_on_failure(self):
        self.bar.pack_forget()
        self.pct.pack_forget()
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
        import progress

        def log(msg=""):
            self.q.put(("log", str(msg)))
        steps = progress.Steps(lambda label, pct: self.q.put(("step", (label, pct))))
        try:
            self.result = self.work(log, steps)
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
                elif kind == "step":
                    label, pct = payload
                    if label:
                        self.status.configure(text=label[:60])
                    if pct is not None:
                        self.smooth.set(pct)
                elif kind == "done":
                    self._finish()
                    return
                elif kind == "fail":
                    self.failed = True
                    self._expand_on_failure()
                    return
        except queue.Empty:
            pass
        self.smooth.tick()
        self.root.after(50, self._drain)

    def _finish(self):
        if self.wait:
            try:
                self.watch = self.wait(self.result)
            except Exception as e:
                self.lines.append(f"{type(e).__name__}: {e}")
                self.failed = True
                self._expand_on_failure()
                return
        if not self.watch:
            self.root.destroy()
            return
        self.smooth.set(self.launch_from)
        self._enter("prism")
        self.root.after(50, self._animate)
        self.root.after(500, self._poll_watch)

    def _enter(self, phase):
        import time
        self.phase = phase
        self.phase_start = time.monotonic()
        if phase == "prism":
            self.status.configure(text="Starting Prism Launcher...")
            self.detail.configure(text="")
        else:
            self.status.configure(text="Loading Minecraft...")
            self.detail.configure(text="Loading the mods. The game window opens when "
                                       "this finishes.")

    def _launch_target(self):
        """How far along the launch part of the bar should be right now.

        Nothing reports real progress from inside Prism or the JVM, so each phase
        fills toward its end on a curve that slows as it goes: honest about direction,
        and it never claims to be done before the game window is actually there.
        """
        import math
        import time
        t = time.monotonic() - self.phase_start
        span = 100 - self.launch_from
        split = self.launch_from + span * self.PRISM_SHARE
        if self.phase == "prism":
            return self.launch_from + (split - self.launch_from) * 0.95 * (1 - math.exp(-t / 8))
        return split + (99 - split) * (1 - math.exp(-t / 25))

    def _animate(self):
        if self.phase is None:
            return
        try:
            self.smooth.set(self._launch_target())
            self.smooth.tick()
        except tk.TclError:
            return
        self.root.after(50, self._animate)

    def _poll_watch(self):
        try:
            outcome = self.watch.poll()
        except Exception as e:
            # A broken watcher must never strand the window: the game is already
            # starting, so just get out of the way.
            outcome = f"error: {type(e).__name__}: {e}"
        if outcome:
            self.outcome = outcome
            self.phase = None
            if outcome == "open":
                self.smooth.set(100)
                self.smooth.shown = 100
                self.smooth.tick()
                self.status.configure(text="Minecraft is open")
                self.root.after(400, self.root.destroy)
            else:
                self.root.destroy()
            return
        import time
        try:
            if self.phase == "prism" and self.watch.game_started():
                self._enter("game")
        except Exception:
            pass
        if (self.phase == "prism"
                and time.monotonic() - self.phase_start > self.SLOW_AFTER):
            self.status.configure(text="Preparing Minecraft...")
            self.detail.configure(
                text="The first launch downloads Minecraft and can take several minutes.")
        self.root.after(500, self._poll_watch)

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

