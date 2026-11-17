/**
 * The arena gates that swing open onto the board.
 *
 * Stone leaves part around the same laurel the champion is crowned with on
 * the podium below -- entering the colosseum and winning it are marked with
 * the one emblem, reused rather than invented twice. Deliberately simpler
 * than the vault's door: a plain vertical seam, not a chevron interlock, and
 * no HUD -- a gate is masonry and torchlight, not a mechanism.
 *
 * Plays every time the reader enters the colosseum theme -- see useIntro in
 * introShared.js for the shared lifecycle every one of the arena's three
 * openings follows.
 */

import { FlameGlyph, Laurel } from './theatrics'
import { useIntro } from './introShared'

const GATES_MS = 1100

export default function ArenaGates({ enabled }) {
  const { mounted, open } = useIntro(enabled, GATES_MS)
  if (!mounted) return null

  return (
    <div className={`gates-door ${open ? 'open' : ''}`} aria-hidden="true">
      <div className="gates-leaf left" />
      <div className="gates-leaf right" />
      <span className="gates-seam" />
      {/* `torch-flame` gives these the same flicker and glow the champion's
          own torches carry; only position and size are gate-specific. */}
      <FlameGlyph className="torch-flame gates-flame gates-flame-left" />
      <FlameGlyph className="torch-flame gates-flame gates-flame-right" />
      <div className="gates-crest">
        <Laurel />
      </div>
    </div>
  )
}
