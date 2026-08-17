# Instagram Followers Exporter

Small, resumable follower exporter built around `subzeroid/instagrapi`.

## Why this implementation

- Uses the current authenticated mobile/private API path exposed by `instagrapi`.
- Streams followers page-by-page instead of loading the whole audience into memory.
- Saves every page to SQLite before continuing, so a failed run does not lose prior results.
- Persists the Instagram session in `state/session.json`.
- Exports CSV and/or JSONL.
- Does not include CAPTCHA bypass, account farming, proxy rotation, or other evasion logic.

## Install

```bash
cd instagram-followers
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure

```bash
export IG_USERNAME='your_login_account'
export IG_PASSWORD='your_password'
```

Use a secondary Instagram account for testing rather than a business-critical account. Credentials and session files stay local and are excluded from Git.

## Run

First smoke test with a small limit:

```bash
python ig_followers.py instagram --limit 100 --format both
```

Larger run:

```bash
python ig_followers.py target_username --limit 5000 --page-size 100
```

Full available follower list:

```bash
python ig_followers.py target_username
```

If Instagram interrupts the run, execute the same command again. The SQLite state keeps the last pagination cursor and already stored users.

Start over for one target:

```bash
python ig_followers.py target_username --fresh
```

Outputs:

- `output/<target>_followers.csv`
- `output/<target>_followers.jsonl`
- `state/followers.sqlite3`
- `state/session.json`

## Data fields

`user_id`, `username`, `full_name`, `is_private`, `is_verified`, `profile_pic_url`, `scraped_at`.

This module is useful for audience research, overlap analysis, creator/competitor intelligence, segmentation, and other internal analytics. Do not treat scraped Instagram usernames as a Meta Customer List Custom Audience: Meta's customer-list terms require the advertiser to have the necessary rights/lawful basis and, for a Meta identifier, to have obtained that identifier directly from the person.
