#!/usr/bin/env python3
"""
Instagram Dashboard Generator — @lamaisondeleoniie
Usage: python3 generate.py
  - If .env exists (run auth.py first): fetches live data from Instagram Graph API
  - Otherwise uses built-in mock data so the dashboard renders immediately
"""

import hashlib
import json
import math
import os
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

ENV_PATH          = Path(__file__).parent / '.env'
HISTORY_PATH      = Path(__file__).parent / 'history.json'
POSTS_CACHE_PATH  = Path(__file__).parent / 'posts_cache.json'
STORIES_CACHE_PATH = Path(__file__).parent / 'stories_cache.json'
OUTPUT_PATH     = Path(os.environ.get('OUTPUT_FILE', str(Path(__file__).parent / 'dashboard.html')))
API_VERSION = 'v22.0'
IG_HANDLE   = '@lamaisondeleoniie'
SITE_LABEL  = 'lamaisondeleoniie'

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_env():
    env = {}
    # Read .env file if present
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if '=' in line and not line.startswith('#'):
                k, _, v = line.partition('=')
                env[k.strip()] = v.strip()
    # Environment variables override (used by GitHub Actions)
    for key in ('IG_SESSION_ID', 'IG_CSRF_TOKEN', 'IG_DS_USER_ID', 'IG_MID', 'IG_DID', 'DASHBOARD_PASSWORD'):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def api_get(path, token, params=None, silent=False):
    p = dict(params or {})
    p['access_token'] = token
    url = f'https://graph.facebook.com/{API_VERSION}{path}?' + urllib.parse.urlencode(p)
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        if not silent:
            print(f'  API error {path}: {e}')
        return {}


def paginate(path, token, params=None, limit=100, max_items=2000):
    results = []
    p = dict(params or {})
    p.setdefault('limit', limit)
    data = api_get(path, token, p)
    results.extend(data.get('data', []))
    while 'paging' in data and 'next' in data.get('paging', {}):
        next_url = data['paging']['next']
        try:
            with urllib.request.urlopen(next_url, timeout=20) as r:
                data = json.loads(r.read())
            results.extend(data.get('data', []))
            if len(results) >= max_items:
                break
        except Exception:
            break
    return results


def fmt_num(n):
    if n is None:
        return '—'
    if n >= 1_000_000:
        return f'{n/1_000_000:.1f}M'
    if n >= 1_000:
        return f'{n/1_000:.1f}K'
    return str(n)


def now_ts():
    return int(datetime.now(timezone.utc).timestamp())


def days_ago(n):
    return int((datetime.now(timezone.utc) - timedelta(days=n)).timestamp())


def fetch_follower_history(today_count=None):
    """Load follower history from history.json, optionally recording today's count."""
    history_json = {}
    daily_total = {}
    if HISTORY_PATH.exists():
        history_json = json.loads(HISTORY_PATH.read_text())
        for date_str, entry in history_json.items():
            if isinstance(entry, dict):
                daily_total[date_str] = entry.get('followers', 0)
            else:
                daily_total[date_str] = int(entry)

    if today_count:
        today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        history_json[today_str] = {'followers': today_count, 'source': 'instagrapi'}
        daily_total[today_str] = today_count
        HISTORY_PATH.write_text(json.dumps(history_json, indent=2, sort_keys=True))

    sorted_dates = sorted(daily_total)
    daily_net = {}
    for i in range(1, len(sorted_dates)):
        d    = sorted_dates[i]
        prev = sorted_dates[i - 1]
        daily_net[d] = daily_total[d] - daily_total[prev]

    return daily_total, daily_net


def aggregate_follower_series(daily_total, daily_net):
    """Aggregate daily data into weekly and monthly series for total + net.
    Spans from the first date with data to the last — no fixed window."""
    from datetime import date as date_cls
    if not daily_total:
        return [], [], [], [], [], []

    all_dates  = sorted(daily_total.keys())
    first_date = date_cls.fromisoformat(all_dates[0])
    last_date  = date_cls.fromisoformat(all_dates[-1])
    today      = datetime.now(timezone.utc).date()
    last_date  = min(last_date, today)

    # ── Weekly: every ISO week from first_date to last_date ────────────────
    w_labels, w_total, w_net = [], [], []
    # start on the Monday of the first week
    cursor = first_date - timedelta(days=first_date.weekday())
    while cursor <= last_date:
        iso = cursor.isocalendar()
        w_labels.append(f"W{iso[1]:02d} '{str(iso[0])[2:]}")

        # Total: last known value within the week
        t = None
        for offset in range(6, -1, -1):
            key = (cursor + timedelta(days=offset)).isoformat()
            if key in daily_total:
                t = daily_total[key]
                break
        w_total.append(t)

        net = sum(daily_net.get((cursor + timedelta(days=d)).isoformat(), 0) for d in range(7))
        w_net.append(net)
        cursor += timedelta(weeks=1)

    # ── Monthly: every calendar month from first_date to last_date ─────────
    m_labels, m_total, m_net = [], [], []
    cursor = first_date.replace(day=1)
    end_month = last_date.replace(day=1)
    while cursor <= end_month:
        if cursor.month == 12:
            next_m = cursor.replace(year=cursor.year + 1, month=1)
        else:
            next_m = cursor.replace(month=cursor.month + 1)
        last_of_m = min(next_m - timedelta(days=1), last_date)

        m_labels.append(cursor.strftime('%b %Y'))

        # Total: last known value in the month
        t = None
        day = last_of_m
        while day >= cursor:
            if day.isoformat() in daily_total:
                t = daily_total[day.isoformat()]
                break
            day -= timedelta(days=1)
        m_total.append(t)

        # Net: sum every day of the month
        net, day = 0, cursor
        while day <= last_of_m:
            net += daily_net.get(day.isoformat(), 0)
            day += timedelta(days=1)
        m_net.append(net)

        cursor = next_m

    return w_labels, w_total, w_net, m_labels, m_total, m_net


def post_frequency(posts, months=12):
    """Build weekly and monthly post count series over the last N months."""
    from datetime import date
    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=months * 31)

    # monthly
    monthly = defaultdict(int)
    weekly  = defaultdict(int)
    for p in posts:
        raw = p.get('date', '')[:10]
        if not raw:
            continue
        try:
            d = date.fromisoformat(raw)
        except ValueError:
            continue
        if d < cutoff:
            continue
        monthly[d.strftime('%Y-%m')] += 1
        # ISO week key e.g. "2025-W03"
        weekly[f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"] += 1

    # Build ordered lists covering the full range
    monthly_series, weekly_series = [], []
    m = today.replace(day=1)
    for _ in range(months):
        key = m.strftime('%Y-%m')
        monthly_series.insert(0, {'label': m.strftime('%b %Y'), 'count': monthly.get(key, 0)})
        m = (m - timedelta(days=1)).replace(day=1)

    # Last 52 weeks
    for w in range(51, -1, -1):
        d = today - timedelta(weeks=w)
        iso = d.isocalendar()
        key = f"{iso[0]}-W{iso[1]:02d}"
        weekly_series.append({'label': f"W{iso[1]:02d} '{str(iso[0])[2:]}", 'count': weekly.get(key, 0)})

    return weekly_series, monthly_series


# ── Live data fetch via Instagram web API ────────────────────────────────────

def _ig_session(env):
    """Return a requests.Session with all Instagram auth cookies + headers."""
    import requests as _req, urllib.parse as _up
    s = _req.Session()
    cookies = {
        'sessionid':  _up.unquote(env.get('IG_SESSION_ID', '')),
        'csrftoken':  env.get('IG_CSRF_TOKEN', ''),
        'ds_user_id': env.get('IG_DS_USER_ID', ''),
        'mid':        env.get('IG_MID', ''),
        'ig_did':     env.get('IG_DID', ''),
    }
    for name, val in cookies.items():
        if val:
            s.cookies.set(name, val, domain='.instagram.com', path='/')
    s.headers.update({
        'User-Agent':       'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'Accept':           '*/*',
        'Accept-Language':  'de-DE,de;q=0.9,en;q=0.8',
        'X-IG-App-ID':      '936619743392459',
        'X-CSRFToken':      cookies['csrftoken'],
        'X-Requested-With': 'XMLHttpRequest',
        'Referer':          'https://www.instagram.com/',
    })
    return s


def fetch_live(env):
    session_id = env.get('IG_SESSION_ID', '')
    if not session_id:
        print('  No IG_SESSION_ID in .env')
        return None

    s = _ig_session(env)

    print('  Fetching account info…')
    try:
        r = s.get('https://www.instagram.com/api/v1/users/web_profile_info/?username=lamaisondeleoniie', timeout=20)
        r.raise_for_status()
        udata = r.json()['data']['user']
    except Exception as e:
        print(f'  Account fetch failed: {e}')
        return None

    user_id   = udata['id']
    followers = udata['edge_followed_by']['count']
    following = udata['edge_follow']['count']
    bio       = udata.get('biography', '')
    fullname  = udata.get('full_name', SITE_LABEL)

    # ── Fetch all posts via feed pagination ───────────────────────────────────
    import time as _time
    print('  Fetching all posts…')
    all_items = []
    next_max_id = None
    page = 0
    while True:
        if page > 0:
            _time.sleep(1.5)
        params = 'count=50'
        if next_max_id:
            params += f'&max_id={next_max_id}'
        try:
            r = s.get(f'https://www.instagram.com/api/v1/feed/user/{user_id}/?{params}', timeout=20)
            r.raise_for_status()
            feed = r.json()
        except Exception as e:
            print(f'  Feed fetch error (page {page}): {e}')
            break
        batch = feed.get('items', [])
        all_items.extend(batch)
        page += 1
        print(f'  Page {page}: {len(batch)} posts ({len(all_items)} total)')
        if not feed.get('more_available') or not feed.get('next_max_id'):
            break
        next_max_id = feed['next_max_id']

    print(f'  Got {len(all_items)} posts')
    if not all_items:
        print('  No posts fetched — falling back to cache.')
        return None

    # media_type: 1=IMAGE, 2=VIDEO/REEL, 8=CAROUSEL_ALBUM
    def _mtype(item):
        mt = item.get('media_type', 1)
        if mt == 8:
            return 'CAROUSEL_ALBUM'
        if mt == 2:
            return 'REEL'
        return 'IMAGE'

    def _thumb(item):
        # For carousels, first resource's image; for video, thumbnail_url
        if item.get('media_type') == 8:
            resources = item.get('carousel_media', [])
            src = resources[0] if resources else item
        else:
            src = item
        cands = src.get('image_versions2', {}).get('candidates', [])
        if cands:
            # pick smallest for thumbnails (last in list)
            return cands[-1].get('url', '')
        return ''

    # Load existing metrics cache (reach / saves / shares)
    cache = {}
    if POSTS_CACHE_PATH.exists():
        try:
            cache = json.loads(POSTS_CACHE_PATH.read_text())
        except Exception:
            cache = {}

    posts = []
    from datetime import datetime as _dt
    for item in all_items:
        mid      = str(item.get('pk', item.get('id', '')))
        mtype    = _mtype(item)
        likes    = item.get('like_count', 0) or 0
        comments = item.get('comment_count', 0) or 0
        views    = item.get('view_count') or item.get('play_count') or 0
        taken_at = item.get('taken_at', 0)
        post_date = _dt.utcfromtimestamp(taken_at).strftime('%Y-%m-%d') if taken_at else ''
        cap_obj  = item.get('caption') or {}
        caption  = (cap_obj.get('text', '') if isinstance(cap_obj, dict) else str(cap_obj))[:120]
        code     = item.get('code', '')

        c            = cache.get(mid, {})
        saved        = c.get('saved', 0)
        reach        = c.get('reach', 0)
        shares       = c.get('shares', 0)
        post_follows = c.get('follows')

        cache[mid] = {'likes': likes, 'comments': comments, 'saved': saved,
                      'reach': reach, 'views': views, 'follows': post_follows, 'shares': shares}

        eng_rate = round((likes + comments + saved) / max(followers, 1) * 100, 2)
        posts.append({
            'id':          mid,
            'type':        mtype,
            'date':        post_date,
            'caption':     caption,
            'likes':       likes,
            'comments':    comments,
            'saved':       saved,
            'reach':       reach,
            'impressions': views,
            'video_views': views,
            'eng_rate':    eng_rate,
            'followers':   post_follows,
            'shares':      shares,
            'thumbnail':   _thumb(item),
            'permalink':   f'https://www.instagram.com/p/{code}/' if code else '',
        })

    POSTS_CACHE_PATH.write_text(json.dumps(cache))

    # Stories — use existing cache
    stories_cache = {}
    if STORIES_CACHE_PATH.exists():
        try:
            stories_cache = json.loads(STORIES_CACHE_PATH.read_text())
        except Exception:
            stories_cache = {}
    stories = sorted(stories_cache.values(), key=lambda x: x.get('date') or '', reverse=True)
    print(f'  Stories: {len(stories)} in cache')

    print('  Updating follower history…')
    daily_total, daily_net = fetch_follower_history(today_count=followers)
    w_labels, w_total, w_net, m_labels, m_total, m_net = aggregate_follower_series(daily_total, daily_net)

    follower_gain = sum(v for k, v in daily_net.items()
                        if k >= (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d'))
    avg_eng = round(sum(p['eng_rate'] for p in posts[:20]) / max(len(posts[:20]), 1), 2) if posts else 0
    weekly_freq, monthly_freq = post_frequency(posts)

    return {
        'generated':        datetime.now().strftime('%-d %b %Y %H:%M'),
        'handle':           'lamaisondeleoniie',
        'name':             fullname,
        'biography':        bio,
        'followers':        followers,
        'following':        following,
        'media_count':      len(posts),
        'follower_gain_30d': follower_gain,
        'reach_30d':        0,
        'avg_eng_rate':     avg_eng,
        'reach_ts':         [],
        'profile_views_ts': [],
        'w_labels': w_labels, 'w_total': w_total, 'w_net': w_net,
        'm_labels': m_labels, 'm_total': m_total, 'm_net': m_net,
        'posts':            posts,
        'stories':          stories,
        'weekly_freq':      weekly_freq,
        'monthly_freq':     monthly_freq,
        'is_mock':          False,
    }


# ── Mock data ─────────────────────────────────────────────────────────────────

FULL_POSTS_CACHE_PATH = Path(__file__).parent / 'full_posts_cache.json'


def build_from_cache():
    """Render dashboard entirely from local cache files — no API needed."""
    print('  Building dashboard from cached data…')

    # Load full posts (metadata + metrics)
    full_posts = []
    if FULL_POSTS_CACHE_PATH.exists():
        try:
            full_posts = json.loads(FULL_POSTS_CACHE_PATH.read_text())
        except Exception:
            full_posts = []

    # Merge with fresh metrics cache where available
    metrics = {}
    if POSTS_CACHE_PATH.exists():
        try:
            metrics = json.loads(POSTS_CACHE_PATH.read_text())
        except Exception:
            metrics = {}

    # Follower history
    daily_total, daily_net = fetch_follower_history()
    w_labels, w_total, w_net, m_labels, m_total, m_net = aggregate_follower_series(daily_total, daily_net)

    follower_now  = max(daily_total.values()) if daily_total else 0
    follower_gain = sum(v for k, v in daily_net.items()
                        if k >= (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d'))

    posts = []
    for p in full_posts:
        mid = p['id']
        c   = metrics.get(mid, {})
        likes    = c.get('likes',    p.get('likes', 0))
        comments = c.get('comments', p.get('comments', 0))
        saved    = c.get('saved',    p.get('saved', 0))
        reach    = c.get('reach',    p.get('reach', 0))
        views    = c.get('views',    p.get('impressions', 0))
        shares   = c.get('shares',   p.get('shares', 0))
        follows  = c.get('follows',  p.get('followers'))
        eng_rate = round((likes + comments + saved) / max(follower_now, 1) * 100, 2)
        posts.append({
            'id':          mid,
            'type':        p.get('type', 'IMAGE'),
            'date':        p.get('date', ''),
            'caption':     p.get('caption', ''),
            'likes':       likes,
            'comments':    comments,
            'saved':       saved,
            'reach':       reach,
            'impressions': views,
            'video_views': views,
            'eng_rate':    eng_rate,
            'followers':   follows,
            'shares':      shares,
            'thumbnail':   p.get('thumbnail', ''),
            'permalink':   p.get('permalink', ''),
        })

    # Stories
    stories_cache = {}
    if STORIES_CACHE_PATH.exists():
        try:
            stories_cache = json.loads(STORIES_CACHE_PATH.read_text())
        except Exception:
            stories_cache = {}
    stories = sorted(stories_cache.values(), key=lambda x: x.get('date') or '', reverse=True)

    avg_eng = round(sum(p['eng_rate'] for p in posts[:20]) / max(len(posts[:20]), 1), 2) if posts else 0
    weekly_freq, monthly_freq = post_frequency(posts)
    print(f'  Posts: {len(posts)}, Stories: {len(stories)}, Followers: {follower_now}')

    return {
        'generated':        datetime.now().strftime('%-d %b %Y %H:%M'),
        'handle':           'lamaisondeleoniie',
        'name':             'lamaisondeleoniie',
        'biography':        '',
        'followers':        follower_now,
        'following':        0,
        'media_count':      len(posts),
        'follower_gain_30d': follower_gain,
        'reach_30d':        0,
        'avg_eng_rate':     avg_eng,
        'reach_ts':         [],
        'profile_views_ts': [],
        'w_labels': w_labels, 'w_total': w_total, 'w_net': w_net,
        'm_labels': m_labels, 'm_total': m_total, 'm_net': m_net,
        'posts':            posts,
        'stories':          stories,
        'weekly_freq':      weekly_freq,
        'monthly_freq':     monthly_freq,
        'is_mock':          False,
    }


def build_mock():
    print('  No .env found — using mock data. Run auth.py to connect the live API.')
    today = datetime.now(timezone.utc).date()

    def make_ts(days_back):
        d = today - timedelta(days=days_back)
        return {'date': d.isoformat(), 'value': None}

    # Build realistic follower growth curve
    base = 14800
    followers_ts = []
    for i in range(30, -1, -1):
        base += int(18 + 12 * math.sin(i / 5) + (3 - i % 7))
        followers_ts.append({'date': (today - timedelta(days=i)).isoformat(), 'value': base})

    impressions_ts = []
    reach_ts = []
    profile_views_ts = []
    for i in range(30, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        impressions_ts.append({'date': d, 'value': 900 + int(400 * math.sin(i / 4) + 200 * (i % 3))})
        reach_ts.append({'date': d, 'value': 620 + int(280 * math.sin(i / 4) + 150 * (i % 3))})
        profile_views_ts.append({'date': d, 'value': 80 + int(40 * math.sin(i / 3))})

    post_types = ['IMAGE', 'IMAGE', 'IMAGE', 'CAROUSEL_ALBUM', 'REEL', 'REEL', 'IMAGE', 'CAROUSEL_ALBUM']
    captions = [
        'Nouvelle collection printemps — des pièces pensées pour être portées, pas juste admirées ✨',
        'Behind the scenes de notre dernier shooting. La lumière du soir fait tout.',
        'Ces détails qui font la différence. Broderie faite à la main, en France.',
        'Trois façons de porter notre robe Léa cet été.',
        'Drop surprise ce vendredi à 10h. Mettez votre réveil. 🕙',
        'On ne fait pas de mode rapide. On fait de la mode durable.',
        'Votre avis nous intéresse — quelle couleur préférez-vous pour la prochaine collection ?',
        'Merci pour vos 15 000 messages de soutien. On continue ensemble.',
    ]
    posts = []
    followers_now = followers_ts[-1]['value']
    for i, (pt, cap) in enumerate(zip(post_types, captions)):
        days_back = 2 + i * 4
        likes    = 320 - i * 18 + (80 if pt == 'REEL' else 0)
        comments = 24 - i * 2 + (12 if pt == 'REEL' else 0)
        saved    = 88 - i * 6
        reach    = 2800 - i * 180 + (600 if pt == 'REEL' else 0)
        imps     = int(reach * 1.4)
        eng      = round((likes + comments + saved) / followers_now * 100, 2)
        posts.append({
            'id':          f'mock_{i}',
            'type':        pt,
            'date':        (today - timedelta(days=days_back)).isoformat(),
            'caption':     cap,
            'likes':       max(likes, 10),
            'comments':    max(comments, 2),
            'saved':       max(saved, 5),
            'reach':       max(reach, 100),
            'impressions': max(imps, 150),
            'video_views': (reach + 400) if pt == 'REEL' else 0,
            'eng_rate':    eng,
            'thumbnail':   '',
            'permalink':   f'https://www.instagram.com/p/mock{i}/',
        })

    follower_now = followers_ts[-1]['value']

    # Build 12-month mock follower series
    daily_total_mock = {p['date']: p['value'] for p in followers_ts}
    daily_net_mock   = {}
    for i in range(1, len(followers_ts)):
        d = followers_ts[i]['date']
        daily_net_mock[d] = followers_ts[i]['value'] - followers_ts[i-1]['value']
    w_labels, w_total, w_net, m_labels, m_total, m_net = aggregate_follower_series(daily_total_mock, daily_net_mock)

    weekly_freq, monthly_freq = post_frequency(posts)
    return {
        'generated':         datetime.now().strftime('%-d %b %Y %H:%M'),
        'handle':            'lamaisondeleoniie',
        'name':              SITE_LABEL,
        'biography':         'Mode consciente & artisanale · Pièces limitées · Paris 🇫🇷',
        'followers':         follower_now,
        'following':         312,
        'media_count':       184,
        'follower_gain_30d': follower_now - followers_ts[0]['value'],
        'reach_30d':         sum(p['value'] for p in reach_ts),
        'avg_eng_rate':      round(sum(p['eng_rate'] for p in posts) / len(posts), 2),
        'reach_ts':          reach_ts,
        'profile_views_ts':  profile_views_ts,
        'w_labels': w_labels, 'w_total': w_total, 'w_net': w_net,
        'm_labels': m_labels, 'm_total': m_total, 'm_net': m_net,
        'posts':             posts,
        'stories':           [],
        'weekly_freq':       weekly_freq,
        'monthly_freq':      monthly_freq,
        'is_mock':           True,
    }


# ── HTML generation ───────────────────────────────────────────────────────────

def render(d, password=''):
    posts_json   = json.dumps(d['posts'])
    stories_json = json.dumps(d['stories'])
    pw_hash = hashlib.sha256(password.encode()).hexdigest() if password else ''

    reach_labels = json.dumps([p['date'] for p in d['reach_ts']])
    reach_values = json.dumps([p['value'] for p in d['reach_ts']])
    pv_labels    = json.dumps([p['date'] for p in d['profile_views_ts']])
    pv_values    = json.dumps([p['value'] for p in d['profile_views_ts']])
    w_labels  = json.dumps(d['w_labels'])
    w_total   = json.dumps(d['w_total'])
    w_net     = json.dumps(d['w_net'])
    m_labels  = json.dumps(d['m_labels'])
    m_total   = json.dumps(d['m_total'])
    m_net     = json.dumps(d['m_net'])

    wfreq_labels = json.dumps([p['label'] for p in d['weekly_freq']])
    wfreq_values = json.dumps([p['count'] for p in d['weekly_freq']])
    mfreq_labels = json.dumps([p['label'] for p in d['monthly_freq']])
    mfreq_values = json.dumps([p['count'] for p in d['monthly_freq']])

    gain_sign  = '+' if d['follower_gain_30d'] >= 0 else ''
    mock_badge = '· Mock data — run auth.py to connect live API' if d['is_mock'] else ''

    html = f'''<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{SITE_LABEL} · Instagram Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&display=swap" rel="stylesheet"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'DM Sans','Helvetica Neue',sans-serif;background:#fff;color:#111;min-height:100vh}}

/* ── HEADER ── */
.header{{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;padding:16px 32px;border-bottom:1px solid #f0f0f0;position:sticky;top:0;background:rgba(255,255,255,.96);backdrop-filter:blur(12px);z-index:20}}
.site-name{{font-size:16px;font-weight:700;letter-spacing:-.3px;text-align:center;grid-column:2}}
.header-right{{display:flex;align-items:center;gap:8px;justify-self:end;grid-column:3}}
.refresh-badge{{font-size:11px;color:#bbb;background:#f9f9f9;border:1px solid #ebebeb;border-radius:6px;padding:4px 10px;white-space:nowrap}}
.sync-btn{{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:600;color:#007AFF;background:#e8f3ff;border:1px solid #c5deff;border-radius:6px;padding:4px 10px;cursor:pointer;font-family:inherit;transition:all .15s;white-space:nowrap}}
.sync-btn:hover{{background:#d0e8ff}}
.sync-btn.syncing{{color:#8e8e93;background:#f5f5f7;border-color:#e5e5ea;cursor:default;pointer-events:none}}
.sync-btn.success{{color:#34C759;background:#e8faf0;border-color:#b6eec9}}
.sync-btn.error{{color:#ff3b30;background:#fff2f2;border-color:#ffcdd0}}
.sync-icon{{width:11px;height:11px;transition:transform .6s}}
.sync-btn.syncing .sync-icon{{animation:spin .8s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.mock-bar{{background:#fef9c3;border-bottom:1px solid #fde68a;padding:7px 32px;font-size:11px;color:#92400e;font-weight:500;text-align:center}}

/* ── NAV TABS ── */
.nav{{display:flex;padding:0 32px;border-bottom:1px solid #f0f0f0;gap:0;overflow-x:auto;background:#fff}}
.nav-link{{background:none;border:none;border-bottom:2px solid transparent;padding:12px 18px;font-size:13px;font-weight:500;color:#999;cursor:pointer;margin-bottom:-1px;white-space:nowrap;transition:color .15s;font-family:inherit;display:inline-flex;align-items:center;letter-spacing:-.1px}}
.nav-link:hover{{color:#555}}
.nav-link.active{{color:#111;border-bottom-color:#111;font-weight:600}}
.page{{display:none}}
.page.visible{{display:block}}
.content{{padding:32px;max-width:1160px}}

/* ── SECTION HEADER ── */
.sec-head{{margin-bottom:24px}}
.section-title{{font-size:22px;font-weight:700;color:#111;letter-spacing:-.5px}}
.section-sub{{font-size:12px;color:#aaa;margin-top:3px}}

/* ── STAT TILES ── */
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin-bottom:22px}}
.tile{{background:#f9f9f9;border-radius:12px;padding:16px 18px}}
.tile-val{{font-size:28px;font-weight:700;color:#111;line-height:1;margin-bottom:4px;letter-spacing:-.5px}}
.tile-label{{font-size:10px;color:#aaa;font-weight:500;text-transform:uppercase;letter-spacing:.5px}}
.tile-sub{{font-size:11px;color:#bbb;margin-top:3px}}
.c-fol{{color:#E1306C}}
.c-imp{{color:#F77737}}
.c-reach{{color:#833AB4}}
.c-eng{{color:#405DE6}}
.c-green{{color:#34C759}}

/* ── CHART CARD ── */
.chart-card{{background:#fff;border:1px solid #f0f0f0;border-radius:14px;padding:20px 22px;margin-bottom:14px}}
.chart-top{{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:8px}}
.chart-title{{font-size:13px;font-weight:600;color:#333}}
.chart-legend{{display:flex;gap:14px;font-size:11px;color:#888;flex-wrap:wrap;margin-top:4px}}
.leg-dot{{width:9px;height:9px;border-radius:2px;display:inline-block;margin-right:4px;vertical-align:middle}}
.chart-wrap{{position:relative;height:220px}}
.charts-2col{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}}

/* ── TOGGLE GROUP ── */
.tog-group{{display:flex;background:#f5f5f5;border-radius:9px;padding:3px;gap:2px}}
.tog{{background:none;border:none;border-radius:7px;padding:5px 14px;font-size:12px;font-weight:600;cursor:pointer;color:#888;font-family:inherit;transition:all .15s}}
.tog.on{{background:#fff;color:#111;box-shadow:0 1px 4px rgba(0,0,0,.1)}}

/* ── POSTS TABLE ── */
.filter-row{{display:flex;gap:7px;margin-bottom:16px;flex-wrap:wrap;align-items:center}}
.f-btn{{background:none;border:1.5px solid #e8e8e8;border-radius:9px;padding:7px 14px;font-size:12px;font-weight:500;cursor:pointer;color:#777;font-family:inherit;transition:all .15s}}
.f-btn:hover{{border-color:#aaa;color:#333}}
.f-btn.on{{background:#111;color:#fff;border-color:#111}}
.posts-table{{background:#fff;border:1px solid #f0f0f0;border-radius:14px;overflow:hidden}}
.pt-hdr,.pt-row{{display:grid;grid-template-columns:52px 2.2fr 0.75fr 0.65fr 0.75fr 0.65fr 0.7fr 0.7fr 0.7fr 0.7fr;gap:8px;padding:8px 16px;border-bottom:1px solid #f8f8f8;font-size:12px;align-items:center}}
.pt-hdr{{font-size:10px;font-weight:700;color:#bbb;text-transform:uppercase;letter-spacing:.4px;border-bottom:2px solid #f0f0f0!important;position:sticky;top:0;background:#fff;z-index:1}}
.pt-hdr span{{cursor:pointer;user-select:none;transition:color .15s;white-space:nowrap}}
.pt-hdr span:hover{{color:#555}}
.pt-hdr span.sort-desc::after{{content:' ↓';font-size:9px;font-weight:900}}
.pt-hdr span.sort-asc::after{{content:' ↑';font-size:9px;font-weight:900}}
.pt-hdr span.sort-asc,.pt-hdr span.sort-desc{{color:#111}}
.pt-row:last-child{{border-bottom:none}}
.pt-row:hover{{background:#fafafa}}
.pt-row.top-post{{background:#fffbf0;border-left:3px solid #f5a623}}
.pt-row.top-post:hover{{background:#fff8e6}}
.pt-img{{width:44px;height:44px;border-radius:7px;object-fit:cover;background:#f5f5f5;display:block;flex-shrink:0;transition:opacity .15s}}
.pt-img:hover{{opacity:.85}}
.pt-img-placeholder{{width:44px;height:44px;border-radius:7px;background:#f5f5f5;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0}}
.pt-caption-cell{{min-width:0}}
.pt-caption{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:#222;margin-bottom:3px}}
.pt-caption-sub{{display:flex;align-items:center;gap:5px}}
.pt-meta{{font-size:12px;color:#555;white-space:nowrap}}
.pt-fol{{font-size:12px;font-weight:600;white-space:nowrap}}
.type-badge{{display:inline-block;font-size:9px;font-weight:700;padding:2px 6px;border-radius:5px;text-transform:uppercase;letter-spacing:.2px;flex-shrink:0}}
.tb-slideshow{{background:#e8f3ff;color:#0369a1}}
.tb-reel{{background:#fce7f3;color:#9d174d}}
.pt-link-icon{{font-size:11px;color:#ccc;text-decoration:none;transition:color .15s;flex-shrink:0}}
.pt-link-icon:hover{{color:#555}}
.top-star-badge{{font-size:9px;font-weight:800;color:#f5a623}}
.load-more{{display:block;width:100%;padding:11px;background:#fafafa;border:none;border-top:1px solid #f0f0f0;font-size:12px;color:#999;cursor:pointer;font-family:inherit;font-weight:500;transition:all .15s;border-radius:0 0 14px 14px}}
.load-more:hover{{color:#333;background:#f5f5f5}}

/* ── STORIES TABLE ── */
.st-hdr,.st-row{{display:grid;grid-template-columns:52px 0.8fr 0.7fr 0.7fr 0.7fr 0.7fr;gap:8px;padding:8px 16px;border-bottom:1px solid #f8f8f8;font-size:12px;align-items:center}}
.st-lightbox{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.88);z-index:9999;align-items:center;justify-content:center;cursor:pointer}}
.st-lightbox.open{{display:flex}}
.st-lightbox img{{max-width:min(420px,90vw);max-height:90vh;border-radius:16px;object-fit:contain;box-shadow:0 20px 60px rgba(0,0,0,.5)}}
.st-thumb-btn{{cursor:pointer;border:none;background:none;padding:0}}
.st-hdr{{font-size:10px;font-weight:700;color:#bbb;text-transform:uppercase;letter-spacing:.4px;border-bottom:2px solid #f0f0f0!important;position:sticky;top:0;background:#fff;z-index:1}}
.st-hdr span{{cursor:pointer;user-select:none;transition:color .15s;white-space:nowrap}}
.st-hdr span:hover{{color:#555}}
.st-hdr span.sort-desc::after{{content:' ↓';font-size:9px;font-weight:900}}
.st-hdr span.sort-asc::after{{content:' ↑';font-size:9px;font-weight:900}}
.st-hdr span.sort-asc,.st-hdr span.sort-desc{{color:#111}}
.st-row:last-child{{border-bottom:none}}
.st-row:hover{{background:#fafafa}}
.type-badge-story{{display:inline-block;font-size:9px;font-weight:700;padding:2px 6px;border-radius:5px;text-transform:uppercase;letter-spacing:.2px}}
.tb-photo{{background:#f0fdf4;color:#166534}}
.tb-video{{background:#fdf4ff;color:#7e22ce}}
.st-empty{{text-align:center;padding:48px;color:#bbb;font-size:13px}}
@media(max-width:900px){{.st-hdr,.st-row{{grid-template-columns:44px 1fr 0.7fr 0.7fr}}
  .st-hdr span:nth-child(n+5),.st-row>*:nth-child(n+5){{display:none}}}}

/* ── CALENDAR ── */
.cal-toolbar{{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;flex-wrap:wrap;gap:12px}}
.cal-nav{{display:flex;align-items:center;gap:10px}}
.cal-nav-btn{{background:none;border:1.5px solid #e8e8e8;border-radius:8px;width:30px;height:30px;cursor:pointer;font-size:15px;color:#555;display:flex;align-items:center;justify-content:center;transition:all .15s;font-family:inherit}}
.cal-nav-btn:hover{{border-color:#aaa;color:#111}}
.cal-period{{font-size:15px;font-weight:700;color:#111;min-width:180px;text-align:center}}
.cal-legend{{display:flex;gap:16px;font-size:11px;color:#666;align-items:center}}
.cal-dot{{width:9px;height:9px;border-radius:2px;display:inline-block;margin-right:4px;vertical-align:middle}}
/* yearly */
.cal-year-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:28px 20px}}
.cal-mini-month{{}}
.cal-mini-title{{font-size:11px;font-weight:700;color:#333;margin-bottom:7px;cursor:pointer;display:flex;justify-content:space-between;align-items:baseline}}
.cal-mini-title:hover{{color:#1d70b8}}
.cal-mini-count{{font-size:9px;font-weight:600;color:#bbb}}
.cal-mini-week{{display:grid;grid-template-columns:repeat(7,1fr);gap:2px;margin-bottom:2px}}
.cal-mini-cell{{height:13px;border-radius:2px}}
.cal-mini-hdr{{font-size:7px;color:#ccc;text-align:center;line-height:13px;font-weight:600}}
/* monthly */
.cal-month-grid{{display:grid;grid-template-columns:repeat(7,1fr) 34px;gap:1px;background:#ebebeb;border:1px solid #ebebeb;border-radius:14px;overflow:hidden}}
.cal-day-hdr{{background:#f9f9f9;font-size:10px;font-weight:700;color:#bbb;text-align:center;padding:8px 4px;text-transform:uppercase;letter-spacing:.3px;height:32px;display:flex;align-items:center;justify-content:center}}
.cal-day-cell{{background:#fff;padding:8px;height:100px;overflow:hidden;box-sizing:border-box}}
.cal-day-cell.cal-empty{{background:#fafafa}}
.cal-day-cell.cal-today{{background:#f0f7ff}}
.cal-count-hdr{{background:#f9f9f9;height:32px;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:700;color:#ddd;text-transform:uppercase;letter-spacing:.3px}}
.cal-count-cell{{background:#fafafa;height:100px;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#ccc}}
.cal-count-cell.has-posts{{color:#888}}
.cal-day-num{{font-size:11px;font-weight:600;color:#bbb;margin-bottom:5px}}
.today-num{{color:#1d70b8;font-weight:800}}
.cal-post-chip{{border-radius:4px;padding:3px 6px;margin-bottom:4px;cursor:default}}
.cal-chip-type{{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.3px}}
.cal-chip-caption{{display:block;font-size:9px;color:#555;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}}
/* weekly */
.cal-week-grid{{display:grid;grid-template-columns:repeat(7,1fr);gap:8px}}
.cal-week-col{{border:1px solid #f0f0f0;border-radius:12px;overflow:hidden;min-height:200px}}
.cal-week-col.cal-today-col{{border-color:#1d70b8;border-width:2px}}
.cal-week-col-hdr{{background:#f9f9f9;padding:10px 8px;text-align:center;border-bottom:1px solid #f0f0f0}}
.cal-week-day{{font-size:9px;color:#bbb;font-weight:700;text-transform:uppercase;letter-spacing:.4px}}
.cal-week-date{{font-size:20px;font-weight:700;color:#333;line-height:1.2}}
.cal-week-empty{{padding:24px 8px;text-align:center;color:#e0e0e0;font-size:22px}}
.cal-week-post{{margin:8px;padding:10px;border-radius:8px;background:#f9f9f9}}
.cal-week-post-type{{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.4px;margin-bottom:5px}}
.cal-week-post-caption{{font-size:11px;color:#333;margin-bottom:8px;line-height:1.4;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}}
.cal-week-post-stats{{display:flex;gap:8px;font-size:10px;color:#999;flex-wrap:wrap}}
.cal-week-post-eng{{font-size:10px;color:#bbb;margin-top:4px}}
@media(max-width:900px){{.cal-year-grid{{grid-template-columns:repeat(3,1fr)}}.cal-week-grid{{grid-template-columns:repeat(4,1fr)}}}}

@media(max-width:900px){{
  .charts-2col{{grid-template-columns:1fr}}
  .pt-hdr,.pt-row{{grid-template-columns:44px 2fr 0.7fr 0.7fr 0.7fr}}
  .pt-hdr span:nth-child(n+6),.pt-row>*:nth-child(n+6){{display:none}}
}}
@media(max-width:768px){{
  .content{{padding:20px 16px}}
  .header{{padding:14px 16px}}
  .nav{{padding:0 16px}}
  .mock-bar{{padding:7px 16px}}
}}
</style>
</head>
<body>

<header class="header">
  <div></div>
  <div class="site-name">{IG_HANDLE}</div>
  <div class="header-right">
    <div class="refresh-badge">Updated {d['generated']}</div>
    <button class="sync-btn" id="syncBtn" onclick="doSync()">
      <svg class="sync-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>
        <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/>
      </svg>
      Sync
    </button>
  </div>
</header>

{'<div class="mock-bar">⚠ ' + mock_badge + '</div>' if d['is_mock'] else ''}

<nav class="nav">
  <button class="nav-link active" onclick="showPage('overview',this)">Overview</button>
  <button class="nav-link" onclick="showPage('posts',this)">Posts</button>
  <button class="nav-link" onclick="showPage('stories',this)">Stories</button>
  <button class="nav-link" onclick="showPage('calendar',this)">Calendar</button>
</nav>

<!-- ── OVERVIEW ── -->
<div id="pg-overview" class="page visible">
<div class="content">
  <div class="sec-head">
    <div class="section-title">Account Overview</div>
    <div class="section-sub">Last 30 days · {d['biography']}</div>
  </div>

  <div class="tiles">
    <div class="tile">
      <div class="tile-val c-fol">{fmt_num(d['followers'])}</div>
      <div class="tile-label">Followers</div>
      <div class="tile-sub">{gain_sign}{fmt_num(d['follower_gain_30d'])} this month</div>
    </div>
    <div class="tile">
      <div class="tile-val">{fmt_num(d['media_count'])}</div>
      <div class="tile-label">Posts</div>
      <div class="tile-sub">{fmt_num(d['following'])} following</div>
    </div>
    <div class="tile">
      <div class="tile-val c-imp">{fmt_num(d['reach_30d']) if d['reach_30d'] else '—'}</div>
      <div class="tile-label">Reach (30d)</div>
      <div class="tile-sub">unique accounts</div>
    </div>
    <div class="tile">
      <div class="tile-val c-reach">{('+' if (d.get('follower_gain_30d') or 0) >= 0 else '') + fmt_num(abs(d.get('follower_gain_30d') or 0))}</div>
      <div class="tile-label">New Followers (30d)</div>
      <div class="tile-sub">net gain / loss</div>
    </div>
    <div class="tile">
      <div class="tile-val c-eng">{d['avg_eng_rate'] if d['avg_eng_rate'] else '—'}{'%' if d['avg_eng_rate'] else ''}</div>
      <div class="tile-label">Avg Engagement</div>
      <div class="tile-sub">last 20 posts</div>
    </div>
  </div>

  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px">
    <div style="font-size:13px;font-weight:600;color:#333">Full history</div>
    <div class="tog-group">
      <button class="tog" id="togW" onclick="switchView('weekly')">Weekly</button>
      <button class="tog on" id="togM" onclick="switchView('monthly')">Monthly</button>
    </div>
  </div>
  <div class="charts-2col" style="margin-bottom:14px">
    <div class="chart-card">
      <div class="chart-top">
        <div>
          <div class="chart-title">Total Followers</div>
          <div class="chart-legend"><span><span class="leg-dot" style="background:#E1306C"></span>Followers</span></div>
        </div>
      </div>
      <div class="chart-wrap"><canvas id="chartFolTotal"></canvas></div>
    </div>
    <div class="chart-card">
      <div class="chart-top">
        <div>
          <div class="chart-title">Net New Followers</div>
          <div class="chart-legend"><span><span class="leg-dot" style="background:#34C759"></span>Net new per period</span></div>
        </div>
      </div>
      <div class="chart-wrap"><canvas id="chartFolNet"></canvas></div>
    </div>
  </div>

  <div class="chart-card">
    <div class="chart-top">
      <div>
        <div class="chart-title">Posts per Period</div>
        <div class="chart-legend"><span><span class="leg-dot" style="background:#111"></span>Posts published</span></div>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="chartFreq"></canvas></div>
  </div>
</div>
</div>

<!-- ── POSTS ── -->
<div id="pg-posts" class="page">
<div class="content">
  <div class="sec-head">
    <div class="section-title">Posts</div>
    <div class="section-sub">Sorted by date — most recent first</div>
  </div>

  <div class="filter-row">
    <button class="f-btn on" onclick="filterPosts('ALL',this)">All</button>
    <button class="f-btn" onclick="filterPosts('SLIDESHOW',this)">Slideshows</button>
    <button class="f-btn" onclick="filterPosts('REEL',this)">Reels</button>
  </div>

  <div class="posts-table">
    <div class="pt-hdr">
      <span></span>
      <span onclick="sortBy('caption',this)">Post</span>
      <span onclick="sortBy('date',this)" class="sort-desc">Date</span>
      <span onclick="sortBy('likes',this)">❤️ Likes</span>
      <span onclick="sortBy('comments',this)">💬 Cmts</span>
      <span onclick="sortBy('saved',this)">🔖 Saved</span>
      <span onclick="sortBy('reach',this)">👁 Reach</span>
      <span onclick="sortBy('eng_rate',this)">📈 Eng%</span>
      <span onclick="sortBy('followers',this)">👥 Followers</span>
      <span onclick="sortBy('shares',this)">🔁 Shares</span>
    </div>
    <div id="postRows"></div>
    <button class="load-more" id="loadMoreBtn" onclick="loadMorePosts()">Show more</button>
  </div>
</div>
</div>

<!-- ── STORIES ── -->
<div id="pg-stories" class="page">
<div class="content">
  <div class="sec-head">
    <div class="section-title">Stories</div>
    <div class="section-sub">Accumulated from daily syncs — sorted by date</div>
  </div>

  <div class="filter-row">
    <button class="f-btn on" onclick="filterStories('ALL',this)">All</button>
    <button class="f-btn" onclick="filterStories('IMAGE',this)">Photos</button>
    <button class="f-btn" onclick="filterStories('VIDEO',this)">Videos</button>
  </div>

  <div class="posts-table">
    <div class="st-hdr">
      <span></span>
      <span onclick="sortStories('date',this)" class="sort-desc">Date</span>
      <span onclick="sortStories('views',this)">▶️ Views</span>
      <span onclick="sortStories('replies',this)">💬 Replies</span>
      <span onclick="sortStories('link_clicks',this)">🔗 Link Clicks</span>
      <span onclick="sortStories('type',this)">Type</span>
    </div>
    <div id="storyRows"></div>
    <button class="load-more" id="loadMoreStoriesBtn" onclick="loadMoreStories()">Show more</button>
  </div>
</div>
</div>

<!-- ── CALENDAR ── -->
<div id="pg-calendar" class="page">
<div class="content">
  <div class="sec-head">
    <div class="section-title">Content Calendar</div>
    <div class="section-sub">When you posted — colour-coded by type</div>
  </div>

  <div class="cal-toolbar">
    <div class="cal-nav">
      <button class="cal-nav-btn" onclick="calPrev()">&#8249;</button>
      <div class="cal-period" id="calPeriodLabel"></div>
      <button class="cal-nav-btn" onclick="calNext()">&#8250;</button>
    </div>
    <div class="tog-group">
      <button class="tog on cal-tog" data-view="yearly"  onclick="switchCalView('yearly')">Year</button>
      <button class="tog cal-tog"    data-view="monthly" onclick="switchCalView('monthly')">Month</button>
      <button class="tog cal-tog"    data-view="weekly"  onclick="switchCalView('weekly')">Week</button>
    </div>
    <div class="cal-legend">
      <span><span class="cal-dot" style="background:#1d70b8"></span>Slideshow</span>
      <span><span class="cal-dot" style="background:#be185d"></span>Reel</span>
    </div>
  </div>

  <div id="calBody"></div>
</div>
</div>

<script>
// ── Data ──────────────────────────────────────────────────────────────────────
const POSTS   = {posts_json};
const STORIES = {stories_json};
const WFREQ_LABELS = {wfreq_labels};
const WFREQ_VALUES = {wfreq_values};
const MFREQ_LABELS = {mfreq_labels};
const MFREQ_VALUES = {mfreq_values};

// ── Navigation ────────────────────────────────────────────────────────────────
function showPage(id, btn) {{
  document.querySelectorAll('.page').forEach(p => p.classList.remove('visible'));
  document.querySelectorAll('.nav-link').forEach(b => b.classList.remove('active'));
  document.getElementById('pg-' + id).classList.add('visible');
  btn.classList.add('active');
}}

// ── Sync ──────────────────────────────────────────────────────────────────────
const SYNC_ICON = '<svg class="sync-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/></svg>';
let _syncPoller = null;

const IS_LIVE = location.hostname !== 'localhost' && location.hostname !== '127.0.0.1';

async function doSync() {{
  const btn = document.getElementById('syncBtn');
  if (IS_LIVE) {{
    btn.className = 'sync-btn error';
    btn.textContent = '↻ Updates every hour';
    setTimeout(() => {{ btn.className='sync-btn'; btn.innerHTML=SYNC_ICON+' Sync'; }}, 3000);
    return;
  }}
  btn.className = 'sync-btn syncing';
  btn.innerHTML = SYNC_ICON + ' Syncing…';
  try {{
    const r = await fetch('/sync', {{method:'POST'}});
    if (!r.ok) throw new Error('server error');
    if (_syncPoller) clearInterval(_syncPoller);
    _syncPoller = setInterval(async () => {{
      try {{
        const s = await (await fetch('/sync-status')).json();
        if (!s.running) {{
          clearInterval(_syncPoller); _syncPoller = null;
          if (s.last === 'ok') {{
            btn.className = 'sync-btn success';
            btn.textContent = '✓ Done — reloading…';
            setTimeout(() => location.reload(), 800);
          }} else {{
            btn.className = 'sync-btn error';
            btn.textContent = '✗ ' + (s.error || 'Error');
            setTimeout(() => {{ btn.className='sync-btn'; btn.innerHTML=SYNC_ICON+' Sync'; }}, 4000);
          }}
        }}
      }} catch(e) {{ /* keep polling */ }}
    }}, 4000);
  }} catch(e) {{
    btn.className = 'sync-btn error';
    btn.textContent = '✗ Server offline';
    setTimeout(() => {{ btn.className='sync-btn'; btn.innerHTML=SYNC_ICON+' Sync'; }}, 3000);
  }}
}}

// ── Chart helpers ─────────────────────────────────────────────────────────────
const xScale = (limit=8) => ({{ grid: {{ display:false }}, ticks: {{ font:{{ size:10, family:"'DM Sans'" }}, color:'#bbb', maxTicksLimit:limit }} }});
const yScale = (zero=false) => ({{ grid: {{ color:'#f5f5f5' }}, ticks: {{ font:{{ size:10, family:"'DM Sans'" }}, color:'#bbb' }}, beginAtZero:zero }});

function fmtK(n) {{
  if (n == null) return '';
  return n >= 1000 ? (n/1000).toFixed(1)+'K' : String(n);
}}

function baseOpts(yZero=false) {{
  return {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{ legend: {{ display:false }}, tooltip: {{ callbacks: {{ label: ctx => fmtK(ctx.raw) }} }} }},
    scales: {{ x: xScale(), y: yScale(yZero) }},
  }};
}}

function buildChart(id, type, labels, datasets, yZero=false) {{
  const existing = Chart.getChart(id);
  if (existing) existing.destroy();
  return new Chart(document.getElementById(id), {{
    type, data: {{ labels, datasets }}, options: baseOpts(yZero)
  }});
}}

function netBarColors(vals) {{
  return vals.map(v => (v == null || v >= 0) ? 'rgba(52,199,89,.75)' : 'rgba(255,59,48,.75)');
}}

// ── Follower series data ──────────────────────────────────────────────────────
const W_LABELS = {w_labels};
const W_TOTAL  = {w_total};
const W_NET    = {w_net};
const M_LABELS = {m_labels};
const M_TOTAL  = {m_total};
const M_NET    = {m_net};

// ── Initial render: monthly bar charts ────────────────────────────────────────
let folTotalChart = buildChart('chartFolTotal', 'bar', M_LABELS, [{{
  label:'Followers', data:M_TOTAL,
  backgroundColor:'rgba(225,48,108,.75)', borderRadius:3,
}}]);

let folNetChart = buildChart('chartFolNet', 'bar', M_LABELS, [{{
  label:'Net New', data:M_NET,
  backgroundColor:netBarColors(M_NET), borderRadius:3,
}}]);

let freqChart = buildChart('chartFreq', 'bar', MFREQ_LABELS, [{{
  label:'Posts', data:MFREQ_VALUES,
  backgroundColor:'rgba(17,17,17,.75)', borderRadius:3,
}}], true);

// ── Single toggle controls all charts ─────────────────────────────────────────
function switchView(mode) {{
  document.getElementById('togW').classList.toggle('on', mode === 'weekly');
  document.getElementById('togM').classList.toggle('on', mode === 'monthly');

  const isWeekly = mode === 'weekly';
  const folLabels  = isWeekly ? W_LABELS  : M_LABELS;
  const freqLabels = isWeekly ? WFREQ_LABELS : MFREQ_LABELS;
  const freqVals   = isWeekly ? WFREQ_VALUES : MFREQ_VALUES;

  if (isWeekly) {{
    // Line charts with hover dots for upper 2
    folTotalChart = buildChart('chartFolTotal', 'line', folLabels, [{{
      label:'Followers', data:W_TOTAL,
      borderColor:'#E1306C', backgroundColor:'rgba(225,48,108,.06)',
      borderWidth:2, pointRadius:0, pointHoverRadius:5,
      pointHoverBackgroundColor:'#E1306C', pointHoverBorderColor:'#fff',
      pointHoverBorderWidth:2, tension:0.4, fill:true, spanGaps:true,
    }}]);
    folNetChart = buildChart('chartFolNet', 'line', folLabels, [{{
      label:'Net New', data:W_NET,
      borderColor:'#34C759', backgroundColor:'rgba(52,199,89,.08)',
      borderWidth:2, pointRadius:0, pointHoverRadius:5,
      pointHoverBackgroundColor:'#34C759', pointHoverBorderColor:'#fff',
      pointHoverBorderWidth:2, tension:0.4, fill:true, spanGaps:true,
    }}]);
    freqChart = buildChart('chartFreq', 'line', freqLabels, [{{
      label:'Posts', data:freqVals,
      borderColor:'#111', backgroundColor:'rgba(0,0,0,.04)',
      borderWidth:2, pointRadius:0, pointHoverRadius:5,
      pointHoverBackgroundColor:'#111', pointHoverBorderColor:'#fff',
      pointHoverBorderWidth:2, tension:0.3, fill:true,
    }}], true);
  }} else {{
    // Bar charts for monthly
    folTotalChart = buildChart('chartFolTotal', 'bar', folLabels, [{{
      label:'Followers', data:M_TOTAL,
      backgroundColor:'rgba(225,48,108,.75)', borderRadius:3,
    }}]);
    folNetChart = buildChart('chartFolNet', 'bar', folLabels, [{{
      label:'Net New', data:M_NET,
      backgroundColor:netBarColors(M_NET), borderRadius:3,
    }}]);
    freqChart = buildChart('chartFreq', 'bar', freqLabels, [{{
      label:'Posts', data:freqVals,
      backgroundColor:'rgba(17,17,17,.75)', borderRadius:3,
    }}], true);
  }}
}}

// ── Posts table ───────────────────────────────────────────────────────────────
let postFilter = 'ALL';
let postsShown = 10;
let sortCol    = 'date';
let sortDir    = -1; // -1 desc, +1 asc

const fmtN = n => !n ? '—' : n >= 1000 ? (n/1000).toFixed(1)+'K' : n.toString();

const typeLabel = t => {{
  if (t === 'REEL')  return '<span class="type-badge tb-reel">Reel</span>';
  return '<span class="type-badge tb-slideshow">Slideshow</span>';
}};

// Compute top-performer threshold (top 25% by eng_rate)
const engRates   = POSTS.map(p => p.eng_rate).filter(v => v > 0).sort((a,b) => a-b);
const topThresh  = engRates.length ? engRates[Math.floor(engRates.length * 0.75)] : Infinity;

function getFiltered() {{
  if (postFilter === 'ALL')       return POSTS;
  if (postFilter === 'SLIDESHOW') return POSTS.filter(p => p.type !== 'REEL');
  return POSTS.filter(p => p.type === postFilter);
}}

function getSorted() {{
  return [...getFiltered()].sort((a, b) => {{
    const va = a[sortCol] ?? (typeof a[sortCol] === 'string' ? '' : -Infinity);
    const vb = b[sortCol] ?? (typeof b[sortCol] === 'string' ? '' : -Infinity);
    if (typeof va === 'string') return sortDir * va.localeCompare(vb);
    return sortDir * (va - vb);
  }});
}}

function renderPosts() {{
  const sorted = getSorted();
  const slice  = sorted.slice(0, postsShown);
  const rows   = slice.map(p => {{
    const isTop = p.eng_rate >= topThresh;
    const nf    = p.followers;
    const nfStr = nf == null ? '—' : '+' + nf.toLocaleString();
    const nfClr = nf == null ? '#ccc' : nf > 0 ? '#16a34a' : '#999';
    const imgHtml = p.thumbnail
      ? `<img class="pt-img" src="${{p.thumbnail}}" loading="lazy" onerror="this.outerHTML='<div class=\\'pt-img-placeholder\\'>${{p.type==='REEL'?'🎬':'🖼️'}}</div>'">`
      : `<div class="pt-img-placeholder">${{p.type === 'REEL' ? '🎬' : '🖼️'}}</div>`;
    const thumb = p.permalink
      ? `<a href="${{p.permalink}}" target="_blank" rel="noopener">${{imgHtml}}</a>`
      : imgHtml;
    return `<div class="pt-row${{isTop ? ' top-post' : ''}}">
      ${{thumb}}
      <div class="pt-caption-cell">
        <div class="pt-caption">${{p.caption || '—'}}</div>
        <div class="pt-caption-sub">
          ${{typeLabel(p.type)}}
          ${{isTop ? '<span class="top-star-badge">★ Top</span>' : ''}}
          ${{p.permalink ? `<a class="pt-link-icon" href="${{p.permalink}}" target="_blank" rel="noopener">↗</a>` : ''}}
        </div>
      </div>
      <span class="pt-meta">${{p.date}}</span>
      <span class="pt-meta">${{fmtN(p.likes)}}</span>
      <span class="pt-meta">${{fmtN(p.comments)}}</span>
      <span class="pt-meta">${{fmtN(p.saved)}}</span>
      <span class="pt-meta">${{fmtN(p.reach)}}</span>
      <span class="pt-meta">${{p.eng_rate ? p.eng_rate+'%' : '—'}}</span>
      <span class="pt-fol" style="color:${{nfClr}}">${{nfStr}}</span>
      <span class="pt-meta">${{fmtN(p.shares)}}</span>
    </div>`;
  }}).join('');
  document.getElementById('postRows').innerHTML = rows;
  document.getElementById('loadMoreBtn').style.display =
    sorted.length > postsShown ? 'block' : 'none';
}}

function filterPosts(type, btn) {{
  postFilter = type; postsShown = 10;
  document.querySelectorAll('.filter-row .f-btn').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  renderPosts();
}}

function sortBy(col, th) {{
  // First click on any column always goes descending (highest/newest first)
  if (sortCol === col) {{ sortDir *= -1; }}
  else {{ sortCol = col; sortDir = -1; }}
  document.querySelectorAll('.pt-hdr span').forEach(s => s.classList.remove('sort-asc','sort-desc'));
  th.classList.add(sortDir === -1 ? 'sort-desc' : 'sort-asc');
  postsShown = 10;
  renderPosts();
}}

function loadMorePosts() {{ postsShown += 10; renderPosts(); }}

renderPosts();

// ── Stories ───────────────────────────────────────────────────────────────────
let storyFilter  = 'ALL';
let storiesShown = 20;
let storySortCol = 'date';
let storySortDir = -1;

const storyTypeLabel = t => t === 'VIDEO'
  ? '<span class="type-badge-story tb-video">Video</span>'
  : '<span class="type-badge-story tb-photo">Photo</span>';

function getStoriesFiltered() {{
  if (storyFilter === 'ALL') return STORIES;
  return STORIES.filter(s => s.type === storyFilter);
}}

function getStoriesSorted() {{
  return [...getStoriesFiltered()].sort((a, b) => {{
    const va = a[storySortCol] ?? (typeof a[storySortCol] === 'string' ? '' : -Infinity);
    const vb = b[storySortCol] ?? (typeof b[storySortCol] === 'string' ? '' : -Infinity);
    if (typeof va === 'string') return storySortDir * va.localeCompare(vb);
    return storySortDir * (va - vb);
  }});
}}

function renderStories() {{
  const sorted = getStoriesSorted();
  const slice  = sorted.slice(0, storiesShown);
  if (!slice.length) {{
    document.getElementById('storyRows').innerHTML = '<div class="st-empty">No stories in cache yet — they accumulate with each sync.</div>';
    document.getElementById('loadMoreStoriesBtn').style.display = 'none';
    return;
  }}
  const rows = slice.map(s => {{
    const imgEl = s.thumbnail
      ? `<img class="pt-img" src="${{s.thumbnail}}" loading="lazy" onerror="this.outerHTML='<div class=\\'pt-img-placeholder\\'>${{s.type==='VIDEO'?'🎬':'📸'}}</div>'">`
      : `<div class="pt-img-placeholder">${{s.type === 'VIDEO' ? '🎬' : '📸'}}</div>`;
    const thumb = s.thumbnail
      ? `<button class="st-thumb-btn" onclick="openStoryLightbox('${{s.thumbnail}}')">${{imgEl}}</button>`
      : imgEl;
    const viewsVal = s.views != null ? s.views : s.reach;
    return `<div class="st-row">
      ${{thumb}}
      <span class="pt-meta">${{s.date || '—'}}</span>
      <span class="pt-meta">${{fmtN(viewsVal)}}</span>
      <span class="pt-meta">${{fmtN(s.replies)}}</span>
      <span class="pt-meta">${{fmtN(s.link_clicks)}}</span>
      <span>${{storyTypeLabel(s.type)}}</span>
    </div>`;
  }}).join('');
  document.getElementById('storyRows').innerHTML = rows;
  document.getElementById('loadMoreStoriesBtn').style.display =
    sorted.length > storiesShown ? 'block' : 'none';
}}

function filterStories(type, btn) {{
  storyFilter = type; storiesShown = 20;
  document.querySelectorAll('#pg-stories .filter-row .f-btn').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  renderStories();
}}

function sortStories(col, th) {{
  if (storySortCol === col) {{ storySortDir *= -1; }}
  else {{ storySortCol = col; storySortDir = -1; }}
  document.querySelectorAll('.st-hdr span').forEach(s => s.classList.remove('sort-asc','sort-desc'));
  th.classList.add(storySortDir === -1 ? 'sort-desc' : 'sort-asc');
  storiesShown = 20;
  renderStories();
}}

function loadMoreStories() {{ storiesShown += 20; renderStories(); }}

function openStoryLightbox(src) {{
  const lb = document.getElementById('stLightbox');
  lb.querySelector('img').src = src;
  lb.classList.add('open');
}}
document.addEventListener('DOMContentLoaded', () => {{
  document.getElementById('stLightbox').addEventListener('click', () =>
    document.getElementById('stLightbox').classList.remove('open'));
}});
document.addEventListener('keydown', e => {{
  if (e.key === 'Escape') document.getElementById('stLightbox')?.classList.remove('open');
}});

renderStories();

// ── Calendar ──────────────────────────────────────────────────────────────────
const postsByDate = {{}};
POSTS.forEach(p => {{
  if (!postsByDate[p.date]) postsByDate[p.date] = [];
  postsByDate[p.date].push(p);
}});

const MONTH_NAMES = ['January','February','March','April','May','June',
                     'July','August','September','October','November','December'];
const DAY_NAMES   = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
const SLIDE_CLR   = '#1d70b8';
const REEL_CLR    = '#be185d';

let calView = 'yearly';
let calRef  = new Date();
// Default to the year of the most recent post
if (POSTS.length) calRef = new Date(POSTS[0].date);

function postColor(posts) {{
  const hasSlide = posts.some(p => p.type !== 'REEL');
  const hasReel  = posts.some(p => p.type === 'REEL');
  if (hasSlide && hasReel) return `linear-gradient(135deg,${{SLIDE_CLR}} 50%,${{REEL_CLR}} 50%)`;
  return hasReel ? REEL_CLR : SLIDE_CLR;
}}

function isoDate(y, m, d) {{
  return `${{y}}-${{String(m+1).padStart(2,'0')}}-${{String(d).padStart(2,'0')}}`;
}}
function daysInMonth(y, m) {{ return new Date(y, m+1, 0).getDate(); }}

function weekStart(date) {{
  const d = new Date(date);
  const day = d.getDay();
  d.setDate(d.getDate() + (day === 0 ? -6 : 1 - day));
  return d;
}}

function updatePeriodLabel() {{
  const y = calRef.getFullYear(), m = calRef.getMonth();
  let label = '';
  if (calView === 'yearly')  label = String(y);
  if (calView === 'monthly') label = MONTH_NAMES[m] + ' ' + y;
  if (calView === 'weekly') {{
    const ws = weekStart(calRef), we = new Date(ws);
    we.setDate(ws.getDate() + 6);
    label = ws.toLocaleDateString('en-GB',{{day:'numeric',month:'short'}}) + ' – ' +
            we.toLocaleDateString('en-GB',{{day:'numeric',month:'short',year:'numeric'}});
  }}
  document.getElementById('calPeriodLabel').textContent = label;
}}

function calPrev() {{
  if (calView === 'yearly')  calRef.setFullYear(calRef.getFullYear()-1);
  if (calView === 'monthly') calRef.setMonth(calRef.getMonth()-1);
  if (calView === 'weekly')  calRef.setDate(calRef.getDate()-7);
  renderCalendar();
}}
function calNext() {{
  if (calView === 'yearly')  calRef.setFullYear(calRef.getFullYear()+1);
  if (calView === 'monthly') calRef.setMonth(calRef.getMonth()+1);
  if (calView === 'weekly')  calRef.setDate(calRef.getDate()+7);
  renderCalendar();
}}

function switchCalView(v, dateStr) {{
  calView = v;
  if (dateStr) calRef = new Date(dateStr + 'T12:00:00');
  document.querySelectorAll('.cal-tog').forEach(b => b.classList.toggle('on', b.dataset.view === v));
  renderCalendar();
}}

function renderCalendar() {{
  updatePeriodLabel();
  const body = document.getElementById('calBody');
  if (calView === 'yearly')  body.innerHTML = renderYearly();
  else if (calView === 'monthly') body.innerHTML = renderMonthly();
  else body.innerHTML = renderWeekly();
}}

function renderYearly() {{
  const year = calRef.getFullYear();
  let html = '<div class="cal-year-grid">';
  for (let m = 0; m < 12; m++) {{
    const dim = daysInMonth(year, m);
    const fd  = new Date(year, m, 1).getDay();
    const offset = fd === 0 ? 6 : fd - 1;
    const monthStr = String(m+1).padStart(2,'0');
    // Count posts this month
    let monthCount = 0;
    for (let d = 1; d <= dim; d++) {{
      const ds = isoDate(year, m, d);
      if (postsByDate[ds]) monthCount += postsByDate[ds].length;
    }}
    const countLabel = monthCount > 0 ? `<span class="cal-mini-count">${{monthCount}}p</span>` : '';
    html += `<div class="cal-mini-month">
      <div class="cal-mini-title" onclick="switchCalView('monthly','${{year}}-${{monthStr}}-01')">${{MONTH_NAMES[m]}}${{countLabel}}</div>
      <div class="cal-mini-week">${{['M','T','W','T','F','S','S'].map(d=>`<div class="cal-mini-cell cal-mini-hdr">${{d}}</div>`).join('')}}</div>`;
    let cells = Array(offset).fill('<div class="cal-mini-cell"></div>');
    for (let d = 1; d <= dim; d++) {{
      const ds = isoDate(year, m, d);
      const posts = postsByDate[ds];
      if (posts) {{
        const bg = postColor(posts);
        const dayStr = String(d).padStart(2,'0');
        cells.push(`<div class="cal-mini-cell" style="background:${{bg}};cursor:pointer" title="${{posts.length}} post(s) — ${{ds}}" onclick="switchCalView('monthly','${{year}}-${{monthStr}}-${{dayStr}}')"></div>`);
      }} else {{
        cells.push('<div class="cal-mini-cell" style="background:#f0f0f0"></div>');
      }}
    }}
    while (cells.length % 7) cells.push('<div class="cal-mini-cell"></div>');
    for (let i = 0; i < cells.length; i += 7)
      html += `<div class="cal-mini-week">${{cells.slice(i,i+7).join('')}}</div>`;
    html += '</div>';
  }}
  return html + '</div>';
}}

function renderMonthly() {{
  const year = calRef.getFullYear(), month = calRef.getMonth();
  const dim    = daysInMonth(year, month);
  const fd     = new Date(year, month, 1).getDay();
  const offset = fd === 0 ? 6 : fd - 1;
  const today  = new Date().toISOString().slice(0,10);

  // Build flat list of cells (nulls for padding, objects for real days)
  const cells = Array(offset).fill(null);
  for (let d = 1; d <= dim; d++) {{
    const ds = isoDate(year, month, d);
    cells.push({{ d, ds, posts: postsByDate[ds] || [], isToday: ds === today }});
  }}
  while (cells.length % 7) cells.push(null); // pad to complete weeks

  let html = `<div class="cal-month-grid">
    ${{DAY_NAMES.map(d=>`<div class="cal-day-hdr">${{d}}</div>`).join('')}}
    <div class="cal-count-hdr">#</div>`;

  for (let w = 0; w < cells.length / 7; w++) {{
    const week = cells.slice(w*7, w*7+7);
    const weekCount = week.reduce((s,c) => s + (c ? c.posts.length : 0), 0);

    week.forEach(c => {{
      if (!c) {{ html += '<div class="cal-day-cell cal-empty"></div>'; return; }}
      html += `<div class="cal-day-cell${{c.isToday?' cal-today':''}}">
        <div class="cal-day-num${{c.isToday?' today-num':''}}">${{c.d}}</div>`;
      c.posts.forEach(p => {{
        const clr   = p.type === 'REEL' ? REEL_CLR : SLIDE_CLR;
        const label = p.type === 'REEL' ? 'Reel' : 'Slideshow';
        html += `<div class="cal-post-chip" style="background:${{clr}}18;border-left:3px solid ${{clr}}">
          <span class="cal-chip-type" style="color:${{clr}}">${{label}}</span>
          ${{p.caption ? `<span class="cal-chip-caption">${{p.caption.slice(0,40)}}</span>` : ''}}
        </div>`;
      }});
      html += '</div>';
    }});

    const cls = weekCount > 0 ? ' has-posts' : '';
    html += `<div class="cal-count-cell${{cls}}">${{weekCount || ''}}</div>`;
  }}
  return html + '</div>';
}}

function renderWeekly() {{
  const ws    = weekStart(new Date(calRef));
  const today = new Date().toISOString().slice(0,10);
  let html = '<div class="cal-week-grid">';
  for (let i = 0; i < 7; i++) {{
    const d  = new Date(ws); d.setDate(ws.getDate()+i);
    const ds = d.toISOString().slice(0,10);
    const posts = postsByDate[ds] || [];
    const isToday = ds === today;
    html += `<div class="cal-week-col${{isToday?' cal-today-col':''}}">
      <div class="cal-week-col-hdr">
        <div class="cal-week-day">${{DAY_NAMES[i]}}</div>
        <div class="cal-week-date${{isToday?' today-num':''}}">${{d.getDate()}}</div>
      </div>`;
    if (!posts.length) {{
      html += '<div class="cal-week-empty">·</div>';
    }} else {{
      posts.forEach(p => {{
        const clr   = p.type === 'REEL' ? REEL_CLR : SLIDE_CLR;
        const label = p.type === 'REEL' ? 'Reel' : 'Slideshow';
        html += `<div class="cal-week-post" style="border-top:3px solid ${{clr}}">
          <div class="cal-week-post-type" style="color:${{clr}}">${{label}}</div>
          <div class="cal-week-post-caption">${{p.caption || '—'}}</div>
          <div class="cal-week-post-stats">
            <span>♥ ${{fmtN(p.likes)}}</span>
            <span>💬 ${{fmtN(p.comments)}}</span>
            <span>🔖 ${{fmtN(p.saved)}}</span>
          </div>
          <div class="cal-week-post-eng">${{p.eng_rate}}% eng · ${{fmtN(p.reach)}} reach</div>
        </div>`;
      }});
    }}
    html += '</div>';
  }}
  return html + '</div>';
}}

renderCalendar();
</script>
{f"""
<div id="pw-gate" style="position:fixed;inset:0;background:#fff;z-index:9999;display:flex;align-items:center;justify-content:center;font-family:'DM Sans',sans-serif">
  <div style="text-align:center;width:320px;padding:40px 32px;background:#fff;border-radius:20px;box-shadow:0 8px 40px rgba(0,0,0,.1)">
    <div style="font-size:30px;margin-bottom:10px">🌿</div>
    <div style="font-size:17px;font-weight:700;color:#111;margin-bottom:4px">{d['name']}</div>
    <div style="font-size:12px;color:#bbb;margin-bottom:28px;letter-spacing:.2px">ANALYTICS</div>
    <input id="pw-input" type="password" placeholder="Password" autocomplete="current-password"
      style="width:100%;box-sizing:border-box;padding:11px 14px;border:1.5px solid #e8e8e8;border-radius:10px;font-size:14px;font-family:inherit;outline:none;color:#111;margin-bottom:8px;transition:border-color .15s"
      onfocus="this.style.borderColor='#aaa'" onblur="this.style.borderColor='#e8e8e8'"
      onkeydown="if(event.key==='Enter')checkPw()">
    <div id="pw-err" style="color:#dc2626;font-size:12px;margin-bottom:12px;min-height:16px"></div>
    <button onclick="checkPw()" style="width:100%;padding:12px;background:#111;color:#fff;border:none;border-radius:10px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;transition:background .15s"
      onmouseover="this.style.background='#333'" onmouseout="this.style.background='#111'">Enter</button>
  </div>
</div>
<script>
(function(){{
  const HASH = '{pw_hash}';
  const KEY  = 'dash_auth_v1';
  const TTL  = 86400000;
  async function sha256(s) {{
    const b = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
    return Array.from(new Uint8Array(b)).map(x=>x.toString(16).padStart(2,'0')).join('');
  }}
  function unlock() {{ document.getElementById('pw-gate').remove(); localStorage.setItem(KEY, Date.now()); }}
  try {{ const t = +localStorage.getItem(KEY); if (t && Date.now()-t < TTL) {{ unlock(); return; }} }} catch(e) {{}}
  window.checkPw = async function() {{
    const h = await sha256(document.getElementById('pw-input').value);
    if (h === HASH) {{ unlock(); }}
    else {{ document.getElementById('pw-err').textContent = 'Incorrect password'; document.getElementById('pw-input').value=''; document.getElementById('pw-input').focus(); }}
  }};
}})();
</script>""" if pw_hash else ''}
<div id="stLightbox" class="st-lightbox"><img src="" alt="Story"></div>
</body>
</html>'''
    return html


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--full', action='store_true',
                        help='Full refresh: re-fetch insights for all posts (ignores cache)')
    args = parser.parse_args()

    if args.full and POSTS_CACHE_PATH.exists():
        POSTS_CACHE_PATH.unlink()
        print('  Full refresh — cache cleared')

    env = load_env()
    print('Instagram Dashboard Generator')
    print('─' * 40)

    if env.get('IG_SESSION_ID'):
        print('  Session found — fetching live data…')
        data = fetch_live(env)
        if data is None:
            print('  Live fetch failed — using cached data.')
            data = build_from_cache()
    elif FULL_POSTS_CACHE_PATH.exists():
        data = build_from_cache()
    else:
        data = build_mock()

    print('  Rendering dashboard…')
    pw = load_env().get('DASHBOARD_PASSWORD', '')
    html = render(data, password=pw)
    OUTPUT_PATH.write_text(html, encoding='utf-8')
    print(f'  ✓ Written to {OUTPUT_PATH}')
    if pw:
        print(f'  🔒 Password protection enabled')


if __name__ == '__main__':
    main()
