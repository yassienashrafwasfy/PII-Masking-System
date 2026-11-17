/**
 * The starfall that dissolves into the board.
 *
 * A dense field of stars shimmers over the page, then fades to reveal the
 * constellation theme's own slower, sparser sky already turning underneath --
 * entering reads as the shimmer settling into the calmer field, rather than a
 * curtain merely lifting off a static one.
 *
 * Plays every time the reader enters the constellation theme -- see useIntro
 * in introShared.js for the shared lifecycle every one of the arena's three
 * openings follows.
 */

import { useMemo } from 'react'
import { useIntro } from './introShared'

// Just past the CSS fade's own 0.8s, so the veil never lingers fully
// transparent (and still blocking clicks) after the last star has settled.
const STARFALL_MS = 1000

const STAR_COUNT = 46

/**
 * Randomised once per mount, not per render -- Arena's ConfettiBurst uses the
 * same approach, so the randomness only ever decides which shimmer this
 * entry is, never re-triggers mid-fade.
 */
function useStars() {
  return useMemo(
    () =>
      Array.from({ length: STAR_COUNT }, (_, index) => ({
        id: index,
        left: Math.random() * 100,
        top: Math.random() * 100,
        size: 1 + Math.random() * 2.4,
        delay: Math.random() * 0.6,
        duration: 0.5 + Math.random() * 0.5,
      })),
    [],
  )
}

export default function StarfallVeil({ enabled }) {
  const { mounted, open } = useIntro(enabled, STARFALL_MS)
  const stars = useStars()
  if (!mounted) return null

  return (
    <div className={`starfall-veil ${open ? 'fading' : ''}`} aria-hidden="true">
      {stars.map(star => (
        <span
          key={star.id}
          className="starfall-star"
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
  )
}
