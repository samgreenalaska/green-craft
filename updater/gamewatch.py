"""Tell when Minecraft's window has actually appeared.

Prism's launch dialog closes as soon as the game's JVM starts, and the JVM then spends
roughly ten seconds bootstrapping Fabric and the mods before Minecraft creates a
window. With nothing on screen for that stretch it looks as if the launch failed, so
the progress window stays up until a Minecraft window exists.

Plain ctypes against user32/kernel32, so nothing extra goes into the packaged exe.
"""
import ctypes
import os
import time
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TH32CS_SNAPPROCESS = 0x2
_INVALID_HANDLE = wintypes.HANDLE(-1).value

# The game runs under javaw.exe as Prism launches it; java.exe covers a console JVM.
JAVA = {"javaw.exe", "java.exe"}
PRISM = {"prismlauncher.exe"}


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


_kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]


def _exe_name(pid):
    h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        _kernel32.CloseHandle(h)


def minecraft_windows():
    """Handles of visible top-level windows titled "Minecraft..." owned by a JVM.

    Checking the owning process matters: a browser tab or File Explorer folder called
    "Minecraft" would otherwise count.
    """
    found = set()

    def cb(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        n = _user32.GetWindowTextLengthW(hwnd)
        if n < 9:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        if not buf.value.startswith("Minecraft"):
            return True
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if _exe_name(pid.value) in JAVA:
            found.add(hwnd)
        return True

    _user32.EnumWindows(_WNDENUMPROC(cb), 0)
    return found


def running(names):
    """True if any process whose image name is in `names` (lower case) is running."""
    snap = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE:
        return True  # can't tell, so don't conclude it has gone
    try:
        e = _PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = _kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() in names:
                return True
            ok = _kernel32.Process32NextW(snap, ctypes.byref(e))
        return False
    finally:
        _kernel32.CloseHandle(snap)


class LaunchWatch:
    """Polled from the GUI thread until Minecraft's window shows up.

    Take `before` with minecraft_windows() BEFORE starting Prism, so a game that was
    already open does not count as this launch succeeding.

    poll() returns None while still waiting, otherwise why it stopped:
      "open"     a new Minecraft window appeared
      "gone"     Prism is no longer running and no window came (cancelled, or failed --
                 Prism shows its own error for that)
      "timeout"  gave up; Minecraft may still be on its way
    """

    def __init__(self, before, timeout=900.0, grace=20.0, clock=time.monotonic):
        self.before = set(before)
        self.timeout = timeout
        self.grace = grace
        self.clock = clock
        self.start = clock()
        self._absent = 0

    def elapsed(self):
        return self.clock() - self.start

    def poll(self):
        if minecraft_windows() - self.before:
            return "open"
        t = self.elapsed()
        if t > self.timeout:
            return "timeout"
        # Prism stays running for as long as the game does. Give it a grace period to
        # start at all, then require it to be missing twice in a row before giving up.
        if t > self.grace and not running(PRISM):
            self._absent += 1
            if self._absent >= 2:
                return "gone"
        else:
            self._absent = 0
        return None
