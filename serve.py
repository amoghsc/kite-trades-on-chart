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
import base64, hashlib, http.server, json, os, secrets, socketserver, sys, threading, time
import urllib.error, urllib.parse, urllib.request

PORT = int(os.environ.get('PORT', 8770))
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache')
SESSION_FILE = os.path.join(CACHE, '.session')
AUTH_FILE = os.path.join(CACHE, '.auth.json')     # OAuth client id + access token
MCP_URL = 'https://mcp.kite.trade/mcp'
MCP_ROOT = 'https://mcp.kite.trade'
REDIRECT = f'http://localhost:{PORT}/oauth/callback'
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
        self.auth = {}
        self.pending = {}            # OAuth state -> PKCE verifier, for logins in flight
        # Deliberately no network here. The page must still be served when Zerodha is
        # unreachable or the saved session has gone stale; connecting happens on first use.

    def _next(self):
        self._id += 1
        return self._id

    def _forget_session(self):
        self.session = None
        try:
            if os.path.exists(SESSION_FILE):
                os.remove(SESSION_FILE)
        except OSError:
            pass

    # ---- OAuth (Zerodha moved the MCP endpoint behind OAuth in late 2026) ----
    def _load_auth(self):
        try:
            return json.load(open(AUTH_FILE))
        except Exception:
            return {}

    def _save_auth(self, d):
        self.auth = d
        tmp = AUTH_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(d, f)
        os.replace(tmp, AUTH_FILE)
        try:
            os.chmod(AUTH_FILE, 0o600)         # it holds an access token
        except OSError:
            pass

    def forget_token(self):
        a = self._load_auth()
        a.pop('access_token', None)
        self._save_auth(a)
        self._forget_session()

    @staticmethod
    def _json(url, data=None, headers=None, method=None):
        body = None
        h = {'Accept': 'application/json', 'User-Agent': 'kite-charting/2.1'}
        if data is not None:
            body = json.dumps(data).encode()
            h['Content-Type'] = 'application/json'
        h.update(headers or {})
        req = urllib.request.Request(url, data=body, headers=h, method=method)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or '{}')

    def _client_id(self):
        """Register this copy of the app once, as an OAuth public client."""
        a = self._load_auth()
        if a.get('client_id'):
            return a['client_id']
        reg = self._json(MCP_ROOT + '/register', {
            'client_name': 'Kite trades on chart',
            'redirect_uris': [REDIRECT],
            'grant_types': ['authorization_code'],
            'response_types': ['code'],
            'token_endpoint_auth_method': 'none',
        })
        a['client_id'] = reg['client_id']
        if reg.get('client_secret'):
            a['client_secret'] = reg['client_secret']
        self._save_auth(a)
        return a['client_id']

    def start_login(self):
        """Returns the Zerodha URL to open. The browser comes back to /oauth/callback."""
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        state = secrets.token_urlsafe(24)
        self.pending[state] = verifier
        q = urllib.parse.urlencode({
            'response_type': 'code', 'client_id': self._client_id(), 'redirect_uri': REDIRECT,
            'code_challenge': challenge, 'code_challenge_method': 'S256',
            'state': state, 'scope': 'default', 'resource': MCP_URL,
        })
        return MCP_ROOT + '/authorize?' + q

    def finish_login(self, code, state):
        verifier = self.pending.pop(state, None)
        if not verifier:
            raise RuntimeError('that login link has expired, please click Connect Kite again')
        form = urllib.parse.urlencode({
            'grant_type': 'authorization_code', 'code': code, 'redirect_uri': REDIRECT,
            'client_id': self._load_auth().get('client_id', ''), 'code_verifier': verifier,
            'resource': MCP_URL,
        }).encode()
        req = urllib.request.Request(MCP_ROOT + '/token', data=form, method='POST', headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json', 'User-Agent': 'kite-charting/2.1'})
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.loads(r.read().decode())
        if not tok.get('access_token'):
            raise RuntimeError('Zerodha did not return an access token')
        a = self._load_auth()
        a['access_token'] = tok['access_token']
        if tok.get('refresh_token'):
            a['refresh_token'] = tok['refresh_token']
        self._save_auth(a)
        self._forget_session()          # a new identity needs a new MCP session
        return True

    # ---- MCP transport ----
    def _post(self, payload, timeout=60):
        token = self._load_auth().get('access_token')
        headers = {'Content-Type': 'application/json',
                   'Accept': 'application/json, text/event-stream',
                   'User-Agent': 'kite-charting/2.1'}      # a python UA gets a 403
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if self.session:
            headers['Mcp-Session-Id'] = self.session
        req = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(), headers=headers)
        for attempt in range(8):
            gap = MIN_GAP - (time.time() - self.last)
            if gap > 0:
                time.sleep(gap)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
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
        """MCP handshake. Gives us the Mcp-Session-Id that every later call carries."""
        self._post({'jsonrpc': '2.0', 'id': self._next(), 'method': 'initialize',
                    'params': {'protocolVersion': '2025-06-18',
                               'capabilities': {},
                               'clientInfo': {'name': 'kite-charting', 'version': '2.1'}}})
        # The "initialized" notification expects no reply, and Zerodha sometimes just
        # holds the connection open instead of closing it. It is advisory, so send it
        # with a short leash and carry on either way.
        try:
            self._post({'jsonrpc': '2.0', 'method': 'notifications/initialized',
                        'params': {}}, timeout=5)
        except Exception:
            pass

    def call(self, tool, **args):
        body = lambda: {'jsonrpc': '2.0', 'id': self._next(), 'method': 'tools/call',
                        'params': {'name': tool, 'arguments': args}}
        with self.lock:
            try:
                if not self.session:
                    self.initialize()
                r = self._post(body())
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    # the access token is no longer accepted: drop it so the page offers
                    # a fresh Connect Kite rather than failing silently from here on
                    self.forget_token()
                    raise RuntimeError('your Zerodha login has expired, click Connect Kite')
                if e.code != 400:
                    raise
                self._forget_session()      # stale MCP session, token is still good
                self.initialize()
                r = self._post(body())
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
        if not self._load_auth().get('access_token'):
            return False
        try:
            self.call('session', mode='status')
            return True
        except Exception:
            # not authorised yet, or a stale/expired session (Zerodha answers 400/403);
            # either way the app should offer a fresh login rather than crash
            return False

    def login_url(self):
        return self.start_login()


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


def lookup(**args):
    """One instrument-master lookup. 'full' verbosity is what carries listing_date."""
    return rows_of(kite.call('market', mode='search', verbosity='full', **args))


def resolve(isins, symbols, first_trade=None):
    """Newest ISIN first, then exact ticker. Returns (instrument|None, tried)."""
    tried = []
    for isin in isins:
        tried.append('isin:' + isin)
        r = pick(lookup(search_mode='get_by_isin', isin=isin), first_trade)
        if r:
            return r, tried
    for sym in symbols:
        cands = []
        for exch in ('NSE', 'BSE'):
            tried.append(f'{exch}:{sym}')
            rows = lookup(search_mode='get_by_tradingsymbol', exchange=exch, tradingsymbol=sym)
            # exact ticker only: a text search for PARAS also returns PARASPETRO
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
            data = rows_of(kite.call('market', mode='historical', instrument_token=token,
                                     interval='day', from_date=f'{cur} 00:00:00',
                                     to_date=f'{stop} 23:59:59'))
            fresh += [{'t': c['date'][:10], 'o': c['open'], 'h': c['high'],
                       'l': c['low'], 'c': c['close'], 'v': c['volume']}
                      for c in data if c.get('date')]
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

    def send_page(self, title, msg, ok=True):
        body = f'''<!doctype html><meta charset="utf-8"><title>{title}</title>
<style>body{{font:15px/1.6 -apple-system,system-ui,sans-serif;background:#0f1115;color:#e6e8ee;
display:grid;place-items:center;height:100vh;margin:0;text-align:center}}
.c{{max-width:420px;padding:28px}}h1{{font-size:19px;margin:0 0 10px;color:{'#26a69a' if ok else '#ef5350'}}}
p{{color:#8a93a6;margin:0}}a{{color:#5b8def}}</style>
<div class="c"><h1>{title}</h1><p>{msg}</p></div>'''.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith('/oauth/callback'):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get('error'):
                return self.send_page('Not connected',
                                      f"Zerodha said: {q['error'][0]}. You can close this tab and try again.", False)
            try:
                kite.finish_login(q.get('code', [''])[0], q.get('state', [''])[0])
                return self.send_page('Connected to Zerodha',
                                      'You can close this tab and go back to the app.')
            except Exception as e:                              # noqa: BLE001
                return self.send_page('Could not finish signing in', str(e), False)

        if self.path.startswith('/api/status'):
            try:
                return self.send_json({'helper': True, 'loggedIn': kite.logged_in()})
            except Exception as e:                              # noqa: BLE001
                return self.send_json({'helper': True, 'loggedIn': False, 'note': str(e)})
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
