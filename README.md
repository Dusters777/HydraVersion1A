# Hydra

A capital-rotation dashboard that runs on GitHub Pages. It ranks your watchlist by what
hasn't taken off yet and how much upside your own price targets imply, flags assets that
are stretched above or below their normal range, and lets you paper-trade rotations.

## Files

| File | What it does |
| --- | --- |
| `index.html` | The whole website. |
| `fetch_prices.py` | Pulls prices and one year of history from Yahoo Finance (yfinance). |
| `symbols.txt` | The Yahoo symbols to fetch. Edit this to add stocks, ETFs or futures. |
| `.github/workflows/update-prices.yml` | Runs the fetcher about every 15 minutes and saves `data/prices.json`. |

## Setup

1. Create a new **public** repository on GitHub.
2. Add the four files above. For each one: **Add file → Create new file**, type the file
   name exactly as shown (for the workflow, type the full path
   `.github/workflows/update-prices.yml` and GitHub creates the folders), paste the
   contents, and commit.
3. **Settings → Pages →** Source: *Deploy from a branch*, Branch: `main`, folder `/ (root)`. Save.
4. **Settings → Actions → General → Workflow permissions:** choose *Read and write permissions*. Save.
5. **Actions tab → Update Hydra prices → Run workflow.** After about a minute a
   `data/prices.json` file appears in the repo.
6. Open `https://YOUR-USERNAME.github.io/YOUR-REPO/`.

## Where prices come from

| Source | Covers | Freshness |
| --- | --- | --- |
| Yahoo Finance, via the workflow | Stocks, ETFs, futures, option chains, history for indicators | Delayed, about every 15 min |
| CoinGecko, in the browser | Crypto | Live, every 60 s |
| gold-api.com, in the browser | Silver and gold spot | Live, every 60 s |
| open.er-api.com, in the browser | Official exchange rates | Daily |
| DEX Screener, in the browser | Backup for tokens with a contract address | Live, every 60 s |

## Backups

- **Stocks, ETFs, futures:** if Yahoo fails for a symbol, the workflow retries it through
  Yahoo's chart service, then Stooq, then Finnhub. A price-only backup keeps the saved
  history, so indicators keep working.
- **Finnhub (optional, recommended):** get a free key at finnhub.io, then in your repo go to
  **Settings → Secrets and variables → Actions → New repository secret**, name it
  `FINNHUB_API_KEY`, and paste the key. Nothing else to change.
- **Crypto:** tokens with a DEX Screener contract address switch to it when CoinGecko has
  no price. Set the address under **Edit** as `chain:address` (for example `bsc:0x…`).
- **Silver and gold spot:** the futures price stands in if the spot feed is down.

Any price coming from a backup is labelled "backup" on the site.

Every price on the site is labelled with its source and how fresh it is.

## Adding assets

Use **Assets & targets → Add asset** on the site. Crypto works straight away with a
CoinGecko id. For a stock, ETF or future, also add its Yahoo symbol to `symbols.txt`;
**Settings → Data sources** lists any symbol that is missing.

## Good to know

- Targets, weights, holdings and the rotation log are saved in your browser only.
  Use **Settings → Export settings** to back them up or move them to another device.
- GitHub pauses scheduled workflows in repos with no activity for 60 days. If prices stop
  updating, open the Actions tab and re-enable the workflow.
- Hydra is a research and paper-trading tool. Targets are your own assumptions and
  signals are prompts to look closer, not advice to trade.
