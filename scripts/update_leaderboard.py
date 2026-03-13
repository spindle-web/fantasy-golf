#!/usr/bin/env python3
"""
Fantasy Golf League - Leaderboard Update Script
Fetches PGA Tour leaderboard data and saves cleaned JSON for the website.

Data Sources (tried in order):
1. ESPN Golf Leaderboard API (primary - most reliable)
2. PGA Tour statdata leaderboard-v2mini
3. PGA Tour statdata leaderboard-v2

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
PGA_LEADERBOARD_MINI = f'https://statdata.pgatour.com/r/{TOURNAMENT_ID}/leaderboard-v2mini.json'
PGA_LEADERBOARD_FULL = f'https://statdata.pgatour.com/r/{TOURNAMENT_ID}/leaderboard-v2.json'

# ESPN endpoints (try multiple patterns)
ESPN_SCOREBOARD_URL = 'https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard'
ESPN_CDN_LEADERBOARD = 'https://cdn.espn.com/core/golf/pga/leaderboard?xhr=1'


def fetch_url(url, timeout=15):
    """Fetch URL and return parsed JSON."""
    print(f"  Fetching: {url}")
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/html, */*',
            'Accept-Language': 'en-US,en;q=0.9'
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
        pct = i / max(n - 1, 1)
        min_salary = 5000
        max_salary = 18500
        salary = max_salary - (max_salary - min_salary) * (pct ** 0.6)
        salary = round(salary / 500) * 500
        salary = max(min_salary, min(max_salary, salary))
        player['salary'] = int(salary)

    return players


def compute_positions(players):
    """Compute leaderboard positions from total scores, handling ties."""
    # Sort by total (None/large values last)
    sorted_players = sorted(players, key=lambda p: p.get('total', 999) if p.get('total') is not None else 999)

    pos = 1
    for i, player in enumerate(sorted_players):
        if player.get('status') in ('cut', 'wd', 'dq'):
            continue
        if player.get('total') is None:
            player['position'] = '--'
            continue

        if i > 0:
            prev = sorted_players[i - 1]
            if prev.get('total') == player.get('total') and prev.get('status') not in ('cut', 'wd', 'dq'):
                player['position'] = prev['position']  # Tie - same position
            else:
                pos = i + 1
                # Count how many ahead (excluding cut/wd)
                active_ahead = sum(1 for p in sorted_players[:i] if p.get('status') not in ('cut', 'wd', 'dq') and p.get('total') is not None)
                pos = active_ahead + 1
                player['position'] = str(pos)
        else:
            player['position'] = '1'

    # Add 'T' prefix for ties
    pos_counts = {}
    for p in sorted_players:
        if p.get('status') not in ('cut', 'wd', 'dq') and p.get('position', '--') != '--':
            pos_counts[p['position']] = pos_counts.get(p['position'], 0) + 1

    for p in sorted_players:
        if p.get('position', '--') != '--' and pos_counts.get(p['position'], 0) > 1:
            p['position'] = 'T' + p['position']

    return sorted_players


def detect_round_info(players):
    """Detect current round and whether play is in progress from player data."""
    rounds_with_data = [0, 0, 0, 0]  # Count of players with data in each round
    valid_round_scores = [0, 0, 0, 0]  # Count of valid stroke scores (60-90 range)

    for p in players:
        rounds = p.get('rounds', [None, None, None, None])
        for i, r in enumerate(rounds[:4]):
            if r is not None:
                rounds_with_data[i] += 1
                if 55 <= r <= 95:  # Valid golf round score range
                    valid_round_scores[i] += 1

    active_count = sum(1 for p in players if p.get('status') not in ('cut', 'wd', 'dq'))

    # Determine current round: last round where significant number of players have valid scores
    current_round = 1
    for i in range(4):
        if valid_round_scores[i] > active_count * 0.3:  # At least 30% of active players
            current_round = i + 1

    # Detect if play is in progress (some players have thru != 'F' and thru != '--')
    in_progress = False
    for p in players:
        thru = str(p.get('thru', '--'))
        if thru not in ('F', '--', '18', '', 'None'):
            try:
                holes = int(thru)
                if 1 <= holes <= 17:
                    in_progress = True
                    break
            except (ValueError, TypeError):
                pass

    # Check if between rounds: current round scores are complete for most players
    between_rounds = False
    if not in_progress and current_round <= 4:
        completed_current = valid_round_scores[current_round - 1] if current_round <= 4 else 0
        if completed_current > active_count * 0.7:  # 70%+ have completed the round
            between_rounds = True

    return {
        'current_round': current_round,
        'in_progress': in_progress,
        'between_rounds': between_rounds,
        'rounds_with_data': rounds_with_data,
        'valid_round_scores': valid_round_scores
    }


def compute_today_and_thru(players, round_info):
    """Compute today's score and thru holes from available round data."""
    current_round = round_info['current_round']
    between_rounds = round_info['between_rounds']
    par = 72  # Standard par, could be made configurable

    for p in players:
        rounds = p.get('rounds', [None, None, None, None])

        # If player already has valid today/thru from the API, keep them
        if p.get('today') is not None and p.get('thru') not in ('--', '', None):
            continue

        if p.get('status') in ('cut', 'wd', 'dq'):
            p['today'] = None
            p['thru'] = '--'
            continue

        # Get the current round score
        current_rd_score = rounds[current_round - 1] if current_round <= 4 else None

        if between_rounds or (current_rd_score is not None and 55 <= current_rd_score <= 95):
            # Round is complete - compute today relative to par
            if current_rd_score is not None and 55 <= current_rd_score <= 95:
                p['today'] = current_rd_score - par
                p['thru'] = 'F'
            else:
                p['today'] = None
                p['thru'] = '--'
        else:
            # Mid-round: if we have total and previous rounds, compute today
            if p.get('total') is not None:
                prev_total = 0
                for i in range(current_round - 1):
                    if rounds[i] is not None and 55 <= rounds[i] <= 95:
                        prev_total += (rounds[i] - par)
                # today = total - previous rounds total
                p['today'] = p['total'] - prev_total
            else:
                p['today'] = None
            # Thru stays as-is from API, or '--' if not set
            if p.get('thru') in ('--', '', None, 'None'):
                p['thru'] = '--'

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
                    val = int(strokes) if strokes and str(strokes) != '0' and str(strokes) != '--' else None
                    # Validate round score is reasonable (55-95 range)
                    if val is not None and (val < 55 or val > 95):
                        val = None
                    rounds.append(val)
                except (ValueError, TypeError):
                    rounds.append(None)
            elif isinstance(r, (int, float)):
                val = int(r) if 55 <= r <= 95 else None
                rounds.append(val)
            else:
                rounds.append(None)

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


def parse_espn_scoreboard(data):
    """Parse ESPN Golf Scoreboard API format with robust field extraction."""
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
    status_obj = event.get('status', {})
    status_type = status_obj.get('type', {}).get('name', '')
    if status_type == 'STATUS_FINAL':
        tournament_info['status'] = 'completed'
    elif status_type == 'STATUS_IN_PROGRESS':
        tournament_info['status'] = 'in_progress'
    elif status_type == 'STATUS_SCHEDULED':
        tournament_info['status'] = 'upcoming'

    players = []

    competitions = event.get('competitions', [])
    if not competitions:
        return tournament_info, players

    comp = competitions[0]
    competitors = comp.get('competitors', [])

    for c in competitors:
        athlete = c.get('athlete', {})
        name = athlete.get('displayName', athlete.get('shortName', 'Unknown'))

        # Total score (to par)
        total = None
        score_val = c.get('score')
        score_display = c.get('scoreDisplayValue', c.get('score', ''))
        if isinstance(score_val, (int, float)):
            total = int(score_val)
        elif isinstance(score_val, str):
            if score_val == 'E':
                total = 0
            else:
                try:
                    total = int(score_val.replace('+', ''))
                except (ValueError, TypeError):
                    total = None

        # Status
        status = 'active'
        comp_status = c.get('status', {})
        comp_status_type = comp_status.get('type', {}).get('name', '')
        if comp_status_type in ('STATUS_CUT', 'CUT'):
            status = 'cut'
        elif comp_status_type in ('STATUS_WITHDRAWN', 'WD'):
            status = 'wd'
        elif comp_status_type in ('STATUS_DQ', 'DQ'):
            status = 'dq'

        # Position
        position = '--'
        pos_obj = comp_status.get('position', {})
        if pos_obj:
            pos_display = pos_obj.get('displayName', pos_obj.get('abbreviation', ''))
            if pos_display:
                position = str(pos_display)

        # If position not in status, check sortOrder
        if position == '--':
            sort_order = c.get('sortOrder')
            if sort_order is not None:
                position = str(sort_order)

        # Today score and thru from status
        today = None
        thru = '--'

        # Try to get today's score from the competitor status or period
        period = comp_status.get('period', 0)
        if period:
            tournament_info['current_round'] = max(tournament_info['current_round'], int(period))

        # thru from status
        thru_val = comp_status.get('thru', comp_status.get('displayValue', ''))
        if thru_val:
            if str(thru_val) in ('F', '18', '*'):
                thru = 'F'
            elif str(thru_val) == '--' or str(thru_val) == '':
                thru = '--'
            else:
                try:
                    h = int(thru_val)
                    if 1 <= h <= 18:
                        thru = str(h)
                except (ValueError, TypeError):
                    thru = str(thru_val)

        # Today from displayValue on score or status
        today_val = c.get('todayScore', c.get('currentRoundScore'))
        if today_val is not None:
            if isinstance(today_val, (int, float)):
                today = int(today_val)
            elif isinstance(today_val, str):
                if today_val == 'E':
                    today = 0
                else:
                    try:
                        today = int(today_val.replace('+', ''))
                    except (ValueError, TypeError):
                        pass

        # Rounds from linescores
        linescores = c.get('linescores', [])
        rounds = []
        for ls in linescores[:4]:
            # Prefer displayValue (actual strokes) over value (which might be something else)
            display_val = ls.get('displayValue', '')
            value = ls.get('value')

            stroke_score = None

            # Try displayValue first (usually the actual stroke count like "69")
            if display_val:
                try:
                    sv = int(display_val)
                    if 55 <= sv <= 95:
                        stroke_score = sv
                except (ValueError, TypeError):
                    pass

            # Fallback to value, but validate it's a reasonable golf score
            if stroke_score is None and value is not None:
                try:
                    sv = int(value)
                    if 55 <= sv <= 95:
                        stroke_score = sv
                except (ValueError, TypeError):
                    pass

            rounds.append(stroke_score)

        while len(rounds) < 4:
            rounds.append(None)

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


def parse_espn_cdn(data):
    """Parse ESPN CDN leaderboard format (different structure)."""
    if not data:
        return None

    # CDN format nests data under 'content'
    content = data.get('content', data)
    leaderboard = content.get('leaderboard', content)

    if not leaderboard:
        return None

    # Try to find the competitors/players array
    competitors = leaderboard.get('competitors', leaderboard.get('players', []))
    if not competitors:
        return None

    tournament_info = {
        'name': content.get('sbData', {}).get('name', leaderboard.get('header', {}).get('eventName', 'Unknown')),
        'id': TOURNAMENT_ID,
        'course': '',
        'current_round': 1,
        'status': 'in_progress'
    }

    players = []
    for c in competitors:
        name = c.get('displayName', c.get('name', c.get('athlete', {}).get('displayName', 'Unknown')))
        total = c.get('totalToPar', c.get('score', c.get('total')))
        if isinstance(total, str):
            if total == 'E':
                total = 0
            else:
                try:
                    total = int(total.replace('+', ''))
                except:
                    total = None

        status = 'active'
        s = str(c.get('status', '')).lower()
        if s in ('cut', 'mc'):
            status = 'cut'
        elif s in ('wd', 'withdrawn'):
            status = 'wd'

        rounds = []
        for r in c.get('rounds', c.get('linescores', []))[:4]:
            if isinstance(r, dict):
                val = r.get('displayValue', r.get('score', r.get('value')))
            else:
                val = r
            try:
                sv = int(val)
                rounds.append(sv if 55 <= sv <= 95 else None)
            except:
                rounds.append(None)
        while len(rounds) < 4:
            rounds.append(None)

        players.append({
            'id': str(c.get('id', '')),
            'name': name,
            'position': str(c.get('position', c.get('rank', '--'))),
            'total': total,
            'today': c.get('today', c.get('todayToPar', None)),
            'thru': str(c.get('thru', '--')),
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


def enrich_player_data(tournament_info, players):
    """Post-process: compute positions, today/thru, detect rounds and cut."""
    # Detect round info
    round_info = detect_round_info(players)
    print(f"  Round detection: round={round_info['current_round']}, in_progress={round_info['in_progress']}, between_rounds={round_info['between_rounds']}")
    print(f"  Valid round scores per round: {round_info['valid_round_scores']}")

    # Update tournament info
    tournament_info['current_round'] = round_info['current_round']
    if round_info['between_rounds']:
        tournament_info['status'] = 'between_rounds'
    elif round_info['in_progress']:
        tournament_info['status'] = 'in_progress'

    # Compute positions if most are missing
    missing_pos = sum(1 for p in players if p.get('position', '--') == '--')
    if missing_pos > len(players) * 0.5:
        print(f"  Computing positions ({missing_pos}/{len(players)} missing)...")
        players = compute_positions(players)

    # Compute today/thru if missing
    missing_today = sum(1 for p in players if p.get('today') is None and p.get('status') == 'active')
    if missing_today > len(players) * 0.3:
        print(f"  Computing today/thru ({missing_today} missing today)...")
        players = compute_today_and_thru(players, round_info)

    # Auto-detect cut after round 2
    if round_info['current_round'] >= 2:
        cut_count = sum(1 for p in players if p.get('status') == 'cut')
        if cut_count == 0 and round_info['between_rounds'] and round_info['current_round'] == 2:
            # After round 2, if no cuts detected, try to infer from scores
            # Typical PGA cut: top 65 and ties make the cut
            active_with_scores = [p for p in players if p.get('total') is not None and p.get('status') == 'active']
            active_with_scores.sort(key=lambda p: p['total'])
            if len(active_with_scores) > 65:
                # Find the score at position 65
                cut_score = active_with_scores[64]['total']
                # Include all tied at the cut line
                made_cut = [p for p in active_with_scores if p['total'] <= cut_score]
                missed_cut = [p for p in active_with_scores if p['total'] > cut_score]
                if len(missed_cut) > 0:
                    print(f"  Inferred cut line at {'+' + str(cut_score) if cut_score > 0 else ('E' if cut_score == 0 else str(cut_score))}: {len(made_cut)} made, {len(missed_cut)} missed")
                    for p in missed_cut:
                        p['status'] = 'cut'

    return tournament_info, players


def main():
    print("=" * 50)
    print("Fantasy Golf League - Leaderboard Update")
    print(f"Tournament ID: {TOURNAMENT_ID}")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 50)

    result = None

    # Try PGA Tour endpoints first (most data)
    print("\n[1/4] Trying PGA Tour leaderboard-v2mini...")
    data = fetch_url(PGA_LEADERBOARD_MINI)
    if data:
        result = parse_pga_leaderboard(data)
        if result and len(result[1]) > 0:
            print(f"  Success: {len(result[1])} players from PGA v2mini")

    if not result or len(result[1]) == 0:
        print("\n[2/4] Trying PGA Tour leaderboard-v2...")
        data = fetch_url(PGA_LEADERBOARD_FULL)
        if data:
            result = parse_pga_leaderboard(data)
            if result and len(result[1]) > 0:
                print(f"  Success: {len(result[1])} players from PGA v2")

    if not result or len(result[1]) == 0:
        print("\n[3/4] Trying ESPN Scoreboard API...")
        data = fetch_url(ESPN_SCOREBOARD_URL)
        if data:
            result = parse_espn_scoreboard(data)
            if result and len(result[1]) > 0:
                print(f"  Success: {len(result[1])} players from ESPN scoreboard")

    if not result or len(result[1]) == 0:
        print("\n[4/4] Trying ESPN CDN leaderboard...")
        data = fetch_url(ESPN_CDN_LEADERBOARD)
        if data:
            result = parse_espn_cdn(data)
            if result and len(result[1]) > 0:
                print(f"  Success: {len(result[1])} players from ESPN CDN")

    if not result or len(result[1]) == 0:
        print("\nERROR: Could not fetch leaderboard data from any source.")
        print("The data files were not updated.")
        sys.exit(1)

    tournament_info, players = result
    print(f"\nTournament: {tournament_info['name']}")
    print(f"Players found: {len(players)}")
    print(f"Raw status: {tournament_info['status']}")

    # Enrich data (compute positions, today/thru, detect cuts)
    print("\nEnriching player data...")
    tournament_info, players = enrich_player_data(tournament_info, players)

    # Sort by total score
    players.sort(key=lambda p: (
        0 if p.get('status') == 'active' else 1,  # Active first
        p.get('total', 999) if p.get('total') is not None else 999
    ))

    # Assign or preserve salaries
    existing_salaries = load_existing_salaries()
    has_existing = any(v > 0 for v in existing_salaries.values())

    if has_existing:
        print("Preserving existing salary assignments...")
        new_players = []
        for p in players:
            if p['name'] in existing_salaries and existing_salaries[p['name']] > 0:
                p['salary'] = existing_salaries[p['name']]
            else:
                new_players.append(p)

        if new_players:
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

    # Stats
    active = [p for p in players if p['status'] == 'active']
    cut = [p for p in players if p['status'] == 'cut']
    wd = [p for p in players if p['status'] == 'wd']
    print(f"Active: {len(active)}, Cut: {len(cut)}, WD: {len(wd)}")

    # Print top 10
    print(f"\nTop 10:")
    for p in active[:10]:
        score_str = f"{'+' if p['total'] and p['total'] > 0 else ''}{p['total']}" if p['total'] is not None else '--'
        if p.get('total') == 0:
            score_str = 'E'
        today_str = f"{'+' if p.get('today') and p['today'] > 0 else ''}{p.get('today', '--')}" if p.get('today') is not None else '--'
        if p.get('today') == 0:
            today_str = 'E'
        print(f"  {p.get('position', '--'):>5} {p['name']:<25} {score_str:>5}  Today: {today_str:>4}  Thru: {p.get('thru', '--'):<3}  ${p.get('salary', 0):>6,}")

    print("\nUpdate complete!")


if __name__ == '__main__':
    main()
