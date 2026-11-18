"""What the app does on the computer outside its own window and its own data folder.

Three things, each only when the app's page asks for it:

* put a file the creator exported into the Downloads folder, under a name that is not taken;
* show a file it put there in the system's file manager;
* open a link to another site in the system's browser.

Why the core does these and not the window. In a browser a download and a link are the browser's business
and work the same everywhere. Inside the desktop app the page is shown by the system's web view (WebKit on
macOS, WebView2 on Windows), and what such a view does with them differs between the two and from a browser:
a download whose content the view can display may be displayed in place of the app, and a link that asks
for a new window may open nothing. So inside the desktop app the page asks the core, which does the same
thing on every system, and the page says what happened.

Nothing here reads a file. The only thing written is what the creator asked to export, into a new file.
Standard library only.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import unicodedata
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# Characters no file name may hold on Windows. They become a space on every system, so that a file made on
# one computer can be copied to another.
_FORBIDDEN = '<>:"/\\|?*'
# Names Windows keeps for devices, whatever ending follows them.
_DEVICES = frozenset(["CON", "PRN", "AUX", "NUL"] + ["COM%d" % n for n in range(1, 10)] + ["LPT%d" % n for n in range(1, 10)])
# How long a name may be without its ending. File systems allow 255 bytes for a whole name; the ending and
# the " (12)" that keeps two files apart need room, and a Chinese character takes three bytes.
MAX_NAME_CHARS = 120
MAX_NAME_BYTES = 200
# What is kept of the end of a name that has to be shortened: the end says which export it is.
KEPT_END_CHARS = 24
# After this many files with one name in one folder, no further name is tried there.
MAX_SAME_NAME = 999
MAX_LINK_CHARS = 2000


class NotSaved(OSError):
    """None of the folders took the file. The message is what the system said about the last one tried."""


def downloads_folder() -> Path:
    """The Downloads folder of whoever runs the app, as the system names it. It may not exist yet."""
    found: Optional[Path] = None
    try:
        if os.name == "nt":
            found = _windows_downloads()
        elif sys.platform != "darwin":
            found = _xdg_downloads()
    except Exception:                    # noqa: BLE001 - whatever went wrong asking the system, the usual place is the answer
        found = None
    return found or Path.home() / "Downloads"


def _windows_downloads() -> Optional[Path]:
    """Ask Windows where this user's Downloads folder is: people move it to another drive."""
    import ctypes

    class Guid(ctypes.Structure):
        _fields_ = [("first", ctypes.c_uint32), ("second", ctypes.c_uint16), ("third", ctypes.c_uint16),
                    ("rest", ctypes.c_ubyte * 8)]

    # FOLDERID_Downloads, {374DE290-123F-4565-9164-39C4925E467B}
    downloads = Guid(0x374DE290, 0x123F, 0x4565, (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
    ask = ctypes.windll.shell32.SHGetKnownFolderPath
    ask.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    ask.restype = ctypes.c_long
    release = ctypes.windll.ole32.CoTaskMemFree
    release.argtypes = [ctypes.c_void_p]
    release.restype = None
    answer = ctypes.c_void_p()
    result = ask(ctypes.byref(downloads), 0, None, ctypes.byref(answer))
    try:
        if result != 0 or not answer.value:
            return None
        path = Path(ctypes.wstring_at(answer.value))
        return path if path.is_absolute() else None
    finally:
        release(answer)


def _xdg_downloads() -> Optional[Path]:
    """On Linux the desktop writes its folders into ``user-dirs.dirs``; the Downloads folder has a translated name there."""
    config = Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")) / "user-dirs.dirs"
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found = re.search(r'^[ \t]*XDG_DOWNLOAD_DIR[ \t]*=[ \t]*"([^"\n]+)"', text, re.MULTILINE)
    if not found:
        return None
    value = found.group(1)
    if value.startswith("$HOME/"):
        value = str(Path.home() / value[len("$HOME/"):])
    path = Path(value)
    return path if path.is_absolute() and path != Path.home() else None


def safe_name(name: Any) -> str:
    """``name`` as a file name that every system takes, and that shows what it is.

    A script's title becomes part of the name, and a title is free text. What no system or only some refuse
    (a path separator, a colon, a question mark, a line break) becomes a space. Characters that are not shown
    (among them the ones that make text run right to left, which can make ``exe.txt`` read as ``txt.exe``) are
    left out. A name Windows keeps for a device gets an underscore in front. A name that is too long loses its
    middle, not its end: the end says which export it is, and the ending says what kind of file.
    """
    text = unicodedata.normalize("NFC", str(name if name is not None else ""))
    kept: List[str] = []
    for char in text:
        kind = unicodedata.category(char)
        if kind in ("Cf", "Cs"):                    # not shown (also the right-to-left marks), and halves of a character
            continue
        kept.append(" " if char in _FORBIDDEN or kind in ("Cc", "Zl", "Zp") else char)
    text = "".join(kept)
    stem, dot, ending = text.rpartition(".")
    if not dot or not re.fullmatch(r"[A-Za-z0-9]{1,10}", ending):
        stem, ending = text, ""
    stem = " ".join(stem.split()).strip(" .")
    if not stem:
        stem = "export"
    if stem.split(".")[0].strip().upper() in _DEVICES:
        stem = "_" + stem
    if len(stem) > MAX_NAME_CHARS or len(stem.encode("utf-8")) > MAX_NAME_BYTES:
        end = stem[-KEPT_END_CHARS:].lstrip(" .")
        start = stem[:MAX_NAME_CHARS - KEPT_END_CHARS - 1]
        while start and len((start + "…" + end).encode("utf-8")) > MAX_NAME_BYTES:
            start = start[:-1]
        stem = (start.rstrip(" .") + "…" + end).strip(" .")
    return stem + ("." + ending if ending else "")


def _numbered(folder: Path, name: str, number: int) -> Path:
    """``name`` in ``folder``; from the second on as ``name (2).txt``, the way a browser names a second download."""
    if number < 2:
        return folder / name
    stem, dot, ending = name.rpartition(".")
    return folder / ("%s (%d).%s" % (stem, number, ending) if dot and stem else "%s (%d)" % (name, number))


def save(name: Any, data: bytes, folders: Sequence[Path]) -> Path:
    """Write ``data`` into a new file called ``name`` (made safe) in the first of ``folders`` that takes it.

    A file that is there is never replaced: the new one gets a number, and it is made in one step that fails
    when the name was taken meanwhile, so two saves at once cannot land in one file. A folder that is not
    there is made. When a folder cannot be written to (macOS asks the person whether an app may use Downloads,
    and they may say no), the next one is tried. A file that could only be written in part is removed again.
    Returns where the file is. Raises ``NotSaved`` when no folder took it.
    """
    name = safe_name(name)
    problem: Optional[OSError] = None
    for folder in folders:
        folder = Path(folder)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            problem = exc
            continue
        for number in range(1, MAX_SAME_NAME + 1):
            path = _numbered(folder, name, number)
            taken = FileExistsError("%d files called %s are there already" % (MAX_SAME_NAME, name))
            if os.path.lexists(str(path)):          # a file, or a folder or a link of that name: what opening those says differs between systems
                problem = taken
                continue
            try:
                handle = open(str(path), "xb")
            except FileExistsError:                 # taken between the look and the attempt
                problem = taken
                continue
            except OSError as exc:
                problem = exc
                break
            try:
                with handle:
                    handle.write(data)
            except OSError as exc:
                problem = exc
                try:
                    path.unlink()
                except OSError:
                    pass
                break
            return path
    raise NotSaved(str(problem) if problem is not None else "no folder was given")


def reveal(path: Any) -> bool:
    """Show the file at ``path`` in the system's file manager. False when that could not be started."""
    path = Path(path)
    try:
        if sys.platform == "darwin":
            return subprocess.run(["/usr/bin/open", "-R", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=20).returncode == 0
        if os.name == "nt":
            # One command line, written out: Explorer wants the path straight after the comma. A path on Windows
            # cannot hold a quotation mark, so the quoting cannot be broken out of. Its exit code says nothing.
            subprocess.Popen('explorer /select,"%s"' % path, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True
        opener = shutil.which("xdg-open")           # no common way to select a file there: its folder is opened
        if not opener:
            return False
        subprocess.Popen([opener, str(path.parent)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def link_problem(url: Any) -> str:
    """Why ``url`` is not opened, or ``""`` when it may be.

    Only an ordinary web address is handed to the system: ``http`` or ``https``, with a host, written without
    spaces or characters that are not shown. Anything else (a file, a program's own kind of address) is refused,
    because the system would start whatever program is registered for it.
    """
    if not isinstance(url, str) or not url:
        return "missing"
    if len(url) > MAX_LINK_CHARS:
        return "too_long"
    if any(char.isspace() or unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in url) or "\\" in url:
        return "not_plain"
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
    except ValueError:
        return "not_plain"
    if parts.scheme not in ("http", "https"):
        return "not_web"
    if not host:
        return "no_host"
    return ""


def open_link(url: str) -> bool:
    """Open ``url`` in the system's browser. False when it is refused (``link_problem``) or nothing could be started."""
    if link_problem(url):
        return False
    try:
        if sys.platform == "darwin":
            return subprocess.run(["/usr/bin/open", url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=20).returncode == 0
        if os.name == "nt":
            os.startfile(url)                       # noqa: S606 - an http(s) address, checked above: the browser takes it
            return True
        return bool(webbrowser.open(url))
    except (OSError, subprocess.SubprocessError, webbrowser.Error):
        return False


class Recorded:
    """Stands in for this module while the desktop app is being tried by a script, with nobody at the screen.

    Files are saved for real, since that is what is being tried. A link or a folder is written down and not
    opened: a browser or a file window opened on a machine where nobody will close it proves nothing more,
    and what is written down shows that the page's request reached the core.
    """

    def __init__(self, record: Dict[str, Any], real: Any = None) -> None:
        self.record = record
        self.real = real if real is not None else sys.modules[__name__]

    def downloads_folder(self) -> Path:
        return self.real.downloads_folder()

    def save(self, name: Any, data: bytes, folders: Sequence[Path]) -> Path:
        return self.real.save(name, data, folders)

    def link_problem(self, url: Any) -> str:
        return self.real.link_problem(url)

    def open_link(self, url: str) -> bool:
        self.record.setdefault("opened", []).append(url)
        return True

    def reveal(self, path: Any) -> bool:
        self.record.setdefault("shown", []).append(str(path))
        return True
