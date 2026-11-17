/**
 * The arena's theatrical layer: combatants entering the ring, duel sparks at a
 * rank crossover, a laurel and torches for the champion, a typewritten masking
 * preview, and the mirage haze behind the page.
 *
 * All of it is decoration over a board whose numbers are decided elsewhere, and
 * all of it is opt-out. Anything interpolated in JavaScript checks
 * `prefersReducedMotion()` here, because the stylesheet's blanket override
 * cannot reach a value being computed frame by frame; anything driven by CSS is
 * listed in the `prefers-reduced-motion` block in console.css.
 *
 * This lives apart from Arena.jsx only because Arena.jsx is already 1200 lines
 * and fifteen components; nothing here is imported anywhere else.
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Swords } from 'lucide-react'
import { maskedLength, originalSegments, segments } from './maskingSample'

// Shared, never mutated. A stable identity keeps the effects below from
// re-running themselves when they clear -- the same reason Arena.jsx keeps a
// module-level `NO_CHANGES` set.
const NO_DUELS = []

// Every animation added here is decorative, so all of them check this first
// rather than relying on the stylesheet's blanket reduced-motion override.
// Duplicated from Arena.jsx rather than exported across the two files, which
// would make the pair import each other.
function prefersReducedMotion() {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
  )
}

/* ---------- combatants entering the ring ---------- */

/**
 * The silhouette that walks in ahead of a row and fades as the team's orb
 * settles over it. Purely decorative, so it is hidden from assistive tech and
 * carries no team name -- the row's own cell already says who this is.
 *
 * `muted` is the ineligible case: a team with no rank is not in the contest, so
 * it takes the field in grey and never duels.
 */
export function Combatant({ accent, muted = false }) {
  return (
    <svg
      className={`combatant ${muted ? 'muted' : accent}`}
      viewBox="0 0 28 36"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M4 34c0-6 4.5-9.5 10-9.5S24 28 24 34z" />
      <path d="M14 6a6 6 0 0 1 6 6v3.5a6 6 0 0 1-12 0V12a6 6 0 0 1 6-6z" />
      <path d="M8.4 9.5C9.7 4.6 11.8 2 14 2s4.3 2.6 5.6 7.5z" />
      <rect x="25" y="1.5" width="1.7" height="33" rx=".8" />
    </svg>
  )
}

/**
 * A single pass across the board when it arrives.
 *
 * Every theme gets one of these and no theme gets two: the colosseum's
 * spotlight and the vault's radar are the same gesture told differently, so
 * they are variants of one component rather than two things that could both
 * mount. The constellation has no sweep -- its stars ignite instead, which is
 * handled in CSS.
 *
 * Stateless on purpose: Arena remounts it by changing `key` on every refresh,
 * which restarts the CSS animation without a timer to clear. Same trick the
 * confetti burst already uses.
 */
export function BoardSweep({ theme }) {
  if (theme === 'constellation') return null
  if (theme === 'vault') {
    return (
      <span className="radar-sweep" aria-hidden="true">
        {/* The bar is the leading edge and the trail is its wake. Separating
            them lets the edge stay sharp while the wake fades, which is what
            distinguishes a scanner from a plain gradient wipe. */}
        <i className="radar-trail" />
        <i className="radar-edge" />
      </span>
    )
  }
  return <span className="spotlight-sweep" aria-hidden="true" />
}

/* ---------- duels ---------- */

// At most this many sparks per refresh. A board that reshuffles completely
// would otherwise fire one at every boundary at once, which reads as an error
// state rather than as a result.
const MAX_DUELS = 3

// Long enough to register as a clash, short enough that a second refresh does
// not land on top of the first.
const DUEL_MS = 900

/**
 * Rank crossovers between one board and the next.
 *
 * Reads `rank`, deliberately, and not `score`: the displayed score is
 * overlapping recall, while QA's tiebreak is a rule over F1, so the two
 * genuinely disagree. Sparking on a score change would light up pairs whose
 * order never moved and stay dark on pairs that swapped -- see the comment on
 * `_entry_metrics` in ranking.py.
 *
 * Returns each duel already measured against the table, so the caller can
 * render it without touching the DOM itself.
 */
export function useRankDuels(models, tableRef) {
  const [duels, setDuels] = useState(NO_DUELS)
  const [placed, setPlaced] = useState(NO_DUELS)
  const previousRanksRef = useRef(null)
  const timerRef = useRef(null)

  useEffect(() => {
    const ranked = models.filter(model => model.eligible && typeof model.rank === 'number')
    const current = new Map(ranked.map(model => [model.team, model.rank]))
    const previous = previousRanksRef.current
    previousRanksRef.current = current
    // Nothing to compare against on the very first board.
    if (!previous || prefersReducedMotion()) return

    // `ranked` is already in QA's rank order, so a pair that is adjacent here
    // and was the other way round before is exactly one crossover. No need to
    // compare current ranks -- the array position is the comparison.
    const found = []
    for (let index = 0; index < ranked.length - 1; index += 1) {
      const winner = ranked[index]
      const loser = ranked[index + 1]
      const winnerWas = previous.get(winner.team)
      const loserWas = previous.get(loser.team)
      if (typeof winnerWas !== 'number' || typeof loserWas !== 'number') continue
      if (winnerWas > loserWas) {
        found.push({ winner: winner.team, loser: loser.team, climb: winnerWas - winner.rank })
      }
    }
    if (!found.length) return

    // Biggest climbs win the limited number of sparks.
    found.sort((left, right) => right.climb - left.climb)
    setDuels(found.slice(0, MAX_DUELS))
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => setDuels(NO_DUELS), DUEL_MS)
  }, [models])

  useEffect(() => () => clearTimeout(timerRef.current), [])

  // Measured in a layout effect so the spark is positioned before it paints;
  // in a plain effect it would flash at the top of the table for one frame.
  useLayoutEffect(() => {
    const wrap = tableRef.current
    if (!duels.length || !wrap) {
      setPlaced(NO_DUELS)
      return
    }
    const wrapBox = wrap.getBoundingClientRect()
    const positioned = duels
      .map(duel => {
        const row = wrap.querySelector(`tr[data-team-id="${CSS.escape(duel.winner)}"]`)
        if (!row) return null
        // The crossover happened at the boundary the two rows now share, which
        // is the bottom edge of the row that came out on top.
        return { ...duel, top: row.getBoundingClientRect().bottom - wrapBox.top }
      })
      .filter(Boolean)
    setPlaced(positioned)
  }, [duels, tableRef])

  const teams = useMemo(() => {
    const winners = new Set(placed.map(duel => duel.winner))
    const losers = new Set(placed.map(duel => duel.loser))
    return { winners, losers }
  }, [placed])

  return { duels: placed, ...teams }
}

// Spark directions, fixed rather than random: a clash should look like one
// impact struck the same way every time, and a fixed fan also means the spark
// needs no `key`-remount dance to avoid re-randomising mid-flight.
const SPARK_ANGLES = [-72, -40, -14, 14, 40, 72, 128, 180, 232]

/** The clash itself: two blades meeting, and a fan of sparks off the impact. */
export function DuelSparks({ duels }) {
  if (!duels.length) return null
  return (
    <div className="duel-layer" aria-hidden="true">
      {duels.map(duel => (
        <span className="duel" key={`${duel.winner}-${duel.loser}`} style={{ top: `${duel.top}px` }}>
          <Swords className="duel-blades" size={20} />
          {SPARK_ANGLES.map(angle => (
            <i className="duel-spark" key={angle} style={{ '--angle': `${angle}deg` }} />
          ))}
        </span>
      ))}
    </div>
  )
}

/* ---------- champion laurel and torches ---------- */

// One leaf, drawn once and placed seven times per branch. Its own transform
// lives on a wrapping <g>, never on the path: a CSS `transform` animation on
// the path would otherwise override the SVG transform attribute and collapse
// every leaf onto the origin.
const LEAF = 'M0 0C4.6-3.6 10.4-2.8 13-0 10.4 2.8 4.6 3.6 0 0Z'

const LEAVES = [
  { x: 16, y: 54, rotate: 250 },
  { x: 13, y: 44, rotate: 240 },
  { x: 14.5, y: 34, rotate: 228 },
  { x: 19, y: 25, rotate: 212 },
  { x: 27, y: 17, rotate: 196 },
  { x: 37, y: 11.5, rotate: 184 },
]

function LaurelBranch() {
  return (
    <g>
      {/* `pathLength="1"` normalises the stem regardless of its real length, so
          the draw-on in theatrics.css dashes it with a plain 0-1 offset. The
          sparklines already do this. */}
      <path className="laurel-stem" d="M18 60C10 40 18 20 42 10" pathLength="1" />
      {LEAVES.map((leaf, index) => (
        <g key={index} transform={`translate(${leaf.x} ${leaf.y}) rotate(${leaf.rotate})`}>
          <path className="laurel-leaf" style={{ '--leaf-index': index }} d={LEAF} />
        </g>
      ))}
    </g>
  )
}

/**
 * The wreath that draws itself around the champion's crown.
 *
 * The right branch is the left one mirrored in place rather than a second set
 * of coordinates, so the two can never drift apart when the curve is tuned.
 */
export function Laurel() {
  return (
    <svg className="laurel" viewBox="0 0 120 68" aria-hidden="true" focusable="false">
      <LaurelBranch />
      <g transform="translate(120 0) scale(-1 1)">
        <LaurelBranch />
      </g>
    </svg>
  )
}

/* A cartoon flame rather than a teardrop: a tall leaning tongue with a lick
   down each side, drawn as two stacked bodies so the hot core shows through
   the outer sheet. The previous shape was a single symmetrical blob, which
   read as a water drop pointed the wrong way up.

   One path each, closed and non-self-intersecting, so both fill cleanly at
   any size and can be reused at 11px in the streak chip. */
const FLAME_OUTER =
  'M8 19.5C4.1 19.5 1 16.6 1 13c0-2.7 1.4-4.5 2.9-6.1.7-.7 1.4-1.5 1.8-2.4' +
  '.2 1.1.6 2.1 1.3 2.9C7.6 5.2 7.4 2.4 6.6.5c2.8 1.5 5 4.1 6 7.1' +
  '.5-.7.8-1.6.9-2.5.9 1.9 1.5 4 1.5 6.3 0 4.5-3.1 8.1-7 8.1Z'

const FLAME_INNER =
  'M8 17.4c-2.3 0-4.1-1.9-4.1-4.2 0-1.9 1.1-3.2 2.3-4.6.5.8.9 1.5 1.4 2.1' +
  '.3-2.2 0-4.5-.5-6.1 2.1 1.7 3.9 4.1 4.5 6.6.3.8.5 1.4.5 2 0 2.3-1.8 4.2-4.1 4.2Z'

/**
 * The flame glyph, shared by the champion's torches and the streak chip so a
 * flame means the same thing wherever it appears.
 */
export function FlameGlyph({ className = '', size }) {
  return (
    <svg
      className={`flame-glyph ${className}`}
      viewBox="0 0 16 20"
      width={size}
      height={size ? (size * 20) / 16 : undefined}
      aria-hidden="true"
      focusable="false"
    >
      <path className="flame-outer" d={FLAME_OUTER} />
      <path className="flame-inner" d={FLAME_INNER} />
    </svg>
  )
}

/** Two guttering torches flanking the champion's card. */
export function Torches() {
  return (
    <span className="torch-pair" aria-hidden="true">
      {['left', 'right'].map(side => (
        <span className={`torch torch-${side}`} key={side}>
          <FlameGlyph className="torch-flame" />
        </span>
      ))}
    </span>
  )
}

/* ---------- masking preview ---------- */

// Roughly a fast typist. The cap matters more than the rate: the sample is
// ~180 characters, and a preview that outlives the pointer hovering it is just
// a delay.
const MS_PER_CHAR = 12
const TYPE_MAX_MS = 2500

/**
 * How many characters of the masked text have been typed so far.
 *
 * Built on requestAnimationFrame rather than an interval, matching
 * `useCountUp` in Arena.jsx: an interval at 12ms would queue work faster than
 * a loaded main thread can drain it.
 */
function useTypewriter(total) {
  const [shown, setShown] = useState(0)

  useEffect(() => {
    if (prefersReducedMotion()) {
      setShown(total)
      return undefined
    }
    const duration = Math.min(total * MS_PER_CHAR, TYPE_MAX_MS)
    let frame
    let start
    const step = timestamp => {
      if (start === undefined) start = timestamp
      const progress = Math.min((timestamp - start) / duration, 1)
      setShown(Math.round(progress * total))
      if (progress < 1) frame = requestAnimationFrame(step)
    }
    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [total])

  return shown
}

/** The leading slice of `pieces` holding the first `shown` characters. */
function typed(pieces, shown) {
  const visible = []
  let used = 0
  for (const piece of pieces) {
    if (used >= shown) break
    visible.push({ ...piece, text: piece.text.slice(0, shown - used) })
    used += piece.text.length
  }
  return visible
}

/**
 * The hovered row's before/after: the sample as written, then the same sample
 * typed out as this model would leave it.
 *
 * Which spans survive is read from the model's own published per-entity recall
 * (see maskingSample.js), so a model QA measured as weak on one entity type
 * visibly misses it here. The text is invented and the note below says so --
 * this is an illustration of the numbers, not a captured model output.
 */
export function MaskingPreview({ model }) {
  // The component mounts when the row is hovered or focused and unmounts when
  // it is left, so mounting is what starts the typing -- no `active` flag.
  const pieces = useMemo(() => segments(model), [model])
  const before = useMemo(() => originalSegments(), [])
  const total = useMemo(() => maskedLength(pieces), [pieces])
  const shown = useTypewriter(total)
  const visible = typed(pieces, shown)
  const missed = pieces.filter(piece => piece.kind === 'missed')

  return (
    <div className="mask-preview">
      <div className="mask-side original">
        <span className="mask-side-label">Before</span>
        <p>
          {before.map((piece, index) =>
            piece.kind === 'text' ? (
              piece.text
            ) : (
              <mark className="mask-pii" key={index}>{piece.text}</mark>
            ),
          )}
        </p>
      </div>

      <div className="mask-side masked">
        <span className="mask-side-label">After</span>
        <p>
          {visible.map((piece, index) => {
            if (piece.kind === 'text') return piece.text
            return (
              <span className={piece.kind === 'masked' ? 'mask-token' : 'mask-missed'} key={index}>
                {piece.text}
              </span>
            )
          })}
          {shown < total && <i className="mask-caret" aria-hidden="true" />}
        </p>
      </div>

      <p className="mask-note">
        Illustrative sample on synthetic text. Which spans this model catches follows its own
        published per-entity recall — it is not a captured output of this run.
        {missed.length > 0 && (
          <> Shown missing {[...new Set(missed.map(piece => piece.label))].join(', ')}.</>
        )}
      </p>
    </div>
  )
}

/* ---------- mirage ---------- */

/**
 * The heat haze behind the page: a visual pun, since masking is the business of
 * things not being what they seem.
 *
 * Deliberately faint, and made of transform and opacity only so it stays on one
 * compositor layer over the radial gradients already painted on `body`.
 */
export function MirageLayer() {
  return (
    <div className="mirage-layer" aria-hidden="true">
      <span className="mirage-band band-near" />
      <span className="mirage-band band-far" />
    </div>
  )
}
