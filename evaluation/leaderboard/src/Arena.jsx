import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  AlertTriangle, ArrowDown, ArrowUp, Award, BookOpen, Check, Crown, Download, Flame, Gauge,
  HelpCircle, History, Info, LockKeyhole, Medal, Feather, Minus, PartyPopper, Plus, RefreshCw,
  ShieldCheck, Sparkles, Swords, Tag, Target, Timer, TrendingDown, TrendingUp, Trophy, Weight, X, Zap,
} from 'lucide-react'
import { CHAMPION_LABEL, getTeamDisplayName, getTeamLogo } from './teamDisplayConfig'
import {
  firstMetricName, formatEnvelope, formatFirstEnvelope, formatMetric, metricOf, metricValue,
} from './format'
import {
  BoardSweep, Combatant, DuelSparks, FlameGlyph, Laurel, MaskingPreview, Torches, useRankDuels,
} from './theatrics'
import ConstellationMap from './constellation'
import PerLabel from './PerLabel'
import { toCsv, download as downloadCsv, stamp as csvStamp } from './csv'

const ACCENTS = ['ocean', 'teal', 'sky', 'violet', 'coral']

// A standalone color set for the fun extras (confetti, trend overlay, weight
// badges) so they don't depend on the main stylesheet's accent rules, which
// only style ocean/teal/sky and leave violet/coral unstyled.
const ACCENT_HEX = {
  ocean: '#005272', teal: '#008f8b', sky: '#2689b8', violet: '#7c5cbf', coral: '#e2725b',
}

// Illustrative only, not a QA-defined threshold -- just a playful way to read
// the same model_size_mb the real "Smallest model" medal already uses.
const WEIGHT_CLASSES = [
  { max: 50, label: 'Featherweight' },
  { max: 150, label: 'Middleweight' },
  { max: Infinity, label: 'Heavyweight' },
]

function weightClassOf(sizeMb) {
  if (sizeMb === null || sizeMb === undefined) return null
  return WEIGHT_CLASSES.find(tier => sizeMb < tier.max)?.label ?? null
}

// How many runs of history a team needs before its streak is worth bragging
// about. Below this it is just "it ran twice", which no one needs a flame for.
const STREAK_MIN_RUNS = 3

// A lead this small between the top two is a photo finish rather than a win.
// Deliberately looser than QA's own close_threshold (which is an F1 rule that
// decides *rank*): this only decorates the already-published order, so it can
// speak in the 0-100 score the board actually displays.
const PHOTO_FINISH_POINTS = 1

// Which way a team's own score moved between its last two comparable runs.
// Not a rank change -- rank depends on everyone else, and the board only ships
// each team's own history.
function scoreDirection(model) {
  const series = Array.isArray(model.trend) ? model.trend : []
  if (series.length < 2) return null
  const delta = series.at(-1) - series.at(-2)
  if (Math.abs(delta) < 0.05) return { kind: 'flat', delta }
  return { kind: delta > 0 ? 'up' : 'down', delta }
}

// One line of trash talk about the top two, built from the score already shown
// in their rows so it can never disagree with the table underneath it.
function rivalryLine(eligible, basis, defaultBasis) {
  const [leader, chaser] = eligible
  if (!leader || !chaser) return null
  const lead = scoreForBasis(leader, basis, defaultBasis)
  const chase = scoreForBasis(chaser, basis, defaultBasis)
  if (lead === null || chase === null) return null
  const gap = lead - chase
  if (gap <= 0) return null
  const photoFinish = gap < PHOTO_FINISH_POINTS
  return {
    photoFinish,
    text: photoFinish
      ? `Photo finish — ${displayName(leader)} holds off ${displayName(chaser)} by just ${gap.toFixed(2)}.`
      : `${displayName(leader)} leads ${displayName(chaser)} by ${gap.toFixed(1)} points.`,
  }
}

// Span-based name first: that is what the rank is actually sorted by. A
// leak-based pii_* name only stands in for it on documents published before
// QA started reporting span-based metrics.
const RECALL_METRICS_BY_BASIS = {
  overlapping: ['overlapping_recall', 'pii_recall'],
  exact: ['exact_recall', 'pii_recall'],
}
const PRECISION_METRICS_BY_BASIS = {
  overlapping: ['overlapping_precision', 'pii_precision'],
  exact: ['exact_precision', 'pii_precision'],
}
const F1_METRICS_BY_BASIS = {
  overlapping: ['overlapping_f1', 'pii_f1'],
  exact: ['exact_f1', 'pii_f1'],
}

function scoreForBasis(model, basis, defaultBasis) {
  if (basis === defaultBasis) return model.score
  const name = firstMetricName(model, RECALL_METRICS_BY_BASIS[basis])
  const value = metricValue(model, name)
  return value === null ? null : Number((value * 100).toFixed(1))
}

function exportBoardCsv(models) {
  const header = ['rank', 'team', 'model', 'recall', 'precision', 'f1', 'latency_p95_ms', 'eligible']
  const rows = models.map(model => [
    model.rank ?? '',
    model.team,
    model.model || '',
    metricValue(model, firstMetricName(model, RECALL_METRICS_BY_BASIS.overlapping)) ?? '',
    metricValue(model, firstMetricName(model, PRECISION_METRICS_BY_BASIS.overlapping)) ?? '',
    metricValue(model, firstMetricName(model, F1_METRICS_BY_BASIS.overlapping)) ?? '',
    metricValue(model, 'latency_p95_ms') ?? '',
    model.eligible,
  ])
  downloadCsv(`pii-masking-leaderboard-${csvStamp()}.csv`, toCsv(header, rows))
}

// Which medal each award kind renders as. Keys match the API's award kinds.
const AWARD_ICON = {
  overall: Crown,
  recall: ShieldCheck,
  precision: Target,
  f1: Award,
  latency: Zap,
  critical: LockKeyhole,
  throughput: Gauge,
  footprint: Feather,
}

// The comparison table. `lowerIsBetter` drives which column is highlighted.
// It spans both document kinds the API can serve; rows no selected model
// reports are dropped at render time rather than shown as a column of dashes,
// so a board built from either kind stays readable.
const COMPARISON_METRICS = [
  // Cross-team ranking document (scripts/rank_teams.py). Overlap counts a span
  // that found the entity but got its boundaries wrong; exact does not, so the
  // gap between the two pairs is the boundary-precision story.
  {
    name: 'overlapping_recall', label: 'Overlap recall', note: 'The score the published rank is based on',
    description: 'Share of real PII entities the model found, counting a match even when its span boundaries were imprecise — the metric the published rank is actually sorted by. Precision and F1 below are reported for context, not part of the ranking decision.',
  },
  {
    name: 'overlapping_precision', label: 'Overlap precision',
    description: 'Share of the model’s detected spans that overlap a real PII entity, even when the boundaries aren’t exact.',
  },
  {
    name: 'overlapping_f1', label: 'Overlap F1',
    description: 'The harmonic mean of overlap recall and overlap precision — shown for context; ranking is decided by recall alone.',
  },
  {
    name: 'exact_recall', label: 'Exact recall', note: 'Boundaries must match exactly',
    description: 'Share of real PII entities the model found with exactly matching span boundaries.',
  },
  {
    name: 'exact_precision', label: 'Exact precision',
    description: 'Share of the model’s detected spans that exactly match a real PII entity’s boundaries.',
  },
  {
    name: 'exact_f1', label: 'Exact F1',
    description: 'The harmonic mean of exact recall and exact precision.',
  },
  // Per-run qa-result.schema.json document. Labelled "masking coverage",
  // not "PII recall": the wire field is still pii_recall (unchanged, so
  // historical qa-result.json documents keep reading correctly), but the
  // name invited confusion with overlapping_recall above -- this measures
  // whether a PII value's literal text survived in the output at all, not
  // whether a detector found and correctly labelled it. Two genuinely
  // different questions; see overlapping_recall's own note above for the
  // one that actually decides rank.
  {
    name: 'pii_recall', label: 'Masking coverage',
    description: 'Share of real PII values whose literal text no longer appears anywhere in the masked output, from this team’s own per-run QA report — independent of the cross-team ranking document above, and independent of whether the detector used the correct label.',
  },
  {
    name: 'pii_precision', label: 'Masking coverage — precision', note: 'Derived from the non-PII preservation rate',
    description: 'Precision computed from this team’s own per-run QA report, derived from how much non-PII text the model left untouched.',
  },
  {
    name: 'pii_f1', label: 'Masking coverage — F1',
    description: 'The harmonic mean of this run’s own masking-coverage recall and precision.',
  },
  {
    name: 'critical_entity_recall', label: 'Critical-entity recall',
    description: 'Recall restricted to the entity types the safety gate treats as critical (names, IDs, and similar) — this has its own minimum for a run to pass the gate.',
  },
  {
    name: 'document_success_rate', label: 'Document success rate',
    description: 'Share of documents in the run that processed end to end without an error.',
  },
  {
    name: 'contract_pass_rate', label: 'Contract pass rate',
    description: 'Share of API contract checks — request/response shape, required headers — the candidate passed.',
  },
  {
    name: 'leakage_rate', label: 'Leakage rate', lowerIsBetter: true,
    description: 'Share of real PII that made it into the masked output unredacted — the metric the safety gate is most sensitive to.',
  },
  {
    name: 'api_error_rate', label: 'API error rate', lowerIsBetter: true,
    description: 'Share of masking requests that returned an error instead of a result.',
  },
  // Shared.
  {
    name: 'mean_confidence', label: 'Mean confidence',
    description: 'The model’s own average self-reported confidence across its detections — not a measured accuracy.',
  },
  {
    name: 'latency_p50_ms', label: 'P50 latency', lowerIsBetter: true,
    description: 'Typical (median) time to mask one document.',
  },
  {
    name: 'latency_p95_ms', label: 'P95 latency', lowerIsBetter: true,
    description: 'Worst-case time to mask one document for all but the slowest 5% of requests.',
  },
  {
    name: 'throughput_docs_min', label: 'Throughput',
    description: 'Documents the model can mask per minute at steady state.',
  },
  {
    name: 'model_size_mb', label: 'Model size', lowerIsBetter: true, note: 'Backs its own "Smallest model" award',
    description: 'On-disk model weight size — its own award category. Not part of ranking or the tiebreak: a recall tie is broken by P95 latency alone.',
  },
  {
    name: 'parameter_count_millions', label: 'Parameters', lowerIsBetter: true,
    description: 'Number of trainable parameters in the model, in millions.',
  },
  {
    name: 'documents_scored', label: 'Documents scored',
    description: 'How many documents contributed to this row’s numbers — a team missing any requested document is excluded rather than scored on a partial set.',
  },
]

/**
 * Shared open/position state for InfoTip and StreakFlair's popovers.
 *
 * The bubble itself has to render through a portal to document.body, not as
 * a plain child of the trigger: both triggers live inside .table-wrap /
 * .compare-wrap, which are `overflow-x: auto` -- and per spec, setting only
 * overflow-x to a non-visible value forces the unset overflow-y to `auto`
 * too, so the wrapper clips vertically as well. A bubble positioned relative
 * to the trigger would be cut off (or force an unwanted scrollbar) exactly
 * when it is needed, in the middle of a scrollable table. Recording the
 * trigger's own screen position on open and rendering `position: fixed` at
 * document.body escapes that ancestor entirely.
 */
function useTipAnchor() {
  const [open, setOpen] = useState(false)
  const [coords, setCoords] = useState(null)
  const ref = useRef(null)
  const show = () => {
    const rect = ref.current?.getBoundingClientRect()
    if (rect) setCoords({ x: rect.left + rect.width / 2, y: rect.top })
    setOpen(true)
  }
  const hide = () => setOpen(false)
  return { ref, open, coords, show, hide }
}

/**
 * A small "?" glyph that reveals a short description on hover *or* on
 * click/tap -- hover alone leaves touch devices with no way to ever see the
 * text, and a bare `title` attribute (the codebase's previous pattern here)
 * never shows on click at all, which is what actually prompted this: it drew
 * the browser's own `cursor: help`, implying something to reveal, but tapping
 * or clicking it did nothing.
 *
 * Deliberately its own focusable element with `stopPropagation` on click,
 * not just riding the parent's own onClick (e.g. a sortable row) -- without
 * that, tapping the glyph to read the description would also fire whatever
 * action the parent row is for.
 */
function InfoTip({ children }) {
  const { ref, open, coords, show, hide } = useTipAnchor()
  return (
    <span
      ref={ref}
      className="info-tip"
      tabIndex={0}
      role="button"
      aria-expanded={open}
      aria-label="More information"
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      onClick={event => {
        // Not a toggle: on a real mouse, onMouseEnter above already opened
        // this before the click even lands, so toggling here would close it
        // right back -- a click undoing what the hover it's part of just
        // showed. Always-show is what a click/tap can actually add: it is
        // the only one of the two that works on touch, which never fires the
        // hover handlers at all.
        event.stopPropagation()
        show()
      }}
    >
      <HelpCircle size={12} />
      {open && coords && createPortal(
        <span className="info-tip-bubble" role="tooltip" style={{ left: coords.x, top: coords.y }}>
          {children}
        </span>,
        document.body,
      )}
    </span>
  )
}

// Rank 1 always reads as "Cegedim Egypte Model", whichever team holds it --
// the same rule the masking console already applies (see CHAMPION_LABEL in
// App.jsx). `model.rank` is QA's own published rank, never recomputed here,
// so this can never disagree with which podium slot is featured.
function displayName(model) {
  if (model.rank === 1) return CHAMPION_LABEL
  return getTeamDisplayName(model.team, model.rank ? model.rank - 1 : 0)
}

// Every animation added here is decorative, so all of them check this first
// rather than relying on the stylesheet's blanket reduced-motion override,
// which cannot reach a value being interpolated in JavaScript.
function prefersReducedMotion() {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
  )
}

const COUNT_UP_MS = 700

// Interpolates toward `value` on mount and on every later change, so a refresh
// reads as the board moving rather than as digits silently swapping. Counts
// from wherever the previous run finished, which is what makes a refresh that
// nudges a score by 0.3 look like a nudge instead of a fresh count from zero.
function useCountUp(value) {
  const [shown, setShown] = useState(value)
  const fromRef = useRef(value)

  useEffect(() => {
    const settle = () => {
      fromRef.current = value
      setShown(value)
    }
    if (typeof value !== 'number') {
      settle()
      return undefined
    }
    const from = typeof fromRef.current === 'number' ? fromRef.current : 0
    if (from === value || prefersReducedMotion()) {
      settle()
      return undefined
    }
    let frame
    let start
    const step = timestamp => {
      if (start === undefined) start = timestamp
      const progress = Math.min((timestamp - start) / COUNT_UP_MS, 1)
      // Ease-out cubic: moves immediately, then settles instead of stopping dead.
      setShown(from + (value - from) * (1 - (1 - progress) ** 3))
      if (progress < 1) frame = requestAnimationFrame(step)
      else fromRef.current = value
    }
    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [value])

  return shown
}

// Returns a bare string rather than an element, so each caller keeps whatever
// wrapper it already had (the podium's <strong>, a table cell) and none of the
// existing score styling has to move.
function CountUp({ value, digits = 1 }) {
  const numeric = typeof value === 'number' ? value : null
  const shown = useCountUp(numeric)
  if (numeric === null) return '—'
  return Number(shown ?? numeric).toFixed(digits)
}

// Shared, never mutated: a stable identity keeps the flash effect from
// re-running itself when it clears.
const NO_CHANGES = new Set()

function accentOf(model, index) {
  return model.accent && ACCENTS.includes(model.accent) ? model.accent : ACCENTS[index % ACCENTS.length]
}

// A table column cannot be styled as a unit in CSS, so every cell in the
// compared column has to carry the highlight class itself. Takes whatever the
// cell's own class would have been, falsy included, so callers can pass an
// inline `condition && 'best-cell'` without building an array first.
function columnClass(own, isHot) {
  return [own || null, isHot ? 'col-hot' : null].filter(Boolean).join(' ')
}

function Sparkline({ values, accent, large }) {
  const series = Array.isArray(values) && values.length ? values : []
  if (series.length < 2) return <span className="sparkline-empty" title="A trend needs at least two runs">single run</span>
  const width = large ? 480 : 148
  const height = large ? 96 : 34
  const min = Math.min(...series) - 1
  const max = Math.max(...series) + 1
  const points = series
    .map((value, index) => {
      const x = (index / Math.max(series.length - 1, 1)) * width
      const y = height - ((value - min) / Math.max(max - min, 1)) * height
      return `${x},${y}`
    })
    .join(' ')
  const [lastX, lastY] = points.split(' ').at(-1).split(',')
  return (
    <svg className={`sparkline ${accent} ${large ? 'large' : ''}`} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Score trend across ${series.length} runs`}>
      {/* `pathLength="1"` normalises the line's length regardless of how many
          runs it plots, so the draw-on animation in console.css can dash it
          with a plain 0-1 offset instead of guessing a pixel length. */}
      <polyline points={points} fill="none" vectorEffect="non-scaling-stroke" pathLength="1" />
      <circle cx={lastX} cy={lastY} r="3.5" />
    </svg>
  )
}

function ConfettiBurst() {
  // Regenerated once per mount -- Arena remounts this by changing `key`, so the
  // randomness only ever varies which burst it is, never re-triggers mid-fall.
  const pieces = useMemo(
    () =>
      Array.from({ length: 28 }, (_, index) => ({
        id: index,
        left: Math.random() * 100,
        delay: Math.random() * 0.3,
        duration: 1.1 + Math.random() * 0.7,
        color: ACCENT_HEX[ACCENTS[index % ACCENTS.length]],
        rotate: Math.round(Math.random() * 360),
      })),
    [],
  )
  return (
    <div className="confetti-burst" aria-hidden="true">
      {pieces.map(piece => (
        <span
          key={piece.id}
          className="confetti-piece"
          style={{
            left: `${piece.left}%`,
            animationDelay: `${piece.delay}s`,
            animationDuration: `${piece.duration}s`,
            background: piece.color,
            transform: `rotate(${piece.rotate}deg)`,
          }}
        />
      ))}
    </div>
  )
}

function WeightClassBadge({ sizeMb }) {
  const label = weightClassOf(sizeMb)
  if (!label) return null
  return (
    <span className={`weight-chip weight-${label.toLowerCase()}`} title="A playful read of model size, not an official medal">
      <Weight size={11} />{label}
    </span>
  )
}

// Which way this team's own score moved since its previous run. Rendered beside
// the score rather than the rank, because that is what the arrow actually
// measures -- see `scoreDirection`.
function ScoreDirection({ model }) {
  const direction = scoreDirection(model)
  if (!direction) return null
  const Icon = direction.kind === 'up' ? TrendingUp : direction.kind === 'down' ? TrendingDown : Minus
  const sign = direction.delta > 0 ? '+' : ''
  return (
    <span
      className={`score-direction dir-${direction.kind}`}
      title={`${sign}${direction.delta.toFixed(2)} versus its previous run`}
    >
      <Icon size={13} />
    </span>
  )
}

// A flame for a team that keeps showing up. `run_count` is how many comparable
// ranking documents this team appeared in, so it is a participation streak, not
// a winning one -- the tooltip says so.
function StreakFlair({ runCount }) {
  const { ref, open, coords, show, hide } = useTipAnchor()
  if (!runCount || runCount < STREAK_MIN_RUNS) return null
  // A plain `title` only ever surfaced on a mouse hover -- this reveals the
  // same text on click/tap too, matching InfoTip's reasoning above, since the
  // flame+count is itself the trigger here rather than a separate "?" glyph.
  return (
    <span
      ref={ref}
      className="streak-flair"
      tabIndex={0}
      role="button"
      aria-expanded={open}
      aria-label={`Scored in ${runCount} comparable runs in a row`}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      onClick={event => {
        // Always-show, not a toggle -- see InfoTip's identical note above.
        event.stopPropagation()
        show()
      }}
    >
      <FlameGlyph />{runCount}
      {open && coords && createPortal(
        <span className="info-tip-bubble" role="tooltip" style={{ left: coords.x, top: coords.y }}>
          Scored in {runCount} comparable runs in a row
        </span>,
        document.body,
      )}
    </span>
  )
}

// `laurelKey` remounts the wreath so it draws itself again when the top spot
// changes hands. Arena passes the same value it uses for the confetti, so the
// two celebrations are triggered by one event rather than two conditions that
// could drift apart.
// A team's own mark where one exists, falling back to the generic sparkle
// every other team already used -- so adding Team Octo's logo here changes
// nothing about how an unmapped team renders. The champion is the one
// exception: its row reads "Cegedim Egypte Model", so a real team's own logo
// beside that label would look like a mismatched claim rather than a mark.
function TeamMark({ team, size, isChampion }) {
  const logo = isChampion ? null : getTeamLogo(team)
  if (!logo) return <Sparkles size={size} />
  return <img className="team-logo" src={logo} alt="" width={size} height={size} />
}

function PodiumCard({ model, featured, placement, onCrownClick, laurelKey }) {
  if (!model) {
    return (
      <article className={`podium-card podium-empty placement-${placement}`} aria-label={`Rank ${placement} awaiting an eligible model`}>
        <span className="podium-rank">{placement}</span>
        <div><span className="empty-orb"><Medal size={24} /></span><h3>Position open</h3><p>Awaiting an eligible model run.</p></div>
      </article>
    )
  }
  return (
    <article className={`podium-card ${model.displayAccent} ${featured ? 'featured torchlit' : ''}`} data-team-id={model.team}>
      {featured && (
        <>
          <Torches />
          <Laurel key={laurelKey} />
          <span className="crown" onClick={onCrownClick} title={onCrownClick ? 'Curious?' : undefined}>
            <Crown size={17} fill="currentColor" />
          </span>
        </>
      )}
      {/* Wrapped with the rank rather than left as a grid sibling: the podium's
          three columns (63px / 55px / 1fr) are sized for exactly three
          children, and an unwrapped fourth would consume the second column
          and push team-orb and podium-copy one slot to the right. */}
      <div className="podium-rank-cell">
        <span className="podium-rank">{model.displayRank}</span>
        {/* QA's own tiebreak, not this component's -- see load_board in
            ranking.py. `resolved_by` only appears once two teams landed inside
            the ranking document's close_threshold band, where the display
            order can legitimately put a slightly lower score ahead of a
            slightly higher one. The ranking table already explains itself
            this way (see the "tiebreak" note next to rank-number below); the
            podium had no equivalent, so the same near-tie read as the board
            simply not having updated. */}
        {model.resolved_by?.startsWith('tiebreak') && (
          <small className="rank-note" title={model.resolved_by}>tiebreak</small>
        )}
      </div>
      <div className="team-orb" aria-hidden="true"><TeamMark team={model.team} size={27} isChampion={model.rank === 1} /></div>
      <div className="podium-copy">
        <p className="model-kicker">ELIGIBLE MODEL</p>
        <h3>{displayName(model)}</h3>
        <p className="model-name">{model.model}</p>
        <strong><CountUp value={model.score} /><small>/100</small></strong>
      </div>
      <span className="badge"><Medal size={15} />{model.badge || 'Official run'}</span>
    </article>
  )
}

function MedalWall({ awards, championTeam }) {
  if (!awards || !awards.length) return null
  return (
    <section className="medal-wall" aria-label="Category medals">
      <div className="section-heading">
        <div>
          <span className="eyebrow">CATEGORY MEDALS</span>
          <h2>Who wins what</h2>
          <p>One medal per category, awarded among eligible runs. A genuine tie on the metric itself names every tied team.</p>
        </div>
      </div>
      <div className="medal-grid">
        {awards.map((award, index) => {
          const Icon = AWARD_ICON[award.kind] || Medal
          // Staggered the same way the fun-award cards already are, so the two
          // adjacent card grids enter as one motion rather than two styles.
          const enter = { animationDelay: `${index * 0.06}s` }
          if (!award.awarded) {
            return (
              <article key={award.kind} className="medal-card unawarded" style={enter}>
                <span className="medal-orb"><Icon size={20} /></span>
                <h3>{award.label}</h3>
                <p className="medal-empty">Not awarded</p>
                <small>{award.reason}</small>
              </article>
            )
          }
          // A genuine tie lists every co-winner, ordered the same way the
          // fallback tiebreak (lower p95 latency) would -- see
          // results.py's compute_awards/_tie_sort_key -- so the first name
          // here is the team that would actually win a tiebreak, not just
          // whichever team name sorts first alphabetically.
          const winners = award.teams && award.teams.length
            ? award.teams
            : [{ team: award.team, model: award.model }]
          return (
            <article key={award.kind} className={`medal-card kind-${award.kind}`} style={enter}>
              <span className="medal-orb"><Icon size={20} /></span>
              <h3>{award.label}</h3>
              <strong>{formatMetric(award.metric, award.value)}</strong>
              <p className="medal-team">
                {winners
                  .map(entry => getTeamDisplayName(entry.team, 0, championTeam))
                  .join(' & ')}
              </p>
              <small>
                {award.tied
                  ? `tied -- ${winners.map(entry => entry.model).join(', ')}`
                  : award.model}
              </small>
            </article>
          )
        })}
      </div>
    </section>
  )
}

function TrendOverlay({ models, hovered, onHover }) {
  const series = models
    .map(model => ({
      team: model.team,
      name: displayName(model),
      accent: model.displayAccent,
      values: Array.isArray(model.trend) ? model.trend : [],
    }))
    .filter(item => item.values.length >= 2)
  if (series.length < 2) return null
  const width = 560
  const height = 120
  const all = series.flatMap(item => item.values)
  const min = Math.min(...all) - 1
  const max = Math.max(...all) + 1
  return (
    <div className="trend-overlay">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Score trend for the compared models"
        className={hovered ? 'has-hover' : ''}
      >
        {series.map((item, index) => {
          const points = item.values
            .map((value, index) => {
              const x = (index / Math.max(item.values.length - 1, 1)) * width
              const y = height - ((value - min) / Math.max(max - min, 1)) * height
              return `${x},${y}`
            })
            .join(' ')
          return (
            <polyline
              key={item.team}
              className={hovered === item.team ? 'hot' : ''}
              points={points}
              fill="none"
              vectorEffect="non-scaling-stroke"
              stroke={ACCENT_HEX[item.accent] || ACCENT_HEX.ocean}
              pathLength="1"
              // Each line starts drawing slightly after the one above it, so a
              // crowded overlay resolves into separate teams instead of one
              // simultaneous scribble.
              style={{ animationDelay: `${index * 0.12}s` }}
            />
          )
        })}
      </svg>
      <ul className="trend-overlay-legend">
        {series.map(item => (
          // tabIndex rather than a <button>: this only highlights, it performs
          // no action, and the legend already carries the same information in
          // text and colour. The highlight is an enhancement, not the content.
          <li
            key={item.team}
            className={hovered === item.team ? 'hot' : ''}
            tabIndex={0}
            onMouseEnter={() => onHover?.(item.team)}
            onMouseLeave={() => onHover?.(null)}
            onFocus={() => onHover?.(item.team)}
            onBlur={() => onHover?.(null)}
          >
            <span className="legend-dot" style={{ background: ACCENT_HEX[item.accent] || ACCENT_HEX.ocean }} />
            {item.name}
          </li>
        ))}
      </ul>
    </div>
  )
}

// Unofficial, purely for fun: computed client-side from the same run history
// the board already ships (`trend`, `run_count`). Never mixed into
// `board.awards`, so it can never be mistaken for a real medal.
const FUN_AWARD_ICONS = { improved: TrendingUp, comeback: Flame, iron: History }

function computeFunAwards(models) {
  const withTrend = models.filter(model => Array.isArray(model.trend) && model.trend.length >= 2)
  const pick = (list, scoreFn) =>
    list.reduce((best, model) => {
      const score = scoreFn(model)
      return score !== null && !Number.isNaN(score) && (!best || score > best.score) ? { model, score } : best
    }, null)

  const improved = pick(withTrend, model => model.trend.at(-1) - model.trend[0])
  const comeback = pick(withTrend, model => model.trend.at(-1) - model.trend.at(-2))
  const iron = pick(models.filter(model => (model.run_count || 0) > 0), model => model.run_count)

  return [
    {
      key: 'improved', label: 'Most improved', entry: improved,
      format: value => `+${value.toFixed(1)} pts since its first tracked run`,
    },
    {
      key: 'comeback', label: 'Comeback of the round', entry: comeback,
      format: value => `${value >= 0 ? '+' : ''}${value.toFixed(1)} pts in its latest run`,
    },
    {
      key: 'iron', label: 'Iron model', entry: iron,
      format: value => `${value} run${value === 1 ? '' : 's'} tracked`,
    },
  ]
}

function FunAwards({ models }) {
  const awards = useMemo(() => computeFunAwards(models), [models])
  if (!awards.some(award => award.entry)) return null
  return (
    <section className="fun-awards" aria-label="Unofficial fun awards">
      <div className="section-heading">
        <div>
          <span className="eyebrow">JUST FOR FUN · NOT AN OFFICIAL MEDAL</span>
          <h2>Bonus bragging rights</h2>
          <p>Derived from the same run history charted below — none of this affects rank, eligibility, or the real medals above.</p>
        </div>
      </div>
      <div className="fun-award-grid">
        {awards.map((award, index) => {
          const Icon = FUN_AWARD_ICONS[award.key]
          if (!award.entry) {
            return (
              <article key={award.key} className="fun-award-card empty" style={{ animationDelay: `${index * 0.08}s` }}>
                <span className="fun-award-orb"><Icon size={18} /></span>
                <h3>{award.label}</h3>
                <p>Not enough history yet</p>
              </article>
            )
          }
          return (
            <article key={award.key} className="fun-award-card" style={{ animationDelay: `${index * 0.08}s` }}>
              <span className="fun-award-orb"><Icon size={18} /></span>
              <h3>{award.label}</h3>
              <strong>{displayName(award.entry.model)}</strong>
              <p>{award.format(award.entry.score)}</p>
            </article>
          )
        })}
      </div>
    </section>
  )
}

function HistoryModal({ model, onClose }) {
  if (!model) return null
  const values = Array.isArray(model.trend) ? model.trend : []
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section className="modal history-modal" role="dialog" aria-modal="true" aria-labelledby="history-title" onMouseDown={event => event.stopPropagation()}>
        <button className="icon-button modal-close" onClick={onClose} aria-label="Close run history"><X /></button>
        <span className="eyebrow">RUN HISTORY</span>
        <h2 id="history-title">{displayName(model)}</h2>
        <p>
          {values.length
            ? `Score across the last ${values.length} run${values.length === 1 ? '' : 's'} sharing this document set, mode, and evaluation source.`
            : 'No comparable run history yet — this is the first tracked run on this document set.'}
        </p>
        {values.length >= 2 && (
          <div className="history-chart">
            <Sparkline values={values} accent={model.displayAccent} large />
          </div>
        )}
        {values.length > 0 && (
          <ol className="history-list">
            {values.map((value, index) => (
              <li key={index}><span>Run {index + 1}</span><strong>{value.toFixed(1)}</strong></li>
            ))}
          </ol>
        )}
      </section>
    </div>
  )
}

function EasterEggModal({ open, onClose, podium, awards }) {
  if (!open) return null
  const names = podium.filter(Boolean).map(model => displayName(model))
  const medalCount = (awards || []).filter(award => award.awarded).length
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section className="modal easter-egg" role="dialog" aria-modal="true" aria-labelledby="egg-title" onMouseDown={event => event.stopPropagation()}>
        <button className="icon-button modal-close" onClick={onClose} aria-label="Close"><X /></button>
        <span className="eyebrow">SECRET HALL OF FAME</span>
        <h2 id="egg-title"><PartyPopper size={26} /> You found it.</h2>
        <p>
          {names.length
            ? `Today's champion circle: ${names.join(', ')}.`
            : 'No eligible champion circle yet — check back once a run clears the gates.'}
          {' '}{medalCount} medal{medalCount === 1 ? '' : 's'} handed out this round.
        </p>
        <p className="modal-footnote">Five clicks on the crown. Nicely done.</p>
      </section>
    </div>
  )
}

function Comparison({ models, selected, onToggle, onClear }) {
  const [sortBy, setSortBy] = useState(null)
  // Which model the pointer or keyboard is on, so the chart and the table can
  // point at each other. They carried no shared cue before this: the overlay
  // colours its lines by accent, while the table used no model colour at all.
  const [hovered, setHovered] = useState(null)
  const base = models.filter(model => selected.includes(`${model.team}::${model.model}`))
  const chosen = useMemo(() => {
    if (!sortBy) return base
    return [...base].sort((a, b) => {
      const av = metricValue(a, sortBy.name)
      const bv = metricValue(b, sortBy.name)
      if (av === null) return bv === null ? 0 : 1
      if (bv === null) return -1
      const ordered = sortBy.lowerIsBetter ? av - bv : bv - av
      return sortBy.reversed ? -ordered : ordered
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, sortBy])
  // Three clicks, not two: best-first, then worst-first, then back to the
  // table's own order -- a plain on/off toggle meant the direction arrow next
  // to an active sort never had anything to do, since there was only ever one
  // direction to be in. `reversed` flips the *display* order; `lowerIsBetter`
  // never changes once set, since that is a property of the metric itself.
  const toggleSort = metric =>
    setSortBy(current => {
      if (current?.name !== metric.name) return { name: metric.name, lowerIsBetter: !!metric.lowerIsBetter, reversed: false }
      if (!current.reversed) return { ...current, reversed: true }
      return null
    })

  if (!models.length) return null
  return (
    <section className="comparison" id="compare" aria-label="Model comparison">
      <div className="section-heading">
        <div>
          <span className="eyebrow">HEAD TO HEAD</span>
          <h2>Compare models</h2>
          <p>
            {chosen.length
              ? `Comparing ${chosen.length} model${chosen.length === 1 ? '' : 's'}. The best value in each row is highlighted — click a row's label to sort by it.`
              : 'Pick two or more models below — or tick their rows in the table above — to compare every reported metric side by side.'}
          </p>
        </div>
        {chosen.length > 0 && (
          <button className="refresh" onClick={onClear}><X size={16} />Clear selection</button>
        )}
      </div>

      {/* The same selection the ranking table's checkboxes drive, offered
          again here. Without it this panel is a dead end: the table is far
          enough up the page that adding a second model meant scrolling back,
          and comparing needs at least two. Both write through `onToggle`, so
          the chips and the checkboxes can never disagree. */}
      <div className="compare-picker" role="group" aria-label="Choose models to compare">
        {models.map(model => {
          const key = `${model.team}::${model.model}`
          const isPicked = selected.includes(key)
          return (
            <button
              key={key}
              type="button"
              className={`compare-chip ${isPicked ? 'picked' : ''}`}
              aria-pressed={isPicked}
              onClick={() => onToggle(key)}
              title={model.model}
            >
              <span
                className="legend-dot"
                aria-hidden="true"
                style={{ background: ACCENT_HEX[model.displayAccent] || ACCENT_HEX.ocean }}
              />
              {displayName(model)}
              {isPicked ? <Check size={13} /> : <Plus size={13} />}
            </button>
          )
        })}
      </div>

      {chosen.length === 0 ? (
        <p className="availability-note"><Info size={16} />Nothing selected yet — pick a model above to start comparing.</p>
      ) : (
        <>
          <TrendOverlay models={chosen} hovered={hovered} onHover={setHovered} />
          <div className="table-wrap compare-wrap">
            <table className="compare-table">
              <thead>
                <tr>
                  <th scope="col">Metric</th>
                  {chosen.map(model => (
                    <th
                      key={`${model.team}-${model.model}`}
                      scope="col"
                      className={hovered === model.team ? 'col-hot' : ''}
                      style={{ '--col-accent': ACCENT_HEX[model.displayAccent] || ACCENT_HEX.ocean }}
                      tabIndex={0}
                      onMouseEnter={() => setHovered(model.team)}
                      onMouseLeave={() => setHovered(null)}
                      onFocus={() => setHovered(model.team)}
                      onBlur={() => setHovered(null)}
                    >
                      {/* The persistent half of the link. Without a colour on the
                          column at rest, dimming the other lines on hover tells you
                          which line just got brighter but not which model it is.
                          Inside the <b> because `.compare-table thead th b` is
                          display:block, which would strand a preceding sibling on
                          its own line. */}
                      <b>
                        <span className="legend-dot" aria-hidden="true" style={{ background: 'var(--col-accent)' }} />
                        {displayName(model)}
                      </b>
                      <small>{model.model}</small>
                      {!model.eligible && <span className="chip ineligible">Ineligible</span>}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {COMPARISON_METRICS.filter(metric =>
                  chosen.some(model => metricOf(model, metric.name) !== null),
                ).map(metric => {
                  const values = chosen.map(model => metricValue(model, metric.name))
                  const present = values.filter(value => value !== null && value !== undefined)
                  const best = present.length
                    ? (metric.lowerIsBetter ? Math.min(...present) : Math.max(...present))
                    : null
                  return (
                    <tr key={metric.name}>
                      <th
                        scope="row"
                        className="sortable-row"
                        onClick={() => toggleSort(metric)}
                        title={
                          sortBy?.name === metric.name
                            ? (sortBy.reversed ? 'Sorted worst first — click to clear' : 'Sorted best first — click to reverse')
                            : `Click to sort by this metric, best first${metric.lowerIsBetter ? ' (lower values are better here)' : ''}`
                        }
                      >
                        {metric.label}
                        {metric.description && (
                          <InfoTip>
                            {metric.description}
                            {metric.lowerIsBetter && ' Lower values are better for this metric.'}
                          </InfoTip>
                        )}
                        {sortBy?.name === metric.name &&
                          (sortBy.reversed
                            ? <ArrowUp size={12} className="sort-active" />
                            : <ArrowDown size={12} className="sort-active" />)}
                        {metric.note && <small className="metric-note">{metric.note}</small>}
                      </th>
                      {chosen.map((model, index) => {
                        const envelope = formatEnvelope(model, metric.name)
                        const isBest = present.length > 1 && values[index] !== null && values[index] === best
                        return (
                          <td
                            key={`${model.team}-${metric.name}`}
                            className={columnClass(isBest && 'best-cell', hovered === model.team)}
                            title={envelope.reason || undefined}
                          >
                            {envelope.text}
                            {metric.name === 'model_size_mb' && <WeightClassBadge sizeMb={values[index]} />}
                            {isBest && <Check size={14} className="best-tick" />}
                          </td>
                        )
                      })}
                    </tr>
                  )
                })}
                <EntityRecallRows models={chosen} hovered={hovered} />
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}

function EntityRecallRows({ models, hovered }) {
  const entities = useMemo(() => {
    const names = new Set()
    models.forEach(model => Object.keys(model.per_entity_recall || {}).forEach(name => names.add(name)))
    return [...names].sort()
  }, [models])
  if (!entities.length) return null
  return (
    <>
      <tr className="group-row"><th scope="row" colSpan={models.length + 1}>Per-entity recall</th></tr>
      {entities.map(entity => {
        const values = models.map(model => {
          const metric = model.per_entity_recall?.[entity]
          return metric && metric.available ? metric.value : null
        })
        const present = values.filter(value => value !== null)
        const best = present.length ? Math.max(...present) : null
        return (
          <tr key={entity}>
            <th scope="row">{entity.replace(/_/g, ' ')}</th>
            {values.map((value, index) => (
              <td
                key={`${entity}-${index}`}
                className={columnClass(
                  present.length > 1 && value !== null && value === best && 'best-cell',
                  hovered === models[index]?.team,
                )}
              >
                {value === null ? 'n/a' : `${(value * 100).toFixed(2)}%`}
              </td>
            ))}
          </tr>
        )
      })}
    </>
  )
}

// A board built from the cross-team ranking document is not scored by the
// weighted policy below and no safety gate was applied to it, so describing it
// that way would be plainly wrong. This panel explains what QA actually did.
function RankingMethodology({ onClose, source }) {
  const basis = source?.basis === 'exact' ? 'exact' : 'overlap'
  const threshold = source?.close_threshold ?? 0.03
  const documentCount = source?.document_ids?.length ?? 0
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section className="modal" role="dialog" aria-modal="true" aria-labelledby="method-title" onMouseDown={event => event.stopPropagation()}>
        <button className="icon-button modal-close" onClick={onClose} aria-label="Close methodology"><X /></button>
        <span className="eyebrow">CROSS-TEAM RANKING{source?.mode ? ` — ${source.mode.toUpperCase()} MODE` : ''}</span>
        <h2 id="method-title">Detection quality decides. Speed and size only break ties.</h2>
        <p>
          Every team was scored by MLOps/QA on the same{documentCount ? ` ${documentCount}` : ''} document
          {documentCount === 1 ? '' : 's'}, micro-averaged: true positives and entity counts are summed across all
          of them before recall and precision are computed, so a good score on one small document cannot
          outweigh a poor one on a large document. A team missing a report for any single document is
          excluded from the whole comparison rather than scored as zero — every ranked team is measured on
          the identical document set.
        </p>
        <div className="gate-note">
          <LockKeyhole size={19} />
          <span>
            Teams are ordered by {basis} F1. Two teams whose F1 falls within {threshold} of each other are
            treated as equal on detection quality, and the place goes to the lower P95 latency, then to the
            smaller model. A clear lead on F1 is never overridden by speed or size.
          </span>
        </div>
        <p className="modal-footnote">
          <b>Overlap</b> credits a detected span that found the entity but got its boundaries wrong;
          <b> exact</b> requires the boundaries too. Both rankings are published, and the comparison table
          shows them side by side. {source?.notes?.length ? source.notes.join(' ') : ''}
        </p>
      </section>
    </div>
  )
}

function Methodology({ open, onClose, weights, gates, source }) {
  if (!open) return null
  if (source?.kind === 'cross-team-ranking') {
    return <RankingMethodology onClose={onClose} source={source} />
  }
  const weightRows = Object.entries(weights || {})
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section className="modal" role="dialog" aria-modal="true" aria-labelledby="method-title" onMouseDown={event => event.stopPropagation()}>
        <button className="icon-button modal-close" onClick={onClose} aria-label="Close methodology"><X /></button>
        <span className="eyebrow">SCORING POLICY 1.0</span>
        <h2 id="method-title">Privacy comes before speed.</h2>
        <p>Every nominated immutable model is tested by MLOps/QA on the same hidden synthetic dataset. Models must pass safety gates before receiving an official rank.</p>
        <div className="weight-grid">
          {(weightRows.length
            ? weightRows.map(([label, value]) => [label.replace(/_/g, ' '), `${Math.round(value * 100)}%`])
            : [['Recall', '40%'], ['Macro F1', '25%'], ['Precision', '10%'], ['Document success', '10%'], ['Latency', '10%'], ['Throughput', '5%']]
          ).map(([label, value]) => <div key={label}><strong>{value}</strong><span>{label}</span></div>)}
        </div>
        <div className="gate-note">
          <LockKeyhole size={19} />
          <span>
            Critical-entity recall must be at least {Math.round((gates?.critical_entity_recall_min ?? 0.98) * 100)}%,
            with {Math.round((gates?.document_success_rate_min ?? 1) * 100)}% valid output documents
            and at most {((gates?.api_error_rate_max ?? 0.01) * 100).toFixed(0)}% API errors.
          </span>
        </div>
        <p className="modal-footnote">
          Precision is reported as the non-PII preservation rate: the public masking API returns masked
          bytes without entity spans, so span-level precision cannot be measured.
        </p>
      </section>
    </div>
  )
}

// The four places on this page worth jumping to. The decorative sections
// (hero, podium, medal wall, fun awards) are deliberately absent: they are
// what you scroll past, not what you navigate to. Shared with App.jsx via
// onSectionNavChange below -- the topbar and the floating pill render these
// same tabs once the page leaves them, rather than this page owning a second,
// competing sticky bar of its own.
//
// `deepDive` marks a target that only exists while its own panel is mounted.
// Comparison and PerLabel replace each other, so a plain href="#labels" would
// resolve to nothing whenever Compare is showing -- navigating to one of them
// has to switch the panel first and scroll once the anchor exists.
const SECTIONS = [
  { id: 'models', label: 'Rankings', Icon: Trophy },
  { id: 'compare', label: 'Compare', Icon: Swords, deepDive: 'compare' },
  { id: 'labels', label: 'Per-entity', Icon: Tag, deepDive: 'per-entity' },
  { id: 'round', label: 'Methodology', Icon: BookOpen },
]

// Inline in the hero, advertising the deep dives on arrival -- the only place
// this page still renders its own copy of these tabs. Same .basis-toggle the
// deep-dive switch uses, so it inherits the arena's existing control styling.
function SectionNav({ active, onNavigate, className }) {
  return (
    <div className={className}>
      <div className="basis-toggle" role="group" aria-label="Jump to a section">
        {SECTIONS.map(section => (
          <button
            key={section.id}
            type="button"
            className={active === section.id ? 'active' : ''}
            onClick={() => onNavigate(section)}
          >
            <section.Icon size={15} />{section.label}
          </button>
        ))}
      </div>
    </div>
  )
}

// Which of the two heavy analysis panels is on screen. Only one renders at a
// time -- stacking both under the ranking table is what made this page
// unreadable -- but the table itself always stays visible above them, because
// the row checkboxes are how models get into the comparison in the first place.
// Built from .basis-toggle rather than a bespoke tab bar: it is the arena's
// existing segmented control (the ranking basis switch, Library's filters), so
// it already matches the page and already has themes.css coverage.
function DeepDiveSwitch({ active, onChange }) {
  return (
    <div className="deep-dive-switch">
      <div className="basis-toggle" role="group" aria-label="Choose which deep dive to show">
        <button
          type="button"
          className={active === 'compare' ? 'active' : ''}
          onClick={() => onChange('compare')}
        >
          <Swords size={15} />Compare
        </button>
        <button
          type="button"
          className={active === 'per-entity' ? 'active' : ''}
          onClick={() => onChange('per-entity')}
        >
          <Tag size={15} />Per-entity
        </button>
      </div>
    </div>
  )
}

export default function Arena({ board, loading, updated, onRefresh, theme, config, onSectionNavChange }) {
  const [methodOpen, setMethodOpen] = useState(false)
  const [deepDive, setDeepDive] = useState('compare')
  const [selected, setSelected] = useState([])
  const [basisOverride, setBasisOverride] = useState(null)
  const [historyModel, setHistoryModel] = useState(null)
  const [eggOpen, setEggOpen] = useState(false)
  const [confettiKey, setConfettiKey] = useState(null)
  const [previewKey, setPreviewKey] = useState(null)
  const [pendingScroll, setPendingScroll] = useState(null)
  const [activeSection, setActiveSection] = useState('models')
  const championRef = useRef(undefined)
  const crownClicksRef = useRef([])
  const tableRef = useRef(null)
  const navLockRef = useRef(false)

  const source = board?.diagnostics?.results_source
  // Boards built from the cross-team ranking document were never gated, so
  // any copy that promises a gate has to say something else for this source.
  const isRanking = source?.kind === 'cross-team-ranking'
  const rankingsAvailable = source?.rankings_available || []
  const defaultBasis = source?.basis === 'exact' ? 'exact' : 'overlapping'
  const showBasisToggle = isRanking && rankingsAvailable.length > 1
  const basis = showBasisToggle && rankingsAvailable.includes(basisOverride) ? basisOverride : defaultBasis
  const recallNames = RECALL_METRICS_BY_BASIS[basis]
  const precisionNames = PRECISION_METRICS_BY_BASIS[basis]
  const f1Names = F1_METRICS_BY_BASIS[basis]

  const models = useMemo(() => {
    const rows = Array.isArray(board?.models) ? board.models : []
    return rows.map((model, index) => ({
      ...model,
      displayAccent: accentOf(model, index),
      displayRank: model.rank ?? index + 1,
    }))
  }, [board])

  const eligible = models.filter(model => model.eligible)
  const podium = [eligible[1], eligible[0], eligible[2]].map((model, index) =>
    model ? { ...model, displayRank: [2, 1, 3][index] } : null,
  )
  const openPositions = Math.max(0, 3 - eligible.length)
  const round = board?.round || { label: 'Benchmark Round 04', period: 'August 2026' }
  const rivalry = rivalryLine(eligible, basis, defaultBasis)

  // The hero art reads the leader's own headline metrics, using the same
  // basis-aware names the table columns use so the two cannot disagree. It
  // previously hard-coded `pii_recall`, which a cross-team ranking board does
  // not carry at all, so that panel showed a dash on exactly the boards QA
  // actually publishes.
  const leader = eligible[0] || null
  const heroBars = [
    { key: 'recall', names: recallNames, color: 'var(--teal)' },
    { key: 'precision', names: precisionNames, color: 'var(--blue)' },
    { key: 'f1', names: f1Names, color: '#84c6d7' },
  ].map(bar => ({
    ...bar,
    value: leader ? metricValue(leader, firstMetricName(leader, bar.names)) : null,
  }))
  const heroSignal = heroBars[0].value

  // Flash any row whose score moved since the previous board. The confetti
  // above only fires when the champion changes; this covers every other move,
  // which is otherwise invisible on a refresh that reorders nothing.
  const [changedTeams, setChangedTeams] = useState(NO_CHANGES)
  const previousScoresRef = useRef(null)
  const flashTimerRef = useRef(null)

  useEffect(() => {
    const current = new Map(models.map(model => [model.team, model.score]))
    const previous = previousScoresRef.current
    previousScoresRef.current = current
    // Nothing to compare against on the very first board.
    if (!previous || prefersReducedMotion()) return
    const moved = new Set()
    current.forEach((score, team) => {
      const before = previous.get(team)
      if (typeof before === 'number' && typeof score === 'number' && before !== score) {
        moved.add(team)
      }
    })
    if (!moved.size) return
    setChangedTeams(moved)
    clearTimeout(flashTimerRef.current)
    flashTimerRef.current = setTimeout(() => setChangedTeams(NO_CHANGES), 1500)
  }, [models])

  useEffect(() => () => clearTimeout(flashTimerRef.current), [])

  // Which pairs actually swapped places since the previous board. Deliberately
  // a separate signal from the flash above: that one watches `score`, this one
  // watches `rank`, and the two disagree because QA's tiebreak is an F1 rule.
  const { duels, winners, losers } = useRankDuels(models, tableRef)

  const toggle = key =>
    setSelected(current => (current.includes(key) ? current.filter(item => item !== key) : [...current, key]))

  // Never passes `behavior: 'smooth'`. styles.css already sets
  // `html { scroll-behavior: smooth }` and resets it to `auto` under
  // prefers-reduced-motion, so inheriting the stylesheet gets the animation
  // *and* honours the motion preference; naming the option here would
  // override that rule and animate for readers who asked us not to.
  function goToSection(target) {
    // A click is a statement of intent, so it lights the item immediately and
    // holds it: the measurement below would otherwise overwrite the choice a
    // moment later with its own guess. Methodology is the case that exposed
    // this -- it is the last section, so the page runs out before it can reach
    // the top, and whatever sits above it looked "current" instead.
    setActiveSection(target.id)
    navLockRef.current = true
    if (target.deepDive && target.deepDive !== deepDive) {
      // The anchor does not exist yet -- mount the panel, then scroll to it
      // once React has committed, via the effect below.
      setDeepDive(target.deepDive)
      setPendingScroll(target.id)
      return
    }
    document.getElementById(target.id)?.scrollIntoView({ block: 'start' })
  }

  // Released on the reader's own next move rather than on a timer: the point
  // is to stop guessing while they are still, however long that lasts.
  useEffect(() => {
    const release = () => { navLockRef.current = false }
    window.addEventListener('wheel', release, { passive: true })
    window.addEventListener('touchstart', release, { passive: true })
    window.addEventListener('keydown', release)
    return () => {
      window.removeEventListener('wheel', release)
      window.removeEventListener('touchstart', release)
      window.removeEventListener('keydown', release)
    }
  }, [])

  useEffect(() => {
    if (!pendingScroll) return
    document.getElementById(pendingScroll)?.scrollIntoView({ block: 'start' })
    setPendingScroll(null)
  }, [pendingScroll, deepDive])

  // The topbar and floating pill in App.jsx render these same tabs (see
  // SECTIONS below) instead of this page owning a second, competing sticky
  // bar. Reporting rather than lifting the whole apparatus up: App only ever
  // needs the current snapshot to render from, while the section-tracking
  // effect below and goToSection's deep-dive switching stay local, since
  // they read and write state (deepDive, pendingScroll, navLockRef) that has
  // no reason to exist outside this page.
  useEffect(() => {
    onSectionNavChange?.({ sections: SECTIONS, active: activeSection, onNavigate: goToSection })
  }, [activeSection, deepDive])

  // Split from the effect above on purpose: that one re-fires on every
  // relevant change, and returning a cleanup from it would clear the parent's
  // copy between updates, not just on unmount.
  useEffect(() => () => onSectionNavChange?.(null), [])

  // Measured from scroll position rather than watched with an
  // IntersectionObserver. An observer can only report what is on screen, so
  // the rule ends up being "whichever visible section sits highest" -- and
  // that can never select the last section on the page, because the document
  // runs out before it reaches the top and something above it always wins.
  //
  // Recomputed when `deepDive` changes, since #compare and #labels unmount
  // each other and the mounted set differs.
  useEffect(() => {
    let frame = 0

    const measure = () => {
      frame = 0
      // A click owns the highlight until the reader moves. See goToSection.
      if (navLockRef.current) return

      const mounted = SECTIONS
        .map(section => ({ id: section.id, element: document.getElementById(section.id) }))
        .filter(item => item.element)
      if (!mounted.length) return

      // Read from the stylesheet rather than repeating the number here: this
      // pair drifted apart once already, when the bar moved and a hard-coded
      // margin in this file stayed behind.
      const declared = getComputedStyle(document.documentElement).getPropertyValue('--sticky-offset')
      const offset = Number.parseInt(declared, 10) || 96

      // At the end of the document the last section wins outright -- nothing
      // below it can scroll any further, so no distance rule could ever pick
      // it. Guarded on having scrolled at all, because a viewport tall enough
      // to show the whole page is "at the bottom" while sitting at the top.
      const atBottom =
        window.scrollY > 0 &&
        window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2
      if (atBottom) {
        setActiveSection(mounted[mounted.length - 1].id)
        return
      }

      const passed = mounted.filter(item => item.element.getBoundingClientRect().top <= offset + 4)
      setActiveSection((passed.length ? passed[passed.length - 1] : mounted[0]).id)
    }

    const onScroll = () => { if (!frame) frame = requestAnimationFrame(measure) }
    measure()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [deepDive, models.length])

  // Confetti fires only on a genuine change of champion, never on first load
  // or on a refresh that leaves the top spot untouched.
  const championTeam = eligible[0]?.team ?? null
  useEffect(() => {
    if (championRef.current !== undefined && championTeam && championTeam !== championRef.current) {
      setConfettiKey(Date.now())
    }
    championRef.current = championTeam
  }, [championTeam])

  useEffect(() => {
    if (!confettiKey) return
    const timer = setTimeout(() => setConfettiKey(null), 1800)
    return () => clearTimeout(timer)
  }, [confettiKey])

  function handleCrownClick() {
    const now = Date.now()
    const recent = [...crownClicksRef.current, now].filter(timestamp => now - timestamp < 3000)
    crownClicksRef.current = recent
    if (recent.length >= 5) {
      crownClicksRef.current = []
      setEggOpen(true)
      setConfettiKey(Date.now())
    }
  }

  return (
    <>
      <section className="hero">
        <div className="hero-copy">
          <span className="eyebrow">CEGEDIM · OFFICIAL PRIVACY BENCHMARK</span>
          <h1>PII masking,<br /><em>measured with care.</em></h1>
          <p>A fair, reproducible view of privacy-first model performance—designed for trusted healthcare and technology operations.</p>
          <div className="hero-actions">
            <button className="primary" onClick={() => setMethodOpen(true)}><BookOpen size={18} />View methodology</button>
            <span><ShieldCheck size={17} />Synthetic benchmark data</span>
          </div>
          {/* Says up front that the deep dives exist. Without this you only
              find Compare and Per-entity by scrolling past the whole board. */}
          <div className="hero-jump">
            <span className="hero-jump-label">Jump to</span>
            <SectionNav active={activeSection} onNavigate={goToSection} />
          </div>
          {rivalry && (
            <p className={`rivalry-ticker ${rivalry.photoFinish ? 'photo-finish' : ''}`}>
              <Swords size={15} />
              <span>{rivalry.text}</span>
            </p>
          )}
        </div>
        <div className="hero-art" aria-hidden="true">
          <div className="data-panel">
            <span>PRIVACY SIGNAL</span>
            <strong>
              {heroSignal === null ? '—' : <CountUp value={heroSignal * 100} />}
              <span>%</span>
            </strong>
            {/* These three bars used to be fixed 72/87/54% gradient stops while
                the number above them was real. They now plot the leader's own
                recall, precision and F1, and grow to those widths on load. */}
            {heroBars.map(bar => (
              <i
                key={bar.key}
                style={{ '--fill': `${bar.value === null ? 0 : Math.round(bar.value * 100)}%`, '--bar': bar.color }}
              />
            ))}
          </div>
          <div className={`trust-disc ${loading ? 'pulsing' : ''}`}><ShieldCheck size={75} /><span>VERIFIED</span></div>
          <div className="signal-line signal-a"></div><div className="signal-line signal-b"></div><div className="signal-dot dot-a"></div><div className="signal-dot dot-b"></div>
        </div>
      </section>

      {board?.source === 'mock' && (
        <p className="source-banner mock">
          <Info size={16} />
          <span>
            Temporary mock data for preview — randomized on every refresh. Real QA results
            will replace this automatically once published.
          </span>
        </p>
      )}

      {board?.source === 'demo' && (
        <p className="source-banner">
          <AlertTriangle size={16} />
          <span>
            Showing built-in demo data — these are not real results.
            {board?.diagnostics?.error
              ? <> Object storage could not be read: <code>{board.diagnostics.error}</code></>
              : ' No QA results were found in object storage.'}
          </span>
        </p>
      )}

      <section className="podium" aria-label="Top three eligible models">
        {confettiKey && <ConfettiBurst key={confettiKey} />}
        {/* Keyed by team+placement, not left to JSX position: without a key
            here, a refresh that reorders the podium (a new team overtaking the
            old rank 1, say) left React matching each slot's *old* component
            instance to whichever team now occupies it, so `useCountUp` kept
            animating from the previous occupant's score toward the new one's --
            two unrelated teams' numbers interpolating through each other. Rank
            badges swapped instantly since they are plain props, so mid-animation
            the on-screen numbers could briefly rank in the opposite order from
            the cards showing them. The placement suffix still keys an empty slot
            when `model` is null, since two open positions would otherwise share
            a key. */}
        <PodiumCard key={podium[0]?.team ?? 'empty-2'} model={podium[0]} placement={2} />
        <PodiumCard
          key={podium[1]?.team ?? 'empty-1'}
          model={podium[1]}
          featured
          placement={1}
          onCrownClick={handleCrownClick}
          laurelKey={championTeam}
        />
        <PodiumCard key={podium[2]?.team ?? 'empty-3'} model={podium[2]} placement={3} />
      </section>

      <MedalWall awards={board?.awards} championTeam={championTeam} />

      <FunAwards models={models} />

      {theme === 'constellation' && <ConstellationMap models={models} />}

      <section className="ranking" id="models">
        <div className="section-heading">
          <div>
            <span className="eyebrow">{String(round.label || '').toUpperCase()}</span>
            <h2>Official rankings</h2>
            <p>
              {isRanking
                ? 'Every team compared on the identical document set. A team missing any document’s report is excluded rather than scored as zero.'
                : 'Every evaluated model. Only runs that clear the safety gates receive an official rank.'}
            </p>
            {isRanking && source?.document_ids?.length > 0 && (
              <p className="provenance-note">
                Scored across {source.document_ids.length} document{source.document_ids.length === 1 ? '' : 's'} · 1 of{' '}
                {source.candidates_considered ?? '—'} recent QA snapshots · ties within{' '}
                {Math.round((source.close_threshold ?? 0.03) * 100)}% break on latency, then model size.
              </p>
            )}
          </div>
          <div className="heading-actions">
            {showBasisToggle && (
              <div className="basis-toggle" role="group" aria-label="Choose which ranking basis to display">
                {rankingsAvailable.includes('overlapping') && (
                  <button className={basis === 'overlapping' ? 'active' : ''} onClick={() => setBasisOverride('overlapping')}>Overlap</button>
                )}
                {rankingsAvailable.includes('exact') && (
                  <button className={basis === 'exact' ? 'active' : ''} onClick={() => setBasisOverride('exact')}>Exact</button>
                )}
              </div>
            )}
            <button className="refresh" onClick={() => exportBoardCsv(models)}><Download size={16} />Export CSV</button>
            <button className="refresh" onClick={onRefresh} disabled={loading}>
              <RefreshCw className={loading ? 'spin' : ''} />Updated {updated}
            </button>
          </div>
        </div>
        {showBasisToggle && basis !== defaultBasis && (
          <p className="availability-note basis-note">
            <Info size={16} />Showing {basis} metrics below. Row order stays QA's official {defaultBasis} ranking.
          </p>
        )}
        <div className={`table-wrap ${loading ? 'loading' : ''}`} ref={tableRef}>
          {/* Keyed on the board's timestamp so a refresh remounts it and the
              sweep replays -- the same remount-to-replay trick the confetti
              uses, with no timer to clear. Which sweep it is depends on the
              theme; see BoardSweep. */}
          <BoardSweep theme={theme} key={board?.updated_at || 'initial'} />
          <DuelSparks duels={duels} />
          <table>
            <thead>
              <tr>
                <th><span className="sr-only">Compare</span></th>
                <th>Rank</th><th>Team / Model</th><th>Recall</th><th>Precision</th>
                <th>F1</th><th>P95 latency</th><th>Status</th><th>Trend</th>
              </tr>
            </thead>
            <tbody>
              {models.map((model, rowIndex) => {
                const key = `${model.team}::${model.model}`
                return (
                <Fragment key={key}>
                  <tr
                    data-team-id={model.team}
                    className={[
                      model.eligible ? '' : 'row-ineligible',
                      changedTeams.has(model.team) ? 'row-changed' : '',
                      winners.has(model.team) ? 'row-duel-winner' : '',
                      losers.has(model.team) ? 'row-duel-loser' : '',
                    ].filter(Boolean).join(' ')}
                    // Drives the entrance stagger; the podium cards already do
                    // this with nth-child delays, but a row count is unknown
                    // ahead of time so the index comes from the data.
                    //
                    // `--enter-from` is the wing this row walks in from. The
                    // parity is computed here rather than in CSS because calc()
                    // has no modulo and :nth-child would be thrown off by the
                    // gate-reason rows interleaved below.
                    style={{ '--row-index': rowIndex, '--enter-from': rowIndex % 2 ? '38px' : '-38px' }}
                    // Focus mirrors hover so the preview is reachable by
                    // keyboard; the row's own checkbox and history button are
                    // already in the tab order, and focus bubbles from them.
                    onMouseEnter={() => setPreviewKey(key)}
                    onMouseLeave={() => setPreviewKey(current => (current === key ? null : current))}
                    onFocus={() => setPreviewKey(key)}
                    onBlur={event => {
                      if (!event.currentTarget.contains(event.relatedTarget)) {
                        setPreviewKey(current => (current === key ? null : current))
                      }
                    }}
                  >
                    <td>
                      <input
                        type="checkbox"
                        checked={selected.includes(key)}
                        onChange={() => toggle(key)}
                        aria-label={`Compare ${displayName(model)}`}
                      />
                    </td>
                    <td>
                      <span className={`rank-number ${model.displayAccent}`} title={model.resolved_by || undefined}>
                        {model.eligible ? model.displayRank : '—'}
                      </span>
                      {model.resolved_by?.startsWith('tiebreak') && (
                        <small className="rank-note" title={model.resolved_by}>tiebreak</small>
                      )}
                    </td>
                    <td>
                      <div className="team-cell" style={{ '--row-index': rowIndex }}>
                        <Combatant accent={model.displayAccent} muted={!model.eligible} />
                        <span className={`mini-orb ${model.displayAccent}`}><TeamMark team={model.team} size={19} isChampion={model.rank === 1} /></span>
                        <span>
                          <b>{displayName(model)}<StreakFlair runCount={model.run_count} /></b>
                          <small>{model.model}</small>
                        </span>
                      </div>
                    </td>
                    <td>
                      <strong className={`score ${model.displayAccent}`}>
                        <CountUp value={scoreForBasis(model, basis, defaultBasis)} />
                      </strong>
                      <ScoreDirection model={model} />
                    </td>
                    <td title={formatFirstEnvelope(model, precisionNames).reason || undefined}>{formatFirstEnvelope(model, precisionNames).text}</td>
                    <td title={formatFirstEnvelope(model, f1Names).reason || undefined}>{formatFirstEnvelope(model, f1Names).text}</td>
                    <td>
                      {(() => {
                        const p95 = metricValue(model, 'latency_p95_ms')
                        if (p95 === null) return <span>n/a</span>
                        return <span className={p95 < 250 ? 'speed-good' : 'speed-warn'}>{formatMetric('latency_p95_ms', p95)}</span>
                      })()}
                    </td>
                    <td>
                      {model.eligible ? (
                        <span className="status eligible"><Check />Eligible</span>
                      ) : (
                        <span
                          className="status blocked"
                          title={
                            (model.ineligible_reasons || []).join('; ') ||
                            (isRanking ? 'Not comparable on this document set' : 'Did not clear the safety gates')
                          }
                        >
                          <AlertTriangle size={14} />{isRanking ? 'Not compared' : 'Gate review'}
                        </span>
                      )}
                    </td>
                    <td>
                      <div className="trend-cell">
                        <Sparkline values={model.trend} accent={model.displayAccent} />
                        {(model.trend?.length || 0) >= 2 && (
                          <button
                            className="history-trigger"
                            onClick={() => setHistoryModel(model)}
                            aria-label={`View run history for ${displayName(model)}`}
                            title="View run history"
                          >
                            <History size={13} />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                  {!model.eligible && model.ineligible_reasons?.length > 0 && (
                    <tr className="gate-reason-row">
                      <td colSpan={10}>
                        <AlertTriangle size={13} />
                        <span>{model.ineligible_reasons.join('; ')}</span>
                      </td>
                    </tr>
                  )}
                  {/* Mounted only while the row is hovered or focused, which is
                      what starts the typewriter -- see MaskingPreview. The row
                      keeps the same hover handlers, so moving the pointer down
                      into the preview does not close it. */}
                  {previewKey === key && (
                    <tr
                      className="masking-preview-row"
                      onMouseEnter={() => setPreviewKey(key)}
                      onMouseLeave={() => setPreviewKey(current => (current === key ? null : current))}
                    >
                      <td colSpan={10}>
                        <MaskingPreview model={model} />
                      </td>
                    </tr>
                  )}
                </Fragment>
                )
              })}
              {!models.length && (
                <tr><td className="empty-row" colSpan="10"><Medal size={22} />No model runs are available for this round.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {openPositions > 0 && (
          <p className="availability-note">
            <Info size={16} />{openPositions} of the three leaderboard positions {openPositions === 1 ? 'is' : 'are'} awaiting an eligible model run.
          </p>
        )}
      </section>

      {/* Both deep dives sit below the ranking table rather than above it, so
          the checkboxes that feed the comparison stay on screen while their
          result is read. */}
      <DeepDiveSwitch active={deepDive} onChange={setDeepDive} />

      {deepDive === 'compare' ? (
        <Comparison models={models} selected={selected} onToggle={toggle} onClear={() => setSelected([])} />
      ) : (
        <PerLabel
          models={models}
          coverage={board?.diagnostics?.per_label_coverage}
          taxonomy={config?.label_taxonomy}
          championTeam={championTeam}
        />
      )}

      <section className="principles" id="round">
        <div><Trophy /><h3>Fair by design</h3><p>One evaluator runs every immutable release under identical conditions.</p></div>
        <div><LockKeyhole /><h3>Hidden benchmark</h3><p>Teams develop on the public split; official ranking uses a private synthetic split.</p></div>
        <div><Timer /><h3>Safety gates first</h3><p>Only eligible runs can occupy one of the three official positions.</p></div>
      </section>

      <Methodology
        open={methodOpen}
        onClose={() => setMethodOpen(false)}
        weights={board?.weights}
        gates={board?.gates}
        source={source}
      />
      <HistoryModal model={historyModel} onClose={() => setHistoryModel(null)} />
      <EasterEggModal open={eggOpen} onClose={() => setEggOpen(false)} podium={podium} awards={board?.awards} />
    </>
  )
}
