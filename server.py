#!/usr/bin/env python3
"""
Instagram Dashboard — local server.
Usage: python3 server.py   →   opens http://localhost:6060
"""

import json
import subprocess
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path

PORT      = 6060
ROOT      = Path(__file__).parent
DASHBOARD = ROOT / 'dashboard.html'

# Shared sync state (thread-safe)
_state = {'running': False, 'last': None, 'error': None}
_lock  = threading.Lock()


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Handles each request in a separate thread so /sync-status never blocks."""
    daemon_threads = True


def _generate():
    with _lock:
        _state.update(running=True, error=None)
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / 'generate.py')],
            cwd=str(ROOT), capture_output=True, text=True, timeout=300,
        )
        with _lock:
            if r.returncode == 0:
                _state.update(last='ok', error=None)
            else:
                _state.update(last='error', error=(r.stderr or r.stdout).strip())
    except Exception as e:
        with _lock:
            _state.update(last='error', error=str(e))
    finally:
        with _lock:
            _state['running'] = False


class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path in ('/', '/dashboard.html'):
            self._file(DASHBOARD, 'text/html')
        elif self.path == '/sync-status':
            with _lock:
                body = json.dumps(dict(_state)).encode()
            self._json(body)
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path == '/sync':
            with _lock:
                busy = _state['running']
            if not busy:
                threading.Thread(target=_generate, daemon=True).start()
            body = json.dumps({'ok': True, 'running': True}).encode()
            self._json(body)
        else:
            self.send_response(404); self.end_headers()

    def _json(self, body):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, mime):
        if not path.exists():
            self.send_response(404); self.end_headers(); return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', len(data))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass  # silence request noise


def main():
    print(f'Dashboard → http://localhost:{PORT}  (Ctrl+C to stop)')
    import webbrowser
    webbrowser.open(f'http://localhost:{PORT}')
    ThreadingHTTPServer(('localhost', PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
