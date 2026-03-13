/* Fantasy Golf League - Application Logic */

(function() {
  'use strict';

  // ==================== CONFIG ====================
  const CONFIG = {
    dataPath: 'data/',
    teamsAPI: 'https://api.npoint.io/bee59cd9fbe9a9291022',
    defaultSalaryCap: 50000,
    defaultGolfersPerTeam: 4,
    missedCutPenalty: 10,
    par: 72,  // Standard course par
    cacheBuster: () => '?t=' + Math.floor(Date.now() / 60000) // 1-min cache
  };

  // ==================== STATE ====================
  let leaderboardData = null;
  let teamsData = null;
  let selectedGolfers = [];
  let playerName = '';

  // ==================== UTILITIES ====================
  async function fetchJSON(file) {
    try {
      const resp = await fetch(CONFIG.dataPath + file + CONFIG.cacheBuster());
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return await resp.json();
    } catch (e) {
      console.warn(`Failed to load ${file}:`, e);
      return null;
    }
  }

  function formatScore(score) {
    if (score === null || score === undefined || score === '--') return '--';
    const n = Number(score);
    if (isNaN(n)) return score;
    if (n === 0) return 'E';
    return n > 0 ? '+' + n : String(n);
  }

  function scoreClass(score) {
    if (score === null || score === undefined || score === '--' || score === 'E') return 'score-even';
    const n = Number(score);
    if (isNaN(n)) return 'score-even';
    if (n < 0) return 'score-under';
    if (n > 0) return 'score-over';
    return 'score-even';
  }

  function formatMoney(n) {
    return '$' + Number(n).toLocaleString();
  }

  function formatTime(iso) {
    if (!iso) return '--';
    try {
      const d = new Date(iso);
      return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true });
    } catch { return iso; }
  }

  function formatTimeAgo(iso) {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      const now = new Date();
      const diffMs = now - d;
      const diffMins = Math.floor(diffMs / 60000);
      const diffHours = Math.floor(diffMins / 60);
      const diffDays = Math.floor(diffHours / 24);

      if (diffMins < 1) return 'just now';
      if (diffMins < 60) return `${diffMins}m ago`;
      if (diffHours < 24) return `${diffHours}h ${diffMins % 60}m ago`;
      return `${diffDays}d ago`;
    } catch { return ''; }
  }

  function showToast(msg) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = msg;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 2500);
  }

  function getTier(salary) {
    if (salary >= 14000) return 1;
    if (salary >= 11000) return 2;
    if (salary >= 8500) return 3;
    if (salary >= 7000) return 4;
    return 5;
  }

  function getSettings() {
    if (!teamsData || !teamsData.settings) {
      return { golfers_per_team: CONFIG.defaultGolfersPerTeam, salary_cap: CONFIG.defaultSalaryCap, missed_cut_penalty: CONFIG.missedCutPenalty };
    }
    return teamsData.settings;
  }

  function isValidRoundScore(score) {
    return score !== null && score !== undefined && score >= 55 && score <= 95;
  }

  // ==================== CLIENT-SIDE DATA ENRICHMENT ====================
  function enrichLeaderboardData() {
    if (!leaderboardData || !leaderboardData.players) return;

    const players = leaderboardData.players;
    const tournament = leaderboardData.tournament || {};

    // 1. Detect current round from round data
    const roundCounts = [0, 0, 0, 0];
    const activeCount = players.filter(p => p.status !== 'cut' && p.status !== 'wd' && p.status !== 'dq').length;

    players.forEach(p => {
      const rounds = p.rounds || [];
      rounds.forEach((r, i) => {
        if (isValidRoundScore(r)) roundCounts[i]++;
      });
    });

    let currentRound = 1;
    for (let i = 0; i < 4; i++) {
      if (roundCounts[i] > activeCount * 0.3) {
        currentRound = i + 1;
      }
    }
    tournament.current_round = currentRound;

    // 2. Detect if between rounds (most players have completed current round)
    const currentRoundComplete = roundCounts[currentRound - 1] > activeCount * 0.7;
    const anyMidRound = players.some(p => {
      const thru = String(p.thru || '');
      if (thru === 'F' || thru === '--' || thru === '18' || thru === '') return false;
      const h = parseInt(thru);
      return !isNaN(h) && h >= 1 && h <= 17;
    });

    const betweenRounds = currentRoundComplete && !anyMidRound;

    // 3. Determine tournament status
    if (tournament.status === 'completed' || tournament.status === 'Official') {
      // Keep as-is
    } else if (betweenRounds) {
      tournament.status = 'between_rounds';
    } else if (anyMidRound) {
      tournament.status = 'in_progress';
    }

    // 4. Compute positions if most are missing
    const missingPos = players.filter(p => !p.position || p.position === '--').length;
    if (missingPos > players.length * 0.5) {
      computePositions(players);
    }

    // 5. Compute today/thru if missing
    const missingToday = players.filter(p => p.today === null || p.today === undefined).length;
    if (missingToday > players.length * 0.3) {
      computeTodayAndThru(players, currentRound, betweenRounds);
    }

    // 6. Sort players: active by total, then cut/wd
    players.sort((a, b) => {
      const aActive = a.status === 'active' ? 0 : 1;
      const bActive = b.status === 'active' ? 0 : 1;
      if (aActive !== bActive) return aActive - bActive;
      const aTotal = a.total !== null && a.total !== undefined ? a.total : 999;
      const bTotal = b.total !== null && b.total !== undefined ? b.total : 999;
      return aTotal - bTotal;
    });

    leaderboardData.tournament = tournament;
  }

  function computePositions(players) {
    // Only compute for active players with scores
    const active = players.filter(p => p.status === 'active' && p.total !== null && p.total !== undefined);
    active.sort((a, b) => a.total - b.total);

    // Assign positions with tie handling
    for (let i = 0; i < active.length; i++) {
      if (i > 0 && active[i].total === active[i - 1].total) {
        active[i]._rawPos = active[i - 1]._rawPos;
      } else {
        active[i]._rawPos = i + 1;
      }
    }

    // Count players at each position to determine ties
    const posCounts = {};
    active.forEach(p => {
      posCounts[p._rawPos] = (posCounts[p._rawPos] || 0) + 1;
    });

    // Assign display positions with T prefix for ties
    active.forEach(p => {
      const prefix = posCounts[p._rawPos] > 1 ? 'T' : '';
      p.position = prefix + p._rawPos;
      delete p._rawPos;
    });
  }

  function computeTodayAndThru(players, currentRound, betweenRounds) {
    players.forEach(p => {
      // Skip if already has valid data
      if (p.today !== null && p.today !== undefined && p.thru && p.thru !== '--') return;

      if (p.status === 'cut' || p.status === 'wd' || p.status === 'dq') {
        p.today = null;
        p.thru = '--';
        return;
      }

      const rounds = p.rounds || [];
      const currentRdScore = rounds[currentRound - 1];

      if (isValidRoundScore(currentRdScore)) {
        // Round complete
        p.today = currentRdScore - CONFIG.par;
        p.thru = 'F';
      } else if (p.total !== null && p.total !== undefined) {
        // Mid-round or no round score: derive today from total minus previous rounds
        let prevTotal = 0;
        for (let i = 0; i < currentRound - 1; i++) {
          if (isValidRoundScore(rounds[i])) {
            prevTotal += (rounds[i] - CONFIG.par);
          }
        }
        p.today = p.total - prevTotal;
        // If between rounds and no current round score, set thru to F for last completed round
        if (betweenRounds) {
          p.thru = 'F';
        }
      }
    });
  }

  // ==================== FANTASY SCORING ====================
  function calculateFantasyScore(golferName) {
    if (!leaderboardData || !leaderboardData.players) return { score: null, status: 'unknown', position: '--' };

    const player = leaderboardData.players.find(p =>
      p.name.toLowerCase() === golferName.toLowerCase()
    );

    if (!player) return { score: null, status: 'not_found', position: '--' };

    const settings = getSettings();
    let score = player.total;

    if (player.status === 'cut' || player.status === 'CUT') {
      // Add missed cut penalty for weekend rounds
      const roundsPlayed = (player.rounds || []).filter(r => isValidRoundScore(r)).length;
      const weekendRoundsMissed = Math.max(0, 4 - roundsPlayed);
      score = (score || 0) + (weekendRoundsMissed * settings.missed_cut_penalty);
    }

    return {
      score: score,
      status: player.status,
      position: player.position || '--',
      today: player.today,
      thru: player.thru,
      salary: player.salary
    };
  }

  function calculateTeamScore(team) {
    if (!team.golfers || !leaderboardData) return { total: null, golferScores: [] };

    const golferScores = team.golfers.map(name => {
      const result = calculateFantasyScore(name);
      return { name, ...result };
    });

    const validScores = golferScores.filter(g => g.score !== null);
    const total = validScores.length > 0 ? validScores.reduce((sum, g) => sum + g.score, 0) : null;

    return { total, golferScores };
  }

  function getFantasyStandings() {
    if (!teamsData || !teamsData.teams) return [];

    const standings = teamsData.teams.map(team => {
      const { total, golferScores } = calculateTeamScore(team);
      const totalSalary = (team.golfers || []).reduce((sum, name) => {
        const player = leaderboardData?.players?.find(p => p.name.toLowerCase() === name.toLowerCase());
        return sum + (player?.salary || 0);
      }, 0);

      return {
        name: team.name,
        total,
        golferScores,
        totalSalary
      };
    });

    standings.sort((a, b) => {
      if (a.total === null && b.total === null) return 0;
      if (a.total === null) return 1;
      if (b.total === null) return -1;
      return a.total - b.total;
    });

    return standings;
  }

  // ==================== STATUS BADGE LOGIC ====================
  function getStatusBadge(tournament) {
    const status = tournament?.status || 'unknown';

    if (status === 'completed' || status === 'Official') {
      return { className: 'badge badge-completed', text: 'Final' };
    }
    if (status === 'in_progress' || status === 'In Progress') {
      return { className: 'badge badge-live', text: 'Live' };
    }
    if (status === 'between_rounds') {
      const round = tournament.current_round || 1;
      return { className: 'badge badge-between', text: `R${round} Complete` };
    }
    if (status === 'upcoming') {
      return { className: 'badge badge-upcoming', text: 'Upcoming' };
    }
    return { className: 'badge badge-upcoming', text: status };
  }

  function applyStatusBadge(badgeEl, tournament) {
    if (!badgeEl) return;
    const badge = getStatusBadge(tournament);
    badgeEl.className = badge.className;
    badgeEl.textContent = badge.text;
  }

  // ==================== LAST UPDATED DISPLAY ====================
  function renderLastUpdated(containerId) {
    const el = document.getElementById(containerId);
    if (!el || !leaderboardData?.last_updated) return;

    const timeAgo = formatTimeAgo(leaderboardData.last_updated);
    const fullTime = formatTime(leaderboardData.last_updated);

    el.innerHTML = `<span class="last-updated-text" title="${fullTime}">Updated ${timeAgo}</span>`;

    // Update every 30 seconds
    setInterval(() => {
      const newTimeAgo = formatTimeAgo(leaderboardData.last_updated);
      el.innerHTML = `<span class="last-updated-text" title="${fullTime}">Updated ${newTimeAgo}</span>`;
    }, 30000);
  }

  // ==================== CLOUD TEAMS API ====================
  async function fetchCloudTeams() {
    try {
      const resp = await fetch(CONFIG.teamsAPI + CONFIG.cacheBuster());
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      return data.teams || [];
    } catch (e) {
      console.warn('Failed to load cloud teams:', e);
      return [];
    }
  }

  async function saveCloudTeams(teams) {
    const resp = await fetch(CONFIG.teamsAPI, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ teams })
    });
    if (!resp.ok) throw new Error(`Save failed: HTTP ${resp.status}`);
    return await resp.json();
  }

  // ==================== DATA LOADING ====================
  async function loadAllData() {
    const [lb, localTeams, cloudTeams] = await Promise.all([
      fetchJSON('leaderboard.json'),
      fetchJSON('teams.json'),
      fetchCloudTeams()
    ]);

    leaderboardData = lb;

    // Use settings from local teams.json, but teams from cloud
    teamsData = localTeams || { settings: {}, teams: [] };
    teamsData.teams = cloudTeams;

    // Client-side data enrichment (fills in missing positions, today, thru)
    enrichLeaderboardData();

    // Load saved state from localStorage
    const savedPicks = localStorage.getItem('fg_selected_golfers');
    const savedName = localStorage.getItem('fg_player_name');
    if (savedPicks) {
      try { selectedGolfers = JSON.parse(savedPicks); } catch { selectedGolfers = []; }
    }
    if (savedName) playerName = savedName;

    return { leaderboardData, teamsData };
  }

  // ==================== HOME PAGE ====================
  function initHome() {
    if (!leaderboardData) {
      document.getElementById('quick-standings-body').innerHTML = '<div class="empty-state"><h3>No Data Yet</h3><p>Leaderboard data will appear once the update script runs.</p></div>';
      document.getElementById('quick-leaderboard-body').innerHTML = '<div class="empty-state"><h3>No Data Yet</h3><p>Waiting for tournament data...</p></div>';
      return;
    }

    const t = leaderboardData.tournament || {};
    document.getElementById('tournament-name').textContent = t.name || 'Tournament';
    document.getElementById('tournament-course').textContent = t.course || '';
    document.getElementById('tournament-round').textContent = t.current_round ? `Round ${t.current_round}` : '--';
    document.getElementById('last-update').textContent = formatTimeAgo(leaderboardData.last_updated);

    const teamCount = teamsData?.teams?.length || 0;
    document.getElementById('team-count').textContent = `${teamCount} team${teamCount !== 1 ? 's' : ''}`;

    // Stats grid
    const settings = getSettings();
    const statsHTML = `
      <div class="stat-card">
        <div class="stat-label">Format</div>
        <div class="stat-value">${settings.golfers_per_team}</div>
        <div class="stat-detail">golfers per team</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Salary Cap</div>
        <div class="stat-value">${formatMoney(settings.salary_cap)}</div>
        <div class="stat-detail">per team</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Field Size</div>
        <div class="stat-value">${leaderboardData.players?.length || '--'}</div>
        <div class="stat-detail">golfers</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Cut Penalty</div>
        <div class="stat-value">+${settings.missed_cut_penalty}</div>
        <div class="stat-detail">per weekend round</div>
      </div>
    `;
    document.getElementById('stats-grid').innerHTML = statsHTML;

    // Quick fantasy standings
    const standings = getFantasyStandings();
    if (standings.length === 0) {
      document.getElementById('quick-standings-body').innerHTML = '<div class="empty-state"><h3>No Teams Yet</h3><p>Head to the Picks page to build your team.</p></div>';
    } else {
      const rows = standings.slice(0, 5).map((team, i) => `
        <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 0;${i > 0 ? 'border-top:1px solid var(--border-light);' : ''}">
          <div style="display:flex;align-items:center;gap:10px;">
            <span class="team-rank ${i < 3 ? 'rank-' + (i+1) : 'rank-other'}">${i+1}</span>
            <span style="font-weight:600;">${team.name}</span>
          </div>
          <span class="team-score ${scoreClass(team.total)}">${formatScore(team.total)}</span>
        </div>
      `).join('');
      document.getElementById('quick-standings-body').innerHTML = rows;
    }

    // Quick PGA leaderboard
    const players = (leaderboardData.players || []).filter(p => p.status === 'active').slice(0, 8);
    if (players.length === 0) {
      document.getElementById('quick-leaderboard-body').innerHTML = '<div class="empty-state"><h3>No Leaderboard Data</h3><p>Waiting for tournament to begin...</p></div>';
    } else {
      const rows = players.map((p, i) => `
        <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;${i > 0 ? 'border-top:1px solid var(--border-light);' : ''}">
          <div style="display:flex;align-items:center;gap:10px;">
            <span style="width:36px;font-weight:700;color:var(--text-secondary);font-size:0.85rem;text-align:center;">${p.position || '--'}</span>
            <span style="font-weight:500;">${p.name}</span>
          </div>
          <span style="font-family:var(--font-mono);font-weight:700;" class="${scoreClass(p.total)}">${formatScore(p.total)}</span>
        </div>
      `).join('');
      document.getElementById('quick-leaderboard-body').innerHTML = rows;
    }
  }

  // ==================== FANTASY STANDINGS PAGE ====================
  function initFantasy() {
    const t = leaderboardData?.tournament || {};
    const nameEl = document.getElementById('fantasy-tournament-name');
    const badgeEl = document.getElementById('fantasy-status-badge');

    if (nameEl) nameEl.textContent = t.name || 'No Tournament Data';
    applyStatusBadge(badgeEl, t);

    // Last updated
    renderLastUpdated('fantasy-last-updated');

    const container = document.getElementById('fantasy-standings');
    const standings = getFantasyStandings();

    if (standings.length === 0) {
      container.innerHTML = '<div class="empty-state"><h3>No Teams Registered</h3><p>Go to the Picks page to build your team and register for this tournament.</p></div>';
      return;
    }

    container.innerHTML = standings.map((team, i) => {
      const rankClass = i < 3 ? `rank-${i + 1}` : 'rank-other';
      const golfersHTML = team.golferScores.map(g => {
        const isCut = g.status === 'cut' || g.status === 'CUT';
        const isWD = g.status === 'wd' || g.status === 'WD';
        const statusTag = isCut
          ? '<span class="status-cut">CUT</span>'
          : isWD
            ? '<span class="status-wd">WD</span>'
            : '';
        return `
          <div class="golfer-row${isCut ? ' golfer-cut' : ''}">
            <div>
              <span class="golfer-name">${g.name}</span>
              ${statusTag}
              ${g.salary ? `<span class="golfer-salary">${formatMoney(g.salary)}</span>` : ''}
            </div>
            <span class="golfer-score ${scoreClass(g.score)}">${formatScore(g.score)}</span>
          </div>
        `;
      }).join('');

      return `
        <div class="fantasy-team" id="team-${i}">
          <div class="fantasy-team-header" onclick="document.getElementById('team-${i}').classList.toggle('expanded')">
            <div style="display:flex;align-items:center;">
              <span class="team-rank ${rankClass}">${i + 1}</span>
              <div class="team-info">
                <div class="team-name">${team.name}</div>
                <div class="team-detail">${team.golferScores.length} golfers | ${formatMoney(team.totalSalary)} salary</div>
              </div>
            </div>
            <span class="team-score ${scoreClass(team.total)}">${formatScore(team.total)}</span>
          </div>
          <div class="fantasy-team-golfers">${golfersHTML}</div>
        </div>
      `;
    }).join('');
  }

  // ==================== LEADERBOARD PAGE ====================
  function initLeaderboard() {
    const t = leaderboardData?.tournament || {};
    const nameEl = document.getElementById('lb-tournament-name');
    const badgeEl = document.getElementById('lb-status-badge');

    if (nameEl) nameEl.textContent = t.name || 'No Tournament Data';
    applyStatusBadge(badgeEl, t);

    // Last updated marker
    renderLastUpdated('lb-last-updated');

    const tbody = document.getElementById('leaderboard-body');
    const players = leaderboardData?.players || [];

    if (players.length === 0) {
      tbody.innerHTML = '<tr><td colspan="9"><div class="empty-state"><h3>No Leaderboard Data</h3><p>Leaderboard will populate once the update script runs.</p></div></td></tr>';
      return;
    }

    // Find cut line position
    const cutIdx = players.findIndex(p => p.status === 'cut' || p.status === 'CUT');

    tbody.innerHTML = players.map((p, i) => {
      const rounds = p.rounds || [null, null, null, null];
      const isCut = p.status === 'cut' || p.status === 'CUT';
      const isWD = p.status === 'wd' || p.status === 'WD';
      const rowClass = isCut ? 'cut-row' : '';

      // Insert cut line separator
      const cutLine = (cutIdx > 0 && i === cutIdx)
        ? `<tr class="cut-line-row"><td colspan="9"><span class="cut-indicator">Projected Cut</span></td></tr>`
        : '';

      // Check if this golfer is on any fantasy team
      const isRostered = teamsData?.teams?.some(team =>
        team.golfers?.some(g => g.toLowerCase() === p.name.toLowerCase())
      );

      // Position display with CUT/WD badges
      let posDisplay;
      if (isCut) {
        posDisplay = '<span class="status-cut">CUT</span>';
      } else if (isWD) {
        posDisplay = '<span class="status-wd">WD</span>';
      } else {
        posDisplay = p.position || '--';
      }

      // Only show round scores that are valid golf scores
      const r1 = isValidRoundScore(rounds[0]) ? rounds[0] : '--';
      const r2 = isValidRoundScore(rounds[1]) ? rounds[1] : '--';
      const r3 = isValidRoundScore(rounds[2]) ? rounds[2] : '--';
      const r4 = isValidRoundScore(rounds[3]) ? rounds[3] : '--';

      return cutLine + `
        <tr class="${rowClass}">
          <td class="col-rank">${posDisplay}</td>
          <td class="col-name">${p.name}${isRostered ? ' <span style="color:var(--accent);font-size:0.7rem;">&#9733;</span>' : ''}${isCut ? ' <span class="status-cut-inline">MC</span>' : ''}</td>
          <td class="col-score ${scoreClass(p.total)}">${formatScore(p.total)}</td>
          <td class="col-score ${scoreClass(p.today)}">${formatScore(p.today)}</td>
          <td class="col-thru">${p.thru || '--'}</td>
          <td class="col-round hide-mobile">${r1}</td>
          <td class="col-round hide-mobile">${r2}</td>
          <td class="col-round hide-mobile">${r3}</td>
          <td class="col-round hide-mobile">${r4}</td>
        </tr>
      `;
    }).join('');
  }

  // ==================== PICKS / TEAM BUILDER PAGE ====================
  function initPicks() {
    // Tab switching
    const tabs = document.querySelectorAll('#picks-tabs .tab');
    tabs.forEach(tab => {
      tab.addEventListener('click', () => {
        tabs.forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById('tab-builder').style.display = tab.dataset.tab === 'builder' ? '' : 'none';
        document.getElementById('tab-teams').style.display = tab.dataset.tab === 'teams' ? '' : 'none';
        if (tab.dataset.tab === 'teams') renderAllTeams();
      });
    });

    // Restore player name
    const nameInput = document.getElementById('player-name');
    if (nameInput && playerName) nameInput.value = playerName;
    nameInput?.addEventListener('input', (e) => {
      playerName = e.target.value;
      localStorage.setItem('fg_player_name', playerName);
    });

    renderGolferList();
    renderSalaryTracker();
    initGolferSearch();
    initTierFilters();
    initSubmitPicks();
    renderAllTeams();
  }

  function renderGolferList(filter = '', tier = 'all') {
    const container = document.getElementById('golfer-list');
    const players = leaderboardData?.players || [];
    const settings = getSettings();

    if (players.length === 0) {
      container.innerHTML = '<div class="empty-state" style="padding:24px;"><h3>No Golfer Data</h3><p>Golfers will appear once leaderboard data is loaded.</p></div>';
      document.getElementById('golfer-count').textContent = '0 golfers';
      return;
    }

    // Sort by salary descending
    const sorted = [...players].sort((a, b) => (b.salary || 0) - (a.salary || 0));

    const filtered = sorted.filter(p => {
      const matchesSearch = !filter || p.name.toLowerCase().includes(filter.toLowerCase());
      const matchesTier = tier === 'all' || getTier(p.salary || 0) === Number(tier);
      return matchesSearch && matchesTier;
    });

    document.getElementById('golfer-count').textContent = `${filtered.length} golfers`;

    const remainingCap = settings.salary_cap - selectedGolfers.reduce((sum, g) => {
      const player = players.find(p => p.name.toLowerCase() === g.toLowerCase());
      return sum + (player?.salary || 0);
    }, 0);

    container.innerHTML = filtered.map(p => {
      const isSelected = selectedGolfers.some(g => g.toLowerCase() === p.name.toLowerCase());
      const canAfford = (p.salary || 0) <= remainingCap;
      const teamFull = selectedGolfers.length >= settings.golfers_per_team;
      const disabled = !isSelected && (teamFull || !canAfford);
      const tierNum = getTier(p.salary || 0);
      const isCut = p.status === 'cut' || p.status === 'CUT';

      return `
        <div class="golfer-list-item ${isSelected ? 'selected' : ''} ${disabled ? 'disabled' : ''} ${isCut ? 'golfer-cut-item' : ''}" data-name="${p.name}">
          <div class="golfer-list-info">
            <div class="golfer-list-name">${p.name} <span class="tier-badge tier-${tierNum}">T${tierNum}</span>${isCut ? ' <span class="status-cut-sm">CUT</span>' : ''}</div>
            <div class="golfer-list-meta">${p.position || '--'} | ${formatScore(p.total)}</div>
          </div>
          <span class="golfer-list-salary">${formatMoney(p.salary || 0)}</span>
          ${isSelected
            ? '<button class="added-btn">Added</button>'
            : `<button class="add-btn" onclick="window._addGolfer('${p.name.replace(/'/g, "\\'")}')">Add</button>`
          }
        </div>
      `;
    }).join('');
  }

  function renderSalaryTracker() {
    const settings = getSettings();
    const players = leaderboardData?.players || [];

    const usedSalary = selectedGolfers.reduce((sum, name) => {
      const player = players.find(p => p.name.toLowerCase() === name.toLowerCase());
      return sum + (player?.salary || 0);
    }, 0);

    const remaining = settings.salary_cap - usedSalary;
    const pctUsed = (usedSalary / settings.salary_cap) * 100;

    document.getElementById('remaining-cap').textContent = formatMoney(remaining);

    const bar = document.getElementById('salary-bar-fill');
    bar.style.width = pctUsed + '%';
    bar.className = 'salary-bar-fill ' + (pctUsed > 90 ? 'danger' : pctUsed > 70 ? 'warning' : 'ok');

    // Pick dots
    const dotsHTML = Array.from({ length: settings.golfers_per_team }, (_, i) => {
      return `<div class="pick-dot ${i < selectedGolfers.length ? 'filled' : ''}">${i + 1}</div>`;
    }).join('');
    document.getElementById('picks-dots').innerHTML = dotsHTML;

    // Selected golfers list
    const selectedHTML = selectedGolfers.map(name => {
      const player = players.find(p => p.name.toLowerCase() === name.toLowerCase());
      const isCut = player?.status === 'cut';
      return `
        <div class="selected-golfer${isCut ? ' selected-golfer-cut' : ''}">
          <div>
            <span>${name}</span>
            ${isCut ? '<span class="status-cut-sm" style="margin-left:6px;">CUT</span>' : ''}
            <span style="opacity:0.7;margin-left:8px;font-size:0.82rem;">${formatMoney(player?.salary || 0)}</span>
          </div>
          <button class="remove-btn" onclick="window._removeGolfer('${name.replace(/'/g, "\\'")}')">X</button>
        </div>
      `;
    }).join('');
    document.getElementById('selected-golfers').innerHTML = selectedHTML;

    // Submit button state
    const submitBtn = document.getElementById('submit-picks-btn');
    submitBtn.disabled = selectedGolfers.length !== settings.golfers_per_team;
  }

  function initGolferSearch() {
    const searchInput = document.getElementById('golfer-search');
    let debounceTimer;
    searchInput?.addEventListener('input', (e) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        const activeTier = document.querySelector('#tier-filters .filter-pill.active')?.dataset.tier || 'all';
        renderGolferList(e.target.value, activeTier);
      }, 200);
    });
  }

  function initTierFilters() {
    const pills = document.querySelectorAll('#tier-filters .filter-pill');
    pills.forEach(pill => {
      pill.addEventListener('click', () => {
        pills.forEach(p => p.classList.remove('active'));
        pill.classList.add('active');
        const searchVal = document.getElementById('golfer-search')?.value || '';
        renderGolferList(searchVal, pill.dataset.tier);
      });
    });
  }

  function initSubmitPicks() {
    // Submit button - saves team to cloud
    document.getElementById('submit-picks-btn')?.addEventListener('click', async () => {
      const name = document.getElementById('player-name')?.value?.trim();
      if (!name) {
        showToast('Please enter your name first');
        return;
      }

      const submitBtn = document.getElementById('submit-picks-btn');
      const originalText = submitBtn.textContent;
      submitBtn.disabled = true;
      submitBtn.textContent = 'Submitting...';

      try {
        // Fetch current cloud teams
        const currentTeams = await fetchCloudTeams();

        // Build team object
        const newTeam = {
          name: name,
          golfers: [...selectedGolfers],
          submitted: new Date().toISOString()
        };

        // Check for existing team with same name (case-insensitive) and update it
        const existingIdx = currentTeams.findIndex(t => t.name.toLowerCase() === name.toLowerCase());
        if (existingIdx >= 0) {
          currentTeams[existingIdx] = newTeam;
        } else {
          currentTeams.push(newTeam);
        }

        // Save to cloud
        await saveCloudTeams(currentTeams);

        // Update local state
        teamsData.teams = currentTeams;

        // Show success in modal
        const players = leaderboardData?.players || [];
        const lines = selectedGolfers.map(g => {
          const p = players.find(pl => pl.name.toLowerCase() === g.toLowerCase());
          return `  ${g} (${formatMoney(p?.salary || 0)})`;
        });
        const totalSalary = selectedGolfers.reduce((sum, g) => {
          const p = players.find(pl => pl.name.toLowerCase() === g.toLowerCase());
          return sum + (p?.salary || 0);
        }, 0);

        const output = `Team: ${name}\nGolfers:\n${lines.join('\n')}\nTotal Salary: ${formatMoney(totalSalary)}/${formatMoney(getSettings().salary_cap)}`;

        document.getElementById('picks-output').textContent = output;
        document.getElementById('submit-modal-title').textContent = 'Team Submitted!';
        document.getElementById('submit-modal-desc').textContent = existingIdx >= 0
          ? 'Your team has been updated. Everyone can see your picks on the Standings page.'
          : 'Your team is registered! Everyone can see your picks on the Standings page.';
        document.getElementById('submit-modal').classList.add('show');

        // Re-render teams tab
        renderAllTeams();

        showToast(existingIdx >= 0 ? 'Team updated!' : 'Team submitted!');
      } catch (e) {
        console.error('Submit failed:', e);
        document.getElementById('picks-output').textContent = 'Error: ' + e.message;
        document.getElementById('submit-modal-title').textContent = 'Submission Failed';
        document.getElementById('submit-modal-desc').textContent = 'There was a problem saving your team. Please try again.';
        document.getElementById('submit-modal').classList.add('show');
        showToast('Failed to submit picks - please try again');
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = originalText;
      }
    });

    // Close modal
    document.getElementById('close-modal-btn')?.addEventListener('click', () => {
      document.getElementById('submit-modal').classList.remove('show');
    });
    document.getElementById('submit-modal')?.addEventListener('click', (e) => {
      if (e.target === e.currentTarget) {
        document.getElementById('submit-modal').classList.remove('show');
      }
    });
  }

  // Global functions for inline onclick handlers
  window._addGolfer = function(name) {
    const settings = getSettings();
    if (selectedGolfers.length >= settings.golfers_per_team) return;
    if (selectedGolfers.some(g => g.toLowerCase() === name.toLowerCase())) return;

    const players = leaderboardData?.players || [];
    const player = players.find(p => p.name.toLowerCase() === name.toLowerCase());
    const usedSalary = selectedGolfers.reduce((sum, g) => {
      const p = players.find(pl => pl.name.toLowerCase() === g.toLowerCase());
      return sum + (p?.salary || 0);
    }, 0);

    if (usedSalary + (player?.salary || 0) > settings.salary_cap) {
      showToast('Not enough salary cap!');
      return;
    }

    selectedGolfers.push(name);
    localStorage.setItem('fg_selected_golfers', JSON.stringify(selectedGolfers));

    const searchVal = document.getElementById('golfer-search')?.value || '';
    const activeTier = document.querySelector('#tier-filters .filter-pill.active')?.dataset.tier || 'all';
    renderGolferList(searchVal, activeTier);
    renderSalaryTracker();
  };

  window._removeGolfer = function(name) {
    selectedGolfers = selectedGolfers.filter(g => g.toLowerCase() !== name.toLowerCase());
    localStorage.setItem('fg_selected_golfers', JSON.stringify(selectedGolfers));

    const searchVal = document.getElementById('golfer-search')?.value || '';
    const activeTier = document.querySelector('#tier-filters .filter-pill.active')?.dataset.tier || 'all';
    renderGolferList(searchVal, activeTier);
    renderSalaryTracker();
  };

  // ==================== ALL TEAMS DISPLAY ====================
  function renderAllTeams() {
    const container = document.getElementById('all-teams-display');
    if (!container) return;

    const teams = teamsData?.teams || [];

    if (teams.length === 0) {
      container.innerHTML = '<div class="empty-state"><h3>No Teams Yet</h3><p>Be the first to submit your picks!</p></div>';
      return;
    }

    const players = leaderboardData?.players || [];

    container.innerHTML = '<div class="teams-grid">' + teams.map(team => {
      const totalSalary = (team.golfers || []).reduce((sum, name) => {
        const p = players.find(pl => pl.name.toLowerCase() === name.toLowerCase());
        return sum + (p?.salary || 0);
      }, 0);

      const golfersHTML = (team.golfers || []).map(name => {
        const p = players.find(pl => pl.name.toLowerCase() === name.toLowerCase());
        const result = calculateFantasyScore(name);
        const isCut = result.status === 'cut';
        return `
          <div class="team-card-golfer${isCut ? ' team-card-golfer-cut' : ''}">
            <span>${name}${isCut ? ' <span class="status-cut-sm">CUT</span>' : ''}</span>
            <div>
              <span class="${scoreClass(result.score)}" style="font-family:var(--font-mono);font-weight:600;">${formatScore(result.score)}</span>
              <span style="color:var(--text-muted);font-size:0.8rem;margin-left:8px;">${formatMoney(p?.salary || 0)}</span>
            </div>
          </div>
        `;
      }).join('');

      return `
        <div class="team-card">
          <div class="team-card-header">
            <span class="team-card-name">${team.name}</span>
            <span class="team-card-salary">${formatMoney(totalSalary)}</span>
          </div>
          ${golfersHTML}
        </div>
      `;
    }).join('') + '</div>';
  }

  // ==================== INIT ====================
  async function init() {
    await loadAllData();

    const page = document.body.dataset.page;

    switch(page) {
      case 'home': initHome(); break;
      case 'fantasy': initFantasy(); break;
      case 'picks': initPicks(); break;
      case 'leaderboard': initLeaderboard(); break;
    }
  }

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
