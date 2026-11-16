"""Whisper worker. Runs inside local_tools/whisper/venv, never inside the app.

    python -I whisper_worker.py --wav AUDIO.wav --model MODEL_DIR

``studio.transcribe`` starts this as a separate process, hands it a 16 kHz
mono 16-bit WAV it made with macOS tools, and reads one JSON object from the
last line of standard output::

    {"segments": [{"start": 0.0, "end": 2.4, "text": "..."}], "language": "zh",
     "duration_sec": 12.3, "unclear_dropped": 0}

or ``{"error": "..."}`` with a non-zero exit code.

Nothing here talks to the network, and macOS is what makes sure of that: the
app starts this process under rules, enforced by macOS, that deny it and
anything it starts every kind of network access (``studio.transcribe``, "the
seal"). Before it loads any library or opens the audio, the worker checks that
those rules really are in force, and if they are not it does no work at all:
it answers ``{"error": "...", "refused": "network_not_denied"}`` and exits
with code 3. The model is read from a folder on disk, the audio is read with
the standard ``wave`` module, and this process also switches off its own
Python-level ability to open connections, which gives a clear error message
if Python code ever tries.

Only the standard library is imported at the top, so the app's tests can load
this file without Whisper installed. numpy and mlx_whisper are imported inside
``read_wav`` and ``run_whisper``.
"""
import _socket
import argparse
import errno
import io
import json
import socket
import statistics
import sys
import wave

SAMPLE_RATE = 16000

# prompts/01 section 1A, with the example sentence made general: it describes how the
# speech should be written down (verbatim, particles kept) and assumes nothing about
# who is in the video or what it is about.
INITIAL_PROMPT = (
    "以下是小红书视频的原声逐字稿，使用简体中文，包含自然的中英文混说（code-switching）。"
    "请严格逐字记录口语原话，保留所有句尾语气词（如：啦、嘛、哈、喔、呐、诶、呗、呀）和口头连接词"
    "（如：其实、就是说、跟你们讲、说实话），不要把口语改成书面语。"
    "Example: 今天weekend出门逛了一圈，其实这个地方我来过好几次啦，真的蛮舒服的哈。"
)

# prompts/01 section 1A. condition_on_previous_text=False and the silence threshold stop
# Whisper looping a phrase over background music; temperature 0 makes a run repeatable.
SETTINGS = {
    "word_timestamps": True,
    "condition_on_previous_text": False,
    "hallucination_silence_threshold": 1.0,
    "temperature": 0.0,
}

PAUSE_SEC = 0.3        # prompts/01: a pause is 300 ms or more; segments are split there
MIN_LOGPROB = -1.0     # Whisper's own mark for a decode that failed (its logprob_threshold)

_BLOCKED = "network access is switched off while transcribing"
REFUSED = "network_not_denied"      # the "refused" value in the reply; studio.transcribe knows it as WORKER_REFUSED
_NOT_DENIED = ("refusing to transcribe: macOS is not denying this process the network, so it was not started "
               "the way the app starts it")


def network_denied_by_system():
    """True when macOS itself is refusing this process the network.

    ``studio.transcribe`` starts the worker under rules, enforced by macOS,
    that deny every kind of network access; anything the worker starts
    inherits them and nothing inside can lift them. This looks whether they
    really are in force, using the C socket type that sits below everything
    ``block_network`` replaces. A UDP socket is pointed at this computer's own
    address, which sends nothing, and a listening port is asked for on it.
    Under the rules macOS answers both with "Operation not permitted".
    Anything else, including either one working, means the rules are not
    there, and the worker must not run.
    """
    attempts = ((_socket.SOCK_DGRAM, lambda sock: sock.connect(("127.0.0.1", 9))),
                (_socket.SOCK_STREAM, lambda sock: sock.bind(("127.0.0.1", 0))))
    for kind, attempt in attempts:
        try:
            sock = _socket.socket(_socket.AF_INET, kind)
            try:
                attempt(sock)
            finally:
                sock.close()
        except OSError as exc:
            if exc.errno != errno.EPERM:
                return False
            continue
        return False
    return True


def block_network():
    """Make every attempt by this process's Python code to use the network fail.

    Returns a function that undoes it (the worker never calls it; tests do).
    This covers Python code, which is everything the transcription path runs.
    It is a tripwire that names the problem in plain words, not what keeps the
    network out: compiled code and child processes go straight past it. macOS
    keeps the network out (see ``network_denied_by_system``).
    """
    def refuse(*args, **kwargs):
        raise OSError(_BLOCKED)

    saved = []
    for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket.socket, "sendto"),
                        (socket, "create_connection"), (socket, "getaddrinfo"), (socket, "gethostbyname"),
                        (socket, "gethostbyname_ex")):
        saved.append((owner, name, name in vars(owner), getattr(owner, name)))
        setattr(owner, name, refuse)

    def restore():
        for owner, name, own, original in saved:
            if own:
                setattr(owner, name, original)
            else:
                delattr(owner, name)      # was inherited from the C socket type
    return restore


def open_wav(path):
    """``wave.open`` for the file macOS made, including its "extensible" header.

    When the sound comes out of a video, afconvert writes the WAV with format
    tag 0xFFFE (WAVE_FORMAT_EXTENSIBLE). The samples are ordinary PCM, but the
    wave module in Python 3.9 refuses that tag (3.12 accepts it). The tag is
    rewritten to plain PCM in memory; the file itself is not touched.
    """
    with open(path, "rb") as handle:
        data = bytearray(handle.read())
    at = data.find(b"fmt ", 12, 4096)
    if at != -1 and data[at + 8:at + 10] == b"\xfe\xff" and data[at + 32:at + 34] == b"\x01\x00":   # sub-format: PCM
        data[at + 8:at + 10] = b"\x01\x00"
    return wave.open(io.BytesIO(bytes(data)), "rb")


def read_wav(path):
    """Samples of a 16 kHz mono 16-bit WAV as float32 in [-1, 1]. No ffmpeg involved."""
    import numpy as np

    with open_wav(path) as source:
        shape = (source.getnchannels(), source.getsampwidth(), source.getframerate())
        if shape != (1, 2, SAMPLE_RATE):
            raise ValueError("expected 16 kHz mono 16-bit audio, got channels/bytes/rate %s" % (shape,))
        frames = source.readframes(source.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0


def run_whisper(wav_path, model_dir):
    """Transcribe with the settings above. Returns (raw mlx_whisper result, seconds of audio)."""
    audio = read_wav(wav_path)
    duration = len(audio) / float(SAMPLE_RATE)
    if duration < 0.1:
        return {"segments": [], "language": None}, duration
    import mlx_whisper

    result = mlx_whisper.transcribe(audio, path_or_hf_repo=model_dir, initial_prompt=INITIAL_PROMPT, **SETTINGS)
    return result, duration


def breath_groups(raw_segments, pause=PAUSE_SEC):
    """Whisper's output regrouped into one segment per breath. Returns (segments, unclear_dropped).

    Whisper's own segments are too coarse to time speech: it will hand back 20
    seconds as one segment with a 3-second silence inside it. Its word timings
    do show the pauses, so the words are regrouped: a new segment starts
    wherever the gap between two words is ``pause`` seconds or more. The words
    themselves, and the punctuation Whisper wrote, are not changed.

    Two corrections, both Whisper's own rules applied a little more widely:

    - The alignment charges the silence before a word to that word, so the
      first word after a 3-second silence comes back 3 seconds long. A word
      longer than twice the median word (Whisper's limit for words at sentence
      boundaries) has its start moved to that limit before its end.
    - A segment whose average log-probability is below Whisper's failed-decode
      mark is left out and counted in ``unclear_dropped``. With temperature
      fixed at 0 Whisper cannot retry such a decode; in testing this was a
      stray one-character segment invented at the very end of a file.
    """
    items = []                                   # [start, end, text, timed word?]
    dropped = 0
    for seg in raw_segments or []:
        logprob = seg.get("avg_logprob")
        if isinstance(logprob, (int, float)) and logprob < MIN_LOGPROB:
            dropped += 1
            continue
        words = [w for w in (seg.get("words") or []) if str(w.get("word") or "").strip()]
        if words:
            items.extend([float(w["start"]), float(w["end"]), str(w["word"]), True] for w in words)
        elif str(seg.get("text") or "").strip():     # no word timings for this one: keep it whole
            items.append([float(seg["start"]), float(seg["end"]), str(seg["text"]), False])

    lengths = [end - start for start, end, _, timed in items if timed and end > start]
    limit = 2 * min(0.7, statistics.median(lengths)) if lengths else 0.0
    groups = []
    for start, end, text, timed in items:
        end = max(end, start)
        if timed and limit and end - start > limit:
            start = end - limit
        if groups and round((start - groups[-1]["end"]) * 1000) < round(pause * 1000):   # whole ms, as timing.py counts
            groups[-1]["end"] = max(groups[-1]["end"], end)
            groups[-1]["text"] += text
        else:
            groups.append({"start": start, "end": end, "text": text})
    segments = [{"start": round(g["start"], 3), "end": round(g["end"], 3), "text": g["text"].strip()}
                for g in groups if g["text"].strip()]
    return segments, dropped


def main(argv=None, engine=None, out=None):
    parser = argparse.ArgumentParser(description="Transcribe one WAV file on this computer.")
    parser.add_argument("--wav", required=True)
    parser.add_argument("--model", required=True, help="folder holding config.json and weights.safetensors")
    args = parser.parse_args(argv)

    out = out or sys.stdout
    if not network_denied_by_system():                    # before any library is loaded or the audio is opened
        out.write("\n" + json.dumps({"error": _NOT_DENIED, "refused": REFUSED}) + "\n")
        out.flush()
        return 3
    real_stdout, sys.stdout = sys.stdout, sys.stderr     # anything a library prints must not mix with the result
    block_network()
    try:
        result, duration = (engine or run_whisper)(args.wav, args.model)
        segments, dropped = breath_groups(result.get("segments"))
        payload = {
            "segments": segments,
            "language": result.get("language"),
            "duration_sec": round(duration, 3),
            "unclear_dropped": dropped,
        }
        code = 0
    except Exception as exc:                              # reported to the app, which explains it to the person
        payload = {"error": "%s: %s" % (type(exc).__name__, exc)}
        code = 1
    finally:
        sys.stdout = real_stdout
    out.write("\n" + json.dumps(payload) + "\n")          # ASCII-only JSON: safe whatever the terminal encoding
    out.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
