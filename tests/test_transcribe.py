"""Tests for on-device transcription: detection, setup, audio extraction, the seal and the Whisper worker.

No network beyond this computer's own address, and no real Whisper except in RealInstallTest. Package
installs, downloads, macOS tools and the worker are stand-ins, except where a test says it starts a real
process. RealSealTest and CraftedVideoTest start real macOS tools under the real seal and skip where there is
no macOS. RealInstallTest transcribes one short recording with the real install, only reading it, and skips
where the install is absent.
"""
import contextlib
import errno
import hashlib
import io
import json
import math
import os
import shutil
import socket
import struct
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import wave
from pathlib import Path
from unittest import mock

from studio import timing, transcribe, whisper_worker

# Transcription is for a Mac. What it is made of (opening a folder to learn where it really is, the programs it
# starts) has no counterpart on Windows, where the app refuses to transcribe before any of that runs. The tests
# that drive those parts with stand-ins are therefore not run there; the tests of the refusal are.
mac_pipeline = unittest.skipIf(os.name == "nt", "the transcription pipeline is for a Mac and cannot run on Windows")

ROOT = Path(__file__).resolve().parent.parent
REPLY = {
    "segments": [{"start": 0.0, "end": 5.2, "text": "今天去超市买菜啦"},
                 {"start": 5.6, "end": 11.0, "text": "结果人特别多嘛 排队排了半小时"}],     # 8 + 14 units
    "language": "zh", "duration_sec": 12.0,
}
AWKWARD_NAME = "my video; rm -rf ~ $(whoami) 'q' 视频.mp4"


def write_wav(path, seconds, rate=16000, channels=1, tone_on=None):
    """A WAV of silence, or with a 440 Hz tone on one channel (``tone_on`` is the channel index)."""
    frames = []
    for i in range(int(seconds * rate)):
        value = int(12000 * math.sin(2 * math.pi * 440 * i / rate))
        frames.append(struct.pack("<%dh" % channels, *[value if c == tone_on else 0 for c in range(channels)]))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"".join(frames))


class FakeTools:
    """Stands in for every program transcribe starts: python -m venv, pip, say, afconvert, the worker.

    A command started under the seal (``sandbox-exec [-D NAME=VALUE]... -p RULES command``) is recorded as the
    command itself, and what it was sealed with is kept beside it in ``seals``: ``None`` for a command started
    bare, else ``{"rules": RULES, "places": {NAME: VALUE}}``. avconvert and ffmpeg are still recognised, so a
    test can tell if either were ever started again.
    """

    def __init__(self):
        self.calls = []                      # (args, env, timeout), with the seal taken off
        self.seals = []                      # one for each call
        self.fail = set()                    # kinds that exit 1
        self.pip_output = "ERROR: something technical"
        self.readable = lambda source: True  # which inputs afconvert can read
        self.wav_seconds = 12.0
        self.reply = REPLY
        self.worker_exit = 0
        self.worker_out = None

    @staticmethod
    def unseal(args):
        if os.path.basename(args[0]) != "sandbox-exec":
            return None, args
        places, at = {}, 1
        while args[at] == "-D":
            name, _, value = args[at + 1].partition("=")
            places[name] = value
            at += 2
        assert args[at] == "-p", args
        return {"rules": args[at + 1], "places": places}, args[at + 2:]

    @staticmethod
    def kind(args):
        name = os.path.basename(args[0])
        if name in ("afconvert", "avconvert", "say", "ffmpeg"):
            return name
        if "venv" in args:
            return "venv"
        if "pip" in args:
            return "pip"
        return "worker" if any(a.endswith("whisper_worker.py") for a in args) else "python"

    def kinds(self):
        return [self.kind(c[0]) for c in self.calls]

    def of(self, kind):
        return [c for c in self.calls if self.kind(c[0]) == kind]

    def __call__(self, args, env=None, timeout=None):
        seal, args = self.unseal([str(a) for a in args])
        kind = self.kind(args)
        self.calls.append((args, env, timeout))
        self.seals.append(seal)
        if kind in self.fail:
            return 1, "", self.pip_output if kind == "pip" else kind + ": could not do it"
        if kind == "venv":
            python = Path(args[-1]) / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("")
        elif kind == "afconvert":
            if not self.readable(args[-2]):
                return 1, "", "afconvert: this file type is not supported"
            write_wav(args[-1], self.wav_seconds)
        elif kind == "ffmpeg":
            write_wav(args[-1], self.wav_seconds)
        elif kind == "avconvert":
            Path(args[args.index("--output") + 1]).write_bytes(b"m4a")
        elif kind == "say":
            Path(args[args.index("-o") + 1]).write_bytes(b"aiff")
        elif kind == "worker":
            out = self.worker_out if self.worker_out is not None else "library chatter\n" + json.dumps(self.reply) + "\n"
            return self.worker_exit, out, "Traceback: something technical"
        return 0, "", ""


class Reply(io.BytesIO):
    status = 200


class FakeHub:
    """Stands in for the model host: serves the stand-in model files and honours Range."""

    def __init__(self, files):
        self.files = files
        self.requests = []                   # (url, headers)
        self.offline = False
        self.corrupt = False
        self.drop_after = None               # cut the next response short after this many bytes

    def __call__(self, url, headers):
        self.requests.append((url, dict(headers)))
        if self.offline:
            raise urllib.error.URLError("nodename nor servname provided")
        body = self.files[url.rsplit("/", 1)[-1]]
        if self.corrupt:
            body = b"x" * len(body)
        reply = Reply()
        if "Range" in headers:
            body = body[int(headers["Range"][len("bytes="):-1]):]
            reply.status = 206
        if self.drop_after is not None and len(body) > self.drop_after:
            body, self.drop_after = body[:self.drop_after], None
        reply.write(body)
        reply.seek(0)
        return reply


class Sandbox(unittest.TestCase):
    """Points the module at a throwaway folder, a tiny stand-in model and stand-in programs."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.home = self.tmp / "app" / "local_tools" / "whisper"
        self.files = {"config.json": b'{"n_mels": 128}', "weights.safetensors": bytes(range(256)) * 40}
        self.tools = FakeTools()
        self.hub = FakeHub(self.files)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for name in ("afconvert", "say", "sandbox-exec"):
            (bin_dir / name).write_text("")
        self.patch(transcribe, "HOME", self.home)
        self.patch(transcribe, "MODEL_FILES", tuple((n, len(b), hashlib.sha256(b).hexdigest()) for n, b in self.files.items()))
        self.patch(transcribe, "AFCONVERT", str(bin_dir / "afconvert"))
        self.patch(transcribe, "SANDBOX_EXEC", str(bin_dir / "sandbox-exec"))
        self.patch(transcribe, "SAY", str(bin_dir / "say"))
        self.patch(transcribe, "RETRY_WAIT_SEC", 0)
        self.patch(transcribe, "_platform_problem", lambda: None)
        self.patch(transcribe, "_run", self.tools)
        self.patch(transcribe, "_open_url", self.hub)
        self.which = self.patch(transcribe.shutil, "which", mock.Mock(return_value=None))    # no ffmpeg on PATH
        # The seal holds unless a test says otherwise. Trying it for real is SealTrialTest and RealSealTest.
        self.seal = self.patch(transcribe, "_seal_problem", mock.Mock(return_value=None))

    def patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        self.addCleanup(patcher.stop)
        patcher.start()
        return value

    def video(self, name=AWKWARD_NAME):
        path = self.tmp / name
        path.write_bytes(b"not really a video")
        return path

    def failure(self, call, *args):
        with self.assertRaises(transcribe.TranscribeError) as ctx:
            call(*args)
        error = ctx.exception
        self.assertTrue(str(error) and error.message_zh and error.message_zh != str(error))       # both languages
        self.assertNotIn("Traceback", str(error) + error.message_zh)                              # plain words only
        return error


class StatusTest(Sandbox):
    def test_fresh_computer(self):
        self.assertEqual(transcribe.status(), {
            "supported": True, "reason": None, "reason_en": None, "reason_zh": None, "network_block": "enforced",
            "installed": False,
            "model_ready": False, "model": "mlx-community/whisper-large-v3-turbo", "audio_tool": "afconvert",
            "location": str(self.home),
        })
        self.assertEqual(self.tools.calls, [])                                    # status starts no programs
        self.assertFalse((self.tmp / "app").exists())                             # and creates nothing

    def test_real_install_folder_is_inside_the_repository_and_ignored(self):
        mock.patch.stopall()                                                      # look at the real constants
        self.assertEqual(transcribe.HOME, ROOT / "local_tools" / "whisper")
        self.assertIn("local_tools/", (ROOT / ".gitignore").read_text().splitlines())

    def test_computers_that_cannot_run_it(self):
        mock.patch.stopall()
        self.patch(transcribe, "_run", self.tools)
        cases = [
            ("not_macos", mock.patch.object(transcribe.sys, "platform", "linux")),
            ("not_apple_silicon", mock.patch.object(transcribe.platform, "machine", lambda: "x86_64")),
            ("macos_too_old", mock.patch.object(transcribe.platform, "mac_ver", lambda: ("12.7.6", ("", "", ""), "arm64"))),
            ("python_version", mock.patch.object(transcribe, "PYTHON_VERSION", (3, 8))),
        ]
        base = [mock.patch.object(transcribe.sys, "platform", "darwin"),
                mock.patch.object(transcribe.platform, "machine", lambda: "arm64"),
                mock.patch.object(transcribe.platform, "mac_ver", lambda: ("15.1", ("", "", ""), "arm64")),
                mock.patch.object(transcribe, "PYTHON_VERSION", tuple(sys.version_info[:2]))]
        for reason, change in cases:
            with contextlib.ExitStack() as stack:
                for p in base + [change]:
                    stack.enter_context(p)
                state = transcribe.status()
                self.assertEqual((state["supported"], state["reason"]), (False, reason))
                self.assertTrue(state["reason_en"] and state["reason_zh"])
                for call, args in ((transcribe.install, ()), (transcribe.transcribe_file, (__file__,))):
                    error = self.failure(call, *args)
                    self.assertEqual((error.code, error.reason, str(error)), ("unsupported", reason, state["reason_en"]))
        self.assertEqual(self.tools.calls, [])

    def test_audio_tool_detection(self):
        self.assertEqual(transcribe.status()["audio_tool"], "afconvert")
        self.patch(transcribe, "AFCONVERT", str(self.tmp / "missing"))
        state = transcribe.status()
        self.assertEqual((state["supported"], state["reason"], state["audio_tool"]), (False, "no_audio_tool", None))
        self.which.return_value = "/opt/tools/ffmpeg"                             # an ffmpeg on the PATH no longer counts
        state = transcribe.status()
        self.assertEqual((state["supported"], state["reason"], state["audio_tool"]), (False, "no_audio_tool", None))
        self.which.assert_not_called()                                            # and the PATH is not even searched

    def test_the_seal_is_not_tried_on_a_computer_that_cannot_transcribe_anyway(self):
        for problem, change in (("not_macos", ("_platform_problem", lambda: "not_macos")),
                                ("no_audio_tool", ("AFCONVERT", str(self.tmp / "missing")))):
            with mock.patch.object(transcribe, *change):
                state = transcribe.status()
            self.assertEqual((state["supported"], state["reason"], state["network_block"]), (False, problem, None))
        self.seal.assert_not_called()
        self.assertEqual(self.tools.calls, [])

    def test_the_app_side_never_loads_whisper(self):
        """A fresh interpreter with site-packages switched off can import all three modules."""
        code = ("import sys; sys.path.insert(0, %r); import studio.timing, studio.transcribe, studio.whisper_worker; "
                "studio.transcribe.status(); "
                "print(sorted(m for m in sys.modules if m.split('.')[0] in ('numpy', 'mlx', 'mlx_whisper', 'torch')))" % str(ROOT))
        mock.patch.stopall()
        exit_code, out, err = transcribe._run([sys.executable, "-S", "-c", code], timeout=60)
        self.assertEqual((exit_code, out.strip()), (0, "[]"), err)


class InstallTest(Sandbox):
    def install(self):
        seen = []
        transcribe.install(lambda stage, fraction: seen.append((stage, fraction)))
        return seen

    @mac_pipeline
    def test_install_from_nothing_then_again(self):
        with mock.patch.dict(os.environ, {"HF_TOKEN": "secret", "PIP_INDEX_URL": "http://elsewhere.example", "HTTPS_PROXY": "http://proxy:8080"}):
            seen = self.install()
        python = str(self.home / "venv" / "bin" / "python")
        self.assertEqual(self.tools.kinds(), ["venv", "pip", "pip", "say", "afconvert", "worker"])

        venv, pip_self, pip_packages = (c[0] for c in self.tools.calls[:3])
        self.assertEqual(venv, [sys.executable, "-I", "-m", "venv", str(self.home / "venv")])   # made with the Python running the app
        common = [python, "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", "--no-input", "--no-cache-dir",
                  "install", "--index-url", "https://pypi.org/simple", "--only-binary", ":all:"]
        self.assertEqual(pip_self, common + ["pip==26.0.1"])                      # pip upgraded inside the environment only
        self.assertEqual(pip_packages, common + ["--no-deps", "-r", str(self.home / "requirements.txt")])

        pins = (self.home / "requirements.txt").read_text().split()
        self.assertEqual(pins, list(transcribe.PACKAGES))
        self.assertTrue(all(p.count("==") == 1 and not p.startswith("torch") for p in pins))    # exact pins, torch left out
        self.assertTrue({"mlx-whisper==0.4.3", "mlx==0.29.3", "numpy==2.0.2", "numba==0.60.0"} <= set(pins))

        for _, env, _ in self.tools.calls:                                        # nothing secret or stray reaches a child
            self.assertFalse({"HF_TOKEN", "PIP_INDEX_URL"} & set(env))
        pip_env = self.tools.of("pip")[0][1]
        self.assertEqual((pip_env["NETRC"], pip_env["HTTPS_PROXY"]), (os.devnull, "http://proxy:8080"))
        self.assertNotIn("HTTPS_PROXY", self.tools.of("worker")[0][1])            # the worker gets no route out

        base = "https://huggingface.co/mlx-community/whisper-large-v3-turbo/resolve/%s/" % transcribe.MODEL_REVISION
        self.assertEqual(self.hub.requests, [(base + "config.json", {"User-Agent": "learn-my-voice-setup"}),
                                             (base + "weights.safetensors", {"User-Agent": "learn-my-voice-setup"})])   # no login sent
        for name, body in self.files.items():
            self.assertEqual((self.home / "model" / name).read_bytes(), body)

        stages = [s for i, (s, _) in enumerate(seen) if i == 0 or seen[i - 1][0] != s]
        fractions = [f for _, f in seen]
        self.assertEqual(stages, ["environment", "packages", "model", "check", "done"])
        self.assertEqual((fractions, seen[-1]), (sorted(fractions), ("done", 1.0)))

        marker = json.loads((self.home / "installed.json").read_text())
        self.assertEqual((marker["revision"], marker["packages"], marker["self_check"]["used_speech"]),
                         (transcribe.MODEL_REVISION, pins, True))
        state = transcribe.status()
        self.assertEqual((state["installed"], state["model_ready"]), (True, True))
        self.assertEqual(sorted(p.name for p in (self.tmp / "app").iterdir()), ["local_tools"])  # nothing outside the folder
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), ["installed.json", "model", "requirements.txt", "venv"])

        calls, requests = len(self.tools.calls), len(self.hub.requests)
        self.assertEqual(self.install(), [("done", 1.0)])                         # second time: nothing to do
        self.assertEqual((len(self.tools.calls), len(self.hub.requests)), (calls, requests))

    @mac_pipeline
    def test_the_test_transcription_at_the_end_of_setup_is_sealed_too(self):
        self.install()
        sealed = {kind: seal for kind, seal in zip(self.tools.kinds(), self.tools.seals)}
        self.assertEqual((sealed["venv"], sealed["pip"]), (None, None))           # setup itself is what uses the network
        self.assertEqual(sealed["say"], {"rules": transcribe.NETWORK_SEAL, "places": {}})
        self.assertEqual(sealed["worker"], {"rules": transcribe.NETWORK_SEAL, "places": {}})
        self.assertEqual(sealed["afconvert"]["rules"], transcribe.AUDIO_SEAL)

    def test_setup_does_not_start_where_the_seal_cannot_be_set_up(self):
        self.seal.return_value = "no_network_block"
        error = self.failure(transcribe.install)
        self.assertEqual((error.code, error.reason), ("unsupported", "no_network_block"))
        self.assertEqual((self.tools.calls, self.hub.requests), ([], []))         # nothing started, nothing downloaded
        self.assertFalse((self.tmp / "app").exists())

    def test_a_worker_that_finds_the_network_open_during_setup_is_reported_as_that(self):
        self.patch(transcribe, "_SEAL_STATE", {"at": None, "detail": ""})
        self.tools.worker_exit = 3
        self.tools.worker_out = json.dumps({"error": "refusing to transcribe", "refused": whisper_worker.REFUSED}) + "\n"
        error = self.failure(transcribe.install)
        self.assertEqual((error.code, error.reason), ("unsupported", "no_network_block"))     # not "the test transcription failed"
        self.assertFalse(transcribe.status()["installed"])

    def test_failed_package_install_leaves_it_uninstalled(self):
        self.tools.fail = {"pip"}
        error = self.failure(transcribe.install)
        self.assertEqual((error.code, error.reason), ("failed", "packages"))
        self.assertIn("something technical", error.detail)                        # kept for the log, not shown
        self.assertEqual((transcribe.status()["installed"], self.hub.requests), (False, []))
        self.tools.pip_output = "NewConnectionError: Failed to establish a new connection"
        self.assertEqual(self.failure(transcribe.install).reason, "no_internet")

    def test_no_internet_for_the_model(self):
        self.hub.offline = True
        error = self.failure(transcribe.install)
        self.assertEqual((error.code, error.reason), ("failed", "no_internet"))
        self.assertEqual(len(self.hub.requests), transcribe.DOWNLOAD_ATTEMPTS)
        self.assertEqual((transcribe.status()["installed"], transcribe.status()["model_ready"]), (False, False))

    def test_interrupted_download_continues_where_it_stopped(self):
        self.hub.drop_after = 3000                                                # the connection drops part-way
        self.install()
        weights = [h for u, h in self.hub.requests if u.endswith("weights.safetensors")]
        self.assertEqual([h.get("Range") for h in weights], [None, "bytes=3000-"])
        self.assertEqual((self.home / "model" / "weights.safetensors").read_bytes(), self.files["weights.safetensors"])

        transcribe.uninstall()                                                    # and a part left by an earlier run is used
        (self.home / "model").mkdir(parents=True)
        (self.home / "model" / "weights.safetensors.part").write_bytes(self.files["weights.safetensors"][:5000])
        del self.hub.requests[:]
        self.install()
        self.assertEqual(self.hub.requests[-1][1].get("Range"), "bytes=5000-")
        self.assertEqual(sorted(p.name for p in (self.home / "model").iterdir()), ["config.json", "weights.safetensors"])

    def test_damaged_download_is_thrown_away(self):
        self.hub.corrupt = True
        error = self.failure(transcribe.install)
        self.assertEqual((error.code, error.reason), ("failed", "download_damaged"))
        self.assertEqual(list((self.home / "model").iterdir()), [])
        self.hub.corrupt = False
        self.install()                                                            # trying again works
        self.assertTrue(transcribe.status()["installed"])

    def test_not_installed_until_a_test_transcription_works(self):
        self.tools.fail = {"worker"}
        error = self.failure(transcribe.install)
        self.assertEqual((error.code, error.reason), ("failed", "self_check"))
        state = transcribe.status()
        self.assertEqual((state["installed"], state["model_ready"]), (False, True))
        self.assertEqual(self.failure(transcribe.transcribe_file, self.video()).code, "not_installed")
        self.tools.fail = set()
        requests = len(self.hub.requests)
        self.install()
        self.assertEqual((transcribe.status()["installed"], len(self.hub.requests)), (True, requests))   # model not fetched twice

    def test_self_check_without_a_system_voice_uses_silence(self):
        self.tools.fail = {"say"}
        self.install()
        self.assertEqual(self.tools.kinds()[-2:], ["say", "worker"])
        self.assertFalse(json.loads((self.home / "installed.json").read_text())["self_check"]["used_speech"])

    def test_a_changed_package_list_or_missing_model_means_install_again(self):
        self.install()
        (self.home / "model" / "weights.safetensors").unlink()
        self.assertEqual((transcribe.status()["installed"], transcribe.status()["model_ready"]), (True, False))
        self.assertEqual(self.failure(transcribe.transcribe_file, self.video()).code, "not_installed")
        self.install()
        self.patch(transcribe, "PACKAGES", transcribe.PACKAGES + ("extra==1.0",))
        self.assertFalse(transcribe.status()["installed"])

    def test_refuses_when_disk_is_nearly_full_or_setup_is_running(self):
        usage = mock.Mock(return_value=mock.Mock(free=1500 * 1000 ** 2))
        with mock.patch.object(transcribe.shutil, "disk_usage", usage):
            error = self.failure(transcribe.install)
        self.assertEqual((error.code, error.reason), ("failed", "disk_full"))
        self.assertIn("4.0 GB", str(error))
        self.assertIn("1.5 GB", error.message_zh)
        self.assertTrue(transcribe._INSTALL_LOCK.acquire(False))
        try:
            self.assertEqual(self.failure(transcribe.install).reason, "busy")
            self.assertEqual(self.failure(transcribe.uninstall).reason, "busy")
        finally:
            transcribe._INSTALL_LOCK.release()
        self.assertEqual(self.tools.calls, [])

    def test_uninstall_removes_only_what_setup_made(self):
        self.install()
        (self.home / "my notes.txt").write_text("keep me")
        transcribe.uninstall()
        self.assertEqual([p.name for p in self.home.iterdir()], ["my notes.txt"])
        self.assertEqual((transcribe.status()["installed"], transcribe.status()["model_ready"]), (False, False))
        (self.home / "my notes.txt").unlink()
        transcribe.uninstall()                                                    # again: tidies the empty folders, no error
        self.assertEqual(list((self.tmp / "app").iterdir()), [])
        self.assertTrue(self.tmp.exists())


class TranscribeFileTest(Sandbox):
    def setUp(self):
        super().setUp()
        transcribe.install()
        del self.tools.calls[:]
        del self.tools.seals[:]                                                   # kept in step with the calls

    @mac_pipeline
    def test_transcript_with_timings_and_no_route_to_the_network(self):
        seen = []
        source = self.video()
        with mock.patch.dict(os.environ, {"HF_TOKEN": "secret", "HTTPS_PROXY": "http://proxy:8080", "HF_HUB_OFFLINE": "0"}):
            result = transcribe.transcribe_file(str(source), lambda stage, fraction: seen.append((stage, fraction)))
        self.assertEqual(result, {"segments": REPLY["segments"], "language": "zh", "duration_sec": 12.0,
                                  "model": "mlx-community/whisper-large-v3-turbo", "unclear_dropped": 0})
        self.assertEqual(seen, [("audio", None), ("transcribe", None), ("done", 1.0)])
        stats = timing.timing_stats(result["segments"])                           # the shape timing.py uses
        self.assertEqual((stats["units"], stats["spoken_sec"], stats["pauses"], stats["speaking_rate_cpm"]), (22, 11.0, 1, 120))

        (convert, _, _), (worker, env, timeout) = self.tools.calls
        wav = convert[-1]
        self.assertEqual(convert, [transcribe.AFCONVERT, "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", "--mix",
                                   str(source.resolve()), wav])                   # the awkward name is one argument, untouched
        self.assertEqual(worker, [str(self.home / "venv" / "bin" / "python"), "-I", str(transcribe.WORKER),
                                  "--wav", wav, "--model", str(self.home / "model")])
        self.assertEqual(Path(worker[2]).name, "whisper_worker.py")
        self.assertEqual((env["HF_HUB_OFFLINE"], env["TRANSFORMERS_OFFLINE"]), ("1", "1"))      # offline mode, whatever the caller had
        self.assertTrue(env["HF_HOME"].startswith(str(self.home)) and env["NUMBA_CACHE_DIR"].startswith(str(self.home)))
        self.assertFalse({"HF_TOKEN", "HTTPS_PROXY", "https_proxy"} & set(env))
        self.assertGreaterEqual(timeout, 900)
        self.assertFalse(Path(wav).parent.exists())                               # the temporary audio is gone

    @mac_pipeline
    def test_long_audio_gets_a_longer_timeout(self):
        self.tools.wav_seconds = 600.0
        transcribe.transcribe_file(self.video())
        self.assertEqual(self.tools.of("worker")[0][2], 1800.0)

    @mac_pipeline
    def test_avconvert_is_never_used_when_afconvert_cannot_read_the_container(self):
        """It used to be the second step. It follows a crafted video's pointer to a different file, so it is gone."""
        self.tools.readable = lambda source: source.endswith(".m4a")
        (Path(transcribe.AFCONVERT).parent / "avconvert").write_text("")           # it is there, as on every Mac
        source = self.video("clip.mov")
        error = self.failure(transcribe.transcribe_file, source)
        self.assertEqual((error.code, error.reason, self.tools.kinds()), ("no_audio", "no_audio", ["afconvert"]))
        self.assertFalse(hasattr(transcribe, "AVCONVERT"))

    @mac_pipeline
    def test_no_sound_is_reported_and_ffmpeg_is_never_used(self):
        self.tools.readable = lambda source: False
        self.which.return_value = "/opt/tools/ffmpeg"                             # someone who already has ffmpeg
        error = self.failure(transcribe.transcribe_file, self.video())
        self.assertEqual((error.code, self.tools.kinds()), ("no_audio", ["afconvert"]))
        self.assertIn("not supported", error.detail)
        self.which.assert_not_called()                                            # the PATH is not even searched
        scratch = Path(self.tools.calls[0][0][-1]).parent
        self.assertFalse(scratch.exists())                                        # cleaned up after a failure too

    @mac_pipeline
    def test_every_program_is_started_under_the_seal(self):
        source = self.video()
        transcribe.transcribe_file(source)
        self.assertEqual(self.tools.kinds(), ["afconvert", "worker"])
        audio, worker = self.tools.seals
        convert = self.tools.calls[0][0]
        self.assertEqual(worker, {"rules": transcribe.NETWORK_SEAL, "places": {}})
        self.assertEqual(audio["rules"], transcribe.AUDIO_SEAL)
        self.assertEqual(sorted(audio["places"]), ["HOME", "SCRATCH", "SOURCE", "TEMP"])
        self.assertEqual(audio["places"]["SOURCE"], transcribe._kernel_path(source))
        self.assertEqual(audio["places"]["SOURCE"], convert[-2])                   # the one file it may read is the one it is given
        self.assertEqual(audio["places"]["SCRATCH"], os.path.realpath(os.path.dirname(convert[-1])))
        for rules in (transcribe.NETWORK_SEAL, transcribe.AUDIO_SEAL):
            self.assertIn("(deny network*)", rules)
            self.assertNotIn("allow network", rules)
        self.assertTrue(transcribe.AUDIO_SEAL.startswith(transcribe.NETWORK_SEAL))
        self.assertIn('(deny file-read-data (subpath "/Users") (subpath "/Volumes")', transcribe.AUDIO_SEAL)
        self.assertTrue(transcribe.AUDIO_SEAL.endswith('(allow file-read-data (literal (param "SOURCE")) (subpath (param "SCRATCH")))'))
        self.assertNotIn(str(source), transcribe.AUDIO_SEAL)                      # a file name is never pasted into the rules
        self.assertEqual(self.seal.call_args_list[-1], mock.call(fresh=True))     # and the seal was tried just before

    def test_nothing_is_started_when_the_seal_cannot_be_set_up(self):
        self.seal.return_value = "no_network_block"
        state = transcribe.status()
        self.assertEqual((state["supported"], state["reason"], state["network_block"]), (False, "no_network_block", "unavailable"))
        self.assertEqual((state["installed"], state["model_ready"]), (True, True))               # installed, and still refused
        self.assertTrue(state["reason_en"] and state["reason_zh"] and state["reason_en"] != state["reason_zh"])
        error = self.failure(transcribe.transcribe_file, self.video())
        self.assertEqual((error.code, error.reason, str(error), error.message_zh),
                         ("unsupported", "no_network_block", state["reason_en"], state["reason_zh"]))
        self.assertEqual(self.tools.calls, [])                                    # no audio tool, no worker: nothing ran unprotected

    def test_the_seal_is_tried_again_just_before_a_transcription(self):
        self.seal.side_effect = lambda fresh=False: "no_network_block" if fresh else None       # fine when status() looked
        self.assertEqual(transcribe.status()["network_block"], "enforced")
        error = self.failure(transcribe.transcribe_file, self.video())
        self.assertEqual((error.code, error.reason, self.tools.calls), ("unsupported", "no_network_block", []))

    @mac_pipeline
    def test_a_worker_that_finds_the_network_open_is_a_refusal_not_a_transcript(self):
        self.patch(transcribe, "_SEAL_STATE", {"at": None, "detail": ""})
        self.assertEqual(transcribe.WORKER_REFUSED, whisper_worker.REFUSED)
        refusal = {"error": "refusing to transcribe", "refused": whisper_worker.REFUSED}
        for exit_code, reply in ((3, refusal), (0, dict(refusal, segments=REPLY["segments"]))):   # even if it sent words along
            self.tools.worker_exit, self.tools.worker_out = exit_code, json.dumps(reply) + "\n"
            error = self.failure(transcribe.transcribe_file, self.video())
            self.assertEqual((error.code, error.reason), ("unsupported", "no_network_block"))
        self.assertIn("refusing to transcribe", transcribe._SEAL_STATE["detail"])  # remembered, so status() says so too

    def test_empty_audio_counts_as_no_sound(self):
        self.tools.wav_seconds = 0
        self.assertEqual(self.failure(transcribe.transcribe_file, self.video()).code, "no_audio")
        self.assertNotIn("worker", self.tools.kinds())

    def test_missing_file(self):
        error = self.failure(transcribe.transcribe_file, self.tmp / "gone.mp4")
        self.assertEqual((error.code, error.reason, self.tools.calls), ("failed", "file_missing", []))

    @mac_pipeline
    def test_worker_problems_are_reported_in_plain_words(self):
        for exit_code, out, reason in ((1, '{"error": "ValueError: boom"}\n', "worker"), (0, "not json at all\n", "worker"),
                                       (0, '{"language": "zh"}\n', "worker"), (0, "", "worker"), (None, "", "timeout")):
            self.tools.worker_exit, self.tools.worker_out = exit_code, out
            error = self.failure(transcribe.transcribe_file, self.video())
            self.assertEqual((error.code, error.reason), ("failed", reason), out)
        self.tools.worker_exit, self.tools.worker_out = 1, '{"error": "ValueError: boom"}\n'
        self.assertIn("ValueError: boom", self.failure(transcribe.transcribe_file, self.video()).detail)

    @mac_pipeline
    def test_no_speech_is_an_empty_result_and_broken_segments_are_dropped(self):
        self.tools.reply = {"segments": [], "language": "en", "duration_sec": 12.0}
        result = transcribe.transcribe_file(self.video())
        self.assertEqual((result["segments"], result["language"]), ([], "en"))
        self.assertIsNone(timing.timing_stats(result["segments"])["speaking_rate_cpm"])
        self.tools.reply = {"segments": [{"start": 1.0, "text": "没有结束时间"}, {"start": 2.0, "end": 3.0, "text": "  "},
                                         {"start": 4.0, "end": 3.0, "text": " 真的假的 "}, "junk"], "language": "zh", "unclear_dropped": 2}
        result = transcribe.transcribe_file(self.video())
        self.assertEqual((result["segments"], result["unclear_dropped"]), ([{"start": 4.0, "end": 4.0, "text": "真的假的"}], 2))


class RealProcessTest(unittest.TestCase):
    """transcribe._run itself, starting real (tiny) processes."""

    def test_arguments_are_never_interpreted_by_a_shell(self):
        code, out, _ = transcribe._run([sys.executable, "-c", "import sys; sys.stdout.write(sys.argv[1])", AWKWARD_NAME.encode("ascii", "replace").decode()])
        self.assertEqual((code, out), (0, "my video; rm -rf ~ $(whoami) 'q' ??.mp4"))

    def test_timeout_missing_program_and_closed_input(self):
        code, _, err = transcribe._run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
        self.assertEqual((code, "timed out" in err), (None, True))
        self.assertEqual(transcribe._run([str(Path(tempfile.gettempdir()) / "no-such-program")])[0], 127)
        self.assertEqual(transcribe._run([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"])[1].strip(), "''")


@unittest.skipUnless(os.path.exists("/usr/bin/afconvert"), "needs the macOS audio converter")
class RealAudioToolTest(unittest.TestCase):
    """The real afconvert command line, on audio made in the test. Still no network and no Whisper."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.source = self.tmp / "right channel only.wav"
        write_wav(self.source, 1.0, rate=44100, channels=2, tone_on=1)

    def test_makes_16k_mono_and_keeps_a_voice_that_is_on_one_channel(self):
        wav = self.tmp / "out.wav"
        transcribe._extract_audio(self.source, wav, self.tmp)
        with wave.open(str(wav), "rb") as out:
            shape = (out.getnchannels(), out.getsampwidth(), out.getframerate(), out.getnframes())
            peak = max(abs(v) for v in struct.unpack("<%dh" % out.getnframes(), out.readframes(out.getnframes())))
        self.assertEqual(shape, (1, 2, 16000, 16000))
        self.assertGreater(peak, 4000)                                            # dropped channels would leave silence

    def test_sound_that_has_been_through_aac_like_a_real_video_is_still_readable(self):
        """afconvert writes the "extensible" WAV header for such sound; Python 3.9's wave module alone refuses it."""
        m4a = self.tmp / "like a video sound track.m4a"
        self.assertEqual(transcribe._run(["/usr/bin/afconvert", "-f", "m4af", "-d", "aac", self.source, m4a])[0], 0)
        wav = self.tmp / "out.wav"
        transcribe._extract_audio(m4a, wav, self.tmp)
        self.assertAlmostEqual(transcribe._wav_seconds(wav), 1.0, delta=0.15)     # AAC pads the ends a little
        with whisper_worker.open_wav(str(wav)) as out:
            self.assertEqual((out.getnchannels(), out.getsampwidth(), out.getframerate()), (1, 2, 16000))
            peak = max(abs(v) for v in struct.unpack("<%dh" % out.getnframes(), out.readframes(out.getnframes())))
        self.assertGreater(peak, 4000)

    def test_a_file_that_is_not_media_has_no_audio(self):
        note = self.tmp / "notes.mp4"
        note.write_text("这不是视频")
        with self.assertRaises(transcribe.TranscribeError) as ctx:
            transcribe._extract_audio(note, self.tmp / "out.wav", self.tmp)
        self.assertEqual(ctx.exception.code, "no_audio")
        self.assertFalse((self.tmp / "out.wav").exists())

    def test_end_to_end_with_a_stand_in_worker_process(self):
        """Real extraction, real child process, real environment; only Whisper itself is replaced."""
        home = self.tmp / "local_tools" / "whisper"
        (home / "venv" / "bin").mkdir(parents=True)
        os.symlink(sys.executable, str(home / "venv" / "bin" / "python"))
        (home / "model").mkdir()
        (home / "model" / "config.json").write_text("{}")
        (home / "installed.json").write_text(json.dumps({"packages": list(transcribe.PACKAGES), "revision": transcribe.MODEL_REVISION}))
        seen = self.tmp / "seen.json"
        worker = self.tmp / "whisper_worker.py"
        worker.write_text(
            "import json, os, sys, wave\n"
            "w = wave.open(sys.argv[sys.argv.index('--wav') + 1])\n"
            "shape = [w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()]\n"
            "json.dump({'argv': sys.argv[1:], 'env': dict(os.environ), 'shape': shape, 'flags': sys.flags.isolated}, open(%r, 'w'))\n"
            "print('chatter from a library')\n"
            "print(json.dumps({'segments': [{'start': 0.0, 'end': 0.9, 'text': '\\u597d\\u5566'}], 'language': 'zh'}))\n" % str(seen))
        with contextlib.ExitStack() as stack:
            for name, value in (("HOME", home), ("WORKER", worker), ("MODEL_FILES", (("config.json", 2, ""),)),
                                ("_platform_problem", lambda: None)):
                stack.enter_context(mock.patch.object(transcribe, name, value))
            stack.enter_context(mock.patch.dict(os.environ, {"HF_HUB_OFFLINE": "0", "HF_TOKEN": "secret"}))
            result = transcribe.transcribe_file(self.source)
        self.assertEqual(result, {"segments": [{"start": 0.0, "end": 0.9, "text": "好啦"}], "language": "zh",
                                  "duration_sec": 1.0, "model": transcribe.MODEL, "unclear_dropped": 0})
        record = json.loads(seen.read_text())
        self.assertEqual((record["shape"], record["flags"]), ([1, 2, 16000, 16000], 1))
        self.assertEqual((record["env"]["HF_HUB_OFFLINE"], "HF_TOKEN" in record["env"]), ("1", False))
        self.assertFalse(Path(record["argv"][1]).parent.exists())                 # the WAV it read has been deleted


class WorkerTest(unittest.TestCase):
    def test_model_and_settings_are_the_ones_in_the_blueprint(self):
        blueprint = (ROOT / "prompts" / "01_verbatim_transcription_and_visual_ingest.md").read_text(encoding="utf-8")
        section = blueprint.split("## 1A.")[1].split("## 1B.")[0]
        self.assertIn('"%s"' % transcribe.MODEL, section)
        self.assertEqual(whisper_worker.SETTINGS, {"word_timestamps": True, "condition_on_previous_text": False,
                                                   "hallucination_silence_threshold": 1.0, "temperature": 0.0})
        for name, value in whisper_worker.SETTINGS.items():
            self.assertIn("%s=%s" % (name, value), section)

    def test_initial_prompt_keeps_particles_and_assumes_nothing_about_the_video(self):
        prompt = whisper_worker.INITIAL_PROMPT
        for needed in ("逐字", "语气词", "简体中文", "code-switching", "不要把口语改成书面语") + tuple("啦嘛哈喔呐诶呗呀"):
            self.assertIn(needed, prompt)
        for assumed in ("老公", "husband", "好物", "Vlog", "trader", "cream", "面霜"):
            self.assertNotIn(assumed.lower(), prompt.lower())

    # What mlx-whisper really returned on this Mac for 20 s of speech with silences of about 0.9 s, 0.3 s,
    # 3.2 s and 0.7 s in it (measured from the signal): ONE segment from 0.00 to 19.92 holding everything,
    # then a one-character segment it invented at the very end. (start, end, word)
    REAL_WORDS = [
        (0.00, 0.36, "今天"), (0.36, 0.62, "周"), (0.62, 0.86, "末"), (0.86, 1.04, "跟"), (1.04, 1.40, "朋友"), (1.40, 1.74, "去"),
        (1.74, 1.96, "超"), (1.96, 2.24, "市"), (2.24, 2.50, "买"), (2.50, 2.70, "点"), (2.70, 2.92, "东"), (2.92, 3.16, "西"),
        (3.16, 3.32, "啦，"), (3.72, 4.70, "其实"), (4.70, 5.02, "这个"), (5.02, 5.42, "地方"), (5.42, 5.70, "我"), (5.70, 5.90, "来"),
        (5.90, 6.12, "过"), (6.12, 6.42, "好"), (6.42, 6.64, "几"), (6.64, 6.88, "次，"), (7.20, 7.52, "真的"), (7.52, 7.74, "蛮"),
        (7.74, 7.96, "舒"), (7.96, 8.16, "服"), (8.16, 8.28, "的"), (8.28, 8.54, "哈，"), (9.14, 12.06, "说"), (12.06, 12.28, "实"),
        (12.28, 12.52, "话，"), (12.80, 13.02, "我"), (13.02, 13.22, "一"), (13.22, 13.56, "开始"), (13.56, 13.88, "也"),
        (13.88, 14.06, "没"), (14.06, 14.30, "想"), (14.30, 14.54, "到"), (14.54, 14.80, "会"), (14.80, 15.08, "这么"),
        (15.08, 15.38, "好"), (15.38, 15.62, "逛"), (15.62, 15.84, "嘛，"), (16.10, 16.36, "你们"), (16.36, 16.62, "觉得"),
        (16.62, 16.86, "呢，"), (17.52, 18.06, "下次"), (18.06, 18.34, "再"), (18.34, 18.66, "跟你"), (18.66, 18.84, "们"),
        (18.84, 19.06, "讲"), (19.06, 19.34, "讲"), (19.34, 19.56, "别"), (19.56, 19.72, "的"), (19.72, 19.92, "呀，"),
    ]

    @staticmethod
    def raw(words, logprob=-0.07):
        return {"start": words[0][0], "end": words[-1][1], "avg_logprob": logprob, "text": "".join(w for _, _, w in words),
                "words": [{"start": s, "end": e, "word": w, "probability": 0.99} for s, e, w in words]}

    def test_segments_are_rebuilt_per_breath_from_real_whisper_output(self):
        raw = [self.raw(self.REAL_WORDS), self.raw([(19.92, 20.02, "好")], logprob=-1.60)]
        as_whisper_gave_it = timing.timing_stats([{"start": s["start"], "end": s["end"], "text": s["text"]} for s in raw])
        self.assertEqual((as_whisper_gave_it["pauses"], as_whisper_gave_it["speaking_rate_cpm"]), (0, 204))   # silence counted as speech

        segments, dropped = whisper_worker.breath_groups(raw)
        self.assertEqual(segments, [
            {"start": 0.0, "end": 3.32, "text": "今天周末跟朋友去超市买点东西啦，"},
            {"start": 4.22, "end": 6.88, "text": "其实这个地方我来过好几次，"},           # "其实" came back 0.98 s long: silence charged to it
            {"start": 7.2, "end": 8.54, "text": "真的蛮舒服的哈，"},
            {"start": 11.58, "end": 16.86, "text": "说实话，我一开始也没想到会这么好逛嘛，你们觉得呢，"},   # "说" came back 2.92 s long
            {"start": 17.58, "end": 19.92, "text": "下次再跟你们讲讲别的呀，"},
        ])
        self.assertEqual(dropped, 1)                                              # the invented "好" is not passed off as speech
        self.assertEqual("".join(s["text"] for s in segments), raw[0]["text"])    # not one character changed
        self.assertEqual(timing.timing_stats(segments), {
            "cues": 5, "units": 67, "spoken_sec": 16.88, "span_sec": 19.92, "pauses": 4,
            "speaking_rate_cpm": 238,                                             # the signal itself says 16.78 s of speech: 240
        })

    def test_breath_groups_edge_cases(self):
        english = self.raw([(0.0, 0.3, " This"), (0.3, 0.5, " is"), (0.5, 0.6, " a"), (0.6, 1.0, " test.")])
        nearby = self.raw([(1.2, 1.5, " Really"), (1.5, 1.8, " 好用")])           # 0.2 s after the last word: same breath
        later = self.raw([(4.0, 4.4, "真的假的")])
        untimed = {"start": 6.0, "end": 7.5, "text": " 没有逐字时间的一段 ", "words": []}
        blank = {"start": 8.0, "end": 9.0, "text": "   ", "words": [{"start": 8.0, "end": 9.0, "word": " "}]}
        backwards = {"start": 12.0, "end": 11.0, "text": "倒着的"}
        segments, dropped = whisper_worker.breath_groups([english, nearby, later, untimed, blank, backwards])
        self.assertEqual(segments, [
            {"start": 0.0, "end": 1.8, "text": "This is a test. Really 好用"},
            {"start": 4.0, "end": 4.4, "text": "真的假的"},
            {"start": 6.0, "end": 7.5, "text": "没有逐字时间的一段"},              # kept whole, never trimmed
            {"start": 12.0, "end": 12.0, "text": "倒着的"},
        ])
        self.assertEqual(dropped, 0)
        self.assertEqual(whisper_worker.breath_groups(None), ([], 0))
        self.assertEqual(whisper_worker.breath_groups([self.raw([(0.0, 0.2, "嗯")], logprob=-1.01)]), ([], 1))

    def test_reads_the_extensible_wav_header_macos_writes_for_video_sound(self):
        def wav_bytes(tag, sub_format):
            samples = struct.pack("<4h", 100, -200, 300, -400)
            fmt = struct.pack("<HHIIHHHHI", tag, 1, 16000, 32000, 2, 16, 22, 16, 4) + sub_format + bytes.fromhex("000000001000800000aa00389b71")
            body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"FLLR" + struct.pack("<I", 12) + bytes(12)
            body += b"data" + struct.pack("<I", len(samples)) + samples
            return b"RIFF" + struct.pack("<I", len(body)) + body

        with tempfile.TemporaryDirectory() as tmp:
            pcm, floats = Path(tmp) / "pcm.wav", Path(tmp) / "float.wav"
            pcm.write_bytes(wav_bytes(0xFFFE, b"\x01\x00"))
            floats.write_bytes(wav_bytes(0xFFFE, b"\x03\x00"))
            with whisper_worker.open_wav(str(pcm)) as source:
                self.assertEqual((source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getnframes()), (1, 2, 16000, 4))
                self.assertEqual(struct.unpack("<4h", source.readframes(4)), (100, -200, 300, -400))
            self.assertEqual(pcm.read_bytes(), wav_bytes(0xFFFE, b"\x01\x00"))     # the file on disk is not rewritten
            self.assertEqual(transcribe._wav_seconds(pcm), 4 / 16000.0)
            with self.assertRaises(wave.Error):                                   # anything that is not plain PCM is still refused
                whisper_worker.open_wav(str(floats))
            (Path(tmp) / "notes.wav").write_text("not audio")
            self.assertEqual((transcribe._wav_seconds(Path(tmp) / "notes.wav"), transcribe._wav_seconds(Path(tmp) / "gone.wav")), (0.0, 0.0))

    def listener(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(5)
        server.settimeout(0.2)
        self.addCleanup(server.close)
        return server, server.getsockname()

    def accepted(self, server):
        try:
            server.accept()[0].close()
            return True
        except socket.timeout:
            return False

    def test_network_block_stops_connections_and_can_be_undone(self):
        server, address = self.listener()
        socket.create_connection(address).close()
        self.assertTrue(self.accepted(server))
        tcp, udp = socket.socket(), socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(tcp.close)
        self.addCleanup(udp.close)
        restore = whisper_worker.block_network()
        try:
            attempts = [lambda: socket.create_connection(address), lambda: tcp.connect(address),
                        lambda: tcp.connect_ex(address), lambda: socket.getaddrinfo("huggingface.co", 443),
                        lambda: udp.sendto(b"x", address),
                        lambda: urllib.request.urlopen("http://%s:%d/" % address, timeout=2)]
            for attempt in attempts:
                with self.assertRaises(OSError) as ctx:
                    attempt()
                self.assertIn("switched off", str(ctx.exception))
        finally:
            restore()
        self.assertFalse(self.accepted(server))                                   # nothing reached the listener
        socket.create_connection(address).close()                                 # and the block is fully undone
        self.assertTrue(self.accepted(server))

    def run_main(self, engine):
        real_block = whisper_worker.block_network
        undo = []

        def tracked_block():
            undo.append(real_block())
            return undo[-1]

        out, err = io.StringIO(), io.StringIO()
        try:
            # The test process has the network, so the worker's own check would refuse here; that check has its own tests.
            with mock.patch.object(whisper_worker, "network_denied_by_system", lambda: True), \
                    mock.patch.object(whisper_worker, "block_network", tracked_block), contextlib.redirect_stderr(err):
                code = whisper_worker.main(["--wav", "audio.wav", "--model", "model-dir"], engine=engine, out=out)
        finally:
            for restore in undo:
                restore()
        self.assertEqual(len(undo), 1)                                            # the worker always blocks, exactly once
        return code, out.getvalue(), err.getvalue()

    def test_worker_prints_one_json_result_and_has_no_network_while_working(self):
        server, address = self.listener()
        tried = []

        def engine(wav, model):
            print("chatter from a library")
            try:
                socket.create_connection(address)
            except OSError as exc:
                tried.append(str(exc))
            return {"segments": [{"start": 0.0, "end": 1.5, "text": " 好啦 "}], "language": "zh", "text": "好啦"}, 2.0

        code, out, err = self.run_main(engine)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.strip().splitlines()[-1]),
                         {"segments": [{"start": 0.0, "end": 1.5, "text": "好啦"}], "language": "zh", "duration_sec": 2.0,
                          "unclear_dropped": 0})
        self.assertTrue(out.isascii())                                            # safe for any terminal encoding
        self.assertEqual((out.count("chatter"), err.count("chatter")), (0, 1))    # library output kept out of the result
        self.assertEqual((len(tried), self.accepted(server)), (1, False))

    def test_worker_reports_a_failure_as_json(self):
        def engine(wav, model):
            raise ValueError("expected 16 kHz mono 16-bit audio")

        code, out, _ = self.run_main(engine)
        self.assertEqual((code, json.loads(out.strip())), (1, {"error": "ValueError: expected 16 kHz mono 16-bit audio"}))

    @unittest.skipUnless(os.path.exists("/usr/bin/sandbox-exec"), "needs the macOS program that applies the seal")
    def test_real_worker_process_fails_cleanly_when_nothing_is_installed(self):
        """Started under the seal, as the app starts it: outside the seal it now refuses before it gets this far."""
        with tempfile.TemporaryDirectory() as tmp:
            write_wav(Path(tmp) / "audio.wav", 0.5)
            command = transcribe._sealed([sys.executable, "-I", transcribe.WORKER, "--wav", Path(tmp) / "audio.wav", "--model", tmp])
            code, out, _ = transcribe._run(command, timeout=120)
        self.assertEqual(code, 1)
        reply = json.loads(out.strip().splitlines()[-1])
        self.assertIn("error", reply)
        self.assertNotIn("refused", reply)                                        # it got past the check and failed on Whisper

    def test_real_worker_process_refuses_when_started_outside_the_seal(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_wav(Path(tmp) / "audio.wav", 0.5)
            code, out, _ = transcribe._run([sys.executable, "-I", transcribe.WORKER, "--wav", Path(tmp) / "audio.wav", "--model", tmp], timeout=120)
        reply = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((code, reply["refused"], "segments" in reply), (3, "network_not_denied", False))
        self.assertEqual(reply["refused"], whisper_worker.REFUSED)

    def test_worker_does_no_work_unless_the_system_denies_it_the_network(self):
        done = []
        out = io.StringIO()
        with mock.patch.object(whisper_worker, "network_denied_by_system", lambda: False), \
                mock.patch.object(whisper_worker, "block_network", lambda: done.append("switched its own network off")):
            code = whisper_worker.main(["--wav", "audio.wav", "--model", "model-dir"],
                                       engine=lambda wav, model: done.append("transcribed"), out=out)
        self.assertEqual((code, done), (3, []))                                   # refused before anything else happened
        reply = json.loads(out.getvalue().strip())
        self.assertEqual((reply["refused"], sorted(reply)), ("network_not_denied", ["error", "refused"]))
        self.assertIn("refusing to transcribe", reply["error"])

    def test_the_check_says_no_in_a_process_that_has_the_network(self):
        """This test run opens listeners and connects to them, so it is not sealed and the answer must be no."""
        self.assertIs(whisper_worker.network_denied_by_system(), False)

    def test_only_not_permitted_on_both_attempts_counts_as_denied(self):
        def system(outcomes):
            outcomes = list(outcomes)

            class Sock:
                def __init__(self, family, kind):
                    pass

                def attempt(self, address):
                    number = outcomes.pop(0)
                    if number:
                        raise OSError(number, os.strerror(number))

                connect = bind = attempt

                def close(self):
                    pass

            return mock.Mock(socket=Sock, AF_INET=2, SOCK_DGRAM=2, SOCK_STREAM=1)

        cases = [((errno.EPERM, errno.EPERM), True),                              # what macOS answers under the seal
                 ((0, 0), False), ((0, errno.EPERM), False), ((errno.EPERM, 0), False),
                 ((errno.ECONNREFUSED, errno.EPERM), False), ((errno.EPERM, errno.EADDRINUSE), False),
                 ((errno.EACCES, errno.EACCES), False)]
        for outcomes, denied in cases:
            with mock.patch.object(whisper_worker, "_socket", system(outcomes)):
                self.assertIs(whisper_worker.network_denied_by_system(), denied, outcomes)


class Ports:
    """A TCP port and a UDP port on this computer's own address, and a count of what arrived at each."""

    def __init__(self, test):
        self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for sock in (self.tcp, self.udp):
            test.addCleanup(sock.close)
            sock.bind(("127.0.0.1", 0))
            sock.setblocking(False)
        self.tcp.listen(16)
        self.tcp_port, self.udp_port = self.tcp.getsockname()[1], self.udp.getsockname()[1]

    def arrived(self):
        """(connections, datagrams) that have arrived since the last look."""
        time.sleep(0.2)
        counts = []
        for take in (lambda: self.tcp.accept()[0].close(), lambda: self.udp.recvfrom(200)):
            count = 0
            while True:
                try:
                    take()
                except (BlockingIOError, InterruptedError):
                    break
                count += 1
            counts.append(count)
        return tuple(counts)


def fresh_seal_memory():
    """Patches the module's memory of the last trial, so one test's answer never reaches another."""
    return mock.patch.object(transcribe, "_SEAL_STATE", {"at": None, "detail": ""})


class SealMemoryTest(unittest.TestCase):
    """When the seal is tried, and when the last answer is used again (transcribe._seal_problem)."""

    def setUp(self):
        self.answer, self.trials = "", 0

        def trial():
            self.trials += 1
            return self.answer

        for patcher in (fresh_seal_memory(), mock.patch.object(transcribe, "_check_seal", trial)):
            self.addCleanup(patcher.stop)
            patcher.start()

    def test_a_trial_that_passed_is_remembered_and_fresh_tries_again(self):
        self.assertEqual([transcribe._seal_problem() for _ in range(3)], [None, None, None])
        self.assertEqual(self.trials, 1)                                          # status() stays cheap
        self.answer = "it has stopped working"
        self.assertIsNone(transcribe._seal_problem())                             # the remembered answer
        self.assertEqual((transcribe._seal_problem(fresh=True), self.trials), ("no_network_block", 2))
        self.assertEqual(transcribe._seal_problem(), "no_network_block")          # a failure is remembered as well

    def test_a_trial_that_failed_is_repeated_after_a_while(self):
        self.answer = "sandbox-exec is missing"
        self.assertEqual([transcribe._seal_problem() for _ in range(3)], ["no_network_block"] * 3)
        self.assertEqual(self.trials, 1)                                          # not tried over and over
        self.answer = ""
        with mock.patch.object(transcribe, "SEAL_RETRY_SEC", 0):
            self.assertIsNone(transcribe._seal_problem())
        self.assertEqual(self.trials, 2)

    def test_status_says_what_the_trial_found_in_both_languages(self):
        with mock.patch.object(transcribe, "_platform_problem", lambda: None), mock.patch.object(transcribe, "AFCONVERT", __file__):
            state = transcribe.status()
            self.assertEqual((state["supported"], state["reason"], state["network_block"]), (True, None, "enforced"))
            transcribe._note_seal_failure("the worker found the network open")
            state = transcribe.status()
        self.assertEqual((state["supported"], state["reason"], state["network_block"]), (False, "no_network_block", "unavailable"))
        self.assertIn("network", state["reason_en"])
        self.assertIn("网络", state["reason_zh"])
        for jargon in ("sandbox", "seal", "socket", "沙盒"):                        # plain words only
            self.assertNotIn(jargon, (state["reason_en"] + state["reason_zh"]).lower())


@unittest.skipUnless(os.path.exists("/bin/sh") and os.path.exists("/bin/cat"), "needs a shell")
class SealTrialTest(unittest.TestCase):
    """transcribe._check_seal against stand-in "sandbox-exec" programs that do not seal. Real processes; any system.

    Transcription refuses to run on this trial's answer, so the trial has to notice every way a seal can fail to be one.
    """

    STRIP = '#!/bin/sh\nwhile [ "$1" = "-D" ] || [ "$1" = "-p" ]; do shift 2; done\n'     # drop the rules, keep the command
    CAT_AS_SEALED = '*/cat) case "$2" in */other) exit 1;; *) exec "$@";; esac;;\n'       # reads the given file only

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        memory = fresh_seal_memory()
        self.addCleanup(memory.stop)
        memory.start()

    def stand_in(self, body):
        program = self.tmp / "sandbox-exec"
        program.write_text(self.STRIP + body)
        program.chmod(0o755)
        patcher = mock.patch.object(transcribe, "SANDBOX_EXEC", str(program))
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_a_seal_that_applies_no_rules_is_noticed(self):
        self.stand_in('exec "$@"\n')
        self.assertIn("network was not refused", transcribe._check_seal())
        self.assertEqual(transcribe._seal_problem(), "no_network_block")

    def test_a_seal_that_keeps_the_network_out_but_not_other_files_is_noticed(self):
        self.stand_in('case "$1" in */cat) exec "$@";; *) echo refused;; esac\n')
        self.assertIn("different file could be read", transcribe._check_seal())

    def test_a_seal_under_which_the_given_file_cannot_be_read_is_noticed(self):
        self.stand_in('case "$1" in */cat) exit 1;; *) echo refused;; esac\n')
        self.assertIn("given file could not be read", transcribe._check_seal())

    def test_a_connection_that_arrives_is_noticed_whatever_the_program_says(self):
        self.stand_in('case "$1" in\n' + self.CAT_AS_SEALED + '*/nc) exit 1;;\n'
                      '*) "$@" > /dev/null; echo refused;;\n'                      # connects for real, then says it was refused
                      'esac\n')
        self.assertEqual(transcribe._check_seal(), "a connection got through the seal")

    def test_a_missing_program_is_noticed(self):
        with mock.patch.object(transcribe, "SANDBOX_EXEC", str(self.tmp / "no-such-program")):
            self.assertIn("is missing", transcribe._check_seal())

    def test_a_stand_in_that_behaves_like_the_real_thing_passes(self):
        self.stand_in('case "$1" in\n' + self.CAT_AS_SEALED + '*/nc) exit 1;;\n*) echo refused;;\nesac\n')
        self.assertEqual(transcribe._check_seal(), "")

    def test_the_real_worker_started_through_a_seal_that_does_nothing_refuses_and_the_error_says_why(self):
        self.stand_in('exec "$@"\n')
        home = self.tmp / "local_tools" / "whisper"
        (home / "venv" / "bin").mkdir(parents=True)
        os.symlink(sys.executable, str(home / "venv" / "bin" / "python"))
        write_wav(self.tmp / "audio.wav", 0.5)
        with mock.patch.object(transcribe, "HOME", home), self.assertRaises(transcribe.TranscribeError) as ctx:
            transcribe._run_worker(self.tmp / "audio.wav", 120)
        error = ctx.exception
        self.assertEqual((error.code, error.reason), ("unsupported", "no_network_block"))
        self.assertTrue(str(error) and error.message_zh and error.message_zh != str(error))
        self.assertIn("refusing to transcribe", error.detail)
        self.assertEqual(transcribe._seal_problem(), "no_network_block")          # status() says so from now on


# What security review finding 7 tried after the worker's own network switch, to be run inside a process started the way
# the app starts the worker: the C socket type, a C-level connect on a Python socket, a UDP sendmsg, and a child process.
ATTEMPTS = r"""
import json, socket, subprocess, sys
sys.dont_write_bytecode = True
sys.path.insert(0, %(root)r)
from studio import whisper_worker
TCP, UDP = ("127.0.0.1", %(tcp)d), ("127.0.0.1", %(udp)d)
denied = whisper_worker.network_denied_by_system()
whisper_worker.block_network()
raw = socket.socket.__mro__[1]                      # the C type that socket.socket wraps


def c_type():
    sock = raw(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(TCP)
    sock.close()


def c_connect():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw.connect(sock, TCP)
    sock.close()


def sendmsg():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendmsg([b"never connected"], [], 0, UDP)
    sock.close()


def child():
    code = "import socket; socket.create_connection(%%r).close()" %% (TCP,)
    if subprocess.run([sys.executable, "-I", "-S", "-c", code], stderr=subprocess.DEVNULL).returncode:
        raise ChildProcessError("the child could not connect")


seen = {}
for attempt in (c_type, c_connect, sendmsg, child):
    try:
        attempt()
        seen[attempt.__name__] = "got through"
    except Exception as exc:
        seen[attempt.__name__] = type(exc).__name__
print(json.dumps({"segments": [], "attempts": seen, "denied": denied}))
"""

LOOK_UP_RESOLVER = (
    "import ctypes\n"
    "lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')\n"
    "port = ctypes.c_uint(0)\n"
    "print(lib.bootstrap_look_up(ctypes.c_uint.in_dll(lib, 'bootstrap_port'), b'com.apple.dnssd.service', ctypes.byref(port)))\n"
)


@unittest.skipUnless(sys.platform == "darwin" and os.path.exists("/usr/bin/sandbox-exec"), "needs macOS and its sandbox-exec")
class RealSealTest(unittest.TestCase):
    """The real seal, applied by macOS to real processes started the way transcription starts them. No Whisper."""

    @classmethod
    def setUpClass(cls):
        if transcribe._run(["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"], timeout=30)[0] != 0:
            raise unittest.SkipTest("sandbox-exec cannot apply rules in this environment")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.ports = Ports(self)
        memory = fresh_seal_memory()
        self.addCleanup(memory.stop)
        memory.start()

    def fake_install(self):
        """An "install" whose Python is the one running the tests, as in RealAudioToolTest."""
        home = self.tmp / "local_tools" / "whisper"
        (home / "venv" / "bin").mkdir(parents=True)
        os.symlink(sys.executable, str(home / "venv" / "bin" / "python"))
        (home / "model").mkdir()
        (home / "model" / "config.json").write_text("{}")
        (home / "installed.json").write_text(json.dumps({"packages": list(transcribe.PACKAGES), "revision": transcribe.MODEL_REVISION}))
        return home

    def test_the_trial_passes_on_this_mac_and_status_says_enforced(self):
        self.assertEqual(transcribe._check_seal(), "")
        with mock.patch.object(transcribe, "_platform_problem", lambda: None):
            state = transcribe.status()
        self.assertEqual((state["supported"], state["reason"], state["network_block"]), (True, None, "enforced"))
        self.assertEqual(self.ports.arrived(), (0, 0))

    def test_what_the_review_tried_fails_inside_a_process_started_like_the_worker(self):
        stand_in = self.tmp / "whisper_worker.py"
        stand_in.write_text(ATTEMPTS % {"root": str(ROOT), "tcp": self.ports.tcp_port, "udp": self.ports.udp_port})
        everything = {"c_type": "got through", "c_connect": "got through", "sendmsg": "got through", "child": "got through"}

        # Control, started bare: every one of them reaches the listener. Without this the test below would prove nothing.
        code, out, err = transcribe._run([sys.executable, "-I", stand_in], env=transcribe._worker_env(), timeout=120)
        bare = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((code, bare["attempts"], bare["denied"]), (0, everything, False), err)
        self.assertEqual(self.ports.arrived(), (3, 1))

        # The same program, started by the app's own function for starting the worker.
        with mock.patch.object(transcribe, "HOME", self.fake_install()), mock.patch.object(transcribe, "WORKER", stand_in):
            sealed = transcribe._run_worker(self.tmp / "unused.wav", 120)
        self.assertEqual(sealed["attempts"], {"c_type": "PermissionError", "c_connect": "PermissionError",
                                              "sendmsg": "PermissionError", "child": "ChildProcessError"})
        self.assertIs(sealed["denied"], True)                                     # and the worker's own check sees it
        self.assertEqual(self.ports.arrived(), (0, 0))                            # nothing reached the listener

    def test_listening_and_the_resolver_are_out_of_reach_too(self):
        def tried(code):
            script = "try:\n    %s\n    print('worked')\nexcept Exception as exc:\n    print(type(exc).__name__)\n" % code
            return [transcribe._run(wrap([sys.executable, "-I", "-S", "-c", "import socket\n" + script]), timeout=60)[1].strip()
                    for wrap in (list, transcribe._sealed)]

        listen = "s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(1)"
        resolver_socket = "socket.socket(socket.AF_UNIX).connect('/var/run/mDNSResponder')"
        self.assertEqual(tried(listen), ["worked", "PermissionError"])
        self.assertEqual(tried(resolver_socket), ["worked", "PermissionError"])
        bare = transcribe._run([sys.executable, "-I", "-S", "-c", LOOK_UP_RESOLVER], timeout=60)
        if (bare[0], bare[1].strip()) != (0, "0"):
            self.skipTest("the resolver's system service cannot be asked for on this macOS, sealed or not")
        sealed = transcribe._run(transcribe._sealed([sys.executable, "-I", "-S", "-c", LOOK_UP_RESOLVER]), timeout=60)
        self.assertEqual(sealed[1].strip(), "1100")                               # launchd's "not permitted": the rules refused it

    def test_the_audio_seal_lets_the_given_file_be_read_and_no_other(self):
        given = self.tmp / AWKWARD_NAME.replace(".mp4", ' "x" a=b (param "SOURCE").mp4')
        other = self.tmp / "another recording.m4a"
        (self.tmp / "scratch").mkdir()
        given.write_text("given")
        other.write_text("other")
        given_at, other_at, room = (transcribe._kernel_path(p) for p in (given, other, self.tmp / "scratch"))
        cat = lambda path: transcribe._run(transcribe._sealed_audio(given_at, room, ["/bin/cat", path]), timeout=60)[:2]
        self.assertEqual(cat(given_at), (0, "given"))
        self.assertEqual(cat(other_at)[1], "")
        self.assertNotEqual(cat(other_at)[0], 0)
        self.assertNotEqual(cat("/System/Volumes/Data" + other_at)[0], 0)          # another spelling of the same file
        self.assertEqual(transcribe._run(["/bin/cat", other_at], timeout=60)[:2], (0, "other"))     # control: readable when bare
        home_listing = transcribe._run(transcribe._sealed_audio(given_at, room, ["/bin/ls", os.path.expanduser("~")]), timeout=60)
        self.assertNotEqual(home_listing[0], 0)                                   # the home folder cannot even be listed

    def test_rules_macos_will_not_accept_mean_no_transcription_and_nothing_is_started(self):
        source = self.tmp / "tone.wav"
        write_wav(source, 1.0, tone_on=0)
        started = []
        real_run = transcribe._run

        def run(args, env=None, timeout=None):
            started.append([str(a) for a in args])
            return real_run(args, env=env, timeout=timeout)

        with contextlib.ExitStack() as stack:
            for name, value in (("HOME", self.fake_install()), ("MODEL_FILES", (("config.json", 2, ""),)),
                                ("_platform_problem", lambda: None), ("_run", run),
                                ("NETWORK_SEAL", "(version 1)(allow default)(deny no-such-thing)")):
                stack.enter_context(mock.patch.object(transcribe, name, value))
            state = transcribe.status()
            self.assertEqual((state["supported"], state["reason"], state["network_block"], state["installed"]),
                             (False, "no_network_block", "unavailable", True))
            with self.assertRaises(transcribe.TranscribeError) as ctx:
                transcribe.transcribe_file(source)
        error = ctx.exception
        self.assertEqual((error.code, error.reason, str(error), error.message_zh),
                         ("unsupported", "no_network_block", state["reason_en"], state["reason_zh"]))
        self.assertTrue(started)
        for command in started:                                                   # only the trial ran; no audio tool, no worker
            self.assertEqual(command[0], "/usr/bin/sandbox-exec", command)
            self.assertFalse([part for part in command if part.endswith(("afconvert", "whisper_worker.py"))], command)


def atoms(data, start, end):
    """(offset, size, kind, header length) of each atom of an MPEG-4 / QuickTime file between two offsets."""
    at = start
    while at + 8 <= end:
        size, kind, header = int.from_bytes(data[at:at + 4], "big"), bytes(data[at + 4:at + 8]), 8
        if size == 1:
            size, header = int.from_bytes(data[at + 8:at + 16], "big"), 16
        elif size == 0:
            size = end - at
        if size < header:
            return
        yield at, size, kind, header
        at += size


def pointing_elsewhere(original, url):
    """An .m4a/.mp4 changed to say that its sound is kept at ``url``, with its own sound wiped to zero bytes.

    This is the file of security review finding 11: the entry in the file's "data reference" table that normally says
    "the sound is in this file" is replaced by one naming another place.
    """
    data = bytearray(original)
    path, start, end = [], 0, len(data)
    for wanted in (b"moov", b"trak", b"mdia", b"minf", b"dinf", b"dref"):
        for at, size, kind, header in atoms(data, start, end):
            if kind == wanted:
                path.append(at)
                start, end = at + header, at + size
                break
        else:
            raise ValueError("no %r atom" % wanted)
    entry = path[-1] + 16                                # dref: size, kind, version and flags, entry count, then the entry
    old_size = int.from_bytes(data[entry:entry + 4], "big")
    new = b"url " + bytes(4) + url.encode("utf-8") + b"\0"                        # flags 0: "not in this file"
    new = (len(new) + 4).to_bytes(4, "big") + new
    data[entry:entry + old_size] = new
    for at in path:                                      # every enclosing atom grows by the same amount
        data[at:at + 4] = (int.from_bytes(data[at:at + 4], "big") + len(new) - old_size).to_bytes(4, "big")
    for at, size, kind, header in list(atoms(data, 0, len(data))):
        if kind == b"mdat":
            data[at + header:at + size] = bytes(size - header)
    return bytes(data)


@unittest.skipUnless(sys.platform == "darwin" and os.path.exists("/usr/bin/afconvert") and os.path.exists("/usr/bin/sandbox-exec"),
                     "needs the macOS audio converter and sandbox-exec")
class CraftedVideoTest(unittest.TestCase):
    """A video that says its sound is kept in a different file must not get that other file transcribed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        tone = self.tmp / "tone.wav"
        write_wav(tone, 1.0, rate=44100, tone_on=0)
        (self.tmp / "somewhere-else").mkdir()
        (self.tmp / "uploads").mkdir()
        self.other = Path(os.path.realpath(str(self.tmp))) / "somewhere-else" / "private-memo.m4a"      # "another recording on the disk"
        self.assertEqual(transcribe._run(["/usr/bin/afconvert", "-f", "m4af", "-d", "aac", tone, self.other], timeout=120)[0], 0)
        self.crafted = self.tmp / "uploads" / "0123456789ab.mp4"
        self.crafted.write_bytes(pointing_elsewhere(self.other.read_bytes(), "file://" + str(self.other)))

    def sound(self, media):
        """(seconds, loudest sample) of what afconvert, started bare, reads from a file; (0.0, 0) if it reads nothing."""
        wav = self.tmp / ("heard-%s.wav" % media.stem)
        if transcribe._run(["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", media, wav], timeout=120)[0] != 0:
            return 0.0, 0
        with whisper_worker.open_wav(str(wav)) as out:
            samples = struct.unpack("<%dh" % out.getnframes(), out.readframes(out.getnframes()))
        return transcribe._wav_seconds(wav), max(abs(v) for v in samples) if samples else 0

    def test_the_crafted_file_has_no_sound_of_its_own(self):
        own = [self.crafted.read_bytes()[at + header:at + size] for at, size, kind, header in
               atoms(self.crafted.read_bytes(), 0, self.crafted.stat().st_size) if kind == b"mdat"]
        self.assertTrue(own and not any(b"".join(own)))                           # all zero bytes
        self.assertIn(b"file://" + str(self.other).encode(), self.crafted.read_bytes())
        self.assertGreater(self.sound(self.other)[1], 4000)                       # the other file is the one with sound in it

    def test_extraction_gets_nothing_from_the_other_file(self):
        wav = self.tmp / "out.wav"
        try:
            transcribe._extract_audio(self.crafted, wav, self.tmp)
        except transcribe.TranscribeError as exc:
            self.assertEqual(exc.code, "no_audio")
        else:
            self.fail("sound was extracted for a file that has none of its own: %.2f s, taken from the other file"
                      % transcribe._wav_seconds(wav))
        self.assertFalse(wav.exists())
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["somewhere-else", "tone.wav", "uploads"])    # nothing left behind

    @unittest.skipUnless(os.path.exists("/usr/bin/avconvert"), "needs avconvert, the tool known to follow the pointer")
    def test_the_seal_stops_even_a_tool_that_follows_the_pointer(self):
        def follow(source, output):
            return ["/usr/bin/avconvert", "--source", source, "--preset", "PresetAppleM4A", "--output", output]

        env = transcribe._base_env()
        bare = self.tmp / "bare.m4a"
        transcribe._run(follow(self.crafted, bare), env=env, timeout=120)
        if not (bare.is_file() and self.sound(bare)[1] > 4000):
            self.skipTest("avconvert on this macOS does not follow the pointer, so there is nothing for the seal to stop")

        room = self.tmp / "room"
        room.mkdir()
        given, room_at = transcribe._kernel_path(self.crafted), transcribe._kernel_path(room)
        sealed = room / "sealed.m4a"
        transcribe._run(transcribe._sealed_audio(given, room_at, follow(given, sealed)), env=env, timeout=120)
        self.assertFalse(sealed.is_file() and self.sound(sealed)[1] > 0, "the other file was read under the seal")

        honest = transcribe._kernel_path(self.other)                              # the same tool still reads a file given to it
        copied = room / "honest.m4a"
        transcribe._run(transcribe._sealed_audio(honest, room_at, follow(honest, copied)), env=env, timeout=120)
        self.assertGreater(self.sound(copied)[1], 4000)


def install_fingerprint():
    """(files, bytes, newest change) of the real install, to show a test only read it."""
    files = total = newest = 0
    for folder, _, names in os.walk(str(transcribe.HOME)):
        for name in names:
            info = os.lstat(os.path.join(folder, name))
            files, total, newest = files + 1, total + info.st_size, max(newest, info.st_mtime_ns)
    return files, total, newest


@unittest.skipUnless(sys.platform == "darwin" and (ROOT / "local_tools" / "whisper" / "installed.json").is_file()
                     and os.path.exists("/usr/bin/say"), "needs the real Whisper install in local_tools/whisper, which is not here")
class RealInstallTest(unittest.TestCase):
    """The real install, only read: one short recording made here with the Mac's own voice, transcribed under the seal."""

    def test_a_real_transcription_works_under_the_seal_and_leaves_the_install_untouched(self):
        memory = fresh_seal_memory()
        self.addCleanup(memory.stop)
        memory.start()
        state = transcribe.status()
        if not (state["supported"] and state["installed"] and state["model_ready"]):
            self.skipTest("the real install cannot be used here: %s" % (state["reason"] or "it is not complete"))
        self.assertEqual(state["network_block"], "enforced")
        before = install_fingerprint()
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(tmp), True)
        spoken = tmp / "spoken with the mac's own voice.aiff"
        self.assertEqual(transcribe._run(["/usr/bin/say", "-o", spoken, transcribe.SELF_CHECK_TEXT], timeout=120)[0], 0)

        started = []
        real_run = transcribe._run

        def run(args, env=None, timeout=None):
            started.append([str(a) for a in args])
            return real_run(args, env=env, timeout=timeout)

        with mock.patch.object(transcribe, "_run", run):
            result = transcribe.transcribe_file(spoken)
        text = " ".join(segment["text"] for segment in result["segments"]).lower()
        self.assertIn("transcription", text)
        self.assertEqual((result["language"], result["model"]), ("en", transcribe.MODEL))
        tools = [command for command in started if any(part.endswith(("/afconvert", "whisper_worker.py")) for part in command)]
        self.assertEqual(len(tools), 2)                                           # the audio tool and the worker both ran
        for command in started:
            self.assertEqual(command[0], "/usr/bin/sandbox-exec", command)       # and nothing at all was started bare
        self.assertEqual(install_fingerprint(), before)


if __name__ == "__main__":
    unittest.main()
