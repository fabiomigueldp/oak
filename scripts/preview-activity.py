"""Loopback-only visual preview with synthetic sessions and skins, never game data."""
import argparse
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import struct
import time
from urllib.parse import urlsplit
import zlib

ROOT = Path(__file__).resolve().parents[1]
DAY = 86400
NAMES = ['CipoVerde', 'LuaDeMusgo', 'Cedro', 'RaposaAzul', 'SolNascente', '.Amora', 'PedraMansa', 'Vagalume']
COLORS = [(98, 145, 93), (139, 109, 170), (183, 132, 68), (65, 143, 176), (187, 91, 71), (163, 83, 130), (118, 136, 119), (176, 169, 87)]


def png(index):
    """Small deterministic synthetic Minecraft-layout image for offline visual QA."""
    shirt = COLORS[index]
    rows = []
    for y in range(64):
        row = bytearray()
        for x in range(64):
            color = (*shirt, 255)
            if 8 <= x < 16 and 8 <= y < 16:
                color = (183 + index * 4, 144 + index * 3, 112 + index * 2, 255)
                if y < 10 or x == 8 or x == 15:
                    color = (48 + index * 8, 45 + index * 5, 35 + index * 6, 255)
                if y == 12 and x in (10, 13):
                    color = (45, 52, 44, 255)
                if y == 14 and x in (11, 12):
                    color = (110, 75, 58, 255)
            if 32 <= x and y < 16:
                color = (0, 0, 0, 0)
            row.extend(color)
        rows.append(b'\0' + row)
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 64, 64, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b''.join(rows))) + chunk(b'IEND', b'')


def fixture():
    now = int(time.time())
    today = (now - 10800) // DAY * DAY + 10800
    first = today - 7 * DAY
    sessions = []
    for offset in range(8):
        base = first + offset * DAY
        for index in range(len(NAMES)):
            spans = [(8 + index * .43, 9.3 + index * .72), (14.2 + (index % 3) * .6, 15.5 + index * .36), (18 + index * .47, 20 + index * .43)]
            if index in (0, 2, 5):
                spans.append((.15 + index * .1, 2.2 + index * .15))
            for number, (left, right) in enumerate(spans):
                start, end = int(base + left * 3600), min(now, int(base + right * 3600))
                if end <= start:
                    continue
                sessions.append((index, {'id': offset * 1000 + index * 10 + number, 'start': start, 'end': end, 'open': end == now, 'endReason': None if end == now else 'left'}))
    shards = {}
    cursor = first // DAY * DAY
    while cursor <= now:
        key = datetime.fromtimestamp(cursor, timezone.utc).strftime('%Y-%m-%d')
        coverage_start, coverage_end = max(first, cursor), min(now, cursor + DAY)
        gaps = [(max(coverage_start, cursor + 12 * 3600 + 2400), min(coverage_end, cursor + 13 * 3600 + 300))]
        coverage = [[coverage_start, coverage_end]]
        for left, right in gaps:
            if right > left:
                coverage = [[coverage_start, left], [right, coverage_end]]
        players = []
        for index, name in enumerate(NAMES):
            spans = []
            for player_index, session in sessions:
                if player_index != index:
                    continue
                for left, right in coverage:
                    start, end = max(left, session['start']), min(right, session['end'])
                    if end > start:
                        spans.append(dict(session, start=start, end=end, continuedBefore=session['start'] < start, continuesAfter=session['end'] > end, endReason='unknown' if end < session['end'] and end != cursor + DAY else session['endReason']))
            if spans:
                players.append({'id': 'name:' + name.lower(), 'uuid': None, 'name': name, 'skin': format(index + 1, '064x'), 'sessions': spans})
        shards[key] = {'version': 1, 'day': key, 'start': cursor, 'end': cursor + DAY, 'updated': now, 'coverage': coverage, 'players': players, 'truncated': False}
        cursor += DAY
    return {'version': 1, 'updated': now, 'observedSince': first, 'sampleSeconds': 5, 'gapSeconds': 20, 'retentionDays': 90, 'latestDay': list(shards)[-1], 'days': list(shards), 'collecting': True}, shards


class Preview(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'public'), **kwargs)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith('/data/activity/'):
            index, shards = fixture()
            name = path.rsplit('/', 1)[-1]
            if '/skins/' in path:
                try:
                    self.respond(png(int(name.removesuffix('.png'), 16) - 1), 'image/png')
                except (ValueError, IndexError):
                    self.send_error(404)
            elif name == 'index.json':
                self.respond(json.dumps(index).encode(), 'application/json')
            elif name.removesuffix('.json') in shards:
                self.respond(json.dumps(shards[name[:-5]]).encode(), 'application/json')
            else:
                self.send_error(404)
        elif path in ('/activity/', '/activity/index.html'):
            html = (ROOT / 'public/activity/index.html').read_text(encoding='utf-8')
            html = html.replace('<body class="activity-page">', '<body class="activity-page"><div style="text-align:center;padding:7px;background:oklch(30% .04 85);color:oklch(90% .06 85);font:11px system-ui">Prévia local · jogadores e sessões fictícios</div>')
            self.respond(html.encode(), 'text/html; charset=utf-8')
        else:
            super().do_GET()

    def respond(self, value, content_type):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(value)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(value)

    def log_message(self, *_args):
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8081)
    args = parser.parse_args()
    date = datetime.fromtimestamp(time.time() - DAY - 10800, timezone.utc).strftime('%Y-%m-%d')
    print(f'Synthetic preview: http://127.0.0.1:{args.port}/activity/?date={date}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Preview).serve_forever()
