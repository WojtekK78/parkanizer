"""Integration test with real Chrome + selenium-wire against a local page imitating the web app.

Skipped when Chrome/chromedriver can't be started.
"""
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import parkanizer

PAGE = b"""<html><head><title>Parkanizer</title></head><body>
<img src="/logo.png">
<script>
  // like the web app: first call before token is known, then authorized calls
  fetch('/api/get-employee-context')
    .then(() => fetch('/api/get-employee-context', {headers: {Authorization: 'Bearer first'}}))
    .then(() => fetch('/api/get-employee-context', {headers: {Authorization: 'Bearer latest'}}));
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    hits = []

    def do_GET(self):
        Handler.hits.append(self.path)
        if self.path == "/":
            body, ctype = PAGE, "text/html"
        elif self.path.startswith("/api/"):
            body, ctype = b"{}", "application/json"
        else:
            body, ctype = b"x", "image/png"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    Handler.hits = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % httpd.server_port
    httpd.shutdown()


@pytest.fixture
def browser(app, monkeypatch):
    # local page only, don't let selenium-wire chain to a system proxy
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(parkanizer, "CAPTURE_SCOPES", [r"http://127\.0\.0\.1:\d+/api/.*"])
    # Chrome sends localhost traffic around the proxy unless told otherwise
    arguments = ["--proxy-bypass-list=<-loopback>"]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        arguments.append("--no-sandbox")
    monkeypatch.setattr(parkanizer.cfg, "chrome_arguments", arguments)
    # conftest replaces start_driver with a mock, use the real one here
    monkeypatch.setattr(parkanizer, "start_driver", START_DRIVER)
    try:
        parkanizer.start_driver()
    except parkanizer.ParkanizerError:
        pytest.skip("Chrome/chromedriver not available")
    yield parkanizer.driver
    parkanizer.quit_driver()


START_DRIVER = parkanizer.start_driver


def test_authorization_taken_from_authorized_request(browser, server):
    browser.get(server + "/")
    header = parkanizer.get_req_header()
    # the first call without token is skipped, whichever authorized one is seen is used
    assert header["Authorization"] in ("Bearer first", "Bearer latest")


def test_only_api_requests_are_captured_and_images_blocked(browser, server):
    # like login(): drop what Chrome itself requested on startup
    del browser.requests
    browser.get(server + "/")
    parkanizer.get_req_header()
    captured = [r.path for r in browser.requests]
    assert captured and all(path.startswith("/api/") for path in captured)
    assert "/logo.png" not in Handler.hits
