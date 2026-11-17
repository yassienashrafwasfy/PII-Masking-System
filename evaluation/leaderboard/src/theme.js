/**
 * The arena's three visual themes.
 *
 * Each one owns a background, an entrance, a refresh gesture and a champion
 * treatment. They are mutually exclusive by construction rather than by
 * convention: a desert haze and a rotating night sky cannot both be the page
 * background, and a spotlight and a radar sweep are the same gesture told two
 * ways, so layering them would mean one silently winning.
 *
 * The active theme is written to `document.documentElement`'s `data-theme`, so
 * every rule in themes.css and theatrics.css can scope itself without any
 * component having to thread the value down to it. Only the pieces that need
 * to render different *markup* per theme take it as a prop.
 */

export const THEMES = [
  {
    id: 'colosseum',
    label: 'Colosseum',
    hint: 'Combatants, duels and torchlight over a desert haze',
  },
  {
    id: 'vault',
    label: 'Vault',
    hint: 'A vault door on arrival and a security sweep on refresh',
  },
  {
    id: 'constellation',
    label: 'Constellation',
    hint: 'Models as stars, brightness by score, over a turning night sky',
  },
]

export const DEFAULT_THEME = 'colosseum'

const STORAGE_KEY = 'arena.theme'

export function isTheme(value) {
  return THEMES.some(theme => theme.id === value)
}

/**
 * The stored preference, or the default.
 *
 * Wrapped because localStorage throws rather than returning null in a handful
 * of real configurations -- Safari's private mode historically, and any
 * browser with site data blocked. A theme preference is not worth taking the
 * page down for.
 */
export function loadTheme() {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return isTheme(stored) ? stored : DEFAULT_THEME
  } catch {
    return DEFAULT_THEME
  }
}

export function saveTheme(theme) {
  try {
    window.localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    /* A theme that does not persist is still a theme that works. */
  }
}
