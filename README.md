# The $100 Race

Pretend money only. Five rule-based lanes each start with $100 and trade for 30 days using live prices from
Kraken, Coinbase, Alpaca and Yahoo, with news and market mood from free sources. A GitHub job runs every
5 minutes. Nothing here logs in to a broker or places a real order.

## Lanes
| Lane | Rules | Checks |
|---|---|---|
| A | Holds the 2 strongest of SPY, QQQ, IWM, GLD, TLT, XLE, Bitcoin (60-day momentum, above 50-day average) | Rotates Mondays 10:00 ET, sells anything below its 50-day average at 3:45 ET |
| B | Same as A, plus a news check before each buy and careful mode on big event days | Same as A |
| C | Buys stocks and funds breaking their 2-day high; stop-loss 3x recent range (trails up), take-profit 5x, 72-hour limit | Buys hourly 10:00 to 3:00 ET, exits checked every 5 minutes, stops new buys after a 3% down day |
| D | Holds Bitcoin while it is above its 50-day average with rising 60-day momentum | Daily 9:00 ET |
| E | Holds the 2 strongest of 8 coins | Sundays 9:00 ET |
| E RH | Lane E priced at Robinhood's crypto fee, to show the fee cost | Same as E |
| F | Aggressive: holds the 2 strongest of all funds, 10 single stocks and 8 coins on 20-day momentum. No trend exit | Rotates Mondays 10:00 ET |

Benchmarks: hold SPY, hold Bitcoin. Fees: Kraken limit order 0.25%, Robinhood crypto 0.95%, stock spread 0.03%.

## Setup
1. Create a public GitHub repo and upload everything in this folder, including the hidden `.github` folder.
2. Settings > Pages: deploy from branch `main`, folder `/ (root)`.
3. Settings > Secrets and variables > Actions: add the secrets below.
4. Actions tab: enable workflows, open "race", click "Run workflow". The race clock starts on the first run.
5. Your dashboard: `https://YOUR-USERNAME.github.io/REPO-NAME/`

| Secret | Needed for | Required |
|---|---|---|
| NTFY_TOPIC | Phone alerts. Use a long random name, anyone who knows it can read the alerts | Yes |
| ANTHROPIC_API_KEY | AI news check and morning brief for lanes B and C. Set a $5 monthly limit in the Anthropic console | Recommended |
| ALPACA_KEY, ALPACA_SECRET | Benzinga news and live stock quotes (free Alpaca account) | Recommended |
| ALPHAVANTAGE_KEY | Scored news mood (free key) | Recommended |
| SEC_CONTACT_EMAIL | SEC asks automated readers to include a contact email | Optional |
| FORM_URL | I did it and Skip buttons on phone alerts (Google Form pre-filled link with ALERT_ID and CHOICE) | Optional |
| STATUS_URL | Marks alerts Done or Skipped from your private sheet (see google_sheet_script.gs) | Optional |

Without the optional keys the race still runs: the news check falls back to a free keyword screen.

## Settings
Edit `race/config.json`. `alert_mode` is `signal`: lanes listed in `alert_lanes` (A, B, D, E, F) send
"rules say buy or sell" alerts with I did it and Skip buttons. Lane C and the Robinhood-fee copy of lane E
keep trading pretend money only. Set `alert_mode` to `report` to turn signals off.

## Google Form and Sheet
1. Form questions, with these exact titles: `Alert ID` (short answer), `Choice` (multiple choice: Done, Skipped),
   then any of your own: Fill price, Dollars, Platform, Notes.
2. Link the form to a Sheet. The responses tab must be the first tab.
3. Form menu > Get pre-filled link. Type `ALERT_ID` in Alert ID, pick Done, copy the link, and change `Done`
   at the end to `CHOICE`. Save it as the `FORM_URL` secret, and paste it under My trades on the dashboard.
4. In the Sheet: Extensions > Apps Script, paste `google_sheet_script.gs`, then Deploy > New deployment >
   Web app, execute as you, access Anyone. Save the web app link as the `STATUS_URL` secret. It only shares
   alert ids and Done or Skipped.

## Testing
`python tests/replay.py BARS.json OUT_DIR 2026-08-16T12:00:00 30 1800` replays past prices through the full program.
`python tests/check_dashboard.py URL` clicks through every tab and control and prints pass and fail counts.
