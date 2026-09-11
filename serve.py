#!/usr/bin/env python3
"""Local helper for the trade-chart app.

Run this, then open http://localhost:8770

Why it exists: a web page is not allowed to call Zerodha's servers directly (they send
no CORS headers), so this sits in the middle. It serves the page and forwards the two
calls the page needs — look up an instrument, download its daily candles — using your
own Kite login. Candles are cached in cache/ on this machine. Nothing is uploaded
anywhere: your tradebook never leaves the browser, and this process only ever talks to
mcp.kite.trade.

No dependencies beyond Python 3.8+.
"""
import http.server, json, os, re, socketserver, sys, threading, time, urllib.error, urllib.request

PORT = int(os.environ.get('PORT', 8770))
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache')
SESSION_FILE = os.path.join(CACHE, '.session')
MCP_URL = 'https://mcp.kite.trade/mcp'
MIN_GAP = 1.1          # seconds between upstream calls; Zerodha rate-limits hard
WINDOW_DAYS = 1800     # ~2000 daily candles is the per-request ceiling


# ---------------------------------------------------------------- Kite MCP client
class Kite:
    def __init__(self):
        os.makedirs(CACHE, exist_ok=True)
        self.session = open(SESSION_FILE).read().strip() if os.path.exists(SESSION_FILE) else None
        self.lock = threading.Lock()
        self.last = 0.0
        self._id = 0
        if not self.session:
            self.initialize()

    def _post(self, payload):
        headers = {'Content-Type': 'application/json',
                   'Accept': 'application/json, text/event-stream',
                   'User-Agent': 'kite-charting/2.0'}      # a python UA gets a 403
        if self.session:
            headers['Mcp-Session-Id'] = self.session
        req = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(), headers=headers)
        for attempt in range(8):
            gap = MIN_GAP - (time.time() - self.last)
            if gap > 0:
                time.sleep(gap)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    sid, body = resp.headers.get('mcp-session-id'), resp.read().decode()
                self.last = time.time()
                break
            except urllib.error.HTTPError as e:
                self.last = time.time()
                if e.code == 429 and attempt < 7:
                    wait = min(60, 3 * 2 ** attempt)
                    print(f'  rate limited by Zerodha, waiting {wait}s', flush=True)
                    time.sleep(wait)
                    continue
                raise
        if sid and sid != self.session:
            self.session = sid
            open(SESSION_FILE, 'w').write(sid)
        if body.startswith('event:') or body.startswith('data:'):
            body = ''.join(l[5:].strip() for l in body.splitlines() if l.startswith('data:'))
        return json.loads(body) if body else {}

    def initialize(self):
        self._id += 1
        self._post({'jsonrpc': '2.0', 'id': self._id, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-03-26', 'capabilities': {},
            'clientInfo': {'name': 'kite-charting', 'version': '2.0'}}})
        self._post({'jsonrpc': '2.0', 'method': 'notifications/initialized'})

    def call(self, tool, **args):
        with self.lock:
            self._id += 1
            r = self._post({'jsonrpc': '2.0', 'id': self._id, 'method': 'tools/call',
                            'params': {'name': tool, 'arguments': args}})
        if 'error' in r:
            raise RuntimeError(r['error'].get('message', 'unknown error'))
        res = r.get('result', {})
        text = ''.join(c.get('text', '') for c in res.get('content', []) if c.get('type') == 'text')
        if res.get('isError'):
            raise RuntimeError(text)
        try:
            return json.loads(text)
        except ValueError:
            return text

    def logged_in(self):
        try:
            self.call('get_profile')
            return True
        except RuntimeError:
            return False      # the server just says "Failed to execute" until authorised

    def login_url(self):
        # a fresh session so the link cannot collide with a half-finished one
        self.session = None
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
        self.initialize()
        text = self.call('login')
        m = re.search(r'https://mcp\.kite\.trade/authorize\?session_id=\S+', str(text))
        return m.group(0).rstrip(')].,') if m else None


kite = Kite()
EQUITY = {'EQ'}


def rows_of(res):
    return res.get('data', []) if isinstance(res, dict) else (res or [])


def pick(rows, first_trade=None):
    """Choose a listing to chart.

    Normally the NSE line, but a stock that moved to NSE later (Avantel listed there in
    2024) has no NSE history behind that date, so a listing that starts after the user's
    first trade loses to one that covers it — usually the older BSE line.
    """
    rows = [r for r in rows_of(rows) if r.get('instrument_token') and r.get('exchange') in ('NSE', 'BSE')]
    if not rows:
        return None

    def rank(r):
        listed = r.get('listing_date') or ''
        too_new = 1 if (first_trade and listed and listed > first_trade) else 0
        return (too_new,
                0 if r.get('instrument_type') in EQUITY else 1,
                0 if r['exchange'] == 'NSE' else 1)

    return sorted(rows, key=rank)[0]


def resolve(isins, symbols, first_trade=None):
    """Newest ISIN first, then exact ticker. Returns (instrument|None, tried)."""
    tried = []
    for isin in isins:
        tried.append('isin:' + isin)
        r = pick(kite.call('search_instruments', query=isin, filter_on='isin'), first_trade)
        if r:
            return r, tried
    for sym in symbols:
        cands = []
        for exch in ('NSE', 'BSE'):
            tried.append(f'{exch}:{sym}')
            rows = rows_of(kite.call('search_instruments', query=f'{exch}:{sym}', limit=10))
            # exact ticker only: searching PARAS also returns PARASPETRO
            cands += [x for x in rows if x.get('tradingsymbol') == sym and x.get('exchange') == exch]
        r = pick(cands, first_trade)
        if r:
            return r, tried
    return None, tried


def day_add(d, n):
    return time.strftime('%Y-%m-%d', time.localtime(time.mktime(time.strptime(d, '%Y-%m-%d')) + n * 86400 + 43200))


def candles(token, start, end):
    """Cached daily candles for one instrument, extended at either end as needed."""
    path = os.path.join(CACHE, f'{token}.json')
    have = json.load(open(path)) if os.path.exists(path) else []
    ranges = []
    if have:
        if start < have[0]['t']:
            ranges.append((start, day_add(have[0]['t'], -1)))
        if end > have[-1]['t']:
            ranges.append((day_add(have[-1]['t'], 1), end))
    else:
        ranges.append((start, end))
    fresh = []
    for a, b in ranges:
        cur = a
        while cur <= b:
            stop = min(day_add(cur, WINDOW_DAYS), b)
            data = kite.call('get_historical_data', instrument_token=token, interval='day',
                             from_date=f'{cur} 00:00:00', to_date=f'{stop} 23:59:59')
            if isinstance(data, list):
                fresh += [{'t': c['date'][:10], 'o': c['open'], 'h': c['high'],
                           'l': c['low'], 'c': c['close'], 'v': c['volume']} for c in data]
            cur = day_add(stop, 1)
    if fresh:
        merged = {c['t']: c for c in have}
        merged.update({c['t']: c for c in fresh})
        have = [merged[t] for t in sorted(merged)]
        json.dump(have, open(path, 'w'), separators=(',', ':'))
    return have


# ---------------------------------------------------------------- HTTP
class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def log_message(self, fmt, *args):
        if '/api/' in str(args[0] if args else ''):
            sys.stderr.write('  %s\n' % (fmt % args))

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith('/api/status'):
            return self.send_json({'helper': True, 'loggedIn': kite.logged_in()})
        return super().do_GET()

    def do_POST(self):
        if not self.path.startswith('/api/'):
            return self.send_error(404)
        n = int(self.headers.get('Content-Length') or 0)
        req = json.loads(self.rfile.read(n) or b'{}')
        try:
            if self.path == '/api/login':
                url = kite.login_url()
                return self.send_json({'url': url} if url else {'error': 'could not get a login link'})
            if self.path == '/api/instrument':
                r, tried = resolve(req.get('isins', []), req.get('symbols', []), req.get('first_trade'))
                if not r:
                    return self.send_json({'status': 'unresolved', 'tried': tried})
                return self.send_json({'status': 'ok', 'instrument_token': r['instrument_token'],
                                       'exchange': r['exchange'], 'tradingsymbol': r['tradingsymbol'],
                                       'name': r.get('name', ''), 'isin': r.get('isin', ''),
                                       'listing_date': r.get('listing_date', ''), 'tried': tried})
            if self.path == '/api/candles':
                return self.send_json({'status': 'ok',
                                       'candles': candles(req['token'], req['from'], req['to'])})
        except RuntimeError as e:
            return self.send_json({'status': 'error', 'error': str(e)}, 200)
        except Exception as e:                                    # noqa: BLE001
            return self.send_json({'status': 'error', 'error': f'{type(e).__name__}: {e}'}, 200)
        self.send_error(404)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == '__main__':
    print(f'\n  Trade charts running at  http://localhost:{PORT}\n'
          f'  Candle cache: {CACHE}\n  Ctrl-C to stop.\n', flush=True)
    try:
        Server(('127.0.0.1', PORT), Handler).serve_forever()
    except OSError as e:
        if e.errno in (48, 98):        # address already in use
            print(f'  Something is already serving port {PORT}.\n'
                  f'  If that is this app, just open http://localhost:{PORT} — nothing else to do.\n'
                  f'  Otherwise run it elsewhere:  PORT=8771 python3 serve.py')
        else:
            raise
    except KeyboardInterrupt:
        print('\n  stopped')
