# BCLC lottery analyst and ticket planner

Python 3.10+ on Windows. No packages, account access, API keys, or purchases. All budgets apply to **one draw**, in CAD, excluding Extra.

**Rules checked November 7, 2026** against the rendered PlayNow [Lotto Max](https://www.playnow.com/lottery/lotto-max/) and [Lotto 6/49](https://www.playnow.com/lottery/lotto-649/) pages: number pools, prices, the four-line Max play, which lines you choose, every prize tier and its published odds, bonus treatment, Gold Ball, MAXMILLIONS and MAXPLUS. The official BCLC game-conditions PDFs could not be opened by that automated check (HTTP 403 or a forced download), so they were not re-read; PlayNow states the official BCLC records prevail if anything differs. The test `VerifiedRulesTests` reproduces every published PlayNow odds figure from the encoded rules.

## 1. Start on Windows

1. Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/windows/). Include the Python launcher during installation.
2. Extract the ZIP, keeping the `bclc` and `examples` folders beside `lottery_tool.py`.
3. Open that folder in File Explorer, type `powershell` in the address bar, and press Enter.
4. Check Python and view the commands:

   ```powershell
   py -3 --version
   py -3 lottery_tool.py --help
   ```

5. Double-click `run.bat` for a simple game/budget/history prompt, or use the commands below. If your installation has `python` instead of `py`, replace `py -3` with `python`.

## 2. Supply your history CSV

Copy the appropriate empty template from `examples` to `my_649.csv` or `my_max.csv`. Add real **main-draw** results, one row per date, with the main numbers quoted:

```csv
date,numbers,bonus
2026-11-04,"1 3 22 28 36 41",48
```

This single formatting example is the November 4 Lotto 6/49 result from [WCLC](https://www.wclc.com/winning-numbers/lotto-649-extra.htm); it is not a useful statistical sample. Alternatively use `date,n1,n2,n3,n4,n5,n6,bonus`, adding `n7` for Max. Dates must be ISO `YYYY-MM-DD`. Spaces, commas, semicolons, pipes, and square brackets are accepted inside a quoted numbers field. Order within a line does not matter.

Do not combine games, additional prize draws, or multiple main results for the same date. Invalid/duplicate numbers, bonus collisions, duplicate dates, dates not written `YYYY-MM-DD`, future dates, missing values, and numbers outside the rule-era range produce a specific error. Templates intentionally contain no fabricated historical results.

Run the analysis:

```powershell
py -3 lottery_tool.py analyze --game 649 --history my_649.csv --out output/history649
py -3 lottery_tool.py analyze --game max --history my_max.csv --simulations 4999 --out output/historymax
```

Outputs: `analysis.txt` and structured `analysis.json`. Reports include every main number's frequency; separate bonus frequencies; last appearance; gaps in supplied draws and calendar days; the distribution of odd/even and high/low splits; and sum bands, minimum, mean and maximum. Low means `1..floor(N/2)`. A gap of zero means the latest supplied draw. `>=D` means never observed in the D supplied draws; it does not imply D complete historical draws.

Lotto Max history is separated into 1–49 before May 14, 2019; 1–50 through April 10, 2026; and 1–52 from April 14, 2026. Frequencies and gaps are reported within each regime, so new numbers are not incorrectly declared overdue. Missing draw dates are not filled or silently interpreted as losses.

The four Pearson chi-square statistics test number frequencies, odd/even splits, high/low splits and 25-point sum bands. **P-values are Monte Carlo calibrated under uniform draws without replacement**, including sparse cells. A naive multinomial test of all drawn numbers has the wrong reference distribution because numbers within a draw are dependent. Holm correction controls multiple comparisons across all four tests and every regime. `--seed` makes results reproducible. The default 1,999 replicates have minimum p-value 0.0005; use more for greater resolution. Reports distinguish statistical evidence from ordinary variation and never turn a past frequency into a next-draw prediction. A nonsignificant result does not establish perfect randomness; a significant result needs data checks and independent replication.

## 3. Generate a budgeted coverage plan

```powershell
py -3 lottery_tool.py generate --game 649 --budget 30.00 --history my_649.csv --out output/649
py -3 lottery_tool.py generate --game max --budget 30.00 --history my_max.csv --out output/max
```

For an explicitly history-free demonstration:

```powershell
py -3 lottery_tool.py generate --game 649 --budget 30 --allow-missing-history --out output/demo
```

Without history, the report explicitly says past-combination exclusion is unavailable. With history, it excludes exact main-number combinations in **that CSV only**. It cannot promise to exclude every historical result from an incomplete archive. Excluding an old result does not make another result more likely.

Each generated chosen line must:

- Have at least three numbers above 31.
- Contain no run of three consecutive numbers and no three-term arithmetic subsequence, even when the three values are not adjacent in the sorted line.
- Span more than one decade, defined as 1–10, 11–20, etc., and more than one column in the configured slip model.
- Contain at most one multiple of seven, and at most one number containing the digit seven.
- Avoid every past main jackpot combination present in the supplied history.

The column default is an **assumed seven-column ascending row-major grid**, not a verified PlayNow layout. Set `--slip-columns N` to match your grid, or provide `--slip-map columns.json` with every number mapped to its actual column label, such as `{"1":"A","2":"B",...}`. An ellipsis is explanatory, not valid JSON. Verify the current number-pad/slip layout before relying on this geometric filter.

All chosen coverage lines are unique, and any two share at most two numbers. The randomized search prioritizes distinct numbers and distinct unordered pairs, prefers the minimum necessary high-number slots to preserve low-number coverage, then balances usage. `--candidates 2000 --restarts 20` explores more candidates. The report includes mathematical upper bounds: attaining both certifies maximal coverage at that line count; otherwise optimality is unproven. If search cannot fill the requested plays while preserving constraints, it spends less and reports the remainder. Money is parsed as exact cents, never floating-point rounded up. A run supports at most 500 paid plays.

Outputs:

- `tickets.csv`: play/line checklist, chosen numbers, pending Quick Pick positions, and cost charged once per paid play.
- `report.txt`: readable table, total/remainder, coverage, constraints and odds.
- `report.json`: reproducible chosen lines, CSV hash, exact probability fractions, assumptions and audit results.
- `receipt_template.csv`: fill in actual issued Quick Picks for the receipt workflow below.

CSV is for manual entry and checking; PlayNow bulk upload support is not claimed. Popularity rules are your requested heuristics. No BCLC player-selection dataset was provided, so a reduction in prize splitting is **not measured or guaranteed**. All valid lines have the same draw probability.

### Lotto Max: four issued lines, one chosen line

The $6 play contains one chosen line and three automatically issued Quick Picks. Thus $30 buys five chosen lines plus fifteen Quick Picks, not twenty lines that you can all specify for $30. PlayNow's Lotto Max page (checked November 7, 2026) says: "Pick seven numbers you like for the first set. The remaining sets will be Quick Pick."

The tool enforces constraints on the chosen lines. It cannot control patterns, duplicates or intersections in unissued Quick Picks. It gives exact fixed-line odds for the chosen set, and separately labels the complete pre-issue **IID Quick Pick model**. That model assumes uniform independent Quick Picks with replacement; the operator's exact duplicate handling is not assumed known. Import the actual receipt to remove that model assumption. Do not buy extra plays to replace unwanted Quick Picks under the same budget.

## 4. Favourite-number wheel with a verified guarantee

For all 10–15-number pools requested here, a complete triple cover and pairwise overlap of at most two are mathematically incompatible. With that overlap, every triple could occur in only one block; the necessary Steiner-system divisibility conditions fail. The tool gives a concrete divisibility obstruction if you request both.

Explicitly relax the overlap restriction in wheel mode:

```powershell
py -3 lottery_tool.py wheel --game 649 --budget 180 --favourites "2 4 8 9 22 24 30 31 33 34 43 45 48" --allow-wheel-overlap --allow-missing-history --out output/wheel
```

For actual use, replace `--allow-missing-history` with `--history my_649.csv` and choose your own 10–15 favourites. This example pool is a software demonstration, not a prediction. The popularity filters remain active. Some favourite lists make a guarantee impossible: for example, a three-number arithmetic progression cannot be covered while all lines forbid that progression. Such uncoverable triples are listed.

The greedy set-cover search constructs an abbreviated cover and checks **every** `C(v,3)` favourite triple against the exported lines. A reported `VERIFIED MATCH-3-OR-BETTER COVER` guarantees at least one line matches three or more main numbers whenever at least three favourites are drawn, conditional on entering all those chosen lines in the same draw. This is a coverage guarantee, not a guaranteed winning draw. A Max match-3 can be a free play. Bonus numbers do not count toward the condition.

If the found full cover costs too much, the export is prominently marked **NO GUARANTEE**, remains within budget, lists uncovered triples, and exits with code 3. The cost of a found full cover and a counting lower bound are reported; the found cost is not claimed to be the minimum possible. No extra money is spent automatically. A Max wheel costs $6 per chosen block, with three accompanying Quick Picks per block.

## 5. Exact odds for the entire issued portfolio

Fill in all four lines of each Max play in a copy of `receipt_template.csv`, then run:

```powershell
py -3 lottery_tool.py odds --game max --receipt my_receipt.csv --budget 30 --out output/receipt_odds
```

Or try the clearly labelled synthetic one-play fixture:

```powershell
py -3 lottery_tool.py odds --game max --receipt examples/receipt_max_SYNTHETIC.csv --budget 6
```

Receipt columns are `play,line,numbers`. All expected positions must be present. Receipt duplicates are reported, not discarded from the expected number of winning lines; they do not increase the probability of at least one win. The overlap audit covers the actual issued lines. To audit previous combinations too, add `--history my_max.csv`.

Every main-draw tier is implemented, including bonus distinctions and free plays. The report means **at least one line in that exact tier**, not the expected count, a sum of single-line probabilities, or the probability of exactly one winner. Multiple tiers can occur in the same draw; do not add their probabilities. Exact rational numerators and denominators are in JSON; displayed inverse odds are rounded. Expected winning-line counts are separate.

The exact counter groups numbers by ticket membership, uses integer binomial weights, and counts the union of dependent events. The outcome space is `C(N,k)` for main-only events and `C(N,k)*(N-k)` when a bonus matters. For distinct jackpot lines the exact probability is `L/C(N,k)`.

Large overlapping portfolios can make exact lower-tier union counting expensive. The default limit is 200,000 counter states per tier. A stopped count is marked **EXACT COUNT NOT FINISHED**, with rigorous probability bounds, never an approximation labelled exact. The saved lines remain available. Remove the work limit to finish the exact enumeration, allowing more time and memory:

```powershell
py -3 lottery_tool.py odds --game 649 --plan output/649/report.json --exact-work-limit 0 --out output/exact649
py -3 lottery_tool.py odds --game max --receipt my_receipt.csv --exact-work-limit 0 --out output/exactmax
```

No finite runtime or memory bound is promised for arbitrary portfolios. Future free-play winnings, Extra, and temporary promotions are outside the one-draw calculation.

The chosen-line $30 demos finish all tiers exactly; a twenty-line synthetic Max receipt also demonstrates the work-limit case. That larger receipt can require an unlimited run for some lower tiers. The `exact_all_main_tiers_complete` JSON flag and exit code 4 make an unfinished calculation machine-readable. Plans are saved before counting so cancellation does not lose chosen lines.

For dated Gold Ball/MAXMILLIONS/MAXPLUS odds, add `--snapshot my_snapshot.json --draw-date YYYY-MM-DD`. Gold Ball requires national `gold_issued`; Max additional-draw odds require all actual issued lines. Independent full-number additional draws are combined as `1-(1-L/C(52,7))^D`, allowing repeats between separate draws. MAXPLUS is included even though its normal prize is $100,000.

Snapshot Max entries are checked against the published rules: the jackpot must be within the $90M cap, MAXMILLIONS need a jackpot of at least $50M, and MAXPLUS must equal one $100,000 prize per $1M of jackpot, up to 90.

## 6. Compare million-dollar categories this week

```powershell
py -3 lottery_tool.py compare --budget 30 --snapshot examples/current_draw_snapshot.json --week-of 2026-11-02
```

For current draws, first save a fresh snapshot from WCLC's public pages, then compare with it:

```powershell
py -3 lottery_tool.py draws --out output/draw_snapshot.json
py -3 lottery_tool.py compare --budget 30 --snapshot output/draw_snapshot.json
```

`draws` reads the next-jackpot box and both results pages. Lotto Max jackpots come from each draw's published MAXPLUS count (one $100,000 prize per $1M of jackpot); Gold Ball ball counts follow the official rules (a white ball is removed and adds $2M; the gold ball resets to 30 balls and $10M), only along consecutive draws. Anything that cannot be established, such as a $50M+ jackpot whose MAXMILLIONS count is not shown, is left out and listed as a note. Each draw records whether its value was published or derived. BC time is taken as UTC-7.

The included snapshot is dated **November 7, 2026**, and is deliberately not a live feed. It records the November 7 Gold Ball draw with 17 balls, the already-closed November 6 Max draw with two MAXMILLIONS, and the next Max draw on November 10 with no MAXMILLIONS. Dates in different calendar weeks are never silently treated as the same week. Past dates are marked closed relative to the snapshot. Add other verified draws to compare an entire week's schedule; the file does not claim to contain every draw.

The national Gold Ball entry count is `null` because it is not known before sales close. The tool preserves that uncertainty and gives a conditional break-even threshold rather than inventing sales or declaring an exact winner. You can supply a final count, or an explicitly hypothetical sales scenario:

```powershell
py -3 lottery_tool.py compare --budget 30 --snapshot examples/current_draw_snapshot.json --week-of 2026-11-02 --gold-issued 5000000 --out output/sales_scenario
```

**Five million here is an example assumption, not observed sales.** Gold Ball's any-award probability for `a` entries is `a/G`; exact $1M is `(a/G)*(B-1)/B`; the gold jackpot is `a/(G*B)`. Gold Ball awards either $1M or its larger jackpot each draw. The Classic jackpot is a separate category.

For perspective, for a single Max play under the labelled IID Quick Pick model, the Gold Ball **alone** has a higher million-dollar-category probability per dollar when national 6/49 sales G are below approximately **22.30 million** with two MAXMILLIONS, or **66.89 million** with none. These thresholds include Max's main jackpot; they do not include Classic in Gold Ball's side. They are conditional comparisons, not evidence that an actual $1M payout is more likely by the same factor.

The comparison separates Gold Ball, Classic jackpot, Max jackpot, MAXMILLIONS and MAXPLUS, and reports probability per actual CAD spent after flooring to whole plays. Each row allocates the full budget independently; buying every row would use multiple budgets. It does not optimize a multi-draw spending schedule. Max pre-issue figures use the explicitly labelled IID model, including duplicate possibilities and dependencies when the same Quick Picks enter multiple prize draws.

**A million-dollar category hit is not always an exact probability of receiving $1M.** Classic/Max jackpots and MAXMILLIONS are "win or share"; actual payout odds need other players' selections. Gold Ball prizes are never shared, so once the final national entry count is known, its probability is the probability of actually receiving $1M or more (reported as `gold_receive_at_least_1m`). Variable lower-tier pools can also vary. No history-only program can derive exact net-$1M payout odds or a definitive pre-sales-close Gold Ball ranking from the inputs currently available.

## 7. Daily results check (Lotto 6/49)

Record each ticket you actually buy in `results/my_tickets.csv`, one row per ticket per draw:

```csv
draw_date,numbers,gold_ball,purchased
2026-11-07,1 14 30 32 43 45,05426542-01,yes
```

`gold_ball` is the 10-digit Gold Ball number printed on the ticket (leave it blank until you have it). `purchased` is `yes`, `unconfirmed` or `no`. Then run:

```powershell
py -3 lottery_tool.py check
```

It reads the public [WCLC results page](https://www.wclc.com/winning-numbers/lotto-649-extra.htm), checks every ticket for each Classic tier, an exact Gold Ball match (white ball $1M or the gold ball jackpot) and any promotional Super Draw prize, and rebuilds `results/results_log.csv` with **one row for every day** from your first ticket to today. Non-draw days say so; tonight's draw shows as pending until results are posted. The page only lists about four weeks of draws, so every draw seen is saved in `results/official_draws.csv` and older days stay answerable. Unconfirmed tickets are labelled "only if this ticket was bought". A layout change on the results page stops the check with an error instead of guessing. Exit code 5 means the page could not be reached and only saved results were used.

A Claude desktop scheduled task named "Lotto 6/49 daily results check" runs this every morning while the app is open (or at the next launch). It is set to 9:05 AM in the app, which runs at about 10:05 AM on this computer's clock because the app and Windows currently use different UTC offsets for BC. PlayNow also checks online tickets itself; official BCLC results prevail.

## 8. Website

The same engine also runs as a website (`webapp/`, FastAPI). Every validation and calculation happens on the server; the browser only displays results. Tabs: **Dashboard** (game, budget, verified rules, history upload), **History** (frequencies, gaps, odd/even, high/low and sum charts, plain-language significance), **Generator** (filtered plan, pending Quick Picks, coverage, cost, CSV checklist), **Wheel** (10-15 favourites, constraint check before building, explicit overlap relaxation, guarantee shown only after every triple is verified) **Odds & compare** (combined-ticket odds for a plan or uploaded tickets, dated draws, Gold Ball scenarios, million-dollar comparison) and **Check ticket** (enter the lines on a ticket and pick a draw from the last few weeks; the server compares them with WCLC's official results: every prize tier with the bonus number, the 6/49 Gold Ball number and Super Draws, and Lotto Max MAXMILLIONS/MAXPLUS exact matches. Entered numbers are checked once and not saved; prize tiers come from the same verified table as the odds).

Privacy: each browser gets a random session cookie. Uploads, plans and calculations are kept in server memory only, are never written to disk, and are deleted after 60 idle minutes, on restart, or with **Delete my data**. Long calculations show progress and can be cancelled. On the website, each odds tier stops after 20 seconds (or the usual work limit) and then shows proven bounds, never an estimate; use the command line with `--exact-work-limit 0` for unlimited exact counts.

Run it locally:

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m uvicorn webapp.server:app --port 8000
```

Then open http://localhost:8000. Website tests: `.venv\Scripts\python -m unittest discover -s tests -v`. Deployment to a free public HTTPS host is described in [DEPLOY.md](DEPLOY.md). The website reads current draw information from WCLC by itself (cached for 30 minutes) and shows its source on the Dashboard ("Next draws") and in the Odds tab. If WCLC cannot be read it says so and falls back to the last live data, or to the saved snapshot in `examples/current_draw_snapshot.json`.

## 9. Verification and sources

```powershell
py -3 -m unittest discover -s tests -v
```

Tests compare the exact union counter against exhaustive toy-lottery main/bonus enumeration, enumerate Quick Pick/additional-draw outcomes independently, verify known jackpot denominators and tier partitions, reproduce every published PlayNow odds figure, and check CSV validation, regime changes, money flooring, budget boundaries, pending Quick Picks, constrained generation, triple-cover certificates, MAXPLUS/jackpot-cap rules, Gold Ball sharing labels, and unknown-sales handling. Tests use synthetic data only unless explicitly labelled otherwise.

References. "Read" means the page text was checked on November 7, 2026:

- Read: [PlayNow Lotto Max](https://www.playnow.com/lottery/lotto-max/) and [PlayNow Lotto 6/49](https://www.playnow.com/lottery/lotto-649/) (rendered pages: prize tables, odds, ways to play, Gold Ball rules).
- Read: [WCLC 6/49 results](https://www.wclc.com/winning-numbers/lotto-649-extra.htm), [WCLC Max results](https://www.wclc.com/winning-numbers/lotto-max-extra.htm), and [WCLC draw advertisements](https://www.wclc.com/play-now.htm) for the dated example snapshot.
- Not re-read (automated access refused): [BCLC Lotto Max game conditions in effect April 10, 2026](https://corporate.bclc.com/content/dam/bclccorporate/documents/terms-and-conditions/rules-and-regulations/lotto/lotto-max-game-conditions-in-effect-april-10-2026-1030pm-est.pdf), the [Lotto 6/49 game conditions currently linked from PlayNow](https://corporate.bclc.com/content/dam/bclccorporate/documents/terms-and-conditions/rules-and-regulations/lotto/lotto-649-game-conditions.pdf) and its [supplemental conditions](https://corporate.bclc.com/content/dam/bclccorporate/documents/terms-and-conditions/rules-and-regulations/lotto/lotto-649-supplemental-game-conditions.pdf).

Exit codes: 0 completed workflow; 2 invalid input; 3 wheel has no guarantee; 4 one or more exact main-tier counts hit the work limit; 130 cancelled. A valid budgeted plan is still saved for codes 3 or 4. Review per-tier status and scope in JSON.
