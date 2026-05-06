#!/usr/bin/env python3
"""
server.py — SOLVULATOR local bundle server
Single file, zero dependencies beyond stdlib.

Serves:
  GET  /                          → index.html (PWA shell)
  GET  /manifest.json             → PWA manifest
  GET  /sw.js                     → service worker
  GET  /icons/*                   → PWA icons (generated SVG→PNG fallback)
  POST /api/claude                → Anthropic proxy (hides API key)
  GET  /api/sheet                 → Google Sheet CSV proxy (fixes CORS)
  GET  /api/health                → liveness

Usage:
  ANTHROPIC_API_KEY=sk-ant-... python3 server.py
  ANTHROPIC_API_KEY=sk-ant-... PORT=8080 python3 server.py

Then on iPhone: http://YOUR_MAC_IP:8080 → Add to Home Screen
"""

import io, json, os, sys, urllib.request, urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

PORT             = int(os.environ.get('PORT', 8080))
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
CLAUDE_MODEL     = os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-20250514')
STATIC_DIR       = Path(__file__).parent

MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js':   'application/javascript',
  '.json': 'application/json',
  '.css':  'text/css',
  '.png':  'image/png',
  '.svg':  'image/svg+xml',
  '.ico':  'image/x-icon',
}

# ── tiny PNG generator for icons (no Pillow needed) ──────────────────────────
import zlib, struct

def make_icon_png(size: int) -> bytes:
    """Generate a minimal valid purple-on-dark PNG icon."""
    w = h = size
    # ARGB rows: dark bg + centered 'S' approximated by filled rect
    rows = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            margin = size // 6
            inner = margin <= x < w - margin and margin <= y < h - margin
            if inner:
                row += bytes([0x9b, 0x7d, 0xe8, 0xff])  # purple
            else:
                row += bytes([0x08, 0x07, 0x0a, 0xff])  # near-black
        rows.append(b'\x00' + bytes(row))

    raw = b''.join(rows)
    compressed = zlib.compress(raw, 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack('>I', len(data)) + tag + data
        crc = zlib.crc32(tag + data) & 0xffffffff
        return c + struct.pack('>I', crc)

    ihdr_data = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    return (
        b'\x89PNG\r\n\x1a\n' +
        chunk(b'IHDR', ihdr_data) +
        chunk(b'IDAT', compressed) +
        chunk(b'IEND', b'')
    )

ICON_192 = make_icon_png(192)
ICON_512 = make_icon_png(512)

# ── HTTP handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f'  {self.command:5} {self.path[:60]:60} → {args[1] if len(args)>1 else "?"}')

    def cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')

    def send_bytes(self, body: bytes, ct: str, status=200):
        self.send_response(status)
        self.send_header('Content-Type', ct)
        self.send_header('Content-Length', str(len(body)))
        self.cors()
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj: dict, status=200):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode()
        self.send_bytes(body, 'application/json', status)

    def read_body(self) -> dict:
        n = int(self.headers.get('Content-Length', 0))
        if not n: return {}
        try: return json.loads(self.rfile.read(n))
        except: return {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        qs   = parse_qs(urlparse(self.path).query)

        if path == '/api/health':
            self.send_json({'ok': True, 'api_key': bool(ANTHROPIC_API_KEY), 'model': CLAUDE_MODEL})

        elif path == '/api/sheet':
            sheet_id = qs.get('id', [''])[0]
            gid      = qs.get('gid', ['0'])[0]
            if not sheet_id:
                self.send_json({'error': 'id param required'}, 400); return
            url = f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}'
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'solvulator/1'})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    csv = resp.read()
                self.send_bytes(csv, 'text/csv; charset=utf-8')
            except urllib.error.HTTPError as e:
                self.send_json({'error': f'Sheet HTTP {e.code} — check sharing settings'}, 502)
            except Exception as e:
                self.send_json({'error': str(e)}, 502)

        elif path in ('/icons/icon-192.png', '/icons/icon-192'):
            self.send_bytes(ICON_192, 'image/png')

        elif path in ('/icons/icon-512.png', '/icons/icon-512'):
            self.send_bytes(ICON_512, 'image/png')

        elif path in ('/', '/index.html', ''):
            f = STATIC_DIR / 'index.html'
            self.send_bytes(f.read_bytes(), 'text/html; charset=utf-8')

        else:
            # Serve any static file
            f = STATIC_DIR / path.lstrip('/')
            if f.exists() and f.is_file():
                ext  = f.suffix.lower()
                mime = MIME.get(ext, 'application/octet-stream')
                self.send_bytes(f.read_bytes(), mime)
            else:
                # SPA fallback
                idx = STATIC_DIR / 'index.html'
                if idx.exists():
                    self.send_bytes(idx.read_bytes(), 'text/html; charset=utf-8')
                else:
                    self.send_json({'error': f'Not found: {path}'}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/api/claude':
            self._proxy_claude()
        else:
            self.send_json({'error': f'Not found: {path}'}, 404)

    def _proxy_claude(self):
        if not ANTHROPIC_API_KEY:
            self.send_json({'error': 'ANTHROPIC_API_KEY not set on server'}, 503); return
        body       = self.read_body()
        prompt     = body.get('prompt', '')
        system     = body.get('system', '')
        max_tokens = int(body.get('max_tokens', 1000))
        stream     = body.get('stream', False)
        if not prompt:
            self.send_json({'error': 'prompt required'}, 400); return

        messages = [{'role': 'user', 'content': prompt}]
        payload = json.dumps({
            'model':      CLAUDE_MODEL,
            'max_tokens': max_tokens,
            'stream':     stream,
            'messages':   messages,
            **(({'system': system}) if system else {}),
        }).encode()

        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=payload,
            headers={
                'Content-Type':      'application/json',
                'x-api-key':         ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
            },
            method='POST'
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                if stream:
                    # Forward SSE stream directly
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Cache-Control', 'no-cache')
                    self.cors()
                    self.end_headers()
                    while True:
                        chunk = resp.read(256)
                        if not chunk: break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                else:
                    data = json.loads(resp.read())
                    text = ''.join(b.get('text','') for b in data.get('content',[]) if b.get('type')=='text')
                    self.send_json({'text': text, 'model': data.get('model'), 'usage': data.get('usage')})
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            self.send_json({'error': f'Anthropic {e.code}: {body[:200]}'}, 502)
        except Exception as e:
            self.send_json({'error': str(e)}, 502)

if __name__ == '__main__':
    # Print local IP so iPhone can find it
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
        s.close()
    except:
        local_ip = '127.0.0.1'

    print(f'''
╔══════════════════════════════════════════════╗
║  SOLVULATOR — local bundle                   ║
╠══════════════════════════════════════════════╣
║  Mac:     http://localhost:{PORT:<18}║
║  iPhone:  http://{local_ip}:{PORT:<18}║
║                                              ║
║  API key: {'✓ set' if ANTHROPIC_API_KEY else '✗ missing — set ANTHROPIC_API_KEY'}{'':{'✓ set':25,'✗ missing — set ANTHROPIC_API_KEY':2}[('✓ set' if ANTHROPIC_API_KEY else '✗ missing — set ANTHROPIC_API_KEY')]}║
║  Model:   {CLAUDE_MODEL:<36}║
╠══════════════════════════════════════════════╣
║  iPhone: Safari → http://{local_ip}:{PORT}   ║
║  Then: Share → Add to Home Screen            ║
╚══════════════════════════════════════════════╝
''')
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nstopped')
        server.server_close()
