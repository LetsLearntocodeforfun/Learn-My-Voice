"""Tests for what the desktop app relies on in the core.

The desktop app is a small shell: it starts the core as its child, shows the page the core serves, and
goes away. These tests cover the core's side of that, on any system and without the shell: the core takes
any free port when asked, says where it listens, and stops when the one who started it is gone.

Whether the shell itself works can only be seen where it is built and run, on GitHub's machines.
"""
import http.client
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from studio import server
from studio.store import Store

ROOT = Path(__file__).resolve().parent.parent


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)


def answers(port):
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/state")
        status = conn.getresponse().status
        conn.close()
        return status == 200
    except OSError:
        return False


class StopsWithItsParentTest(TempCase):
    def test_the_core_stops_once_its_input_has_ended_and_not_before(self):
        httpd = server.make_server(0, Store(self.tmp / "data"))
        self.addCleanup(httpd.server_close)
        serving = threading.Thread(target=httpd.serve_forever, daemon=True)
        serving.start()
        read_end, write_end = os.pipe()
        stream = os.fdopen(read_end, "rb")
        self.addCleanup(stream.close)
        watcher = threading.Thread(target=server._stop_when_input_ends, args=(httpd, stream), daemon=True)
        watcher.start()
        port = httpd.server_address[1]
        time.sleep(0.3)
        self.assertTrue(serving.is_alive())                                      # the input is open: nothing happens
        self.assertTrue(answers(port))
        os.write(write_end, b"anything")                                         # what is written to it is not a command
        time.sleep(0.2)
        self.assertTrue(serving.is_alive())
        os.close(write_end)                                                      # the one who held it is gone
        serving.join(10)
        self.assertFalse(serving.is_alive())
        watcher.join(5)
        self.assertFalse(watcher.is_alive())

    def test_no_input_at_all_counts_as_its_end(self):
        for stream in (None, open(os.devnull, "rb")):
            httpd = server.make_server(0, Store(self.tmp / ("data-%s" % (stream is None))))
            serving = threading.Thread(target=httpd.serve_forever, daemon=True)
            serving.start()
            server._stop_when_input_ends(httpd, stream)
            serving.join(10)
            self.assertFalse(serving.is_alive())
            httpd.server_close()
            if stream is not None:
                stream.close()

    def start(self, *options, stdin):
        """The core as the shell starts it: its own process, any free port, its output read line by line."""
        env = dict(os.environ, STUDIO_DATA_DIR=str(self.tmp / "data"), PYTHONDONTWRITEBYTECODE="1")
        process = subprocess.Popen([sys.executable, "-X", "utf8", "-u", "-m", "studio.server", "--no-browser", "--port", "0", *options],
                                   cwd=str(ROOT), env=env, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.addCleanup(process.stdout.close)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process

    def address(self, process):
        """The address in the first line the core prints, as the shell reads it."""
        line = process.stdout.readline().decode("utf-8", "replace")
        self.assertIn("http://127.0.0.1:", line)
        text = line[line.index("http://127.0.0.1:"):].split()[0]
        self.assertTrue(text.endswith("/"), text)
        port = int(text[len("http://127.0.0.1:"):-1])
        self.assertGreater(port, 1023)                                           # port 0 was asked for: any free one was taken
        return port

    def test_started_as_the_shell_starts_it_the_core_ends_when_the_shell_is_gone(self):
        process = self.start("--exit-with-parent", stdin=subprocess.PIPE)
        port = self.address(process)
        self.assertTrue(answers(port))
        self.assertIsNone(process.poll())
        process.stdin.close()                                                    # what happens when the shell ends, however it ends
        self.assertEqual(process.wait(15), 0)
        self.assertFalse(answers(port))
        self.assertTrue((self.tmp / "data").is_dir())                            # its data is where it was told to keep it

    def test_without_that_option_closed_input_does_not_stop_it(self):
        process = self.start(stdin=subprocess.DEVNULL)                           # how a launcher or a terminal starts it
        port = self.address(process)
        time.sleep(0.5)
        self.assertIsNone(process.poll())
        self.assertTrue(answers(port))
        process.terminate()
        process.wait(15)


if __name__ == "__main__":
    unittest.main()
