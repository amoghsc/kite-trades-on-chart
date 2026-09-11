# Kite trades on chart

See every trade you have made drawn on that stock's price chart, with your average cost,
realized profit and holding periods. Built for Zerodha tradebook exports.

Your trades never leave your computer. There is no account, no upload, and no server anywhere
else. You do **not** need a Kite Connect API key, a paid subscription, or any developer setup:
prices come from Zerodha's own service, authorised by logging in the way you normally would.

## Quick start

1. Install Python 3 if you do not have it. macOS and Linux already do. On Windows get it from python.org.
2. Double-click **start.command**. It starts the app and opens your browser.
   On Windows, or if you prefer a terminal, run `python3 serve.py` in this folder and open
   http://localhost:8770 yourself.
3. Leave that window open while you use the app. Closing it stops the app; double-click
   start.command again next time. If it says the port is already in use, the app is already
   running, so just open http://localhost:8770.
4. Download your tradebook from [Zerodha Console](https://console.zerodha.com/reports/tradebook):
   Reports → Tradebook → segment **Equity** → pick a date range → download CSV.
   Zerodha allows one year per file, so download one file per year.
5. Drag all the CSV files onto the page.
6. Click **Connect Kite** and log in to your own Zerodha account. That is what lets the app
   download price history. The first download takes a few minutes; after that only new days are fetched.

## Without Python

Open `index.html` directly in a browser and drop your CSVs. You get the instrument list, the full
trade table, realized profit, holdings, average cost and holding periods.

You do not get the price charts. A web page is not permitted to call Zerodha's servers directly,
which is the only reason `serve.py` exists. It serves the page and passes those requests along.

## What the numbers mean

- Green marker below a bar is a buy, red above is a sell. Trades in the same bar on the same side
  merge into one marker, quantity summed and price averaged.
- The dashed yellow line is your average cost of the open position, FIFO.
- **30d before** is how the price moved in the 21 trading days before you traded. **30d after** and
  **90d after** are what happened next. This is the column to read if you want to know whether you
  tend to buy after a run-up.
- Realized profit is FIFO across the trades in the files you loaded. If you sold something you bought
  before your earliest file, that row has no matching buy and is flagged on hover.
- Zerodha's price history is adjusted for splits, bonuses and demergers, but your tradebook records
  what you actually paid. Trades that land outside their day's candle are rescaled and marked
  `÷ratio`. A clean ratio like 2, 5 or 10 is treated as a split and the quantity is scaled too.
  `÷ratio*` means a demerger or similar, where only the price is scaled. This detection is a
  heuristic, so check it if a chart looks wrong.
- Instruments marked `○` have no price history. Usually they are delisted, merged or expired.
  Their trades still appear in the table.

## Controls

Every setting is remembered in your browser.

| Control | What it does |
|---|---|
| Candles / Line | Candlesticks or a close-price line. In line mode a thickness control appears; set it to 1 to keep your trade markers the most prominent thing on the chart. |
| 1D / 1W / 1M | Daily, weekly or monthly bars, built from the daily data already downloaded. |
| MA 50/100/150/200 | Moving averages. Always true **day** averages, so MA200 stays a 200-day line even on the weekly view. |
| Measure | Click to anchor, move, click again to lock. Shows % change, absolute change, days and bar count. Esc or `M` exits. |
| Notes | Show or hide all notes on the chart. Placing a new note switches them back on. |
| Note | Click it, then click the chart where the note should point. Type the text and save. Drag a note to move it, click it to edit, clear the text to delete it. Notes are kept per instrument, in this browser. Shortcut `N`. |
| Trades − 2 + | Marker size. Each triangle is also sized by how many shares that trade was, relative to your typical trade in that stock, so a big position stands out at a glance. |
| Labels | Print quantity and price beside every marker. Off by default, since hovering a marker tells you the same thing without the clutter. |
| Crosshair | Lines that follow the pointer with price and date labels on the axes. Off by default. |
| Data | Add more CSV files, or clear everything. |
| Table | Sits on the bar above the trade table, on the right. Switch it off for a taller chart. |
| ◐ | Light or dark theme. |

Buys are green triangles pointing up from below, sells are red triangles pointing down from above.
The **tip of each triangle sits on the price you actually traded at**, so you can read the entry
and exit straight off the chart. They are semi-transparent, so the price line stays readable
underneath, which matters most when you are zoomed out.

**Hover any triangle** and it tells you what you did in plain words: "Bought 5 @ 65.45", with the
date and total below. Where several trades share a bar it says how many, and where a split or bonus
has moved the chart price it also shows what you actually paid at the time.

## Files

| | |
|---|---|
| `index.html` | The whole app. Loads its charting library from a CDN. |
| `serve.py` | Local helper: serves the page and relays calls to Zerodha. No dependencies. |
| `start.command` | Double-click launcher for macOS. |
| `cache/` | Downloaded candles, and your Kite session token. Safe to delete, it refills. |

Your trades, your notes and your settings live in the browser's own storage, not in this folder.
Clearing site data for localhost clears them, and a different browser starts fresh.

Only equity tradebooks are supported. Futures and options exports have a different shape and are
skipped with a message.

## Sharing this with someone

They do **not** need the Kite MCP, a Kite Connect API key, a paid subscription, Claude, or any
developer setup. All they need is a Zerodha account and Python 3. `serve.py` talks to Zerodha's
own hosted service directly, and they authorise it by logging in the normal way, in a browser.

### What to send

Four files, and nothing else:

```
index.html
serve.py
start.command
README.md
```

**Never send the `cache/` folder.** It holds your Kite session token. Zip the four files, or send
the folder only after deleting `cache/`.

### What they do

1. **Install Python 3** if they do not have it. macOS and Linux already do. Windows users get it
   from python.org and must tick "Add Python to PATH" during install.
2. **Put the four files in a folder** anywhere, for example Documents/trades.
3. **Start it.** On a Mac, double-click `start.command`. The first time, macOS may block it: right-click
   the file, choose Open, then confirm. On Windows or Linux, open a terminal in that folder and run
   `python3 serve.py`, then open http://localhost:8770 in a browser.
4. **Download their tradebook.** At [console.zerodha.com](https://console.zerodha.com/reports/tradebook)
   go to Reports, then Tradebook, choose segment **Equity**, pick a date range, and download CSV.
   Zerodha allows one year per file, so one file per year they want to see.
5. **Drag those CSV files onto the page.** It shows what it read. They click "Use this data".
   At this point the trade table, realized profit, holdings and holding periods already work.
6. **Click "Connect Kite".** A Zerodha login page opens in a new tab. They log in to their own
   account and approve. The app notices and continues on its own.
7. **Let the prices download.** A progress bar runs through their instruments. Budget roughly five
   to fifteen minutes the first time, depending on how many stocks they have traded. After that only
   new days are fetched, which takes seconds.
8. **Done.** Next time they just double-click `start.command` again. Their trades, notes and settings
   are remembered in their browser.

### What stays private

Their tradebook is read in their browser and never sent anywhere. The helper on their machine only
ever talks to Zerodha, using their own login. You cannot see their trades and they cannot see yours.

### If something goes wrong

| Symptom | Cause and fix |
|---|---|
| "Charts need the helper" | `serve.py` is not running. Start it and reload the page. |
| The port is already in use | The app is already running. Just open http://localhost:8770. |
| macOS refuses to open start.command | Right-click the file, choose Open, then confirm once. |
| "python3: command not found" on Windows | Try `py serve.py`, or reinstall Python with "Add to PATH" ticked. |
| Login link does nothing | Their Zerodha session may have expired. Click Connect Kite again. |
| A stock shows `○` and no chart | It is delisted, merged or expired, so no price history exists. Its trades still show in the table. |

## Disclaimer

This is a personal tool for reviewing your own past trades. It is not investment advice, it does
not place orders, and the numbers it shows are reconstructed from your tradebook rather than taken
from a broker statement. Check anything that matters against Zerodha Console.

MIT licensed. Not affiliated with or endorsed by Zerodha.
