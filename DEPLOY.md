# Deploying the website (free, public HTTPS)

Target: **Render free web service**, defined in `render.yaml` (`plan: free`, no secrets).

Free-plan facts from Render's docs (checked November 7, 2026): 512 MB memory and 0.1 CPU; the service sleeps after 15 minutes without visitors and takes about a minute to wake; 750 free instance hours per month; it may restart at any time, which clears every visitor's in-memory data; no persistent disk. Heavy calculations run slower than on a PC because of the 0.1 CPU share.

Render deploys from a Git repository. The `.gitignore` keeps personal files out: `results/` (your tickets and logs), `output/` and `.venv/`.

## One-time setup

1. **GitHub account** (free) at github.com, signed in by you.
2. **Put this folder in a GitHub repository.** Either:
   - with Git and the GitHub CLI installed: `gh auth login` (you sign in yourself in the browser), then from this folder:
     ```
     git init
     git add .
     git commit -m "BCLC lottery planner"
     gh repo create bclc-lottery-planner --public --source . --push
     ```
   - or create an empty repository on github.com and use "uploading an existing file" to drag in everything except `results`, `output` and `.venv`.
3. **Render account** (free) at render.com: sign in with GitHub.
4. In Render choose **New > Blueprint**, pick the repository, and approve. Render reads `render.yaml`, installs `requirements.txt`, and starts `uvicorn webapp.server:app --host 0.0.0.0 --port $PORT`. Check that the plan shown is **Free** before confirming; no payment method is needed for it.
5. When the deploy is live, Render shows a URL like `https://bclc-lottery-planner.onrender.com`. Open `/healthz` on it; it should return `{"ok": true, ...}`.

## Updating

Change the code (or `examples/current_draw_snapshot.json` for new dated jackpots), run the tests, commit and push. `autoDeploy: true` makes Render redeploy automatically.

```
.venv\Scripts\python -m unittest discover -s tests -v
git add .
git commit -m "Update draw snapshot"
git push
```

## Settings (optional environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `BCLC_WORKERS` | 2 | Calculations that run at the same time |
| `BCLC_TIER_SECONDS` | 20 | Time limit per odds tier before proven bounds are shown |
| `BCLC_WORK_LIMIT` | 200000 | Counter-state limit per odds tier |
| `BCLC_SESSION_IDLE_SECONDS` | 3600 | Idle time before a visitor's uploads are deleted |
