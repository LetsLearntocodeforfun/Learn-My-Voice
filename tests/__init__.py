"""Test package (stdlib unittest).

Ensures ``STUDIO_KEYSTORE_FILE`` points to a throwaway file whenever tests are
run without setting it explicitly, so running ``python3 tools/run_tests.py``
never reads or modifies a developer's or user's real OS keystore file.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile

if not os.environ.get("STUDIO_KEYSTORE_FILE"):
    _TEST_KEYSTORE_DIR = tempfile.mkdtemp(prefix="lmv-test-keystore-")
    os.environ["STUDIO_KEYSTORE_FILE"] = os.path.join(_TEST_KEYSTORE_DIR, "keystore.key")
    atexit.register(shutil.rmtree, _TEST_KEYSTORE_DIR, True)
