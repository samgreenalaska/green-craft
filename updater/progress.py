"""One progress bar across several named steps.

The work runs on a worker thread and knows what it is doing; the window only knows how
to draw. Steps sits between them: the work says "now updating mods, which ends at 75%"
and then reports how far through that step it is, and Steps turns that into a single
0-100 figure plus a label for the window.

Headless runs pass no emitter, and every call is then a no-op.
"""


class Steps:
    def __init__(self, emit=None):
        self.emit = emit or (lambda label, pct: None)
        self.lo = 0.0
        self.hi = 0.0
        self.base = ""

    def step(self, label, upto):
        """Start a step that fills the bar up to `upto` percent when finished."""
        self.lo = max(self.hi, self.lo)
        self.hi = max(float(upto), self.lo)
        self.base = label
        self.emit(label, self.lo)

    def part(self, done, total):
        """Report progress within the current step."""
        if total and total > 0:
            f = min(max(done / total, 0.0), 1.0)
            self.emit(None, self.lo + (self.hi - self.lo) * f)

    def label(self, text=None):
        """Change the step's text without moving the bar; None puts the step's own
        label back."""
        self.emit(text if text is not None else self.base, None)

    def bytes_cb(self, start=0.0, end=1.0):
        """A download callback that fills a slice [start, end] of the current step."""
        def cb(got, total):
            if total:
                self.part(start + (end - start) * min(got / total, 1.0), 1.0)
        return cb


NULL = Steps()
