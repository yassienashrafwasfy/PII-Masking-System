/**
 * The board as a star map.
 *
 * Each eligible model is a star: brighter and larger the higher its score,
 * placed on a ring by rank so the leader sits nearest the centre. Faint lines
 * join consecutive ranks, which is what makes it a constellation rather than a
 * scatter of dots -- the figure you trace is the ranking itself.
 *
 * Every position is derived from rank and team id, never from Math.random, so
 * the same board always draws the same sky. A constellation that rearranged
 * itself on each render would read as noise, and would also fight the
 * count-up animation happening in the table below it.
 *
 * Two kinds of decoration sit behind that figure and are allowed to be random,
 * because neither one carries any information: a couple of comets crossing
 * the panel on long, mostly-idle loops, and a handful of small ambient stars
 * that twinkle continuously. Both are a fixed, small element count animating
 * only opacity/transform (plus two elements' own left/top, which -- being
 * absolutely positioned and out of flow -- never triggers a reflow of
 * anything else on the page), so the cost stays negligible regardless of how
 * long the reader leaves the tab open.
 *
 * The models' own glow also breathes, on a per-star negative animation-delay
 * so the pulses fall out of phase with each other -- a cheap stand-in for
 * "real" light physics: no actual illumination is computed, only the same
 * opacity/scale wobble already used for the torches and the vault's core.
 */

import { useMemo } from 'react'
import { Sparkles } from 'lucide-react'
import { CHAMPION_LABEL, getTeamDisplayName } from './teamDisplayConfig'

const WIDTH = 900
const HEIGHT = 340

// Brightness floor. A model at the bottom of the board is still on the map, so
// its star has to be visible -- a truly proportional opacity would erase it.
const MIN_ALPHA = 0.34

/**
 * A small deterministic hash of the team id, used only to scatter stars off a
 * perfectly regular arc. Any stable integer would do; this is FNV-1a's mixing
 * step, chosen because it is three lines and has no dependencies.
 */
function hash(text) {
  let value = 2166136261
  for (let index = 0; index < text.length; index += 1) {
    value ^= text.charCodeAt(index)
    value = Math.imul(value, 16777619)
  }
  return (value >>> 0) / 4294967295
}

/**
 * Star positions for one board.
 *
 * Rank drives the angle so the figure reads left to right in ranking order,
 * and score drives the distance from the horizon line -- a better model sits
 * higher in the sky. The team hash only nudges each star a little off that
 * grid so the result looks like a sky rather than a bar chart.
 */
function starsFor(models) {
  const scored = models.filter(model => typeof model.score === 'number')
  if (!scored.length) return []
  const best = Math.max(...scored.map(model => model.score))
  const worst = Math.min(...scored.map(model => model.score))
  const span = Math.max(best - worst, 1)

  return scored.map((model, index) => {
    const jitter = hash(model.team)
    // Spread across the width with a margin, in rank order.
    const x = 90 + (index / Math.max(scored.length - 1, 1)) * (WIDTH - 180)
    // Higher score sits higher. The jitter is worth ~18px, enough to break the
    // line up without letting a worse model appear above a better one.
    const normalised = (model.score - worst) / span
    const y = HEIGHT - 70 - normalised * (HEIGHT - 165) - jitter * 18
    const alpha = MIN_ALPHA + normalised * (1 - MIN_ALPHA)
    return {
      team: model.team,
      // model.rank, not model.displayRank -- displayRank falls back to array
      // index for an ineligible model (one can still carry a real score, as
      // an entry stuck in gate review does), so it can misread as rank 1 for
      // a model that was never actually QA's champion. rank is null unless QA
      // published it, which only happens for an eligible, ranked entry.
      name: model.rank === 1 ? CHAMPION_LABEL : getTeamDisplayName(model.team, index),
      model: model.model,
      score: model.score,
      accent: model.displayAccent,
      eligible: model.eligible,
      rank: model.displayRank,
      x,
      y,
      radius: 3.4 + normalised * 5.2,
      alpha,
    }
  })
}

// Two comets, opposite diagonals, each on its own long loop so they never
// share a path or a rhythm. `useMemo` only decides which particular comet
// this mount is (its delay and pace) -- like the starfall veil's stars, the
// randomness is chosen once and never needs to change again while the panel
// stays mounted.
const COMET_COUNT = 2

function useComets() {
  return useMemo(
    () =>
      Array.from({ length: COMET_COUNT }, (_, index) => ({
        id: index,
        reverse: index % 2 === 1,
        delay: Math.random() * 6,
        duration: 6 + Math.random() * 3,
      })),
    [],
  )
}

// A light scattering of small, continuously twinkling background stars --
// distinct from the ranked models: these carry no data, so they may as well
// be wherever they land.
const SHIMMER_COUNT = 14

function useShimmerStars() {
  return useMemo(
    () =>
      Array.from({ length: SHIMMER_COUNT }, (_, index) => ({
        id: index,
        left: Math.random() * 100,
        top: Math.random() * 100,
        size: 1 + Math.random() * 1.6,
        delay: Math.random() * 4,
        duration: 2.4 + Math.random() * 2.4,
      })),
    [],
  )
}

export default function ConstellationMap({ models }) {
  const stars = useMemo(() => starsFor(models), [models])
  const comets = useComets()
  const shimmerStars = useShimmerStars()
  if (stars.length < 1) return null

  const path = stars.map(star => `${star.x},${star.y}`).join(' ')

  return (
    <section className="constellation" aria-labelledby="constellation-heading">
      <div className="section-heading">
        <div>
          <span className="eyebrow">THE BOARD AS A SKY</span>
          <h2 id="constellation-heading">Star map</h2>
          <p>
            One star per scored model, joined in ranking order. A star sits higher and burns
            brighter the higher its score — the same numbers as the table below, drawn as a figure.
          </p>
        </div>
      </div>

      <div className="constellation-frame">
        {/* Purely ambient, so it is plain HTML behind the SVG rather than part
            of it -- percentage left/top has to be relative to this frame, and
            an SVG viewBox's coordinate system is the wrong space for that. */}
        <div className="comet-field" aria-hidden="true">
          {comets.map(comet => (
            <span
              key={comet.id}
              className={`comet ${comet.reverse ? 'reverse' : ''}`}
              style={{ animationDelay: `${comet.delay}s`, animationDuration: `${comet.duration}s` }}
            />
          ))}
          {shimmerStars.map(star => (
            <span
              key={star.id}
              className="map-shimmer-star"
              style={{
                left: `${star.left}%`,
                top: `${star.top}%`,
                width: `${star.size}px`,
                height: `${star.size}px`,
                animationDelay: `${star.delay}s`,
                animationDuration: `${star.duration}s`,
              }}
            />
          ))}
        </div>
        <svg
          className="constellation-sky"
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="img"
          aria-label={`Star map of ${stars.length} scored model${stars.length === 1 ? '' : 's'}, ordered by rank`}
        >
          {/* The figure, drawn before the stars so the line passes behind them.
              pathLength normalises the dash so the draw-on works whatever the
              board's size, the same trick the sparklines use. */}
          {stars.length > 1 && (
            <polyline className="constellation-line" points={path} pathLength="1" />
          )}

          {stars.map((star, index) => (
            <g
              key={star.team}
              className={`constellation-star ${star.accent} ${star.eligible ? '' : 'dim'}`}
              style={{ '--star-index': index, '--star-alpha': star.alpha }}
            >
              <circle className="star-glow" cx={star.x} cy={star.y} r={star.radius * 3.4} />
              <circle className="star-core" cx={star.x} cy={star.y} r={star.radius} />
              <text className="star-label" x={star.x} y={star.y - star.radius - 13}>
                {star.name}
              </text>
              <text className="star-score" x={star.x} y={star.y + star.radius + 19}>
                {star.score.toFixed(1)}
              </text>
            </g>
          ))}
        </svg>
      </div>

      <p className="availability-note">
        <Sparkles size={16} />
        Position and brightness are read from the scores already published above — the map adds no
        ranking of its own.
      </p>
    </section>
  )
}
