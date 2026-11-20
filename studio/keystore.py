"""At-rest protection for API keys and Windows NTFS ACL hardening (stdlib only).

Why this exists:
- Storing raw API keys in ``data/config.json`` exposes them whenever ``data/`` is backed up to cloud
  storage, zipped for troubleshooting, or read by another process that lacks access to the user's OS
  profile secrets.
- On Windows, ``os.chmod(path, 0o700)`` only toggles the read-only attribute and leaves inherited NTFS
  DACLs intact when unpacked outside ``%USERPROFILE%``.

How keys are protected:
- On Windows, ``seal`` / ``unseal`` use Win32 ``CryptProtectData`` / ``CryptUnprotectData`` (DPAPI) with
  ``CRYPTPROTECT_UI_FORBIDDEN``, binding ciphertext to the signed-in Windows user's logon credentials
  without ever prompting.
- On macOS and Linux, ``seal`` / ``unseal`` use Encrypt-then-MAC (``SHA256`` stream + ``HMAC-SHA256``)
  under a 256-bit master key stored with ``0600`` permissions inside the user's OS application-config
  directory (outside ``data/``), without invoking modal GUI keychain dialogs that hang headless runs or
  ad-hoc-signed builds.
- Legacy plaintext entries in ``config.json`` remain readable and are sealed on the next config save.
"""
from __future__ import annotations

import base64
import binascii
import ctypes
import csv
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
from pathlib import Path
from typing import Any, Dict, Optional, Set

APP_ID = "io.github.letslearntocodeforfun.learn-my-voice"
_DPAPI_ENTROPY = b"learn-my-voice:config:v1"
_MAC_DOMAIN = b"lmv-keystore-v1:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_CREATE_NO_WINDOW = 0x08000000
_SID_RE = re.compile(r"^S-1-\d+(?:-\d+)+$")

_LOCK = threading.Lock()
_EPHEMERAL_KEYS: Dict[str, bytes] = {}
_WINDOWS_SID: Optional[str] = None
_WINDOWS_SID_CHECKED = False
_ACL_APPLIED: Set[str] = set()


def _key_path() -> Path:
    override = os.environ.get("STUDIO_KEYSTORE_FILE")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_ID / "keystore.key"
    config_root = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return config_root / "learn-my-voice" / "keystore.key"


def _load_or_create_master_key() -> bytes:
    target = _key_path()
    cache_id = str(target)
    with _LOCK:
        try:
            if target.is_file() and not target.is_symlink():
                info = os.lstat(str(target))
                if info.st_mode & 0o077:
                    os.chmod(str(target), stat.S_IMODE(info.st_mode) & ~0o077)
                raw = target.read_bytes()
                if len(raw) == 32:
                    return raw
        except OSError:
            pass
        key = secrets.token_bytes(32)
        try:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            parent_info = os.lstat(str(target.parent))
            if stat.S_ISDIR(parent_info.st_mode) and (parent_info.st_mode & 0o077):
                os.chmod(str(target.parent), 0o700)
            fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix="keystore.", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as handle:
                    set_mode = getattr(os, "fchmod", None)
                    if set_mode is not None:
                        set_mode(handle.fileno(), 0o600)
                    handle.write(key)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, str(target))
                os.chmod(str(target), 0o600)
                return key
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError:
            return _EPHEMERAL_KEYS.setdefault(cache_id, key)


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
    """Encrypt ``secret`` so ``config.json`` never stores plaintext API keys."""
    raw = str(secret or "").encode("utf-8")
    dpapi = _dpapi_protect(raw)
    if dpapi is not None:
        return "dpapi:" + base64.urlsafe_b64encode(dpapi).decode("ascii")
    return _seal_v1(raw, _load_or_create_master_key())


def unseal(token: Any) -> Optional[str]:
    """Decrypt a sealed token back to its string, or None when tampered or unreadable."""
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
        raw = _unseal_v1(token, _load_or_create_master_key())
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
