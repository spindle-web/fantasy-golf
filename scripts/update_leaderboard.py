#!/usr/bin/env python3
"""
Fantasy Golf League - Leaderboard Update Script
Fetches PGA Tour leaderboard data and saves cleaned JSON for the website.

Data Sources (tried in order):
1. ESPN Golf Scoreboard API (primary - rich hole-by-hole data)
2. PGA Tour statdata leaderboard-v2mini (fallback)
3. PGA Tour statdata leaderboard-v2 (fallback)

ESPN API field mapping (site.api.espn.com/.../golf/pga/scoreboard):
  competitor.order          → leaderboard position (integer)
  competitor.score          → total score relative to par (string: "-10", "E", "+3")
  competitor.linescores[]   → array of round objects
    .value                  → total strokes for round (e.g. 69)
    .displayValue           → score relative to par for round (e.g. "-3")
    .period                 → round number (1-4)
    .linescores[]           → array of hole-by-hole scores
      .value                → strokes on that hole
      .period               → hole number (1-18)
      .scoreType.displayValue → relative to par ("-1", "E", "+1")

  Thru = len(current_round.linescores)  →  0=not started, 1-17=playing, 18=done
  Today = current_round.displayValue    →  "-3", "E", "+2" (relative to par)

Salary Assignment:
- Based on field position/ranking, normalized to fit salary cap constraints
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

# ESPN endpoint (primary)
ESPN_SCOREBOARD_URL = 'https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard'

# PGA Tour endpoints (fallback)
PGA_LEADERBOARD_MINI = f'https://statdata.pgatour.com/r/{TOURNAMENT_ID}/leaderboard-v2mini.json'
PGA_LEADERBOARD_FULL = f'https://statdata.pgatour.com/r/{TOURNAMENT_ID}/leaderboard-v2.json'

PAR = 72  # Standard par, could be made configurable per tournament


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
                player['position'] = prev['position']
            else:
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


def parse_espn_scoreboard(data):
    """
    Parse ESPN Golf Scoreboard API response.

    This uses the actual ESPN API structure where:
    - Position comes from competitor.order
    - Thru is derived from counting holes in the current round's linescores array
    - Today score is the current round's displayValue (relative to par)
    - Round strokes come from linescores[i].value
    """
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

    # Try to get course name
    courses = event.get('courses', [])
    if courses:
        tournament_info['course'] = courses[0].get('name', '')

    # Event-level status
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

    # Track overall round progress to determine current round and between-rounds state
    max_round_with_holes = 0
    players_with_mid_round = 0  # players currently on the course (1-17 holes)
    players_with_complete_round = {}  # round_num -> count of players who finished that round

    for c in competitors:
        athlete = c.get('athlete', {})
        name = athlete.get('displayName', athlete.get('shortName', 'Unknown'))

        # --- Total score (to par) ---
        total = None
        score_val = c.get('score')
        if isinstance(score_val, str):
            if score_val == 'E':
                total = 0
            else:
                try:
                    total = int(score_val.replace('+', ''))
                except (ValueError, TypeError):
                    total = None
        elif isinstance(score_val, (int, float)):
            total = int(score_val)

        # --- Position from order field ---
        position = str(c.get('order', '--'))
        if position == 'None' or position == '0':
            position = '--'

        # --- Parse linescores (rounds and holes) ---
        linescores = c.get('linescores', [])
        rounds = []
        today = None
        thru = '--'
        current_player_round = 0

        for round_obj in linescores[:4]:
            round_num = round_obj.get('period', len(rounds) + 1)
            stroke_total = round_obj.get('value')  # e.g. 69
            display_val = round_obj.get('displayValue', '')  # e.g. "-3" (relative to par)
            holes = round_obj.get('linescores', [])  # hole-by-hole array
            holes_played = len(holes)

            # Track round progress
            if holes_played > 0:
                max_round_with_holes = max(max_round_with_holes, round_num)
                current_player_round = round_num

            # Determine round stroke score
            round_strokes = None
            if stroke_total is not None:
                try:
                    sv = int(stroke_total)
                    if 55 <= sv <= 95:
                        round_strokes = sv
                except (ValueError, TypeError):
                    pass

            # If no valid stroke total but we have all 18 holes, sum them
            if round_strokes is None and holes_played == 18:
                try:
                    total_from_holes = sum(h.get('value', 0) for h in holes)
                    if 55 <= total_from_holes <= 95:
                        round_strokes = total_from_holes
                except (TypeError, ValueError):
                    pass

            rounds.append(round_strokes)

            # If this is the latest round with activity, extract today & thru
            if holes_played > 0 and round_num == current_player_round:
                if holes_played >= 18:
                    thru = 'F'
                    players_with_complete_round[round_num] = players_with_complete_round.get(round_num, 0) + 1
                else:
                    thru = str(holes_played)
                    players_with_mid_round += 1

                # Today score from round's displayValue (relative to par)
                if display_val:
                    if display_val == 'E' or display_val == '0':
                        today = 0
                    else:
                        try:
                            today = int(display_val.replace('+', ''))
                        except (ValueError, TypeError):
                            # If displayValue isn't a simple number, compute from strokes
                            if round_strokes is not None:
                                today = round_strokes - PAR
                elif round_strokes is not None and holes_played == 18:
                    today = round_strokes - PAR

                # For mid-round: compute today from holes played so far
                if holes_played < 18 and holes_played > 0 and today is None:
                    try:
                        strokes_so_far = sum(h.get('value', 0) for h in holes)
                        # Approximate: we don't know exact par per hole, but can use
                        # the scoreType data to compute relative to par
                        relative = 0
                        for h in holes:
                            score_type = h.get('scoreType', {}).get('displayValue', 'E')
                            if score_type == 'E':
                                pass  # par
                            else:
                                try:
                                    relative += int(score_type)
                                except (ValueError, TypeError):
                                    pass
                        today = relative
                    except (TypeError, ValueError):
                        pass

        # Pad rounds to always have 4 entries
        while len(rounds) < 4:
            rounds.append(None)

        # --- Status: determine cut/wd/dq ---
        # ESPN doesn't have an explicit status field in this endpoint format.
        # We infer from context:
        # - If a player has completed rounds 1-2 but has 0 holes in round 3/4
        #   AND their position/order is high, they likely missed the cut
        # - For now, mark as active; the enrich step will handle cut detection
        status = 'active'

        players.append({
            'id': str(athlete.get('id', c.get('id', ''))),
            'name': name,
            'position': position,
            'total': total,
            'today': today,
            'thru': thru,
            'rounds': rounds[:4],
            'status': status
        })

    # Determine current round from the data
    if max_round_with_holes > 0:
        tournament_info['current_round'] = max_round_with_holes

    # Determine if between rounds
    if players_with_mid_round == 0 and len(players) > 0:
        # Nobody is mid-round, check if most have completed the current round
        current_rd = tournament_info['current_round']
        completed = players_with_complete_round.get(current_rd, 0)
        active_count = sum(1 for p in players if p['status'] == 'active')
        if completed > active_count * 0.5 and active_count > 0:
            tournament_info['status'] = 'between_rounds'

    # Determine if play is in progress
    if players_with_mid_round > 0:
        tournament_info['status'] = 'in_progress'

    print(f"  ESPN parse: {len(players)} players, round={tournament_info['current_round']}, "
          f"mid_round={players_with_mid_round}, status={tournament_info['status']}")

    return tournament_info, players


def parse_pga_leaderboard(data):
    """Parse PGA Tour leaderboard-v2mini or leaderboard-v2 format (fallback)."""
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
    """Post-process: add tie prefixes to positions, detect cut status."""

    # Add T-prefix for tied positions
    # First, convert order-based positions to proper tied format
    pos_counts = {}
    for p in players:
        if p.get('status') not in ('cut', 'wd', 'dq') and p.get('position', '--') != '--':
            pos_val = p['position'].replace('T', '')  # Strip any existing T
            pos_counts[pos_val] = pos_counts.get(pos_val, 0) + 1

    for p in players:
        if p.get('position', '--') != '--':
            pos_val = p['position'].replace('T', '')
            if pos_counts.get(pos_val, 0) > 1:
                p['position'] = 'T' + pos_val
            else:
                p['position'] = pos_val

    # If most positions are missing, compute from scores
    missing_pos = sum(1 for p in players if p.get('position', '--') == '--')
    if missing_pos > len(players) * 0.5:
        print(f"  Computing positions ({missing_pos}/{len(players)} missing)...")
        players = compute_positions(players)

    # Auto-detect cut after round 2
    current_round = tournament_info.get('current_round', 1)
    if current_round >= 3:
        # If round 3+ has started, players with only 2 rounds of data missed the cut
        for p in players:
            if p['status'] != 'active':
                continue
            rounds = p.get('rounds', [None, None, None, None])
            # Has round 1 & 2 scores but no round 3 data and no holes in round 3
            has_r1 = rounds[0] is not None and 55 <= rounds[0] <= 95
            has_r2 = rounds[1] is not None and 55 <= rounds[1] <= 95
            has_r3 = rounds[2] is not None and 55 <= rounds[2] <= 95
            thru = p.get('thru', '--')

            if has_r1 and has_r2 and not has_r3 and thru in ('--', 'F', '0'):
                # Check if this player's thru for their last active round was F (round 2 complete)
                # If round 3 has started for others but this player has nothing, they missed the cut
                p['status'] = 'cut'

    elif current_round == 2:
        # Between round 2 rounds or after round 2: infer cut from scores
        between_rounds = tournament_info.get('status') == 'between_rounds'
        cut_count = sum(1 for p in players if p.get('status') == 'cut')

        if cut_count == 0 and between_rounds:
            active_with_scores = [p for p in players if p.get('total') is not None and p.get('status') == 'active']
            active_with_scores.sort(key=lambda p: p['total'])
            if len(active_with_scores) > 65:
                cut_score = active_with_scores[64]['total']
                made_cut = [p for p in active_with_scores if p['total'] <= cut_score]
                missed_cut = [p for p in active_with_scores if p['total'] > cut_score]
                if len(missed_cut) > 0:
                    cut_str = f"+{cut_score}" if cut_score > 0 else ('E' if cut_score == 0 else str(cut_score))
                    print(f"  Inferred cut line at {cut_str}: {len(made_cut)} made, {len(missed_cut)} missed")
                    for p in missed_cut:
                        p['status'] = 'cut'

    cut_count = sum(1 for p in players if p.get('status') == 'cut')
    if cut_count > 0:
        print(f"  Cut players: {cut_count}")

    return tournament_info, players


def main():
    print("=" * 50)
    print("Fantasy Golf League - Leaderboard Update")
    print(f"Tournament ID: {TOURNAMENT_ID}")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 50)

    result = None

    # Try ESPN Scoreboard first (primary - most reliable, richest data)
    print("
[1/3] Trying ESPN Scoreboard API...")
    data = fetch_url(ESPN_SCOREBOARD_URL)
    if data:
        result = parse_espn_scoreboard(data)
        if result and len(result[1]) > 0:
            print(f"  Success: {len(result[1])} players from ESPN scoreboard")

    # Fallback to PGA Tour endpoints
    if not result or len(result[1]) == 0:
        print("
[2/3] Trying PGA Tour leaderboard-v2mini...")
        data = fetch_url(PGA_LEADERBOARD_MINI)
        if data:
            result = parse_pga_leaderboard(data)
            if result and len(result[1]) > 0:
                print(f"  Success: {len(result[1])} players from PGA v2mini")

    if not result or len(result[1]) == 0:
        print("
[3/3] Trying PGA Tour leaderboard-v2...")
        data = fetch_url(PGA_LEADERBOARD_FULL)
        if data:
            result = parse_pga_leaderboard(data)
            if result and len(result[1]) > 0:
                print(f"  Success: {len(result[1])} players from PGA v2")

    if not result or len(result[1]) == 0:
        print("
ERROR: Could not fetch leaderboard data from any source.")
        print("The data files were not updated.")
        sys.exit(1)

    tournament_info, players = result
    print(f"
Tournament: {tournament_info['name']}")
    print(f"Players found: {len(players)}")
    print(f"Raw status: {tournament_info['status']}")

    # Enrich data (tie positions, cut detection)
    print("
Enriching player data...")
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

    print(f"
Data saved to: {output_path}")
    print(f"Total players: {len(players)}")

    # Stats
    active = [p for p in players if p['status'] == 'active']
    cut = [p for p in players if p['status'] == 'cut']
    wd = [p for p in players if p['status'] == 'wd']
    print(f"Active: {len(active)}, Cut: {len(cut)}, WD: {len(wd)}")

    # Print top 10
    print(f"
Top 10:")
    for p in active[:10]:
        score_str = f"{'+' if p['total'] and p['total'] > 0 else ''}{p['total']}" if p['total'] is not None else '--'
        if p.get('total') == 0:
            score_str = 'E'
        today_str = f"{'+' if p.get('today') and p['today'] > 0 else ''}{p.get('today', '--')}" if p.get('today') is not None else '--'
        if p.get('today') == 0:
            today_str = 'E'
        print(f"  {p.get('position', '--'):>5} {p['name']:<25} {score_str:>5}  Today: {today_str:>4}  Thru: {p.get('thru', '--'):<3}  ${p.get('salary', 0):>6,}")

    print("
Update complete!")


if __name__ == '__main__':
    main()
