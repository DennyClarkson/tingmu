"""Serve the installation's demo catalog and its listed videos on loopback."""
import json
import re
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.request import ProxyHandler, build_opener

from tingmu.runtime import confined_path


def open_local_url(url):
    from tingmu.providers import NoRedirect
    return build_opener(ProxyHandler({}), NoRedirect()).open(url, timeout=30)


class DemoSource:
    def __init__(self, root):
        manifest = confined_path(root, "catalog.json")
        rows = json.loads(manifest.read_bytes())
        files = {}
        for row in rows:
            ident = row["id"]
            if not re.fullmatch(r"[a-z0-9_-]+", ident) or row["suffix"] not in (".mp4", ".mkv"):
                raise ValueError("体验片单中的视频名无效。")
            route = "/" + ident + row["suffix"]
            files[route] = confined_path(root, route[1:])

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/catalog.json":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                path = files.get(self.path)
                if path is None or not path.is_file():
                    self.send_error(404)
                    return
                with path.open("rb") as src:
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp4" if path.suffix == ".mp4" else "video/x-matroska")
                    self.send_header("Content-Length", str(path.stat().st_size))
                    self.end_headers()
                    try:
                        shutil.copyfileobj(src, self.wfile, 256 * 1024)
                    except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                        pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        base = f"http://127.0.0.1:{self.server.server_port}"
        self.url = base + "/catalog.json"
        payload = json.dumps([{**row, "url": base + "/" + row["id"] + row["suffix"]} for row in rows],
                             ensure_ascii=False).encode()
        self.thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
