"""At-rest protection for API keys and Windows NTFS ACL hardening (stdlib only).

What sealing protects against — and what it does not:
- A copy, backup, zip archive, or cloud sync of ``data/`` alone cannot be decrypted because ``data/config.json``
  holds only sealed ciphertext while the master key lives outside ``data/`` in the user's OS configuration
  folder (on macOS and Linux) or in Windows DPAPI tied to the signed-in Windows account.
- Another program running under the same OS user account can read both ``data/config.json`` and the user's key
  file (or call DPAPI as that user); sealing is not a boundary against code running as the same person.
- On macOS and Linux, ``seal`` / ``unseal`` use Encrypt-then-MAC (``HMAC-SHA256`` keystream + ``HMAC-SHA256`` tag)
  under a 256-bit master key stored with ``0600`` permissions inside the user's OS application-config directory
  (outside ``data/``), without invoking modal GUI keychain dialogs that hang headless runs or ad-hoc-signed
  builds.
- On Windows, ``os.chmod(path, 0o700)`` only toggles the read-only attribute and leaves inherited NTFS DACLs
  intact when unpacked outside ``%USERPROFILE%``; ``seal`` / ``unseal`` use Win32 ``CryptProtectData`` /
  ``CryptUnprotectData`` (DPAPI) with ``CRYPTPROTECT_UI_FORBIDDEN``, and ``restrict_windows_acl`` restricts
  directory inheritance to the current user's SID.
- Reading never overwrites or creates the master key file, and first-time key creation is atomic across
  concurrent processes without ever falling back to process-ephemeral memory keys.
"""
from __future__ import annotations

import base64
import binascii
import ctypes
import csv
import errno
import hashlib
import hmac
import io
import os
import re
import secrets
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set

APP_ID = "io.github.letslearntocodeforfun.learn-my-voice"
_DPAPI_ENTROPY = b"learn-my-voice:config:v1"
_MAC_DOMAIN = b"lmv-keystore-v1:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_CREATE_NO_WINDOW = 0x08000000
_SID_RE = re.compile(r"^S-1-\d+(?:-\d+)+$")

_LOCK = threading.Lock()
_WINDOWS_SID: Optional[str] = None
_WINDOWS_SID_CHECKED = False
_ACL_APPLIED: Set[str] = set()


class KeystoreUnavailable(OSError):
    """The OS keystore or master key file cannot be read or safely created right now."""

    def __init__(self, path: str, reason: str, detail: str = "") -> None:
        self.path = path
        self.reason = reason
        msg = "Keystore unavailable (%s): %s" % (reason, path)
        if detail:
            msg += " (%s)" % detail
        super().__init__(msg)


KeyStoreError = KeystoreUnavailable


def _key_path() -> Path:
    override = os.environ.get("STUDIO_KEYSTORE_FILE")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_ID / "keystore.key"
    config_root = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return config_root / "learn-my-voice" / "keystore.key"


def key_path_display() -> str:
    """Human-readable path (or DPAPI label) for user-facing notices when sealing fails."""
    if os.name == "nt" and not os.environ.get("STUDIO_KEYSTORE_FILE"):
        return "Windows DPAPI"
    return str(_key_path())


def _read_existing_master_key(target: Path) -> bytes:
    """Read an existing 32-byte master key without ever modifying, replacing, or following symlinks."""
    path_str = str(target)
    for attempt in range(6):
        try:
            info = os.lstat(path_str)
        except FileNotFoundError:
            raise KeystoreUnavailable(path_str, "missing")
        except OSError as exc:
            raise KeystoreUnavailable(path_str, "unreadable", str(exc))
        if stat.S_ISLNK(info.st_mode):
            raise KeystoreUnavailable(path_str, "symlink")
        if not stat.S_ISREG(info.st_mode):
            raise KeystoreUnavailable(path_str, "not_regular_file")
        if (info.st_mode & 0o400) and (info.st_mode & 0o077):
            try:
                os.chmod(path_str, stat.S_IMODE(info.st_mode) & ~0o077)
            except OSError:
                pass
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path_str, flags)
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.ELOOP:
                raise KeystoreUnavailable(path_str, "symlink", str(exc))
            raise KeystoreUnavailable(path_str, "unreadable", str(exc))
        try:
            fstat = os.fstat(fd)
            if not stat.S_ISREG(fstat.st_mode):
                raise KeystoreUnavailable(path_str, "not_regular_file")
            raw = os.read(fd, 64)
        except OSError as exc:
            raise KeystoreUnavailable(path_str, "unreadable", str(exc))
        finally:
            os.close(fd)
        if len(raw) == 32:
            return raw
        if len(raw) < 32 and attempt < 5:
            # A peer process using the O_CREAT|O_EXCL fallback may still be writing its 32 bytes.
            time.sleep(0.01)
            continue
        raise KeystoreUnavailable(path_str, "bad_length", "%d bytes" % len(raw))
    raise KeystoreUnavailable(path_str, "bad_length")


def _load_or_create_master_key() -> bytes:
    """Return the 32-byte master key, creating it atomically only when the path does not exist."""
    target = _key_path()
    path_str = str(target)
    with _LOCK:
        try:
            return _read_existing_master_key(target)
        except KeystoreUnavailable as exc:
            if exc.reason != "missing":
                raise
        parent = target.parent
        parent_str = str(parent)
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            parent_info = os.lstat(parent_str)
            if not stat.S_ISDIR(parent_info.st_mode):
                raise KeystoreUnavailable(path_str, "parent_not_directory")
            if parent_info.st_mode & 0o077:
                try:
                    os.chmod(parent_str, 0o700)
                except OSError:
                    pass
        except KeystoreUnavailable:
            raise
        except OSError as exc:
            raise KeystoreUnavailable(path_str, "parent_unwritable", str(exc))

        key = secrets.token_bytes(32)
        tmp: Optional[str] = None
        try:
            fd, tmp = tempfile.mkstemp(dir=parent_str, prefix="keystore.", suffix=".tmp")
            with os.fdopen(fd, "wb") as handle:
                set_mode = getattr(os, "fchmod", None)
                if set_mode is not None:
                    set_mode(handle.fileno(), 0o600)
                handle.write(key)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                os.chmod(tmp, 0o600)
            try:
                os.link(tmp, path_str)
                return key
            except FileExistsError:
                return _read_existing_master_key(target)
            except (AttributeError, NotImplementedError, OSError):
                # Filesystem does not support hard links (e.g. FAT32/exFAT): fall back to exclusive O_CREAT|O_EXCL.
                pass
        except KeystoreUnavailable:
            raise
        except OSError as exc:
            raise KeystoreUnavailable(path_str, "unwritable", str(exc))
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

        excl_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            excl_fd = os.open(path_str, excl_flags, 0o600)
        except FileExistsError:
            return _read_existing_master_key(target)
        except OSError as exc:
            raise KeystoreUnavailable(path_str, "unwritable", str(exc))
        try:
            set_mode = getattr(os, "fchmod", None)
            if set_mode is not None:
                set_mode(excl_fd, 0o600)
            os.write(excl_fd, key)
            os.fsync(excl_fd)
        except BaseException as exc:
            try:
                os.close(excl_fd)
            except OSError:
                pass
            try:
                os.unlink(path_str)
            except OSError:
                pass
            if isinstance(exc, OSError):
                raise KeystoreUnavailable(path_str, "unwritable", str(exc))
            raise
        os.close(excl_fd)
        return key


def _xor_stream(enc_key: bytes, data: bytes) -> bytes:
    out = bytearray(len(data))
    offset = 0
    counter = 0
    while offset < len(data):
        block = hmac.digest(enc_key, struct.pack(">Q", counter), hashlib.sha256)
        chunk = data[offset:offset + 32]
        for idx, byte in enumerate(chunk):
            out[offset + idx] = byte ^ block[idx]
        offset += len(chunk)
        counter += 1
    return bytes(out)


def _seal_v1(plaintext: bytes, master: bytes) -> str:
    nonce = secrets.token_bytes(16)
    enc_key = hmac.digest(master, b"enc:" + nonce, hashlib.sha256)
    mac_key = hmac.digest(master, b"mac:" + nonce, hashlib.sha256)
    ciphertext = _xor_stream(enc_key, plaintext)
    tag = hmac.digest(mac_key, _MAC_DOMAIN + nonce + ciphertext, hashlib.sha256)
    packed = base64.urlsafe_b64encode(nonce + tag + ciphertext).decode("ascii")
    return "v1:" + packed


def _unseal_v1(token: str, master: bytes) -> Optional[bytes]:
    try:
        raw = base64.urlsafe_b64decode(token[3:].encode("ascii"))
    except (ValueError, binascii.Error):
        return None
    if len(raw) < 48:
        return None
    nonce, tag, ciphertext = raw[:16], raw[16:48], raw[48:]
    mac_key = hmac.digest(master, b"mac:" + nonce, hashlib.sha256)
    expected = hmac.digest(mac_key, _MAC_DOMAIN + nonce + ciphertext, hashlib.sha256)
    if not hmac.compare_digest(expected, tag):
        return None
    enc_key = hmac.digest(master, b"enc:" + nonce, hashlib.sha256)
    return _xor_stream(enc_key, ciphertext)


def _dpapi_protect(plaintext: bytes) -> Optional[bytes]:
    if os.name != "nt" or os.environ.get("STUDIO_KEYSTORE_FILE"):
        return None
    try:
        from ctypes import wintypes

        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        in_buf = ctypes.create_string_buffer(plaintext, len(plaintext))
        ent_buf = ctypes.create_string_buffer(_DPAPI_ENTROPY, len(_DPAPI_ENTROPY))
        in_blob = DataBlob(len(plaintext), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
        ent_blob = DataBlob(len(_DPAPI_ENTROPY), ctypes.cast(ent_buf, ctypes.POINTER(ctypes.c_char)))
        out_blob = DataBlob()
        ok = crypt32.CryptProtectData(
            ctypes.byref(in_blob), None, ctypes.byref(ent_blob), None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
        )
        if not ok:
            return None
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception:  # noqa: BLE001
        return None


def _dpapi_unprotect(ciphertext: bytes) -> Optional[bytes]:
    if os.name != "nt":
        return None
    try:
        from ctypes import wintypes

        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        in_buf = ctypes.create_string_buffer(ciphertext, len(ciphertext))
        ent_buf = ctypes.create_string_buffer(_DPAPI_ENTROPY, len(_DPAPI_ENTROPY))
        in_blob = DataBlob(len(ciphertext), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
        ent_blob = DataBlob(len(_DPAPI_ENTROPY), ctypes.cast(ent_buf, ctypes.POINTER(ctypes.c_char)))
        out_blob = DataBlob()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, ctypes.byref(ent_blob), None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
        )
        if not ok:
            return None
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception:  # noqa: BLE001
        return None


def seal(secret: str) -> str:
    """Encrypt ``secret`` so ``config.json`` never stores plaintext API keys.

    Raises ``KeystoreUnavailable`` when the OS keystore / master key file cannot be read or safely created.
    """
    raw = str(secret or "").encode("utf-8")
    if os.name == "nt" and not os.environ.get("STUDIO_KEYSTORE_FILE"):
        dpapi = _dpapi_protect(raw)
        if dpapi is None:
            raise KeystoreUnavailable("Windows DPAPI", "dpapi_failed")
        return "dpapi:" + base64.urlsafe_b64encode(dpapi).decode("ascii")
    return _seal_v1(raw, _load_or_create_master_key())


def unseal(token: Any) -> Optional[str]:
    """Decrypt a sealed token back to its string, or None when tampered, missing, or unreadable.

    Never creates or overwrites the master key file.
    """
    if not isinstance(token, str) or not token:
        return None
    raw: Optional[bytes] = None
    if token.startswith("dpapi:"):
        try:
            cipher = base64.urlsafe_b64decode(token[6:].encode("ascii"))
        except (ValueError, binascii.Error):
            return None
        raw = _dpapi_unprotect(cipher)
    elif token.startswith("v1:"):
        with _LOCK:
            try:
                master = _read_existing_master_key(_key_path())
            except KeystoreUnavailable:
                return None
        raw = _unseal_v1(token, master)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _current_windows_sid() -> Optional[str]:
    global _WINDOWS_SID, _WINDOWS_SID_CHECKED
    if _WINDOWS_SID_CHECKED:
        return _WINDOWS_SID
    with _LOCK:
        if _WINDOWS_SID_CHECKED:
            return _WINDOWS_SID
        _WINDOWS_SID_CHECKED = True
        win_kw: Dict[str, Any] = {"creationflags": _CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        try:
            done = subprocess.run(
                ["whoami", "/user", "/fo", "csv", "/nh"],
                capture_output=True, timeout=10, **win_kw,
            )
            if done.returncode == 0:
                line = done.stdout.decode("utf-8", "replace").strip()
                rows = list(csv.reader(io.StringIO(line)))
                if rows and len(rows[0]) >= 2:
                    candidate = rows[0][1].strip()
                    if _SID_RE.fullmatch(candidate):
                        _WINDOWS_SID = candidate
        except (OSError, subprocess.SubprocessError):
            _WINDOWS_SID = None
        return _WINDOWS_SID


def restrict_windows_acl(folder: Path) -> bool:
    """Restrict NTFS inheritance on ``folder`` to the current Windows user, SYSTEM, and Administrators."""
    if os.name != "nt":
        return False
    norm = os.path.normcase(os.path.abspath(str(folder)))
    if norm in _ACL_APPLIED:
        return True
    sid = _current_windows_sid()
    if not sid:
        return False
    win_kw: Dict[str, Any] = {"creationflags": _CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    try:
        if os.path.islink(str(folder)) or not os.path.isdir(str(folder)):
            return False
        done = subprocess.run(
            [
                "icacls", str(folder),
                "/inheritance:r",
                "/grant:r",
                "*S-1-5-18:(OI)(CI)F",
                "*S-1-5-32-544:(OI)(CI)F",
                "*%s:(OI)(CI)F" % sid,
            ],
            capture_output=True, timeout=15, **win_kw,
        )
        if done.returncode == 0:
            _ACL_APPLIED.add(norm)
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False
