"""Static server for the render page, plus a POST endpoint that saves a PNG to disk.

The page hands back its render as a data URI. Routing that through the conversation would
cost a few hundred KB of base64 per image, so the page POSTs it here instead and this
writes the bytes straight to a file.
"""

import base64
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


class Handler(SimpleHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - name fixed by the base class
        if not self.path.startswith("/save/"):
            self.send_error(404)
            return
        name = os.path.basename(self.path[len("/save/") :])
        if not name.endswith(".png") or "/" in name or "\\" in name:
            self.send_error(400, "bad name")
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("ascii")
        prefix = "data:image/png;base64,"
        if not body.startswith(prefix):
            self.send_error(400, "not a png data uri")
            return
        raw = base64.b64decode(body[len(prefix) :])
        with open(os.path.join(HERE, name), "wb") as f:
            f.write(raw)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(f"wrote {name} ({len(raw)} bytes)".encode())

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    os.chdir(HERE)
    ThreadingHTTPServer(("127.0.0.1", 8138), Handler).serve_forever()
