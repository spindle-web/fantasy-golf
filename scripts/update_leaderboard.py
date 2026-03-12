#!/usr/bin/env python3
"""
Fantasy Golf League - Leaderboard Update Script
Fetches PGA Tour leaderboard data and saves cleaned JSON for the website.

Data Sources (tried in order):
1. statdata.pgatour.com leaderboard-v2mini.json
2. statdata.pgatour.com leaderboard-v2.json
3. ESPN Golf API (fallback)

Salary Assignment:
- Based on field position/ranking, normalized to fit salary cap constraints
- Tier 1 (top ~10): $14,000 - $18,500
- Tier 2 (11-25): $11,000 - $13,500
- Tier 3 (26-50): $8,500 - $10,500
- Tier 4 (51-80): $7,000 - $8,000
- Tier 5 (81+): $5,000 - $6,500
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
import math

# Configuration
TOURNAMENT_ID = os.environ.get('TOURNAMENT_ID', '011')  # The Players Championship
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')

# PGA Tour endpoints
PGA_CURRENT_URL = 'https://statdata.pgatour.com/r/current/message.json'
PGA_LEADERBOARD_MINI = f'https://statdata.pgatour.com/r/{TOURNAMENT_ID}/leaderboard-v2mini.json'
PGA_LEADERBOARD_FULL = f'https://statdata.pgatour.com/r/{TOURNAMENT_ID}/leaderboard-v2.json'

# ESPN fallback
ESPN_GOLF_URL = 'https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard'


def fetch_url(url, timeout=15):
    """Fetch URL and return parsed JSON."""
    print(f"  Fetching: {url}")
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; FantasyGolfBot/1.0)',
            'Accept': 'application/json'
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode('utf-8')
            return json.loads(data)
    except urllib.error.HTTPError as e:
        print(f"  HTTP Error {e.code}: {e.reason}")
        return None
    except urllib.error.URLError as e:
        print(f"  URL Error: {e.reason}")
        return None
    except Exception as e:
        print(f"  Error: {e}")
        return None


def assign_salaries(players):
    """Assign salaries based on field position (index in sorted list)."""
    n = len(players)
    if n == 0:
        return players

    for i, player in enumerate(players):
        # Calculate salary based on position in field
        # Use exponential decay so top players cost significantly more
        pct = i / max(n - 1, 1)  # 0.0 for top, 1.0 for bottom

        # Salary range: $5,000 to $18,500
        min_salary = 5000
        max_salary = 18500

        # Exponential curve - steeper at the top
        salary = max_salary - (max_salary - min_salary) * (pct ** 0.6)

        # Round to nearest 500
        salary = round(salary / 500) * 500
        salary = max(min_salary, min(max_salary, salary))

        player['salary'] = int(salary)

    return players


def parse_pga_leaderboard(data):
    """Parse PGA Tour leaderboard-v2mini or leaderboard-v2 format."""
    if not data:
        return None

    lb = data.get('leaderboard', data)

    tournament_info = {
        'name': lb.get('tournament_name', lb.get('tournament', {}).get('name', 'Unknown Tournament')),
        'id': TOURNAMENT_ID,
        'course': lb.get('courses', [{}])[0].get('course_name', '') if lb.get('courses') else '',
        'current_round': lb.get('current_round', 1),
        'status': 'in_progress' if lb.get('is_started', True) and not lb.get('is_finished', False) else
                  'completed' if lb.get('is_finished', False) else 'upcoming'
    }

    players = []
    raw_players = lb.get('players', [])

    for rp in raw_players:
        bio = rp.get('player_bio', {})
        first = bio.get('first_name', rp.get('first_name', ''))
        last = bio.get('last_name', rp.get('last_name', ''))
        name = f"{first} {last}".strip()

        if not name:
            name = rp.get('player_name', rp.get('name', 'Unknown'))

        # Parse total score
        total = rp.get('total', rp.get('total_to_par'))
        if isinstance(total, str):
            total = total.replace('+', '')
            if total == 'E' or total == '-':
                total = 0
            else:
                try:
                    total = int(total)
                except ValueError:
                    total = None

        # Parse today score
        today = rp.get('today', rp.get('today_to_par'))
        if isinstance(today, str):
            today = today.replace('+', '')
            if today == 'E' or today == '-' or today == '':
                today = 0
            else:
                try:
                    today = int(today)
                except ValueError:
                    today = None

        # Parse thru
        thru = rp.get('thru', '')
        if isinstance(thru, str) and thru in ('F', '18', '*'):
            thru = 'F'

        # Parse rounds
        rounds_raw = rp.get('rounds', [])
        rounds = []
        for r in rounds_raw:
            if isinstance(r, dict):
                strokes = r.get('strokes', r.get('round_score'))
                try:
                    rounds.append(int(strokes) if strokes and str(strokes) != '0' and str(strokes) != '--' else None)
                except (ValueError, TypeError):
                    rounds.append(None)
            elif isinstance(r, (int, float)):
                rounds.append(int(r) if r > 0 else None)
            else:
                rounds.append(None)

        # Pad rounds to 4
        while len(rounds) < 4:
            rounds.append(None)

        # Determine status
        status = rp.get('status', 'active')
        if isinstance(status, str):
            status = status.lower()
        if status in ('cut', 'mc'):
            status = 'cut'
        elif status in ('wd', 'withdrawn'):
            status = 'wd'
        elif status in ('dq', 'disqualified'):
            status = 'dq'
        else:
            status = 'active'

        players.append({
            'id': str(rp.get('player_id', rp.get('id', ''))),
            'name': name,
            'position': rp.get('current_position', rp.get('position', '--')),
            'total': total,
            'today': today,
            'thru': str(thru) if thru else '--',
            'rounds': rounds[:4],
            'status': status
        })

    return tournament_info, players


def parse_espn_leaderboard(data):
    """Parse ESPN Golf API format."""
    if not data:
        return None

    events = data.get('events', [])
    if not events:
        return None

    event = events[0]

    # Tournament info
    tournament_info = {
        'name': event.get('name', 'Unknown Tournament'),
        'id': TOURNAMENT_ID,
        'course': '',
        'current_round': 1,
        'status': 'in_progress'
    }

    # Try to get course
    courses = event.get('courses', [])
    if courses:
        tournament_info['course'] = courses[0].get('name', '')

    # Get status
    status_text = event.get('status', {}).get('type', {}).get('name', '')
    if status_text == 'STATUS_FINAL':
        tournament_info['status'] = 'completed'
    elif status_text == 'STATUS_IN_PROGRESS':
        tournament_info['status'] = 'in_progress'
    elif status_text == 'STATUS_SCHEDULED':
        tournament_info['status'] = 'upcoming'

    players = []

    competitions = event.get('competitions', [])
    if not competitions:
        return tournament_info, players

    competitors = competitions[0].get('competitors', [])

    for comp in competitors:
        athlete = comp.get('athlete', {})
        name = athlete.get('displayName', athlete.get('shortName', 'Unknown'))

        # Score
        score_data = comp.get('score', comp.get('linescores', []))
        total = comp.get('score')
        if isinstance(total, str):
            if total == 'E':
                total = 0
            else:
                try:
                    total = int(total.replace('+', ''))
                except ValueError:
                    total = None

        # Status
        status = 'active'
        comp_status = comp.get('status', {}).get('type', {}).get('name', '')
        if comp_status == 'STATUS_CUT':
            status = 'cut'
        elif comp_status == 'STATUS_WITHDRAWN':
            status = 'wd'

        # Rounds
        linescores = comp.get('linescores', [])
        rounds = []
        for ls in linescores[:4]:
            val = ls.get('value')
            if val and val > 0:
                rounds.append(int(val))
            else:
                rounds.append(None)
        while len(rounds) < 4:
            rounds.append(None)

        today = None
        thru = '--'

        # Position
        position = comp.get('status', {}).get('position', {}).get('displayName', '--')
        if position and position.isdigit():
            position = position

        players.append({
            'id': str(athlete.get('id', '')),
            'name': name,
            'position': position,
            'total': total,
            'today': today,
            'thru': thru,
            'rounds': rounds[:4],
            'status': status
        })

    return tournament_info, players


def load_existing_salaries():
    """Load existing salary data to preserve across updates."""
    path = os.path.join(DATA_DIR, 'leaderboard.json')
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        return {p['name']: p.get('salary', 0) for p in data.get('players', [])}
    except Exception:
        return {}


def main():
    print("=" * 50)
    print("Fantasy Golf League - Leaderboard Update")
    print(f"Tournament ID: {TOURNAMENT_ID}")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 50)

    result = None

    # Try PGA Tour endpoints first
    print("\n[1/3] Trying PGA Tour leaderboard-v2mini...")
    data = fetch_url(PGA_LEADERBOARD_MINI)
    if data:
        result = parse_pga_leaderboard(data)

    if not result:
        print("\n[2/3] Trying PGA Tour leaderboard-v2...")
        data = fetch_url(PGA_LEADERBOARD_FULL)
        if data:
            result = parse_pga_leaderboard(data)

    if not result:
        print("\n[3/3] Trying ESPN fallback...")
        data = fetch_url(ESPN_GOLF_URL)
        if data:
            result = parse_espn_leaderboard(data)

    if not result:
        print("\nERROR: Could not fetch leaderboard data from any source.")
        print("The data files were not updated.")
        sys.exit(1)

    tournament_info, players = result
    print(f"\nTournament: {tournament_info['name']}")
    print(f"Players found: {len(players)}")
    print(f"Status: {tournament_info['status']}")

    # Assign or preserve salaries
    existing_salaries = load_existing_salaries()
    has_existing = any(v > 0 for v in existing_salaries.values())

    if has_existing:
        # Preserve existing salaries, only assign to new players
        print("Preserving existing salary assignments...")
        new_players = []
        for p in players:
            if p['name'] in existing_salaries and existing_salaries[p['name']] > 0:
                p['salary'] = existing_salaries[p['name']]
            else:
                new_players.append(p)

        if new_players:
            # Assign salaries to new players based on their position in the list
            new_players = assign_salaries(new_players)
            for np in new_players:
                for p in players:
                    if p['name'] == np['name']:
                        p['salary'] = np['salary']
    else:
        print("Assigning initial salaries...")
        players = assign_salaries(players)

    # Build output
    output = {
        'tournament': tournament_info,
        'last_updated': datetime.now(timezone.utc).isoformat(),
        'players': players
    }

    # Ensure data directory exists
    os.makedirs(DATA_DIR, exist_ok=True)

    # Write leaderboard data
    output_path = os.path.join(DATA_DIR, 'leaderboard.json')
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\nData saved to: {output_path}")
    print(f"Total players: {len(players)}")

    # Print top 10
    active = [p for p in players if p['status'] == 'active']
    active.sort(key=lambda x: x.get('total', 999) if x.get('total') is not None else 999)
    print(f"\nTop 10:")
    for p in active[:10]:
        score_str = f"{'+' if p['total'] and p['total'] > 0 else ''}{p['total']}" if p['total'] is not None else '--'
        if p['total'] == 0:
            score_str = 'E'
        print(f"  {p['position']:>5} {p['name']:<25} {score_str:>5}  ${p.get('salary', 0):>6,}")

    print("\nUpdate complete!")


if __name__ == '__main__':
    main()
