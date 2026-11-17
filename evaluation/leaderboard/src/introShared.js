/**
 * The mount/open/skip choreography shared by the arena's three per-theme
 * openings -- the vault's blast door, the colosseum's gates, the
 * constellation's starfall. Each one plays every time its theme becomes the
 * active one, not once per browser session: switching into vault a second
 * time in the same visit plays the door again, because these are meant to be
 * seen on every entrance, not remembered around.
 */

import { useEffect, useRef, useState } from 'react'

export function prefersReducedMotion() {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
  )
}

/**
 * Runs one intro's whole lifecycle: watches `enabled` for the moment it turns
 * true -- entering the theme -- holds it mounted for `durationMs`, and lets
 * any click or key press end it early.
 *
 * `enabled` has to be watched in an effect rather than decided once in a lazy
 * initial state. Every intro is rendered unconditionally in App and never
 * unmounts across a theme switch, so a value fixed at the component's own
 * first mount would only ever reflect whichever theme happened to be active
 * when the whole page first painted -- switching into the theme afterward
 * would change the `enabled` prop, which a state initialiser has no way to
 * react to, and the intro would simply never play. This was a real bug in an
 * earlier version of the vault door.
 */
export function useIntro(enabled, durationMs) {
  const [open, setOpen] = useState(false)
  const [mounted, setMounted] = useState(false)
  const timerRef = useRef(null)

  useEffect(() => {
    if (!enabled || prefersReducedMotion()) return
    setOpen(false)
    setMounted(true)
  }, [enabled])

  useEffect(() => {
    if (!mounted) return undefined
    // Two frames, not one: the closed/opaque state must paint before the
    // class that opens it lands, or the browser coalesces both into one state
    // and there is no transition to see.
    const frame = requestAnimationFrame(() => requestAnimationFrame(() => setOpen(true)))
    timerRef.current = window.setTimeout(() => setMounted(false), durationMs)

    const skip = () => setMounted(false)
    window.addEventListener('pointerdown', skip)
    window.addEventListener('keydown', skip)
    return () => {
      cancelAnimationFrame(frame)
      clearTimeout(timerRef.current)
      window.removeEventListener('pointerdown', skip)
      window.removeEventListener('keydown', skip)
    }
  }, [mounted, durationMs])

  return { mounted, open }
}
