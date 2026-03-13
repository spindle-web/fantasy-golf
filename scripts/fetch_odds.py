#!/usr/bin/env python3
"""
Fantasy Golf League - Pre-Tournament Odds Fetcher

Fetches outright (tournament winner) betting odds and converts them to
fair implied probabilities for salary assignment.

Primary source: The Odds API (free tier - 500 requests/month)
  https://the-odds-api.com/

Usage:
  python scripts/fetch_odds.py

Environment variables:
  ODDS_API_KEY    - API key from The Odds API (required)
  GOLF_EVENT_KEY  - Override the auto-detected golf event key (optional)

Output:
  data/odds.json - Player odds data with fair probabilities and salaries

The odds are fetched ONCE before the tournament starts and locked in.
Subsequent runs will NOT overwrite existing odds unless --force is passed.
"""

import json
import os
import sys
import math
import urllib.request
import urllib.error
from datetime import datetime, timezone

# Configuration
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
ODDS_FILE = os.path.join(DATA_DIR, 'odds.json')

# The Odds API configuration
ODDS_API_BASE = 'https://api.the-odds-api.com/v4'
ODDS_API_KEY = os.environ.get('ODDS_API_KEY', '')

# Salary range
MIN_SALARY = 5000
MAX_SALARY = 18500
SALARY_STEP = 500  # Round to nearest $500


def fetch_url(url, timeout=15):
    """Fetch URL and return parsed JSON."""
    print(f"  Fetching: {url[:100]}...")
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'FantasyGolfLeague/1.0',
            'Accept': 'application/json'
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode('utf-8')
            # Check remaining API requests (The Odds API returns this in headers)
            remaining = resp.headers.get('x-requests-remaining', '?')
            used = resp.headers.get('x-requests-used', '?')
            print(f"  API requests used: {used}, remaining: {remaining}")
            return json.loads(data)
    except urllib.error.HTTPError as e:
        print(f"  HTTP Error {e.code}: {e.reason}")
        if e.code == 401:
            print("  -> Invalid API key. Get a free key at https://the-odds-api.com/")
        elif e.code == 429:
            print("  -> Rate limited. Free tier allows 500 requests/month.")
        return None
    except urllib.error.URLError as e:
        print(f"  URL Error: {e.reason}")
        return None
    except Exception as e:
        print(f"  Error: {e}")
        return None


def get_available_golf_events():
    """Get list of currently available golf events from The Odds API."""
    url = f"{ODDS_API_BASE}/sports?apiKey={ODDS_API_KEY}"
    data = fetch_url(url)
    if not data:
        return []

    golf_events = []
    for sport in data:
        key = sport.get('key', '')
        if 'golf' in key.lower():
            golf_events.append({
                'key': key,
                'title': sport.get('title', ''),
                'description': sport.get('description', ''),
                'active': sport.get('active', False),
                'has_outrights': sport.get('has_outrights', False)
            })

    return golf_events


def fetch_outright_odds(sport_key, regions='us,us2,eu'):
    """
    Fetch outright (tournament winner) odds for a golf event.

    Args:
        sport_key: The Odds API sport key (e.g., 'golf_pga_players_championship_winner')
        regions: Comma-separated bookmaker regions to include

    Returns:
        List of bookmaker odds data
    """
    url = (
        f"{ODDS_API_BASE}/sports/{sport_key}/odds"
        f"?apiKey={ODDS_API_KEY}"
        f"&regions={regions}"
        f"&markets=outrights"
        f"&oddsFormat=american"
    )
    return fetch_url(url)


def american_to_implied_probability(american_odds):
    """
    Convert American odds to implied probability.

    For negative odds (favorites): prob = |odds| / (|odds| + 100)
    For positive odds (underdogs): prob = 100 / (odds + 100)

    Returns probability as a decimal (0.0 to 1.0)
    """
    if american_odds < 0:
        return abs(american_odds) / (abs(american_odds) + 100)
    elif american_odds > 0:
        return 100 / (american_odds + 100)
    else:
        return 0.5  # Even odds


def compute_fair_probabilities(player_odds_list):
    """
    Convert raw bookmaker odds to fair (no-vig) probabilities.

    Steps:
    1. Average implied probabilities across all bookmakers for each player
    2. Sum all probabilities (will be > 1.0 due to vig/overround)
    3. Normalize so they sum to exactly 1.0 (removes the vig)

    Returns dict of {player_name: fair_probability}
    """
    # Collect all implied probs per player across bookmakers
    player_probs = {}

    for player_name, odds_by_book in player_odds_list.items():
        probs = []
        for book_odds in odds_by_book:
            prob = american_to_implied_probability(book_odds)
            if prob > 0:
                probs.append(prob)

        if probs:
            # Average across bookmakers
            player_probs[player_name] = sum(probs) / len(probs)

    # Normalize to remove vig (fair probabilities sum to 1.0)
    total_prob = sum(player_probs.values())
    if total_prob > 0:
        fair_probs = {name: prob / total_prob for name, prob in player_probs.items()}
    else:
        fair_probs = player_probs

    return fair_probs


def probability_to_salary(fair_probability, all_probs):
    """
    Convert fair probability to salary using log-scale mapping.

    Golf odds span orders of magnitude (0.1% to 12%), so a log scale
    gives a much better salary distribution than linear:
      - Favorites (~8-12%) â ~$18,500
      - Contenders (~2-5%) â ~$13,000-$16,000
      - Mid-field (~0.5-1.5%) â ~$9,000-$12,000
      - Longshots (~0.1-0.4%) â ~$5,000-$8,000

    Formula:
      salary = MIN + (MAX - MIN) * (log(prob) - log(min_prob)) / (log(max_prob) - log(min_prob))
    """
    probs = [p for p in all_probs.values() if p > 0]
    if not probs or fair_probability <= 0:
        return MIN_SALARY

    log_prob = math.log(fair_probability)
    log_min = math.log(min(probs))
    log_max = math.log(max(probs))

    if log_max == log_min:
        normalized = 0.5
    else:
        normalized = (log_prob - log_min) / (log_max - log_min)

    # Clamp to [0, 1]
    normalized = max(0.0, min(1.0, normalized))

    # Map to salary range
    salary = MIN_SALARY + (MAX_SALARY - MIN_SALARY) * normalized

    # Round to nearest SALARY_STEP
    salary = round(salary / SALARY_STEP) * SALARY_STEP

    # Clamp to valid range
    return max(MIN_SALARY, min(MAX_SALARY, int(salary)))


def parse_odds_response(odds_data):
    """
    Parse The Odds API response into player odds dict.

    Returns:
        dict: {player_name: [odds_from_book1, odds_from_book2, ...]}
        str: event name/description
    """
    if not odds_data or not isinstance(odds_data, list):
        return {}, 'Unknown'

    player_odds = {}
    event_name = 'Unknown'

    for event in odds_data:
        # The event object contains bookmakers
        event_name = event.get('sport_title', event.get('sport_key', 'Golf'))

        bookmakers = event.get('bookmakers', [])
        for bookmaker in bookmakers:
            markets = bookmaker.get('markets', [])
            for market in markets:
                if market.get('key') != 'outrights':
                    continue

                outcomes = market.get('outcomes', [])
                for outcome in outcomes:
                    name = outcome.get('name', '')
                    price = outcome.get('price')

                    if name and price is not None:
                        if name not in player_odds:
                            player_odds[name] = []
                        player_odds[name].append(price)

    return player_odds, event_name


def load_existing_odds():
    """Load existing odds file if it exists."""
    if not os.path.exists(ODDS_FILE):
        return None
    try:
        with open(ODDS_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"  Warning: Could not load existing odds: {e}")
        return None


def save_odds(odds_data):
    """Save odds data to JSON file."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ODDS_FILE, 'w') as f:
        json.dump(odds_data, f, indent=2)
    print(f"  Saved odds to: {ODDS_FILE}")


def main():
    force = '--force' in sys.argv

    print("=" * 50)
    print("Fantasy Golf League - Odds Fetcher")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 50)

    # Check for existing locked-in odds
    existing = load_existing_odds()
    if existing and not force:
        locked = existing.get('locked', False)
        if locked:
            print()
            print("Odds are already locked in for this tournament.")
            print(f"  Event: {existing.get('event_name', 'Unknown')}")
            print(f"  Players: {len(existing.get('players', {}))}")
            print(f"  Fetched: {existing.get('fetched_at', 'Unknown')}")
            print("Use --force to re-fetch and overwrite.")
            return

    # Validate API key
    if not ODDS_API_KEY:
        print()
        print("ERROR: No ODDS_API_KEY environment variable set.")
        print("Get a free API key at: https://the-odds-api.com/")
        print("Then set it as a GitHub secret named ODDS_API_KEY")
        print()
        print("Falling back to no-odds mode (position-based salaries will be used).")
        sys.exit(0)  # Don't fail the workflow - just use fallback salaries

    # Step 1: Find available golf events
    print()
    print("[1/3] Finding available golf events...")
    golf_events = get_available_golf_events()

    if not golf_events:
        print("  No golf events found. The API may be down or key invalid.")
        sys.exit(0)

    print(f"  Found {len(golf_events)} golf events:")
    active_events = []
    for evt in golf_events:
        status = "ACTIVE" if evt['active'] else "inactive"
        outrights = "outrights" if evt['has_outrights'] else "no outrights"
        print(f"    {evt['key']} - {evt['title']} [{status}, {outrights}]")
        if evt['active'] and evt['has_outrights']:
            active_events.append(evt)

    # Step 2: Select the right event
    override_key = os.environ.get('GOLF_EVENT_KEY', '')
    selected_event = None

    if override_key:
        # Use override
        selected_event = next((e for e in golf_events if e['key'] == override_key), None)
        if not selected_event:
            print(f"  Warning: Override key '{override_key}' not found in available events.")

    if not selected_event and active_events:
        # Auto-select: prefer PGA events, then any active golf outright
        pga_events = [e for e in active_events if 'pga' in e['key'].lower()]
        if pga_events:
            selected_event = pga_events[0]
        else:
            selected_event = active_events[0]

    if not selected_event:
        print()
        print("  No active golf events with outrights found.")
        print("  This is normal between tournaments.")
        print("  Odds will be fetched when the next event becomes available.")
        sys.exit(0)

    print()
    print(f"  Selected event: {selected_event['title']} ({selected_event['key']})")

    # Step 3: Fetch odds
    print()
    print("[2/3] Fetching outright odds...")
    odds_data = fetch_outright_odds(selected_event['key'])

    if not odds_data:
        print("  Failed to fetch odds data.")
        sys.exit(0)

    player_odds, event_name = parse_odds_response(odds_data)

    if not player_odds:
        print("  No player odds found in response.")
        sys.exit(0)

    print(f"  Got odds for {len(player_odds)} players")

    # Step 4: Compute fair probabilities and salaries
    print()
    print("[3/3] Computing fair probabilities and salaries...")
    fair_probs = compute_fair_probabilities(player_odds)

    # Sort by probability (highest first = favorites)
    sorted_players = sorted(fair_probs.items(), key=lambda x: x[1], reverse=True)

    # Compute salaries
    player_data = {}
    for name, prob in sorted_players:
        salary = probability_to_salary(prob, fair_probs)
        avg_odds = sum(player_odds[name]) / len(player_odds[name])
        player_data[name] = {
            'fair_probability': round(prob, 6),
            'implied_probability_pct': round(prob * 100, 3),
            'avg_american_odds': round(avg_odds),
            'num_books': len(player_odds[name]),
            'salary': salary
        }

    # Build output
    output = {
        'event_key': selected_event['key'],
        'event_name': selected_event.get('title', event_name),
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'locked': True,
        'salary_range': {'min': MIN_SALARY, 'max': MAX_SALARY, 'step': SALARY_STEP},
        'total_players': len(player_data),
        'players': player_data
    }

    save_odds(output)

    # Print summary
    print()
    print(f"Event: {output['event_name']}")
    print(f"Players with odds: {len(player_data)}")
    print()
    print("Top 15 by salary:")
    print(f"  {'Name':<30} {'Odds':>8} {'Fair %':>8} {'Salary':>8}")
    print("  " + "-" * 58)
    for name, prob in sorted_players[:15]:
        info = player_data[name]
        odds_str = f"{'+' if info['avg_american_odds'] > 0 else ''}{info['avg_american_odds']}"
        salary_str = f"${info['salary']:,}"
        print(f"  {name:<30} {odds_str:>8} {info['implied_probability_pct']:>7.2f}% {salary_str:>8}")

    print()
    print("Bottom 5 by salary:")
    for name, prob in sorted_players[-5:]:
        info = player_data[name]
        odds_str = f"{'+' if info['avg_american_odds'] > 0 else ''}{info['avg_american_odds']}"
        salary_str = f"${info['salary']:,}"
        print(f"  {name:<30} {odds_str:>8} {info['implied_probability_pct']:>7.2f}% {salary_str:>8}")

    # Salary distribution
    salary_counts = {}
    for info in player_data.values():
        tier = "Elite ($15k+)" if info['salary'] >= 15000 else \
               "High ($11k-$14.5k)" if info['salary'] >= 11000 else \
               "Mid ($8k-$10.5k)" if info['salary'] >= 8000 else \
               "Value ($5k-$7.5k)"
        salary_counts[tier] = salary_counts.get(tier, 0) + 1

    print()
    print("Salary distribution:")
    for tier, count in sorted(salary_counts.items()):
        print(f"  {tier}: {count} players")

    print()
    print("Odds locked in! These salaries will persist through the tournament.")


if __name__ == '__main__':
    main()
