/**
 * The blast door that opens onto the board.
 *
 * Futuristic rather than bank-vault: two chevron-notched leaves part along a
 * glowing seam, behind a HUD ring of rotating tick arcs, over a hex field.
 * There is no spoked wheel and there are no rivets -- the weight is carried by
 * the interlock and the light, not by pretending to be cast iron.
 *
 * Plays every time the reader enters the vault theme -- see useIntro in
 * introShared.js for the shared lifecycle every one of the arena's three
 * openings follows.
 *
 * It is decorative and it covers the page, which makes it an accessibility
 * hazard rather than a flourish if handled carelessly. Three things keep it
 * honest: it is aria-hidden and never focusable, it is removed from the DOM
 * the moment it finishes, and any click or key press skips it immediately.
 * Under prefers-reduced-motion it never mounts at all.
 */

import { useIntro } from './introShared'

// Long enough to read as heavy, short enough not to be a toll gate. The seam
// charges for the first ~40% and the leaves part over the remainder.
const DOOR_MS = 1100

// Tick marks around the HUD ring. Fixed rather than random so the ring reads
// as an instrument with a calibrated scale instead of as noise.
const TICKS = Array.from({ length: 36 }, (_, index) => index * 10)

/** The HUD ring: concentric arcs, a calibrated scale, and a charging core. */
function DoorRing() {
  return (
    <svg className="vault-ring" viewBox="0 0 160 160" aria-hidden="true" focusable="false">
      {/* Two arc segments rather than full circles, counter-rotating, so the
          ring reads as tracking something rather than merely spinning. */}
      <circle className="vault-arc outer" cx="80" cy="80" r="70" pathLength="100" />
      <circle className="vault-arc inner" cx="80" cy="80" r="56" pathLength="100" />
      <circle className="vault-scale" cx="80" cy="80" r="63" />
      <g className="vault-ticks">
        {TICKS.map(angle => (
          <rect
            key={angle}
            x="79.4"
            y="12"
            width="1.2"
            height={angle % 90 === 0 ? 9 : 5}
            transform={`rotate(${angle} 80 80)`}
          />
        ))}
      </g>
      <circle className="vault-core" cx="80" cy="80" r="14" />
      <circle className="vault-core-pulse" cx="80" cy="80" r="14" />
    </svg>
  )
}

export default function VaultDoor({ enabled }) {
  const { mounted, open } = useIntro(enabled, DOOR_MS)
  if (!mounted) return null

  return (
    <div className={`vault-door ${open ? 'open' : ''}`} aria-hidden="true">
      <div className="vault-leaf left" />
      <div className="vault-leaf right" />
      <span className="vault-seam" />
      <DoorRing />
    </div>
  )
}
