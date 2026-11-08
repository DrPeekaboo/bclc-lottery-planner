# Verification

Re-verified November 7, 2026 on Windows 11 with Python 3.14.6 (`py -3`), standard library only. Earlier wording in this file named Python 3.12.14; that interpreter is not installed on this machine, so those claims were re-run here.

## Rules

Checked November 7, 2026 against the rendered PlayNow Lotto Max and Lotto 6/49 pages and the WCLC draw pages:

- Lotto 6/49: $3 per play, 6 numbers from 1-49, Classic jackpot $5M (win or share), tiers 5/6+bonus, 5/6, 4/6 (pool shares), 3/6 $10, 2/6+bonus $5, 2/6 $3 Free Play.
- Gold Ball: one 10-digit number per $3 play; exact match only; drum starts at 30 balls (29 white = $1M, 1 gold = jackpot from $10M, +$2M per draw, up to $68M); prizes never shared; odds are one out of all Gold Ball numbers issued for that draw.
- Lotto Max: $6 per play = four 7-number lines from 1-52; the player picks the first line only ("The remaining sets will be Quick Pick"); jackpot up to $90M; tiers 6/7+bonus to 4/7+bonus (pool shares), 4/7 $20, 3/7+bonus $20, 3/7 $6 Free Play.
- MAXMILLIONS: $1M each, exact 7/7 match, win or share, only on jackpots of $50M or more.
- MAXPLUS: $100,000 each, exact 7/7 match, win or share, one per $1M of jackpot, up to 90.
- The BCLC game-conditions PDFs could not be opened by the automated check (HTTP 403 or a forced download), so they were not re-read.

## Commands run and results

- `py -3 lottery_tool.py --help` and each subcommand's `--help`: exit 0. The top-level help now describes all five commands.
- `py -3 -m unittest discover -s tests -v`: 38 tests, all pass (20 original + 8 audit tests + 10 results-checker tests, including a clear message when Excel has the log open).
- Results checker: parsed the live WCLC 6/49 page (8 draws, Oct 10 to Nov 4, 2026, including 75 Super Draw prizes on Oct 31); the Nov 4, Oct 31 and Oct 28 numbers match an independent read of the same page. A live `check` run wrote the cache and a one-row log for 2026-11-07 (draw pending). Exit 0. The added tests reproduce every published PlayNow odds figure from the encoded rules, enforce the MAXPLUS count and $90M cap, check Gold Ball "never shared" labelling, reject non-`YYYY-MM-DD` and future history dates, check top-level help, list uncovered triples in a partial wheel report, and check a $17.99 Max budget (2 plays, $5.99 unspent, 6 pending Quick Picks).
- $30 Lotto 6/49 demo (no history): 10 lines, max overlap 1, 48 numbers and 150 pairs; both coverage upper bounds attained; all tiers exact. Exit 0.
- $30 Lotto Max demo (no history): 5 chosen lines, 15 pending Quick Picks, max overlap 0, 35 numbers and 105 pairs; all chosen-line tiers exact; pre-issue figures labelled as the IID Quick Pick model. Exit 0.
- $180 wheel, 13 favourites, overlap explicitly relaxed: all 286 triples verified covered with 56 plays ($168, $12 unspent); not claimed minimum. Exit 0. The same request without `--allow-wheel-overlap` is refused with the divisibility obstruction (exit 2).
- One-play synthetic Max receipt with the November 10 snapshot: jackpot 1 in 33,446,140 (matches PlayNow), MAXPLUS series exact. Exit 0.
- Twenty-line synthetic Max receipt, default work limit: 7 of 9 tiers exact; 4/7 and 3/7 reported as EXACT COUNT NOT FINISHED with valid bounds. Exit 4.
- Same receipt with `--exact-work-limit 0`: all 9 tiers exact after 1,869.5 seconds (about 31 minutes) at a steady ~58 MB. Exit 0. The two previously unfinished tiers fall inside their reported bounds: 4/7 = 405735779/6020305200 (6.74%; bounds 0.35% to 6.93%) and 3/7 = 352470059/668922800 (52.69%; bounds 3.55% to 71.03%).
- $3 Lotto 6/49 plan (one line, no history): 1 14 30 32 43 45; passes every filter on independent re-check; cost $3.00, unspent $0.00; all seven tiers exact. Exit 0.
- Synthetic 60-draw 6/49 history: all four Monte Carlo chi-square tests non-significant after Holm correction. Exit 0. No real history was supplied or analyzed.
- Week-of-November-2 comparison: Gold Ball probability stays unknown without national sales; November 6 Max marked closed; November 10 kept in its own week; sharing rules printed per category. Exit 0.
- Error paths: receipt over budget, Max plan with additional-draw snapshot, missing history without the explicit flag, and a 3-decimal budget each exit 2 without writing output.

## Website (local only, November 7, 2026)

- Tests: `.venv\Scripts\python -m unittest discover -s tests -v` ran 47 tests, all pass (38 engine/CLI/results + 9 website tests). With plain `py -3` (no web packages) the website tests skip and the other 38 pass.
- Browser checks on a local server (http://localhost:8770, built-in browser): rules dashboard; $30 6/49 plan (10 plays, $0.00 left, all tiers exact, identical lines to the CLI); CSV checklist download (11 rows); synthetic 400-draw history upload and analysis with 4 charts; wheel constraint warning before building, refusal without overlap relaxation, NO GUARANTEE at $30 (173/286 triples) and VERIFIED cover at $180 (56 plays, $168.00); plan odds with a dated Gold Ball scenario (56 plays / 5,000,000 = 1 in 89,286); synthetic Lotto Max receipt upload with Nov 10 MAXPLUS odds; week comparison; readable error for a 3.333 budget; progress display and Cancel on a 4,999-replicate analysis; no horizontal scrolling at 375 px on any tab; no server errors.
- Production start command (`uvicorn webapp.server:app --host ... --port $PORT` from the project root): `/healthz` and `/` return 200.
- Not yet deployed: no public URL exists until the Render steps in DEPLOY.md are completed.

## Live draw information (version 1.2.0, November 7-8, 2026)

- New `bclc/draws.py` reads WCLC's next-jackpot box and both results pages. On the real pages at 22:26-22:40 BC it produced 18 dated draws (Oct 13 to Nov 11) with no notes. Lotto Max jackpots from MAXPLUS counts matched the known history (record $90M around Oct 16-20 with 18-20 MAXMILLIONS; $50M with 2 MAXMILLIONS on Nov 6; $10M announced for Nov 10).
- WCLC's 6/49 box still showed the finished Nov 7 draw; the module derived Nov 11 = 16 balls / $38M from the Nov 7 white ball. WCLC's own box later showed exactly 16 balls / $38M for Nov 11.
- Tests: 59 pass with the web packages (10 new draw-builder tests on synthetic WCLC-layout pages, 2 new cache/fallback tests); the plain CLI suite passes with website tests skipped. Tests never use the network.
- Local browser check (http://localhost:8770): Dashboard "Next draws" shows only undrawn draws with published/derived basis; Odds tab lists live dated draws newest first with "(drawn)" labels; week of Nov 9 comparison now has both Nov 10 and Nov 11 draws; no page overflow at 375 px; no server errors.

## Ticket checker (version 1.3.0, November 8, 2026)

- New `bclc/tickets.py` and endpoints `/api/results/{game}` and `/api/check-ticket`; Lotto Max results now keep the MAXMILLIONS/MAXPLUS winning sets and per-draw prize-breakdown links.
- Tests: 69 pass with the web packages. Tier decisions are cross-checked on 3,000 random 6/49 lines against the separate results-checker classifier and on 3,000 random Lotto Max lines against an independent statement of PlayNow's table; every tier occurs.
- Local browser check against live official results: Ken's Nov 7 line 1 14 30 32 43 45 -> 0 matched, no prize; Nov 7 numbers -> 6/6; 6 9 15 22 40 41 -> 2/6+bonus $5; Gold Ball 42827458-02 -> $1,000,000 match; Lotto Max Nov 6 numbers -> 7/7 and 6 7 25 35 39 42 47 -> MAXMILLIONS match; 5-number Max line -> readable error; no overflow at 375 px; no server errors.

## Accuracy audit of the live site (version 1.3.0, November 8, 2026)

Independent of the project's own tests:
- Rules: every price, number range, draw time, Gold Ball rule and all 16 prize tiers with published odds on the live site match PlayNow's rendered Lotto 6/49 and Lotto Max pages (re-read November 8), including "Pick seven numbers you like for the first set. The remaining sets will be Quick Pick."
- Exact odds: a separate brute-force program counted all 601,304,088 Lotto 6/49 outcomes (13,983,816 main draws x 43 bonus numbers) for the live $30 plan; all 7 tier probabilities equal the site's exact fractions.
- Plans: a separate re-implementation of the line rules confirmed the live $30 6/49 plan (10 lines, max overlap 1, $0.00 left) and $17.99 Max plan (2 plays, $5.99 left, 6 pending Quick Picks); the live $180 wheel covers all 286 favourite triples for $168.00; the $30 wheel's 113 uncovered triples are exact; the strict wheel is refused.
- Live data: Nov 7 6/49 numbers, bonus, Gold Ball winner 42827458-02 and next Gold Ball jackpot $38M match PlayNow; Nov 6 Lotto Max numbers and 2 MAXMILLIONS / 50 MAXPLUS match PlayNow; Nov 3 Lotto Max numbers match Narcity; derived Lotto Max jackpots ($85M Oct 13, $90M Oct 16-20, $20M Oct 23, $40M Nov 3, $50M Nov 6) match news reports.
- Dates: BC stays on UTC-7 from November 2026 (B.C. government and news reports); Lotto Max's 1-50 field began May 14, 2019 and its first draw was September 25, 2009, as the history checker assumes.

## Plain-language wording (version 1.3.1, November 8, 2026)

- Reworded visitor-facing notes without changing their meaning: comparison timing ("Not yet drawn when this information was read: check PlayNow's sales cutoff before buying"), the saved-data note, the Quick Pick model notes, the odds definition, the slip-column assumption, the no-history warning, the number-rules note and the wheel explanations. No calculation changed.

## Not verified

- PlayNow's on-screen number-pad layout (it sits inside the logged-in purchase flow). The 7-column grid remains an assumption; use `--slip-columns` or `--slip-map`.
- Any reduction in prize sharing from the popularity filters (no player-selection data exists here).
- National Gold Ball sales for any draw.

Examples are test inputs and checklists, not recommendations or purchased tickets. Exact numerical odds do not establish reduced prize sharing or predictive ability.
