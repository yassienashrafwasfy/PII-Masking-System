// MLOps: replace these labels with the approved team names when they are ready.
// Keep backend/QA team_id values unchanged; this module maps them for display only.
//
// 'Cegedim Egypte Model' is not one team's name -- it is whichever team
// currently holds rank 1. The masking console already reads it that way (see
// CHAMPION_LABEL's use in App.jsx); getTeamDisplayName below applies the same
// rule everywhere a team is named, so the leaderboard, podium, and per-entity
// breakdown all read the same as the console. A team drops back to its own
// name the moment it is no longer first -- never two rows claiming the same
// name, and never a real result quietly relabelled as someone else's.
import octoLogo from './assets/octo-logo.png'

export const CHAMPION_LABEL = 'Cegedim Egypte Model'

export const TEAM_DISPLAY_NAMES = ['Team Synthify', 'Team Octo', 'Team Berts']

export const TEAM_DISPLAY_NAME_BY_ID = Object.freeze({
  // Canonical ids emitted by QA and validated by the gateway.
  synthify: TEAM_DISPLAY_NAMES[0],
  octo: TEAM_DISPLAY_NAMES[1],
  berts: TEAM_DISPLAY_NAMES[2],
  // Retained so older MLflow runs still resolve to a name.
  'team-1': TEAM_DISPLAY_NAMES[0],
  'team-2': TEAM_DISPLAY_NAMES[1],
})

/**
 * `championTeamId` is optional so every existing call site keeps working
 * unchanged; pass it (typically `board?.champion?.team`) wherever the current
 * rank-1 team should read as "Cegedim Egypte Model" instead of its own name.
 */
export function getTeamDisplayName(teamId, placement, championTeamId) {
  if (!teamId) return TEAM_DISPLAY_NAMES[placement] ?? 'Unknown team'
  const id = String(teamId).toLowerCase()
  if (championTeamId && id === String(championTeamId).toLowerCase()) return CHAMPION_LABEL
  const known = TEAM_DISPLAY_NAME_BY_ID[id]
  if (known) return known
  // An unmapped id is shown as itself rather than borrowed from another team.
  return teamId
}

// A team's own mark, keyed the same way as its display name above. Every
// other team falls back to the generic sparkle glyph the orb already used
// for everyone -- adding a second team's logo here needs nothing beyond one
// more import and one more entry.
export const TEAM_LOGO_BY_ID = Object.freeze({
  octo: octoLogo,
  'team-2': octoLogo,
})

/**
 * The champion's row always uses the generic mark, never the winning team's
 * own logo -- a real team's logo next to the "Cegedim Egypte Model" label
 * would read as a mismatch between the icon and the name beside it.
 */
export function getTeamLogo(teamId, championTeamId) {
  if (!teamId) return null
  const id = String(teamId).toLowerCase()
  if (championTeamId && id === String(championTeamId).toLowerCase()) return null
  return TEAM_LOGO_BY_ID[id] ?? null
}
