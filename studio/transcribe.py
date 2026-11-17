"""Transcribe the creator's videos on this Mac with Whisper. Nothing is uploaded.

Why this exists: a transcript made here carries real timestamps, so speaking
rate is measured (``studio.timing``) instead of assumed, and Whisper is asked
to write the words down as spoken, sentence particles included. It does not
always manage that. In testing on synthetic speech it wrote 嘛 as 吗, 呗 as 背,
喔 as 哦, 呐 as 呢, once 啦 as 了, and dropped one 诶. Several of those pairs
sound the same, so no transcriber can settle them by ear. Treat the wording of
a transcript as something the creator checks, not as proof of what they said.

Where things live: everything this module installs is under
``<repo>/local_tools/whisper/`` and is git-ignored. A private Python
environment made with the Python that runs the app, an exact list of packages,
and the speech model. Nothing is installed system-wide and nothing is written
to ``~/.cache``. ``uninstall()`` removes it.

Inside the desktop app two of those are different, because the app folder is
part of a signed program that an update replaces, and the Python inside it is
not the one the packages were proven with. There everything is under
``whisper/`` beside the data folder, in the system's folder for application
data (``_install_folder``), and the private environment is made with the
Python that comes with Apple's Command Line Tools, when that is installed
(``_environment_python``). The Python inside the app only starts the
programs; it never loads the speech libraries.

Two things do sit outside that folder. This module writes the audio it is
about to transcribe as a temporary WAV in the system temporary folder and
deletes it as soon as the transcription ends (the two tiny files it uses to
try the seal out, see below, come and go in the same place). And macOS, not this code, keeps
its own compiled-shader cache for any program that uses the graphics chip
(seen at ``/var/folders/.../C/com.apple.python3/``). That cache belongs to the
system, is shared with anything else that runs python3, and ``uninstall()``
leaves it.

What uses the network: only ``install()``. It downloads packages from pypi.org
and one model from huggingface.co (which redirects the large file to its own
download host; ``us.aws.cdn.hf.co`` when this was tested), and sends nothing
about the creator.
``transcribe_file()`` makes no network requests, and that is enforced by macOS
rather than expected of the code: the audio tool (afconvert, built into macOS)
and the worker process are both started under rules, applied by macOS itself,
that deny them the network, and the audio tool also every personal file but
the one it was given ("the seal", below). Before each transcription the seal
is tried out on this Mac; where it cannot be set up, nothing is transcribed
and the creator is told why. The worker is still started in offline mode, and
still switches off its own Python-level network access, as a second line.

This module is standard library only and imports without Whisper installed.
The heavy libraries are only ever loaded by ``whisper_worker.py`` in its own
process.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import buildinfo

try:
    import fcntl                        # not on every system this module can be imported on; only a Mac uses it
except ImportError:                     # pragma: no cover
    fcntl = None                        # type: ignore[assignment]

MODEL = "mlx-community/whisper-large-v3-turbo"          # prompts/01 section 1A
MODEL_REVISION = "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb"
# (file, bytes, sha256). Pinned to one revision and checked after download.
MODEL_FILES: Tuple[Tuple[str, int, str], ...] = (
    ("config.json", 268, "b34fc29e4e11e0a25e812775dd67f4dd16fc2c8eb43d28ae25ff7d660ecb6379"),
    ("weights.safetensors", 1613977612, "951ed3fc1203e6a62467abb2144a96ce7eafca8fa77e3704fdb8635ff3e7f8a6"),
)
MODEL_URL = "https://huggingface.co/%s/resolve/%s/%s"

# Proven together on Python 3.9.6, macOS 26.7, Apple M5 Pro (14 Nov 2026): installed from
# nothing with exactly these pins, then used to transcribe speech. mlx 0.29.3 is what pip's
# resolver chose for Python 3.9 (the newest mlx, 0.32.4, needs 3.10). torch is a declared dependency
# of mlx-whisper, but the only file in it that imports torch is mlx_whisper/torch_whisper.py,
# which transcription never loads; torch and the five packages only torch needs are left out.
# Only this Mac was tested. Other chips, macOS versions and Python builds are unproven.
PYTHON_VERSION = (3, 9)
# MLX's install page (ml-explore.github.io/mlx, read 15 Nov 2026) states macOS 14.0 or newer.
# The pinned mlx 0.29.3 wheels on PyPI are tagged macOS 13.0+, so 13.x may work, but nothing
# older than this Mac's macOS 26 was tested; the documented figure is used.
MIN_MACOS = (14, 0)
INDEX_URL = "https://pypi.org/simple"
PIP_PIN = "pip==26.0.1"
PACKAGES: Tuple[str, ...] = (
    "annotated-doc==0.0.5", "anyio==4.12.1", "certifi==2026.10.23", "charset-normalizer==3.5.2",
    "click==8.1.8", "exceptiongroup==1.3.1", "filelock==3.19.1", "fsspec==2025.10.0", "h11==0.16.0",
    "hf-xet==1.8.0", "httpcore==1.0.9", "httpx==0.28.1", "huggingface-hub==1.8.0", "idna==3.20",
    "llvmlite==0.43.0", "markdown-it-py==3.0.0", "mdurl==0.1.2", "mlx==0.29.3", "mlx-metal==0.29.3",
    "mlx-whisper==0.4.3", "more-itertools==10.8.0", "numba==0.60.0", "numpy==2.0.2", "packaging==26.3",
    "pygments==2.21.0", "pyyaml==6.0.3", "regex==2026.1.15", "requests==2.32.5", "rich==15.0.0",
    "scipy==1.13.1", "shellingham==1.5.4", "tiktoken==0.14.0", "tqdm==4.70.1", "typer==0.23.2",
    "typing-extensions==4.16.0", "urllib3==2.6.3",
)

ROOT = Path(__file__).resolve().parent.parent
# The Pythons Apple ships with its developer tools, at the places the tools install them. /usr/bin/python3 is
# deliberately not here: on a Mac without the tools that file is not a Python but a program that puts up a
# window offering to install them.
APPLE_PYTHONS = ("/Library/Developer/CommandLineTools/usr/bin/python3",
                 "/Applications/Xcode.app/Contents/Developer/usr/bin/python3")


def _install_folder() -> Path:
    """Where everything this module installs is kept.

    From the zip or the source: ``local_tools/whisper`` in the app folder. Inside the desktop app: ``whisper``
    beside the data folder, which the shell names in STUDIO_DATA_DIR before it starts the core. That is in the
    system's folder for application data, so an update of the app keeps what was set up, and nothing is ever
    written into the app itself. (Inside the desktop app with no data folder named, which the shell never does,
    the app folder is answered and ``_platform_problem`` then refuses to set anything up there.)
    """
    data = os.environ.get("STUDIO_DATA_DIR")
    if data and buildinfo.desktop():
        return Path(os.path.abspath(data)).parent / "whisper"
    return ROOT / "local_tools" / "whisper"


HOME = _install_folder()
WORKER = Path(__file__).resolve().with_name("whisper_worker.py")
AFCONVERT = "/usr/bin/afconvert"
SAY = "/usr/bin/say"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"  # ships with macOS: starts a program under rules that macOS itself enforces
CAT = "/bin/cat"                        # two small programs that ship with macOS, used only to try the seal out
NC = "/usr/bin/nc"

# The seal. Every program this module starts in order to transcribe (the audio tool, the Whisper worker,
# and the system voice the self-check uses) is started through sandbox-exec with these rules. macOS
# enforces them on that program and on anything it starts in turn, and they cannot be lifted from inside.
# Apple marks sandbox-exec as deprecated but ships it with every macOS this app supports; on a Mac where
# it is missing or will not take these rules, transcription refuses to run rather than run without them.
#
# NETWORK_SEAL: the program, and anything it starts, cannot open a network connection of any kind. No
# connection out, none in and no listening port, for TCP, UDP and local (Unix) sockets alike, whether it
# is asked for from Python, from compiled code inside a package or by a child process. Looking a name up
# is network use too: the resolver's socket goes with the rest, and the last line closes the resolver's
# other door, a system service.
#
# What it is not: a full cage. Everything else stays allowed, so a program written on purpose to get round
# the rules could still ask another part of macOS to act for it. One such door was seen to be open on
# macOS 26.7: Launch Services, the service that hands a web address to the browser, still answers a sealed
# program. The rules make "the transcription programs use no network" true of the code that is installed,
# including when a package misbehaves by itself. They are not a defence against a package that is hostile
# on purpose; that needs rules that deny everything and allow only what transcription uses.
NETWORK_SEAL = (
    "(version 1)"
    "(allow default)"
    "(deny network*)"
    '(deny mach-lookup (global-name "com.apple.dnssd.service"))'
)
# AUDIO_SEAL: the same, and the program cannot read the contents of any file in the personal places (every
# home folder, other disks, temporary folders) except the one file it was given and the folder it writes
# into. The audio tool is the one program that opens a file that came from outside, and a video file can
# name a different file as the place where its sound is kept. The four places are handed over with -D
# (see _sealed_audio). Files outside those places (the system, installed programs) stay readable.
AUDIO_SEAL = NETWORK_SEAL + (
    '(deny file-read-data (subpath "/Users") (subpath "/Volumes") (subpath "/private/var/folders")'
    ' (subpath "/private/tmp") (subpath "/private/var/tmp") (subpath (param "HOME")) (subpath (param "TEMP")))'
    '(allow file-read-data (literal (param "SOURCE")) (subpath (param "SCRATCH")))'
)
WORKER_REFUSED = "network_not_denied"   # the worker's answer when it finds it can reach the network (whisper_worker.REFUSED)

FREE_SPACE_NEEDED = 4 * 1000 ** 3       # bytes free before a first install is attempted
EXTRACT_TIMEOUT = 900                   # seconds, for the audio tool
WORKER_TIMEOUT_MIN = 900                # seconds; longer audio gets three times its own length
SEAL_TIMEOUT = 20                       # seconds, for each short program run to try the seal out
SEAL_RETRY_SEC = 30.0                   # a trial of the seal that failed is repeated after this long
SELF_CHECK_TEXT = "This is a short test of transcription on this Mac."

_INSTALL_LOCK = threading.Lock()        # one setup at a time
_RUN_LOCK = threading.Lock()            # one model in memory at a time
_SEAL_LOCK = threading.Lock()           # one trial of the seal at a time
_SEAL_STATE: Dict[str, Any] = {"at": None, "detail": ""}    # when the seal was last tried; what went wrong ("" = it held)

# Plain wording for every way this can fail: (English, Chinese). ``reason`` picks the entry.
_USE_SUBTITLES = (" You can still add subtitle files (.srt or .vtt) or paste the words.",
                  "你仍然可以添加字幕文件（.srt 或 .vtt），或者直接粘贴文字。")
_TEXT: Dict[str, Tuple[str, str]] = {
    "not_macos": ("Transcribing on this computer only works on a Mac with an Apple chip." + _USE_SUBTITLES[0],
                  "在本机转写只支持 Apple 芯片的 Mac。" + _USE_SUBTITLES[1]),
    "not_apple_silicon": ("This Mac does not have an Apple chip (M1 or newer), which on-device transcription needs."
                          + _USE_SUBTITLES[0],
                          "这台 Mac 不是 Apple 芯片（M1 或更新），无法在本机转写。" + _USE_SUBTITLES[1]),
    "macos_too_old": ("On-device transcription needs macOS 14 or newer. Update macOS in System Settings, "
                      "or add subtitle files instead.",
                      "在本机转写需要 macOS 14 或更新版本。你可以在「系统设置」里更新 macOS，或者改用字幕文件。"),
    "python_version": ("On-device transcription has only been proven with the Python that comes with Apple's "
                       "Command Line Tools (3.9). Start the app by double-clicking \"Start Learn My Voice.command\".",
                       "在本机转写目前只在苹果命令行工具自带的 Python（3.9）上验证过。请双击「启动工作台.command」来打开应用。"),
    "desktop_app": ("Transcribing videos does not work in the desktop app on this Mac yet. Subtitle files (.srt or "
                    ".vtt) and pasted words work here.",
                    "在这台 Mac 上，桌面应用暂时还不能转写视频。字幕文件（.srt 或 .vtt）和粘贴文字在这里都可以用。"),
    "desktop_needs_tools": ("To transcribe videos, the desktop app uses the Python that comes with Apple's free "
                            "Command Line Tools, and they are not installed on this Mac. To install them, open the "
                            "Terminal app, copy this into it: xcode-select --install (two hyphens before install), "
                            "press Return, and follow the window that appears. Then come back here. Subtitle files "
                            "(.srt or .vtt) and pasted words work without them.",
                            "桌面应用转写视频时，用的是苹果免费的「命令行工具」里自带的 Python，而这台 Mac 还没有安装它。"
                            "安装方法：打开「终端」应用，把这一行复制进去：xcode-select --install（install 前面是两个短横线），"
                            "按回车，然后按弹出的窗口操作。"
                            "装好以后回到这里就可以了。字幕文件（.srt 或 .vtt）和粘贴文字不需要它也能用。"),
    "no_audio_tool": ("The macOS audio converter (afconvert) is missing on this Mac, so sound cannot be read "
                      "from videos." + _USE_SUBTITLES[0],
                      "这台 Mac 上找不到系统自带的音频转换工具（afconvert），无法从视频里读出声音。" + _USE_SUBTITLES[1]),
    "no_network_block": ("This Mac did not let the app cut the transcription step off from the network and from "
                         "your other files, so transcription is switched off. It only runs while macOS itself "
                         "is blocking it from using the network." + _USE_SUBTITLES[0],
                         "这台 Mac 没能让应用把转写这一步和网络、和你的其他文件隔开，所以转写已停用。"
                         "只有在 macOS 自己拦住它、不让它联网的时候，转写才会运行。" + _USE_SUBTITLES[1]),
    "not_installed": ("On-device transcription is not set up yet. Set it up first; it is a one-time download.",
                      "本机转写还没有安装。请先完成安装，只需要下载一次。"),
    "busy": ("Setup is already running. Wait for it to finish.",
             "安装正在进行中，请等它完成。"),
    "disk_full": ("Not enough free space. Setup needs about {need} GB free and this disk has {free} GB. "
                  "Free up some space and try again.",
                  "磁盘空间不够。安装大约需要 {need} GB 可用空间，现在只有 {free} GB。请你腾出一些空间后再试。"),
    "environment": ("Could not create the private Python environment for transcription in {where}. Check that "
                    "this folder can be written to, then try again.",
                    "无法在 {where} 里创建转写用的独立 Python 环境。请确认这个文件夹可以写入，然后再试一次。"),
    "no_internet": ("Could not reach the internet to finish the download. Check your connection and try again. "
                    "What was already downloaded is kept.",
                    "无法连接网络完成下载。请检查你的网络后再试一次，已经下载的部分会保留。"),
    "packages": ("The transcription software could not be installed. Try again; if it fails the same way, "
                 "use subtitle files instead.",
                 "转写软件没有安装成功。请再试一次；如果还是失败，可以改用字幕文件。"),
    "download_damaged": ("The downloaded speech model did not match its expected fingerprint, so it was thrown "
                         "away. Try again.",
                         "下载的语音模型和预期的校验值不一致，已经丢弃。请再试一次。"),
    "self_check": ("Everything downloaded, but a test transcription on this Mac did not work, so on-device "
                   "transcription was left switched off. Try again; if it fails the same way, use subtitle files.",
                   "下载已完成，但在这台 Mac 上试转写没有成功，所以本机转写没有启用。请再试一次；如果还是失败，可以改用字幕文件。"),
    "file_missing": ("That file could not be found. Choose it again.",
                     "找不到这个文件，请你重新选择。"),
    "no_audio": ("No sound could be read from this file. Check that the video has sound and is an .mp4 or .mov "
                 "file, or add its subtitle file instead.",
                 "没能从这个文件里读出声音。请确认视频有声音，并且是 .mp4 或 .mov 格式；也可以改为添加它的字幕文件。"),
    "timeout": ("Transcription took too long and was stopped. Try a shorter video, or add its subtitle file instead.",
                "转写时间太长，已经停止。你可以换一个短一点的视频，或者改为添加它的字幕文件。"),
    "worker": ("Transcription did not finish. Try once more; if it fails again, add the subtitle file for this "
               "video instead.",
               "转写没有完成。请再试一次；如果还是失败，可以改为添加这个视频的字幕文件。"),
}

_NETWORK_HINTS = ("NewConnectionError", "Failed to establish a new connection", "Could not fetch URL",
                  "Connection refused", "ConnectTimeoutError", "ReadTimeoutError", "ProxyError",
                  "nodename nor servname", "Temporary failure in name resolution", "Network is unreachable")


class TranscribeError(Exception):
    """On-device transcription could not be set up or run.

    ``code`` is the stable identifier the UI translates: unsupported,
    not_installed, no_audio, failed. ``reason`` says which case it was (a key
    of the wording table above). The message is plain English that is safe to
    show and tells the person what to do; ``message_zh`` is the same in
    Chinese. ``detail`` keeps the technical output for a log, never for display.
    """

    def __init__(self, message: str, code: str = "failed", reason: str = "", message_zh: str = "",
                 detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason or code
        self.message_zh = message_zh or message
        self.detail = detail


def _error(code: str, reason: str, detail: str = "", **values: Any) -> TranscribeError:
    en, zh = _TEXT[reason]
    return TranscribeError(en.format(**values), code, reason, zh.format(**values), detail[-2000:])


# --------------------------------------------------------------------------- where things are

def _venv_python() -> Path:
    return HOME / "venv" / "bin" / "python"


def _model_dir() -> Path:
    return HOME / "model"


def _marker() -> Path:
    return HOME / "installed.json"


# --------------------------------------------------------------------------- running other programs

def _run(args: Sequence[Any], env: Optional[Dict[str, str]] = None,
         timeout: Optional[float] = None) -> Tuple[Optional[int], str, str]:
    """Run a program and wait for it. Returns (exit code, stdout, stderr); code None means timed out.

    Arguments are passed as a list and never through a shell, so a file name
    with spaces, quotes or ``;`` is only ever a file name.
    """
    try:
        done = subprocess.run([str(a) for a in args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env, timeout=timeout, shell=False)
    except subprocess.TimeoutExpired as exc:        # subprocess.run has already killed and reaped the child
        return None, "", "timed out after %s seconds\n%s" % (timeout, _decode(exc.stderr))
    except OSError as exc:
        return 127, "", str(exc)
    return done.returncode, _decode(done.stdout), _decode(done.stderr)


def _decode(data: Optional[bytes]) -> str:
    return (data or b"").decode("utf-8", "replace")


def _base_env() -> Dict[str, str]:
    """A small fixed environment: no tokens, keys or Python settings are passed along."""
    env = {k: os.environ[k] for k in ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL") if k in os.environ}
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    return env


def _install_env() -> Dict[str, str]:
    env = _base_env()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy",
                "all_proxy", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        if key in os.environ:
            env[key] = os.environ[key]
    env["NETRC"] = os.devnull                       # pip must not send saved logins to the package index
    return env


def _worker_env() -> Dict[str, str]:
    """Environment for transcription: Hugging Face offline mode on, caches inside the install folder."""
    env = _base_env()
    env.update({
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HOME": str(HOME / "hf"),
        "NUMBA_CACHE_DIR": str(HOME / "numba-cache"),
    })
    return env


# --------------------------------------------------------------------------- the seal

def _kernel_path(path: Any) -> str:
    """``path`` as macOS itself spells it, which is the spelling the seal's file rules are compared with.

    One file can be reached by several spellings (``/var/...`` and
    ``/private/var/...``; ``/System/Volumes/Data/Users/...`` and
    ``/Users/...``). The rules that deny a folder hold whichever is used, but
    the rule that allows the one given file only matches this spelling.
    Raises ``OSError`` if the path cannot be opened.
    """
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        get_path = getattr(fcntl, "F_GETPATH", None) if fcntl is not None else None
        if get_path is not None:
            return fcntl.fcntl(descriptor, get_path, bytes(1024)).split(b"\0", 1)[0].decode("utf-8", "surrogateescape")
    finally:
        os.close(descriptor)
    return os.path.realpath(str(path))


def _place(path: str, fallback: str) -> str:
    try:
        return _kernel_path(path)
    except OSError:
        return fallback


def _sealed(args: Sequence[Any]) -> List[Any]:
    """``args`` as a command that macOS runs with the network denied (NETWORK_SEAL)."""
    return [SANDBOX_EXEC, "-p", NETWORK_SEAL] + list(args)


def _sealed_audio(source: str, scratch: str, args: Sequence[Any]) -> List[Any]:
    """``args`` as a command that, besides having no network, cannot read personal files other than ``source`` (AUDIO_SEAL).

    Both paths must be spelled the way ``_kernel_path`` spells them. They are
    handed to sandbox-exec as separate values (``-D``) and never pasted into
    the rules, so no file name, however odd, can change the rules.
    """
    places = (("SOURCE", source), ("SCRATCH", scratch), ("HOME", _place(os.path.expanduser("~"), "/Users")),
              ("TEMP", _place(tempfile.gettempdir(), "/private/tmp")))
    command: List[Any] = [SANDBOX_EXEC]
    for name, value in places:
        command += ["-D", "%s=%s" % (name, value)]
    return command + ["-p", AUDIO_SEAL] + list(args)


# What _check_seal runs under the network seal: one attempt to connect to a port this app has open on
# this computer's own address. Only "not permitted" (errno EPERM, which is what macOS answers under the
# seal) counts as refused; a connection that works, or fails some other way, does not.
_NETWORK_PROBE = (
    "import socket, sys\n"
    "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
    "s.settimeout(5)\n"
    "try:\n"
    "    s.connect(('127.0.0.1', int(sys.argv[1])))\n"
    "    print('allowed')\n"
    "except PermissionError:\n"
    "    print('refused')\n"
    "except Exception as exc:\n"
    "    print('error ' + type(exc).__name__)\n"
)
_SEAL_GIVEN, _SEAL_OTHER = "the file that was given", "a different file"


def _check_seal() -> str:
    """Try the seal out on this Mac. Returns ``""`` when it holds, else what went wrong (kept for a log).

    Nothing about it is taken on trust. With a port open on this computer's
    own address and two small files in a temporary folder:

    1. A program started under the network seal tries to connect to that
       port. macOS must answer "not permitted".
    2. ``cat``, started under the audio seal and told that one of the files is
       the one it was given, must fail to read the other file,
    3. and must read the given one, so a real video can still be read.
    4. ``nc``, started under the audio seal, tries the port as well.

    At the end the port must not have seen a single connection. A missing
    sandbox-exec, rules this version of macOS will not accept, or a seal that
    does not actually seal all end up here as a reason, and transcription
    then refuses to run.
    """
    if not os.path.exists(SANDBOX_EXEC):
        return "%s is missing" % SANDBOX_EXEC
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen(8)
            listener.setblocking(False)
        except OSError as exc:
            return "could not open a test port on this computer: %s" % exc
        port = str(listener.getsockname()[1])
        env = _base_env()
        with tempfile.TemporaryDirectory(prefix="learn-my-voice-") as tmp:
            given, other, room = Path(tmp) / "given", Path(tmp) / "other", Path(tmp) / "scratch"
            given.write_text(_SEAL_GIVEN, encoding="ascii")
            other.write_text(_SEAL_OTHER, encoding="ascii")
            room.mkdir()
            try:
                given_at, other_at, room_at = _kernel_path(given), _kernel_path(other), _kernel_path(room)
            except OSError as exc:
                return "could not open the test files: %s" % exc
            code, out, err = _run(_sealed([sys.executable, "-I", "-S", "-c", _NETWORK_PROBE, port]), env=env,
                                  timeout=SEAL_TIMEOUT)
            if code != 0 or out.strip() != "refused":
                return "the network was not refused under the seal: exit %s %s %s" % (code, out.strip()[:80], err.strip()[-200:])
            code, out, err = _run(_sealed_audio(given_at, room_at, [CAT, other_at]), env=env, timeout=SEAL_TIMEOUT)
            if code == 0 or _SEAL_OTHER in out:
                return "a different file could be read under the seal"
            code, out, err = _run(_sealed_audio(given_at, room_at, [CAT, given_at]), env=env, timeout=SEAL_TIMEOUT)
            if code != 0 or out != _SEAL_GIVEN:
                return "the given file could not be read under the seal: exit %s %s" % (code, err.strip()[-200:])
            _run(_sealed_audio(given_at, room_at, [NC, "-z", "-w", "2", "127.0.0.1", port]), env=env, timeout=SEAL_TIMEOUT)
        try:
            listener.accept()[0].close()
        except (BlockingIOError, InterruptedError):
            return ""
        return "a connection got through the seal"
    finally:
        listener.close()


def _seal_problem(fresh: bool = False) -> Optional[str]:
    """``None`` when the seal holds on this Mac, else the reason code ``no_network_block``.

    A trial that passed is remembered for as long as the app runs, which keeps
    ``status()`` cheap. ``fresh`` tries again regardless; ``transcribe_file``
    uses it, so every transcription follows a trial made moments before. A
    trial that failed is repeated once SEAL_RETRY_SEC have gone by, in case
    whatever was wrong has passed.
    """
    with _SEAL_LOCK:
        tried_at = _SEAL_STATE["at"]
        failed_a_while_ago = bool(_SEAL_STATE["detail"]) and tried_at is not None and \
            time.monotonic() - tried_at >= SEAL_RETRY_SEC
        if fresh or tried_at is None or failed_a_while_ago:
            _SEAL_STATE["detail"] = _check_seal()
            _SEAL_STATE["at"] = time.monotonic()
        return "no_network_block" if _SEAL_STATE["detail"] else None


def _note_seal_failure(detail: str) -> None:
    """Record that the seal turned out not to hold (the worker said so), so ``status()`` says it too."""
    with _SEAL_LOCK:
        _SEAL_STATE["detail"] = detail or "the seal did not hold"
        _SEAL_STATE["at"] = time.monotonic()


# --------------------------------------------------------------------------- status

_APPLE_PYTHON_VERSIONS: Dict[str, Optional[Tuple[int, ...]]] = {}   # what each of APPLE_PYTHONS said it is, asked once


def _version_of(python: str) -> Optional[Tuple[int, ...]]:
    """``(major, minor)`` as the Python at ``python`` reports it, or None when it does not run or says something else."""
    code, out, _ = _run([python, "-I", "-S", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                        env=_base_env(), timeout=30)
    try:
        major, minor = out.strip().split(".")
        return (int(major), int(minor)) if code == 0 else None
    except ValueError:
        return None


def _environment_python() -> Optional[str]:
    """The Python the private environment is made with: one of the version the packages were proven with.

    That is the Python that runs the app, when it is that version: the zip started with its launcher. Inside
    the desktop app it never is, so there the Python of Apple's developer tools is looked for at the places
    those tools put it (APPLE_PYTHONS), and taken when it is that version. Each is asked for its version once
    while the app runs; whether it is there is looked at every time, so tools installed while the app is open
    are found without starting it again. None when there is no such Python.
    """
    if tuple(sys.version_info[:2]) == PYTHON_VERSION:
        return sys.executable
    if not buildinfo.desktop():
        return None                     # from the zip or the source the answer stays "start it with the launcher"
    for python in APPLE_PYTHONS:
        if os.path.exists(python):
            if python not in _APPLE_PYTHON_VERSIONS:
                _APPLE_PYTHON_VERSIONS[python] = _version_of(python)
            if _APPLE_PYTHON_VERSIONS[python] == PYTHON_VERSION:
                return python
    return None


def _platform_problem() -> Optional[str]:
    if sys.platform != "darwin":
        return "not_macos"
    if platform.machine() != "arm64":
        return "not_apple_silicon"
    try:
        version = tuple(int(p) for p in platform.mac_ver()[0].split(".")[:2])
    except ValueError:
        version = ()
    if version < MIN_MACOS:
        return "macos_too_old"
    inside_desktop_app = bool(buildinfo.desktop())
    if _environment_python() is None:
        if not inside_desktop_app:
            return "python_version"
        # The advice to start the app with its launcher means nothing to someone who installed the desktop app.
        # What is missing there is Apple's tools, or, when they are there, a Python of the proven version in them.
        return "desktop_app" if any(os.path.exists(python) for python in APPLE_PYTHONS) else "desktop_needs_tools"
    if inside_desktop_app and ROOT in HOME.parents:
        return "desktop_app"            # no data folder was named, and nothing is ever set up inside the app
    return None


def _audio_tool() -> Optional[str]:
    # Only the converter that ships with macOS. An ffmpeg on the person's PATH used to count; it no longer does.
    return "afconvert" if os.path.exists(AFCONVERT) else None


def _read_marker() -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(_marker().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _model_ready() -> bool:
    try:
        return all((_model_dir() / name).stat().st_size == size for name, size, _ in MODEL_FILES)
    except OSError:
        return False


def status() -> Dict[str, Any]:
    """What on-device transcription can do right now. Cheap: a few file checks, nothing is loaded.

    The one thing that is not a file check is the seal: the first call on a
    Mac that could otherwise transcribe tries it out (a few very short-lived
    programs, see ``_check_seal``) and the answer is remembered while the app
    runs, so later calls start nothing.

    - ``supported``: this computer can run it (Apple-chip Mac, recent macOS, the audio tool, and
      macOS enforcing the seal).
    - ``reason``: why not, as a code (``not_macos``, ``not_apple_silicon``, ``macos_too_old``,
      ``python_version``, ``desktop_app``, ``desktop_needs_tools``, ``no_audio_tool``, ``no_network_block``),
      else ``None``.
      ``reason_en`` / ``reason_zh`` say it plainly.
    - ``network_block``: ``"enforced"`` when macOS was seen, on this Mac and in this run of the app,
      refusing the network (and other personal files) to a program started the way transcription
      is started; ``"unavailable"`` when that could not be set up, in which case ``supported`` is
      false, ``reason`` is ``no_network_block`` and nothing will be transcribed; ``None`` when it
      was not tried because this computer cannot transcribe for another reason.
    - ``installed``: setup finished here with the current package list, including a test
      transcription. Written only at the very end of a successful ``install()``.
    - ``model_ready``: the model files are on disk at their expected sizes.
    - ``model``, ``audio_tool`` (``afconvert`` or ``None``), ``location`` (the folder).
    """
    reason = _platform_problem()
    tool = _audio_tool()
    if reason is None and tool is None:
        reason = "no_audio_tool"
    block: Optional[str] = None
    if reason is None:
        reason = _seal_problem()
        block = "unavailable" if reason else "enforced"
    marker = _read_marker()
    installed = bool(marker and marker.get("packages") == list(PACKAGES) and marker.get("revision") == MODEL_REVISION
                     and _venv_python().exists())
    return {
        "supported": reason is None,
        "reason": reason,
        "reason_en": _TEXT[reason][0] if reason else None,
        "reason_zh": _TEXT[reason][1] if reason else None,
        "network_block": block,
        "installed": installed,
        "model_ready": _model_ready(),
        "model": MODEL,
        "audio_tool": tool,
        "location": str(HOME),
    }


# --------------------------------------------------------------------------- install

def _open_url(url: str, headers: Dict[str, str]):
    """Open a download. Returns an object with ``.status`` and ``.read(n)``. No login is ever sent."""
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fetch_file(url: str, dest: Path, size: int, on_bytes: Callable[[int], None]) -> None:
    """Download ``url`` to ``dest``, continuing a partial download left by an earlier attempt."""
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have > size:
        part.unlink()
        have = 0
    if have < size:
        headers = {"User-Agent": "learn-my-voice-setup"}
        if have:
            headers["Range"] = "bytes=%d-" % have
        response = _open_url(url, headers)
        try:
            if have and getattr(response, "status", 200) != 206:
                have = 0                              # server sent the whole file again
            on_bytes(have)
            with open(str(part), "ab" if have else "wb") as handle:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    handle.write(block)
                    have += len(block)
                    on_bytes(have)
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
    if part.stat().st_size != size:
        raise IOError("download ended early: %d of %d bytes" % (part.stat().st_size, size))


DOWNLOAD_ATTEMPTS = 4                   # a dropped connection resumes where it stopped
RETRY_WAIT_SEC = 2.0


def _download_model(on_fraction: Callable[[float], None]) -> None:
    """Fetch the pinned model files that are not already here and intact; check each by SHA-256."""
    folder = _model_dir()
    folder.mkdir(parents=True, exist_ok=True)
    total = float(sum(size for _, size, _ in MODEL_FILES)) or 1.0
    before = 0
    for name, size, sha256 in MODEL_FILES:
        dest = folder / name
        if not (dest.is_file() and dest.stat().st_size == size and _sha256(dest) == sha256):
            if dest.exists():
                dest.unlink()
            url = MODEL_URL % (MODEL, MODEL_REVISION, name)
            for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
                try:
                    _fetch_file(url, dest, size, lambda n, b=before: on_fraction((b + n) / total))
                    break
                except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
                    if attempt == DOWNLOAD_ATTEMPTS:
                        raise _error("failed", "no_internet", detail="%s: %s" % (type(exc).__name__, exc))
                    time.sleep(RETRY_WAIT_SEC)
            part = dest.with_name(name + ".part")
            if _sha256(part) != sha256:
                part.unlink()
                raise _error("failed", "download_damaged", detail="sha256 mismatch for " + name)
            os.replace(str(part), str(dest))
        before += size
        on_fraction(before / total)


def _pip(*args: str) -> List[Any]:
    return [_venv_python(), "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", "--no-input",
            "--no-cache-dir"] + list(args)


def _pip_install(*args: str) -> None:
    code, out, err = _run(_pip("install", "--index-url", INDEX_URL, "--only-binary", ":all:", *args),
                          env=_install_env(), timeout=1800)
    if code != 0:
        output = out + "\n" + err
        offline = any(hint in output for hint in _NETWORK_HINTS)
        raise _error("failed", "no_internet" if offline else "packages", detail=output)


def _make_environment() -> None:
    python = _venv_python()
    if python.exists() and _run([python, "-I", "-c", "import sys"], env=_base_env(), timeout=120)[0] == 0:
        return
    shutil.rmtree(str(HOME / "venv"), ignore_errors=True)
    # Made with the Python the packages were proven with (``_environment_python``), which inside the desktop app
    # is Apple's and not the one that runs the app. ``status`` has said there is one before setup gets here.
    base = _environment_python() or sys.executable
    code, out, err = _run([base, "-I", "-m", "venv", HOME / "venv"], env=_base_env(), timeout=600)
    if code != 0 or not python.exists():
        raise _error("failed", "environment", detail=out + "\n" + err, where=str(HOME))


def _self_check() -> Dict[str, Any]:
    """Say a sentence with the Mac's own voice into a file and transcribe it, end to end.

    Every program involved is started under the seal, as in a real transcription.
    """
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="learn-my-voice-") as tmp:
        wav = Path(tmp) / "audio.wav"
        spoken = Path(tmp) / "spoken.aiff"
        used_speech = False
        if os.path.exists(SAY) and _run(_sealed([SAY, "-o", spoken, SELF_CHECK_TEXT]), env=_base_env(), timeout=120)[0] == 0:
            try:
                _extract_audio(spoken, wav, Path(tmp))
                used_speech = True
            except TranscribeError:
                pass
        if not used_speech:                           # no system voice: two seconds of silence still loads the model
            with wave.open(str(wav), "wb") as silent:
                silent.setnchannels(1)
                silent.setsampwidth(2)
                silent.setframerate(16000)
                silent.writeframes(b"\x00\x00" * 32000)
        try:
            payload = _run_worker(wav, WORKER_TIMEOUT_MIN)
        except TranscribeError as exc:
            if exc.reason == "no_network_block":      # not a failed check: this Mac cannot be made safe to transcribe on
                raise
            raise _error("failed", "self_check", detail=exc.detail)
    return {"used_speech": used_speech, "segments": len(payload["segments"]),
            "text": " ".join(s["text"] for s in payload["segments"])[:200], "seconds": round(time.time() - started, 1)}


def install(progress: Optional[Callable[[str, Optional[float]], None]] = None) -> None:
    """Set up on-device transcription under ``local_tools/whisper/`` (``_install_folder``). Safe to call again.

    ``progress(stage, fraction)`` is called with stage ``environment``,
    ``packages``, ``model``, ``check`` then ``done``; ``fraction`` is overall
    progress from 0 to 1 (it follows the model download byte by byte, which is
    almost all of the wait).

    A second call does nothing when setup is complete, and otherwise finishes
    what is missing: an interrupted model download continues where it stopped.
    ``status()["installed"]`` only becomes true after a real test transcription
    on this Mac has worked. Raises ``TranscribeError``.
    """
    notify = progress or (lambda stage, fraction: None)
    state = status()
    if not state["supported"]:
        raise _error("unsupported", state["reason"])
    if state["installed"] and state["model_ready"]:
        notify("done", 1.0)
        return
    if not _INSTALL_LOCK.acquire(False):
        raise _error("failed", "busy")
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        if not state["model_ready"]:
            free = shutil.disk_usage(str(HOME)).free
            if free < FREE_SPACE_NEEDED:
                raise _error("failed", "disk_full", need=round(FREE_SPACE_NEEDED / 1e9, 1), free=round(free / 1e9, 1))
        try:
            _marker().unlink()                        # "installed" must only ever describe a setup that finished
        except OSError:
            pass
        notify("environment", 0.0)
        _make_environment()
        notify("packages", 0.03)
        _pip_install(PIP_PIN)                         # upgrades pip inside this environment only
        requirements = HOME / "requirements.txt"
        requirements.write_text("\n".join(PACKAGES) + "\n", encoding="utf-8")
        _pip_install("--no-deps", "-r", str(requirements))
        notify("model", 0.12)
        _download_model(lambda fraction: notify("model", 0.12 + 0.8 * fraction))
        notify("check", 0.93)
        check = _self_check()
        marker = {
            "model": MODEL, "revision": MODEL_REVISION, "packages": list(PACKAGES), "pip": PIP_PIN,
            # The Python the environment was made with: the one that runs the app, or inside the desktop app Apple's.
            "python": platform.python_version() if _environment_python() in (None, sys.executable) else "%d.%d" % PYTHON_VERSION,
            "python_from": _environment_python() or sys.executable,
            "macos": platform.mac_ver()[0], "self_check": check,
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        pending = _marker().with_name("installed.json.tmp")
        pending.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        os.replace(str(pending), str(_marker()))
        notify("done", 1.0)
    finally:
        _INSTALL_LOCK.release()


def uninstall() -> None:
    """Remove what ``install()`` put on this computer, and nothing else.

    Only the folders and files setup creates are deleted; anything else a
    person placed in ``local_tools/whisper/`` is left where it is.
    """
    if not _INSTALL_LOCK.acquire(False):
        raise _error("failed", "busy")
    try:
        with _RUN_LOCK:                               # let a transcription that is running finish first
            for name in ("installed.json", "installed.json.tmp", "requirements.txt"):
                try:
                    (HOME / name).unlink()
                except OSError:
                    pass
            for name in ("venv", "model", "hf", "numba-cache"):
                target = HOME / name
                if target.is_symlink():
                    target.unlink()
                else:
                    shutil.rmtree(str(target), ignore_errors=True)
            try:                                      # tidy up the folders only if nothing else is in them
                HOME.rmdir()
                if HOME.parent.name == "local_tools":
                    HOME.parent.rmdir()
            except OSError:
                pass
    finally:
        _INSTALL_LOCK.release()


# --------------------------------------------------------------------------- transcribe

MIN_AUDIO_SEC = 0.1                     # less extracted sound than this is "no sound"


def _wav_seconds(path: Path) -> float:
    """Seconds of sound in a WAV file, from its header; 0.0 if it is not a readable WAV.

    Read by hand rather than with the ``wave`` module because afconvert writes
    the "extensible" header for sound taken out of a video, which Python 3.9's
    ``wave`` refuses. The byte rate and data size sit in the same place in both.
    """
    try:
        with open(str(path), "rb") as handle:
            if handle.read(12)[8:] != b"WAVE":
                return 0.0
            byte_rate = 0
            while True:
                header = handle.read(8)
                if len(header) < 8:
                    return 0.0
                size = int.from_bytes(header[4:], "little")
                if header[:4] == b"fmt ":
                    byte_rate = int.from_bytes(handle.read(size + (size & 1))[8:12], "little")
                elif header[:4] == b"data":
                    return size / float(byte_rate) if byte_rate else 0.0
                else:
                    handle.seek(size + (size & 1), 1)
    except OSError:
        return 0.0


def _afconvert_args(source: Any, wav: Path) -> List[Any]:
    # 16 kHz, one channel, 16-bit little-endian WAV. --mix folds stereo down to mono;
    # without it afconvert keeps only the first channel and drops the rest.
    return [AFCONVERT, "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", "--mix", source, wav]


def _extract_audio(source: Path, wav: Path, scratch: Path) -> None:
    """Write the sound of ``source`` to ``wav`` with afconvert, which ships with macOS.

    afconvert is the only program that ever opens the creator's file, and it
    is started under the audio seal (``_sealed_audio``): macOS denies it the
    network, and denies it every file in the home folder, on other disks and
    in temporary folders except ``source`` itself and the ``scratch`` folder
    it writes into.

    In testing on macOS 26.7 it read audio files and the sound track of small
    H.264 + AAC .mp4 / .mov / .m4v videos directly, with the same output under
    the seal as without it; other codecs and recordings straight from a phone
    were not tried.

    There is no second tool. Two used to follow when afconvert could not open
    a file, and both are gone on purpose:

    - avconvert. A video file can say that its sound is stored in a different
      file, and avconvert goes and reads that file. A crafted video got another
      recording on the disk transcribed that way. afconvert read only the file
      it was given in every case tried, and the seal stops it reading another
      one even if it wanted to. No real file had needed avconvert.
    - ffmpeg from the person's PATH. It is not part of macOS, it is whatever
      program happens to carry that name, and it was started with the network
      open.

    So a file afconvert cannot read is reported as having no readable sound.
    Raises ``TranscribeError("no_audio")``.
    """
    try:
        given, room = _kernel_path(source), _kernel_path(scratch)
    except OSError as exc:
        raise _error("no_audio", "no_audio", detail="could not open: %s" % exc)
    code, _, said = _run(_sealed_audio(given, room, _afconvert_args(given, wav)), env=_base_env(), timeout=EXTRACT_TIMEOUT)
    if code == 0 and _wav_seconds(wav) >= MIN_AUDIO_SEC:
        return
    try:
        wav.unlink()
    except OSError:
        pass
    raise _error("no_audio", "no_audio", detail="afconvert: exit %s %s" % (code, said.strip()[-300:]))


def _run_worker(wav: Path, timeout: float) -> Dict[str, Any]:
    """Run the Whisper worker on a WAV, with the network denied by macOS, and return its checked reply."""
    code, out, err = _run(_sealed([_venv_python(), "-I", WORKER, "--wav", wav, "--model", _model_dir()]),
                          env=_worker_env(), timeout=timeout)
    if code is None:
        raise _error("failed", "timeout", detail=err)
    payload: Any = None
    for line in reversed(out.splitlines()):
        if line.strip():
            try:
                payload = json.loads(line)
            except ValueError:
                payload = None
            break
    if isinstance(payload, dict) and payload.get("refused") == WORKER_REFUSED:
        # The worker looked for itself and found it could reach the network, so it did no work.
        detail = "the worker was not cut off from the network: %s" % payload.get("error")
        _note_seal_failure(detail)
        raise _error("unsupported", "no_network_block", detail=detail)
    if code != 0 or not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        problem = payload.get("error") if isinstance(payload, dict) else None
        raise _error("failed", "worker", detail="exit %s\n%s\n%s" % (code, problem or out[-500:], err))
    segments = []
    for seg in payload["segments"]:
        try:
            start, end, text = float(seg["start"]), float(seg["end"]), str(seg["text"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if text:
            segments.append({"start": start, "end": max(end, start), "text": text})
    payload["segments"] = segments
    return payload


def transcribe_file(path: Any, progress: Optional[Callable[[str, Optional[float]], None]] = None) -> Dict[str, Any]:
    """Transcribe one audio or video file on this computer. No network requests are made.

    That is enforced by macOS, not assumed. Before anything is started the
    seal is tried out on this Mac (``_seal_problem``). The audio tool and the
    Whisper worker are then both started under it, and the worker checks for
    itself that it has no network before it loads anything. If the seal cannot
    be set up, nothing is run at all and the error says why
    (``unsupported`` with reason ``no_network_block``).

    Returns ``{"segments", "language", "duration_sec", "model", "unclear_dropped"}``.

    - ``segments``: ``{"start", "end", "text"}`` in seconds, the shape
      ``studio.timing`` uses, so ``timing.timing_stats(result["segments"])``
      works directly. There is one segment per breath: Whisper's words,
      unchanged, regrouped wherever its word timings show a pause of 0.3 s or
      more. A file with sound but no speech gives an empty list; that is a
      result, not an error.
    - ``language``: what Whisper detected from the first 30 seconds (``zh``, ``en``, ...).
    - ``duration_sec``: length of the sound track.
    - ``unclear_dropped``: how many stretches Whisper itself scored as a failed
      decode and were therefore left out instead of being passed off as the
      creator's words. Usually 0.

    ``progress(stage, fraction)`` is called with ``audio``, ``transcribe`` and
    ``done``; the fraction is ``None`` until done because Whisper does not
    report how far along it is.

    Raises ``TranscribeError``: ``unsupported``, ``not_installed``,
    ``no_audio`` (no sound track, or a file macOS cannot read) or ``failed``.
    """
    notify = progress or (lambda stage, fraction: None)
    state = status()
    if not state["supported"]:
        raise _error("unsupported", state["reason"])
    if not (state["installed"] and state["model_ready"]):
        raise _error("not_installed", "not_installed")
    if _seal_problem(fresh=True):                     # tried again now, not remembered from an earlier look
        raise _error("unsupported", "no_network_block", detail=str(_SEAL_STATE["detail"]))
    source = Path(str(path)).expanduser()
    if not source.is_file():
        raise _error("failed", "file_missing")
    source = source.resolve()
    with _RUN_LOCK:
        with tempfile.TemporaryDirectory(prefix="learn-my-voice-") as tmp:   # the WAV is deleted on the way out
            wav = Path(tmp) / "audio.wav"
            notify("audio", None)
            _extract_audio(source, wav, Path(tmp))
            duration = _wav_seconds(wav)
            notify("transcribe", None)
            payload = _run_worker(wav, max(WORKER_TIMEOUT_MIN, 3 * duration))
    notify("done", 1.0)
    dropped = payload.get("unclear_dropped")
    return {
        "segments": payload["segments"],
        "language": payload.get("language"),
        "duration_sec": round(duration, 3),
        "model": MODEL,
        "unclear_dropped": dropped if isinstance(dropped, int) and not isinstance(dropped, bool) else 0,
    }
