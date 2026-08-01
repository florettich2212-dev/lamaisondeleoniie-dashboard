#!/bin/zsh
# Refresh the dashboard data and publish it.
#
# Instagram blocks GitHub Actions runners, so live data can only be fetched
# from this machine. The hourly Action only re-renders whatever cache is
# committed — without this script the site silently goes stale while still
# showing a current "Updated" timestamp.

set -u

REPO=/Users/flo/instagram_dashboard
LOG=$REPO/refresh.log

cd "$REPO" || exit 1
export PATH=/usr/bin:/bin:/usr/sbin:/sbin

DATA_FILES=(full_posts_cache.json history.json posts_cache.json)

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') refresh"

  python3 generate.py 2>&1 | grep -v -e NotOpenSSLWarning -e 'warnings.warn'

  if git diff --quiet -- $DATA_FILES; then
    echo "no data changes — nothing to publish"
  else
    git add $DATA_FILES dashboard.html
    if git commit -q -m "data refresh $(date '+%Y-%m-%d')"; then
      if git push -q origin main; then
        echo "pushed — site redeploys on the next Action run"
      else
        echo "PUSH FAILED — commit is local only"
      fi
    else
      echo "COMMIT FAILED"
    fi
  fi
} >>"$LOG" 2>&1

# keep the log from growing without bound
tail -n 2000 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
