import { useEffect, useState } from 'react'
import {
  AlertTriangle, BarChart3, Beaker, Info, Library as LibraryIcon, ShieldCheck, Users,
} from 'lucide-react'
import { MaskingPreview } from './theatrics'
import { CHAMPION_LABEL, getTeamDisplayName } from './teamDisplayConfig'
import { firstMetricName, formatMetric, metricValue, timeAgo } from './format'
import { fetchLibrary } from './api'

// The three numbers a first-time reader can act on, rather than the full
// scoring policy. `lowerIsBetter` is stated on the card instead of assumed:
// a leakage rate of 0.4% and a recall of 90% look alike until you say which
// direction is good. Metric names are ordered exactly as the ranking table
// resolves them, so the hub can never quote a different number from the board.
const HIGHLIGHTS = [
  {
    key: 'score',
    label: 'Leaderboard score',
    caption: 'The single number the ranking is sorted by',
    // Every board shape carries a score, including the short legacy rows the
    // demo board uses -- so this card always has something real to show.
    score: true,
  },
  {
    key: 'detection',
    label: 'Detection',
    caption: 'Share of real PII the leader found',
    names: ['pii_recall', 'overlapping_recall', 'exact_recall'],
  },
  {
    key: 'leakage',
    label: 'Leakage',
    caption: 'PII that survived into the masked output',
    names: ['leakage_rate'],
    lowerIsBetter: true,
  },
]

// Rank 1 always reads as "Cegedim Egypte Model", the same rule the podium,
// medals and console apply. Never the raw team name.
function displayName(model, championTeam) {
  if (!model) return null
  if (model.rank === 1) return CHAMPION_LABEL
  return getTeamDisplayName(model.team, model.rank ? model.rank - 1 : 0, championTeam)
}

function Highlight({ item, leader }) {
  // Deliberately resolved through the same metric names the ranking table
  // uses, and left as an em dash when they are absent. A board that shows "—"
  // in its own table must not show a number here: the demo board carries only
  // a short legacy row (`recall`, `p95`) and quoting those would make the hub
  // disagree with the board it is summarising.
  const name = !item.score && leader ? firstMetricName(leader, item.names) : null
  const value = item.score
    ? (typeof leader?.score === 'number' ? leader.score : null)
    : (name ? metricValue(leader, name) : null)

  let shown = '—'
  if (value !== null && value !== undefined) {
    shown = item.score ? <>{value}<small className="home-of">/100</small></> : formatMetric(name, value)
  }

  return (
    <article className="home-highlight">
      <span className="eyebrow">{item.label}</span>
      <strong>{shown}</strong>
      <p>{item.caption}</p>
      {/* A bare dash reads as a bug. Saying the board did not report it makes
          the gap self-explanatory, which matters because the demo board omits
          most canonical metrics. */}
      {value === null || value === undefined ? (
        <small className="home-missing">Not reported on this board</small>
      ) : (
        <small className={item.lowerIsBetter ? 'lower-better' : 'higher-better'}>
          {item.lowerIsBetter ? 'Lower is better' : 'Higher is better'}
        </small>
      )}
    </article>
  )
}

function EntryCard({ href, Icon, title, body, disabled, reason }) {
  if (disabled) {
    return (
      <div className="home-entry disabled" title={reason}>
        <span className="home-entry-orb"><Icon size={19} /></span>
        <h3>{title}</h3>
        <p>{reason}</p>
      </div>
    )
  }
  return (
    <a className="home-entry" href={href}>
      <span className="home-entry-orb"><Icon size={19} /></span>
      <h3>{title}</h3>
      <p>{body}</p>
    </a>
  )
}

export default function Home({ board, config, championTeam }) {
  const models = Array.isArray(board?.models) ? board.models : []
  // The board is already sorted, so rank 1 is normally first -- but fall back
  // through eligibility rather than assuming, because a board built from the
  // cross-team ranking document can carry ineligible rows at the top.
  const leader = models.find(model => model.rank === 1) || models.find(model => model.eligible) || models[0] || null
  const leaderName = displayName(leader, championTeam)

  const storageOff = config?.storage_configured === false
  // Waits for config to actually arrive before probing. Optional chaining
  // alone is not enough: while config is still null every `!== false` test
  // passes, so the first render fired a request that /api/v1/library answers
  // with 503 on any deployment without object storage.
  const libraryOn =
    !!config && config.library?.enabled !== false && config.storage_configured !== false
  const isPreview = board?.source === 'mock' || board?.source === 'demo'

  const [recent, setRecent] = useState([])
  useEffect(() => {
    if (!libraryOn) return
    // A quiet extra: the hub still reads correctly with no activity at all, so
    // a failure here is swallowed rather than surfaced as a page error.
    fetchLibrary({ limit: 5 })
      .then(page => setRecent(page?.entries || []))
      .catch(() => setRecent([]))
  }, [libraryOn])

  return (
    <>
      <section className="home-hero">
        <div className="home-hero-copy">
          <span className="eyebrow">CEGEDIM · PII MASKING</span>
          <h1>Know which model you can trust with patient data.</h1>
          <p>
            Every model is measured by MLOps/QA on the same documents. Watch one mask a
            record, then see how they rank.
          </p>
          <div className="hero-actions">
            <a className="primary" href="#/console"><Beaker size={18} />Mask a document</a>
            <a className="refresh" href="#/arena"><BarChart3 size={16} />See the rankings</a>
          </div>
        </div>

        {/* The product performing itself. MaskingPreview types the masked side
            out on mount and honours prefers-reduced-motion internally, and its
            sample text is fixed -- so this needs no document, no request, and
            has no empty state even before the board arrives. */}
        <div className="home-hero-demo">
          <MaskingPreview model={leader} />
          <p className="home-hero-caption">
            {leader
              ? <>Masked by <b>{leaderName}</b>{typeof leader.score === 'number' && <> · {leader.score}/100</>}</>
              : 'Masked by the current leading model'}
          </p>
        </div>
      </section>

      {isPreview && (
        <p className="source-banner mock">
          <Info size={16} />
          <span>
            Showing {board.source === 'mock' ? 'temporary mock' : 'built-in demo'} data — these are
            not real QA results.
          </span>
        </p>
      )}

      <section className="home-highlights" aria-label="Headline results">
        <div className="section-heading">
          <div>
            <span className="eyebrow">HIGHLIGHTS</span>
            <h2>How the leader performs</h2>
          </div>
        </div>
        <div className="home-highlight-grid">
          {HIGHLIGHTS.map(item => <Highlight key={item.key} item={item} leader={leader} />)}
        </div>
      </section>

      <section className="home-entries" aria-label="Where to go next">
        <EntryCard
          href="#/arena"
          Icon={BarChart3}
          title="Rankings"
          body="Who leads, the medals awarded, and where each model is weakest."
        />
        <EntryCard
          href="#/console"
          Icon={Beaker}
          title="Mask a document"
          body="Run a file through the pipeline with the top-ranked model."
          disabled={storageOff}
          reason="Object storage is not configured on this deployment."
        />
        <EntryCard
          href="#/teams"
          Icon={Users}
          title="Team console"
          body="Mask the same document with any team's model."
          disabled={storageOff}
          reason="Object storage is not configured on this deployment."
        />
        {libraryOn && (
          <EntryCard
            href="#/library"
            Icon={LibraryIcon}
            title="Files"
            body="Every masked document, with the original beside it."
          />
        )}
      </section>

      {libraryOn && recent.length > 0 && (
        <section className="home-activity" aria-label="Recent activity">
          <div className="section-heading">
            <div>
              <span className="eyebrow">RECENT ACTIVITY</span>
              <h2>Lately in the pipeline</h2>
            </div>
            <a className="refresh" href="#/library"><LibraryIcon size={16} />Browse all</a>
          </div>
          <ul className="home-activity-list">
            {recent.map(entry => (
              <li key={entry.run_id}>
                <span className={`chip status-${entry.status}`}>{entry.status}</span>
                <a href={`#/library/${encodeURIComponent(entry.run_id)}`} title={entry.filename}>{entry.filename}</a>
                <span className="home-activity-team" title={getTeamDisplayName(entry.team, 0, championTeam)}>
                  {getTeamDisplayName(entry.team, 0, championTeam)}
                </span>
                <small title={entry.completed_at || entry.submitted_at || ''}>
                  {timeAgo(entry.completed_at || entry.submitted_at)}
                </small>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="principles home-principles">
        <div>
          <ShieldCheck /><h3>Synthetic by default</h3>
          <p>The benchmark runs on a private synthetic split, never on real patient records.</p>
        </div>
        <div>
          <BarChart3 /><h3>One evaluator</h3>
          <p>Every model is scored by MLOps/QA on the identical document set.</p>
        </div>
        <div>
          <AlertTriangle /><h3>Gates before rank</h3>
          <p>A run has to clear the safety gates before it can hold an official position.</p>
        </div>
      </section>
    </>
  )
}
