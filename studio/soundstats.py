"""What can be measured from the sound of a recording: its pauses, how far its loudness moves, where the voice sits.

The counterpart of ``textstats`` for sound. Standard library only, and nothing from the rest of the app, so
``transcribe`` can call it and a test can run it with nothing installed.

What is read: the WAV that ``transcribe`` makes from a video or a recording with the converter that ships
with macOS (16 kHz, one channel, 16-bit). The upload itself is a video or an ``.m4a`` and is never read here.
A file of any other shape is not measured, and the answer says why (``reason``).

What is measured, and how (``measure``):

- sound and silence. The recording is cut into stretches of 25 ms. A stretch holds sound when its level is
  within 30 dB of the loud level of the recording (the level only one stretch in a hundred exceeds) and above
  -60 dB of full scale. ``sound_pct`` is the share of stretches that hold sound.
- pauses: silences of 0.3 s or more with sound before and after, the length ``timing`` and the transcriber
  use for a pause. How many, their middle length, and how many per minute between the first and the last sound.
- loudness range: how far apart the quieter and the louder seconds of sound are, in decibels. The level of
  every full second that holds sound for at least half its length is taken; the range is from the 10th to the
  90th percentile of those levels. Needs ten such seconds.
- pitch: how high the voice sits, and the span it mostly moves in. Up to 400 stretches of 45 ms, spread evenly
  over the sound, are thinned from 16 kHz to 4 kHz and compared with themselves shifted by every period from
  70 to 400 Hz (autocorrelation). A stretch that repeats clearly at one of them counts, with that period. The
  answer is the middle value, the 10th and the 90th percentile, and the distance between those two in
  semitones. Needs 30 such stretches, and at least one in five of the stretches tried.

What none of this can tell:

- whose sound it is. Music under the voice, a second speaker, a hum or street noise are measured along with
  the voice. Music hides pauses (nothing falls silent), narrows the loudness range and has a pitch of its own.
- anything about quality. A wide loudness range is not "lively" and a narrow one is not "flat": moving towards
  the microphone widens it, and an editing app that evens out loudness narrows it.
- intonation. The pitch span of Mandarin holds the tones of the words themselves, so it is not a measure of
  expressiveness. Pitch is an estimate from short samples: a creaky or very breathy voice is partly missed, and
  a voice outside 70 to 400 Hz is missed or taken for an octave of itself.

The limits above (30 dB, -60 dB, 25 ms, the percentiles, how clearly a stretch must repeat) are this project's
own choices. They were tried on tones, noise and silence made in the tests and on the voices that macOS speaks
with (five of them, English and Chinese, 21 November 2026). There the two limits for pitch could be set
anywhere from 0.4 to 0.9 and from 0.85 to 0.95 and the middle pitch moved by 5 Hz at most. None of it was
tuned on, or checked against, recordings of people, and no pitch was compared with another program's.

How long it takes: every sample of the measured part is read once, in this process, in plain Python. Ten
minutes of sound (9.6 million samples) took 0.28 s: one Mac with an Apple M5 Pro, Python 3.9.6, 21 November
2026, three runs of 0.28 to 0.29 s. No other machine was timed. A longer recording is measured up to
MAX_MEASURED_SEC and the answer says so (``cut_short``), so the time does not grow with what is uploaded.
"""
from __future__ import annotations

import array
import itertools
import math
import operator
import struct
import sys
from typing import Any, BinaryIO, Dict, Iterable, List, Optional

RATE = 16000                    # the one sample rate that is measured: what ``transcribe`` asks the converter for
MAX_MEASURED_SEC = 600          # ten minutes: a longer recording is measured up to here (``cut_short``)
MIN_MEASURED_SEC = 1.0          # under one second of sound in the file nothing is measured
FRAME = 400                     # samples in one stretch: 25 ms, short enough to find the edges of a 0.3 s pause
PAUSE_SEC = 0.3                 # a silence this long is a pause, as in ``timing.PAUSE_SEC``
SOUND_WITHIN_DB = 30.0          # a stretch holds sound when it is within this of the recording's loud level
QUIETEST_SOUND_DB = -60.0       # ... and above this, relative to full scale, however quiet the rest is
LOUD_PERCENTILE = 99            # the loud level: only one stretch in a hundred is louder, so one click does not set it
MIN_RATE_SEC = 10.0             # under this much time from first to last sound no pauses per minute, as ``timing.MIN_RATE_SEC``
LOUDNESS_WINDOW_FRAMES = 40     # loudness range is taken over whole seconds
MIN_LOUDNESS_SECONDS = 10       # ... and needs this many seconds that hold sound: fewer give a range of two or three values
PITCH_LOW_HZ, PITCH_HIGH_HZ = 70, 400     # speaking voices of adults sit inside this; singing and small children may not
THIN = 4                        # pitch is looked for at 16 kHz / 4 = 4 kHz, which keeps everything under 2 kHz
PITCH_SAMPLES = 720             # one stretch for pitch: 45 ms, room for two periods of the lowest pitch at every shift
MAX_PITCH_STRETCHES = 400       # how many stretches are tried at most, so the time does not grow with the recording
MIN_PITCH_STRETCHES = 30        # under this many stretches with a clear pitch none is reported
MIN_PITCH_SHARE = 0.2           # ... and at least one in five of those tried: fewer is as likely music or chance as a voice
CLEAR_PITCH = 0.5               # how alike a stretch and its shifted self must be (1 = the same) to count as having a pitch
SAME_PEAK = 0.9                 # of periods that fit about equally well the shortest is taken: twice the period always fits too
MAX_CHUNKS = 64                 # parts of a WAV file that are stepped over on the way to the sound

REASONS = ("unreadable", "other_format", "too_short")
_FULL_SCALE_DB = 20.0 * math.log10(32768.0)
_SILENT_DB = -200.0
_LAG_LOW = RATE // THIN // PITCH_HIGH_HZ - 1        # one shift beyond each end, so that a peak at the end can be seen
_LAG_HIGH = RATE // THIN // PITCH_LOW_HZ + 1
_PAUSE_FRAMES = int(round(PAUSE_SEC * RATE / FRAME))


def wav_header(handle: BinaryIO) -> Optional[Dict[str, Any]]:
    """Where the samples of a WAV are and what shape they have, read from the file's own parts; None when it is no WAV.

    ``{"pcm", "channels", "rate", "byte_rate", "bits", "data_at", "data_bytes"}``. Read by hand and not with the
    ``wave`` module, because the macOS converter writes the "extensible" header for sound taken out of a video,
    which the ``wave`` of Python 3.9 refuses. ``pcm`` is true for plain samples under either header.
    ``data_bytes`` is what the file says of itself; a caller reads no more than is there.

    At most MAX_CHUNKS parts are stepped over before the samples, and of the part that describes the format
    only the beginning is read, so a file made to look like a WAV cannot keep this busy.
    """
    start = handle.read(12)
    if len(start) < 12 or start[8:] != b"WAVE":
        return None
    shape: Optional[Dict[str, Any]] = None
    for _ in range(MAX_CHUNKS):
        head = handle.read(8)
        if len(head) < 8:
            return None
        size = int.from_bytes(head[4:], "little")
        if head[:4] == b"fmt ":
            body = handle.read(min(size, 40))
            if len(body) < 16:
                return None
            tag, channels, rate, byte_rate, _, bits = struct.unpack("<HHIIHH", body[:16])
            if tag == 0xFFFE:                           # "extensible": what the samples are stands further on
                tag = int.from_bytes(body[24:26], "little") if len(body) >= 26 else 0
            shape = {"pcm": tag == 1, "channels": channels, "rate": rate, "byte_rate": byte_rate, "bits": bits}
            handle.seek(size + (size & 1) - len(body), 1)
        elif head[:4] == b"data":
            return dict(shape, data_at=handle.tell(), data_bytes=size) if shape else None
        else:
            handle.seek(size + (size & 1), 1)
    return None


def _percentile(ordered: List[float], pct: float) -> float:
    """Nearest-rank percentile of values in rising order, as ``corpus.voice_stats`` takes it."""
    rank = max(1, int(-(-pct * len(ordered) // 100)))
    return ordered[min(rank, len(ordered)) - 1]


def _samples(raw: bytes) -> "array.array":
    block = array.array("h")
    block.frombytes(raw)
    if sys.byteorder != "little":
        block.byteswap()
    return block


def _energies(handle: BinaryIO, data_at: int, frames: int) -> List[int]:
    """The sum of squares of every stretch of FRAME samples, for at most ``frames`` stretches; fewer when the file ends."""
    out: List[int] = []
    handle.seek(data_at)
    mul = operator.mul
    while len(out) < frames:
        want = min(frames - len(out), LOUDNESS_WINDOW_FRAMES)           # a second at a time
        raw = handle.read(want * FRAME * 2)
        got = len(raw) // (FRAME * 2)
        block = _samples(raw[:got * FRAME * 2])
        for at in range(0, got * FRAME, FRAME):
            frame = block[at:at + FRAME]
            out.append(sum(map(mul, frame, frame)))
        if got < want:
            break
    return out


def _level_db(energy: float, samples: int) -> float:
    return 10.0 * math.log10(energy / samples) - _FULL_SCALE_DB if energy > 0 else _SILENT_DB


def _pauses(sounds: List[bool]) -> Optional[Dict[str, Any]]:
    """Silences of PAUSE_SEC or more between sound; None when nothing sounds at all."""
    if True not in sounds:
        return None
    first = sounds.index(True)
    last = len(sounds) - 1 - sounds[::-1].index(True)
    runs: List[int] = []
    run = 0
    for holds in sounds[first:last + 1]:
        if holds:
            if run >= _PAUSE_FRAMES:
                runs.append(run)
            run = 0
        else:
            run += 1
    span_sec = (last - first + 1) * FRAME / float(RATE)
    return {"count": len(runs),
            "median_sec": round(_percentile(sorted(runs), 50) * FRAME / float(RATE), 2) if runs else None,
            "per_minute": round(len(runs) * 60.0 / span_sec, 1) if span_sec >= MIN_RATE_SEC else None}


def _loudness(energies: List[int], sounds: List[bool]) -> Optional[Dict[str, Any]]:
    """The 10th to 90th percentile of the level of the seconds that hold sound; None with under MIN_LOUDNESS_SECONDS of them."""
    levels: List[float] = []
    for at in range(0, len(energies) - LOUDNESS_WINDOW_FRAMES + 1, LOUDNESS_WINDOW_FRAMES):
        held = [energies[i] for i in range(at, at + LOUDNESS_WINDOW_FRAMES) if sounds[i]]
        if 2 * len(held) >= LOUDNESS_WINDOW_FRAMES:
            levels.append(_level_db(sum(held), len(held) * FRAME))
    if len(levels) < MIN_LOUDNESS_SECONDS:
        return None
    levels.sort()
    return {"range_db": round(_percentile(levels, 90) - _percentile(levels, 10), 1), "seconds": len(levels)}


def pitch_of(stretch: Any) -> Optional[float]:
    """The pitch of PITCH_SAMPLES samples at 16 kHz in Hz, or None when the stretch does not repeat clearly.

    The stretch is thinned to 4 kHz (four neighbours added up, which also takes out what lies above 2 kHz) and
    its mean is taken off. Then its beginning is compared with itself shifted by every whole number of samples
    from the period of PITCH_HIGH_HZ to that of PITCH_LOW_HZ: the sum of products, divided so that 1 means "the
    same" whatever the loudness. Where that has a peak of CLEAR_PITCH or more, the shift is the period. Twice
    and three times the period fit as well, so of the peaks within SAME_PEAK of the best the shortest is taken.
    The peak is placed between whole shifts by a parabola through it and its two neighbours.
    """
    thin = [a + b + c + d for a, b, c, d in zip(stretch[0::THIN], stretch[1::THIN], stretch[2::THIN], stretch[3::THIN])]
    width = len(thin) - _LAG_HIGH                       # as many products at every shift
    if width < _LAG_HIGH:
        return None
    mean = sum(thin) / float(len(thin))
    x = [value - mean for value in thin]
    mul = operator.mul
    squares = [0.0] + list(itertools.accumulate(map(mul, x, x)))       # squares[n]: the energy of the first n samples
    head, head_energy = x[:width], squares[width]
    if head_energy <= 0:
        return None
    alike: List[float] = []
    for lag in range(_LAG_LOW, _LAG_HIGH + 1):
        energy = squares[lag + width] - squares[lag]
        alike.append(sum(map(mul, head, x[lag:lag + width])) / math.sqrt(head_energy * energy) if energy > 0 else 0.0)
    peaks = [i for i in range(1, len(alike) - 1) if alike[i] > alike[i - 1] and alike[i] >= alike[i + 1]]
    best = max((alike[i] for i in peaks), default=0.0)
    if best < CLEAR_PITCH:
        return None
    at = next(i for i in peaks if alike[i] >= SAME_PEAK * best)
    before, here, after = alike[at - 1], alike[at], alike[at + 1]
    bend = before - 2.0 * here + after
    lag = _LAG_LOW + at + (0.5 * (before - after) / bend if bend < 0 else 0.0)
    return RATE / float(THIN) / lag


def _pitch(handle: BinaryIO, data_at: int, sounds: List[bool], samples: int) -> Optional[Dict[str, Any]]:
    """Pitch over at most MAX_PITCH_STRETCHES stretches spread evenly over the sound; None when too few have one."""
    starts = [i * FRAME for i, holds in enumerate(sounds) if holds and i * FRAME + PITCH_SAMPLES <= samples]
    if len(starts) > MAX_PITCH_STRETCHES:
        starts = [starts[k * len(starts) // MAX_PITCH_STRETCHES] for k in range(MAX_PITCH_STRETCHES)]
    found: List[float] = []
    for start in starts:
        handle.seek(data_at + 2 * start)
        raw = handle.read(2 * PITCH_SAMPLES)
        if len(raw) == 2 * PITCH_SAMPLES:
            hz = pitch_of(_samples(raw))
            if hz is not None:
                found.append(hz)
    if len(found) < MIN_PITCH_STRETCHES or len(found) < MIN_PITCH_SHARE * len(starts):
        return None
    found.sort()
    low, high = _percentile(found, 10), _percentile(found, 90)
    return {"median_hz": int(round(_percentile(found, 50))), "low_hz": int(round(low)), "high_hz": int(round(high)),
            "span_semitones": round(12.0 * math.log(high / low, 2), 1), "stretches": len(found), "tried": len(starts)}


def _not_measured(reason: str) -> Dict[str, Any]:
    return {"measured": False, "reason": reason}


def measure(path: Any) -> Dict[str, Any]:
    """Pauses, loudness range and pitch of the WAV at ``path``, or that it was not measured and why.

    Measured::

        {"measured": True, "measured_sec", "duration_sec", "cut_short", "sound_pct",
         "pauses": {"count", "median_sec", "per_minute"} or None,
         "loudness": {"range_db", "seconds"} or None,
         "pitch": {"median_hz", "low_hz", "high_hz", "span_semitones", "stretches", "tried"} or None}

    ``duration_sec`` is the sound the file holds and ``measured_sec`` how much of it was measured; ``cut_short``
    is true when the recording is longer than MAX_MEASURED_SEC, and only its beginning was. Each of the three
    parts is None when there was too little to measure it from (the module's text says how much each needs):
    silence has no pauses, noise has no pitch. ``median_sec`` is None with no pause, ``per_minute`` with under
    MIN_RATE_SEC from the first sound to the last.

    Not measured: ``{"measured": False, "reason"}`` with ``unreadable`` (no file, or not a WAV), ``other_format``
    (a WAV, but not 16 kHz, one channel, 16-bit samples) or ``too_short`` (under MIN_MEASURED_SEC of sound).
    Nothing is raised for anything a file can contain.
    """
    try:
        with open(str(path), "rb") as handle:
            header = wav_header(handle)
            if header is None:
                return _not_measured("unreadable")
            if not (header["pcm"] and (header["channels"], header["rate"], header["bits"]) == (1, RATE, 16)):
                return _not_measured("other_format")
            data_at = header["data_at"]
            there = max(0, min(header["data_bytes"], handle.seek(0, 2) - data_at)) // 2      # samples really in the file
            energies = _energies(handle, data_at, min(there, MAX_MEASURED_SEC * RATE) // FRAME)
            if len(energies) * FRAME < MIN_MEASURED_SEC * RATE:
                return _not_measured("too_short")
            levels = [_level_db(energy, FRAME) for energy in energies]
            quietest = max(_percentile(sorted(levels), LOUD_PERCENTILE) - SOUND_WITHIN_DB, QUIETEST_SOUND_DB)
            sounds = [level > quietest for level in levels]
            pitch = _pitch(handle, data_at, sounds, len(energies) * FRAME)
    except OSError:
        return _not_measured("unreadable")
    return {"measured": True,
            "measured_sec": round(len(energies) * FRAME / float(RATE), 2),
            "duration_sec": round(there / float(RATE), 2),
            "cut_short": there > MAX_MEASURED_SEC * RATE,
            "sound_pct": int(round(100.0 * sounds.count(True) / len(sounds))),
            "pauses": _pauses(sounds), "loudness": _loudness(energies, sounds), "pitch": pitch}


# ---- what was stored, read back --------------------------------------------------------------------------------

def _number(value: Any, low: float, high: float) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value) if low <= value <= high else None


def _part(value: Any, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """``value`` as a dict of exactly ``fields`` (name -> (low, high, whole number?, may be None?)), else None."""
    if not isinstance(value, dict):
        return None
    out: Dict[str, Any] = {}
    for name, (low, high, whole, optional) in fields.items():
        given = value.get(name)
        if given is None and optional:
            out[name] = None
            continue
        number = _number(given, low, high)
        if number is None or (whole and number != int(number)):
            return None
        out[name] = int(number) if whole else number
    return out


_PAUSE_FIELDS = {"count": (0, 1e6, True, False), "median_sec": (0, MAX_MEASURED_SEC, False, True),
                 "per_minute": (0, 200, False, True)}
_LOUDNESS_FIELDS = {"range_db": (0, 200, False, False), "seconds": (1, MAX_MEASURED_SEC, True, False)}
_PITCH_FIELDS = {"median_hz": (PITCH_LOW_HZ - 1, PITCH_HIGH_HZ + 1, True, False),
                 "low_hz": (PITCH_LOW_HZ - 1, PITCH_HIGH_HZ + 1, True, False),
                 "high_hz": (PITCH_LOW_HZ - 1, PITCH_HIGH_HZ + 1, True, False),
                 "span_semitones": (0, 40, False, False),
                 "stretches": (1, MAX_PITCH_STRETCHES, True, False), "tried": (1, MAX_PITCH_STRETCHES, True, False)}


def clean(value: Any) -> Optional[Dict[str, Any]]:
    """A measurement as ``measure`` gives it, checked; None for anything else.

    A measurement is kept with a library item, and a library can come back from a backup file that somebody
    else made. So before a stored measurement is shown, pooled or handed to a model, every number in it is
    checked to be a number in the span ``measure`` can give. A part that does not pass is dropped (None), and
    a measurement whose own fields do not pass is dropped whole.
    """
    if not isinstance(value, dict):
        return None
    if value.get("measured") is False:
        return _not_measured(value["reason"]) if value.get("reason") in REASONS else None
    if value.get("measured") is not True or not isinstance(value.get("cut_short"), bool):
        return None
    top = _part(value, {"measured_sec": (MIN_MEASURED_SEC, MAX_MEASURED_SEC, False, False),
                        "duration_sec": (MIN_MEASURED_SEC, 1e7, False, False), "sound_pct": (0, 100, True, False)})
    if top is None:
        return None
    return dict(top, measured=True, cut_short=value["cut_short"],
                pauses=_part(value.get("pauses"), _PAUSE_FIELDS),
                loudness=_part(value.get("loudness"), _LOUDNESS_FIELDS),
                pitch=_part(value.get("pitch"), _PITCH_FIELDS))


def _middle(values: List[float]) -> Optional[Dict[str, Any]]:
    """The middle value of several recordings (the lower of the two middle ones of an even number), the lowest and the highest."""
    if not values:
        return None
    ordered = sorted(values)
    return {"median": _percentile(ordered, 50), "low": ordered[0], "high": ordered[-1], "recordings": len(ordered)}


def across(measurements: Iterable[Any]) -> Dict[str, Any]:
    """Several recordings side by side: for each quantity the middle recording, the lowest and the highest.

    ``{"recordings", "cut_short", "loudness_range_db", "pitch_hz", "pitch_span_semitones", "pauses_per_minute",
    "pause_sec"}``. ``recordings`` is how many measurements passed ``clean`` and were measured, ``cut_short`` how
    many of them cover only the beginning of their recording. Each quantity is ``{"median", "low", "high",
    "recordings"}`` over the recordings that have it, or None when none has. Recordings are not weighted by
    their length and nothing is averaged: one recording's number is never mixed into another's.
    """
    kept = [m for m in (clean(m) for m in measurements) if m and m["measured"]]

    def of(part: str, field: str) -> Optional[Dict[str, Any]]:
        return _middle([m[part][field] for m in kept if m[part] is not None and m[part][field] is not None])

    return {"recordings": len(kept), "cut_short": sum(1 for m in kept if m["cut_short"]),
            "loudness_range_db": of("loudness", "range_db"), "pitch_hz": of("pitch", "median_hz"),
            "pitch_span_semitones": of("pitch", "span_semitones"),
            "pauses_per_minute": of("pauses", "per_minute"), "pause_sec": of("pauses", "median_sec")}
