import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  BarChart3, Beaker, CalendarDays, Library as LibraryIcon, Menu, ShieldCheck, Sparkles, Swords, Users, Vault,
} from 'lucide-react'
import Arena from './Arena'
import Home from './Home'
import Console from './Console'
import Library from './Library'
import cegedimLogo from './assets/cegedim-logo.png'
import { MirageLayer } from './theatrics'
import ArenaGates from './gates'
import VaultDoor from './vault'
import StarfallVeil from './starfall'
import { CHAMPION_LABEL } from './teamDisplayConfig'
import { THEMES, loadTheme, saveTheme } from './theme'
import { fetchConfig, fetchLeaderboard } from './api'

const VIEWS = { home: 'home', arena: 'arena', console: 'console', teams: 'teams', library: 'library' }

// One list, rendered into both the topbar and the floating pill that takes
// over once the topbar scrolls out of view, so the two never drift out of
// sync with each other. Files is appended separately below, since it's the
// one entry gated on config.
const NAV_ITEMS = [
  { key: VIEWS.arena, href: '#/arena', icon: BarChart3, label: 'Leaderboard' },
  { key: VIEWS.console, href: '#/console', icon: Beaker, label: 'Masking console' },
  { key: VIEWS.teams, href: '#/teams', icon: Users, label: 'Team console' },
]

function renderNavLinks(items, activeView, onNavigate) {
  return items.map(({ key, href, icon: Icon, label }) => (
    <a key={key} className={activeView === key ? 'active' : ''} href={href} onClick={onNavigate}>
      <Icon />{label}
    </a>
  ))
}

// The Arena page's own in-page jump tabs (Rankings/Compare/Per-entity/
// Methodology), reported up via onSectionNavChange rather than rendered by
// that page itself -- see the comment above SECTIONS in Arena.jsx. Rendered
// into both the static header and the floating pill below from this one
// function, same reasoning as renderNavLinks above.
function renderSectionTabs(sectionNav) {
  // The full section object goes back into onNavigate, not a rebuilt subset
  // of it -- goToSection in Arena.jsx also reads `deepDive` off it to decide
  // whether a panel needs mounting before the scroll happens.
  return sectionNav.sections.map(section => (
    <button
      key={section.id}
      type="button"
      className={sectionNav.active === section.id ? 'active' : ''}
      onClick={() => sectionNav.onNavigate(section)}
    >
      <section.Icon size={15} />{section.label}
    </button>
  ))
}

// The champion console presents whichever team currently holds rank 1 under one
// fixed name, so the page reads the same whoever is winning. The team id still
// decides the upload prefix -- only the label is constant. Every other team-named
// surface (podium, medals, per-entity breakdown, library, star map) applies the
// same rule via getTeamDisplayName's championTeamId argument -- this is just the
// one place the label is a fixed function rather than a lookup, because the
// console never shows a non-champion team at all.
const championName = () => CHAMPION_LABEL

function routeFromHash() {
  // Two segments, not one: `#/library/<run_id>` has to open that document
  // directly. Splitting here is what makes a masked file bookmarkable and
  // shareable at all -- the previous lookup matched the whole fragment, so any
  // hash carrying an id fell through to the leaderboard.
  //
  // A lookup rather than a ternary, so adding a view stays a one-line change
  // and an unknown fragment still lands somewhere real.
  const [requested, param = ''] = window.location.hash.replace(/^#\/?/, '').split('/')
  // Unknown or empty fragments land on the hub, which orients rather than
  // dropping an arrival straight into the ranking table. `#/arena` stays a
  // real route, so an existing bookmark still opens the board directly.
  return { view: VIEWS[requested] || VIEWS.home, param: decodeURIComponent(param) }
}

function CegedimLogo({ compact = false }) {
  // The corporate logo as published on cegedim.com, which ships it only as a
  // white knockout PNG -- there is no public SVG or higher-resolution file. It
  // is therefore mounted on a navy chip, because white artwork is invisible on
  // this app's light header. Swap in an approved vector here if the brand team
  // supplies one; only this component and `.cegedim-logo` need to change.
  return (
    <span className={`cegedim-logo ${compact ? 'compact' : ''}`}>
      <img src={cegedimLogo} alt="Cegedim" />
    </span>
  )
}

const THEME_ICON = { colosseum: Swords, vault: Vault, constellation: Sparkles }

// A radio group in behaviour but buttons in markup: `aria-pressed` states each
// option independently, which reads correctly when only the icon is visible.
// The visible name lives in the tooltip and the accessible name, because three
// labels do not fit a top bar that already carries three nav links.
function ThemePicker({ theme, onChange }) {
  return (
    <div className="theme-picker" role="group" aria-label="Arena theme">
      {THEMES.map(option => {
        const Icon = THEME_ICON[option.id] || Sparkles
        return (
          <button
            key={option.id}
            type="button"
            aria-pressed={theme === option.id}
            aria-label={`${option.label} theme — ${option.hint}`}
            title={`${option.label} — ${option.hint}`}
            onClick={() => onChange(option.id)}
          >
            <Icon />
          </button>
        )
      })}
    </div>
  )
}

export default function App() {
  const [route, setRoute] = useState(routeFromHash)
  const { view, param: routeParam } = route
  const [board, setBoard] = useState(null)
  const [loading, setLoading] = useState(true)
  const [updated, setUpdated] = useState('—')
  const [config, setConfig] = useState(null)
  const [configError, setConfigError] = useState(null)
  const [mobileNav, setMobileNav] = useState(false)
  const topbarRef = useRef(null)
  const [floatingNav, setFloatingNav] = useState(false)
  // Populated by Arena via onSectionNavChange while it's the mounted view,
  // and cleared when it unmounts -- see the comment above SECTIONS in
  // Arena.jsx for why that page reports this rather than rendering its own
  // copy of these tabs.
  const [arenaSectionNav, setArenaSectionNav] = useState(null)
  // Read in the initialiser, not in a mount effect. An effect that loads the
  // stored theme and an effect that saves the current one race: the save runs
  // on the very first commit, writing the default over the stored choice
  // before the load has been applied. StrictMode's double-invoke makes that
  // deterministic rather than occasional, and the second load then reads back
  // the default it just clobbered. Reading once, eagerly, has neither problem
  // and also avoids a frame of the wrong theme. This is a client-only SPA, so
  // there is no server render for localStorage access to break.
  const [theme, setTheme] = useState(loadTheme)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    saveTheme(theme)
  }, [theme])

  useEffect(() => {
    const onHashChange = () => setRoute(routeFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  // The topbar is `position: relative` and scrolls away with the page (see
  // the note above .topbar in styles.css) -- this is what notices it's gone.
  // An IntersectionObserver on the header itself, rather than a scroll
  // listener with a hand-picked threshold, so the floating pill appears at
  // the exact moment the topbar leaves the viewport regardless of its height
  // at any given breakpoint.
  useEffect(() => {
    const el = topbarRef.current
    if (!el || typeof IntersectionObserver === 'undefined') return undefined
    const observer = new IntersectionObserver(([entry]) => setFloatingNav(!entry.isIntersecting))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchLeaderboard()
      setBoard(data)
      setUpdated(new Date(data.updated_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }))
    } catch {
      setBoard({ models: [], awards: [], source: 'unavailable' })
      setUpdated('unavailable')
    } finally {
      window.setTimeout(() => setLoading(false), 240)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    fetchConfig()
      .then(setConfig)
      .catch(error => setConfigError(error.message))
  }, [])

  const round = board?.round || { label: 'Benchmark Round 04', period: 'August 2026' }

  const navItems = useMemo(() => {
    if (config?.library?.enabled === false) return NAV_ITEMS
    return [...NAV_ITEMS, { key: VIEWS.library, href: '#/library', icon: LibraryIcon, label: 'Files' }]
  }, [config])

  // TEMPORARY DEMO OVERRIDE: pin the champion console to team octo regardless
  // of the actual QA ranking. Revert by deleting this constant and the branch
  // below once the demo is done -- see the masking-console-temp-octo-hardcode
  // memory for why this exists and that it must not become the new default.
  const DEMO_FORCED_CHAMPION_TEAM = 'octo'

  // Which team the champion console uploads to. The board names the winner, but
  // only the API's own team list says which ids it will accept -- the demo and
  // mock boards carry display labels like "Team Orion" that no upload could use
  // -- so a champion the API does not know falls back rather than 400s on submit.
  const championTeam = useMemo(() => {
    const allowed = config?.teams || []
    if (DEMO_FORCED_CHAMPION_TEAM && allowed.includes(DEMO_FORCED_CHAMPION_TEAM)) {
      return DEMO_FORCED_CHAMPION_TEAM
    }
    const winner = board?.champion?.team
    if (winner && allowed.includes(winner)) return winner
    const fallback = config?.champion_fallback_team
    return fallback && allowed.includes(fallback) ? fallback : allowed[0] || null
  }, [board, config])

  // Only while there is still an answer coming. Once both requests have settled
  // the fallback above always yields something, so this does not linger.
  const championPending =
    !championTeam && !configError && (loading || !config)
      ? 'Resolving the current champion…'
      : null

  return (
    <div className="app-shell">
      {/* First child and `z-index: -1`, so the heat haze paints above the page
          background and below every section without any of them needing a
          stacking context of their own. Colosseum only -- the constellation
          paints its own sky, and two of them would fight. */}
      {theme === 'colosseum' && <MirageLayer />}
      {/* One opening per theme, each mounted unconditionally and deciding for
          itself whether to play -- see useIntro in introShared.js for why
          that has to be a reactive effect rather than a value read once. */}
      <ArenaGates enabled={theme === 'colosseum'} />
      <VaultDoor enabled={theme === 'vault'} />
      <StarfallVeil enabled={theme === 'constellation'} />
      <header className="topbar" ref={topbarRef}>
        <a className="brand" href="#/home" aria-label="Cegedim PII Masking Model Arena">
          <CegedimLogo />
          <span className="brand-divider" aria-hidden="true"></span>
          <span>PII Masking <b>Arena</b></span>
        </a>
        <button
          className="mobile-toggle"
          onClick={() => setMobileNav(!mobileNav)}
          aria-expanded={mobileNav}
          aria-controls="primary-nav"
          aria-label="Toggle navigation"
        >
          <Menu />
        </button>
        <nav id="primary-nav" className={mobileNav ? 'open' : ''} aria-label="Primary navigation">
          {renderNavLinks(navItems, view, () => setMobileNav(false))}
        </nav>
        <ThemePicker theme={theme} onChange={setTheme} />
        <div className="round-pill">
          <CalendarDays size={17} />
          <span>{round.label} · {round.period}</span>
        </div>
      </header>

      {/* A second row under the topbar, only while the Arena page has jump
          tabs to offer. Deliberately not wrapped in .basis-toggle: a boxed
          segmented control here read as a widget sitting inside the header
          rather than part of it. These buttons share #primary-nav's rule in
          styles.css instead, so the two rows are literally the same control
          in two lines. */}
      {view === VIEWS.arena && arenaSectionNav && (
        <div className="topbar-subnav" role="group" aria-label="Jump to a section">
          {renderSectionTabs(arenaSectionNav)}
        </div>
      )}

      {/* Takes over once .topbar scrolls out of view (see the effect above),
          carrying the four destinations plus, on the Arena page, the same
          jump tabs the row above shows -- those stay behind with the header,
          brand mark, theme picker and round pill included. */}
      <nav className={`floating-nav ${floatingNav ? 'stuck' : ''}`} aria-label="Primary navigation, floating">
        <div className="floating-nav-row">{renderNavLinks(navItems, view)}</div>
        {view === VIEWS.arena && arenaSectionNav && (
          <div className="floating-nav-row floating-nav-subrow">{renderSectionTabs(arenaSectionNav)}</div>
        )}
      </nav>

      <main id="arena">
        {view === VIEWS.home && (
          <Home board={board} config={config} championTeam={championTeam} />
        )}
        {view === VIEWS.arena && (
          <Arena
            board={board}
            loading={loading}
            updated={updated}
            onRefresh={refresh}
            theme={theme}
            config={config}
            onSectionNavChange={setArenaSectionNav}
          />
        )}
        {view === VIEWS.console && (
          // Unchanged page, dynamic destination: one fixed chip reading
          // "Cegedim Egypte Model" over whichever team QA currently ranks first.
          <Console
            config={config}
            configError={configError}
            teams={championTeam ? [championTeam] : []}
            displayNameFor={championName}
            pending={championPending}
          />
        )}
        {view === VIEWS.teams && (
          // Same component, every team offered under its own name.
          <Console
            config={config}
            configError={configError}
            copy={{
              eyebrow: 'MASK WITH A CHOSEN MODEL',
              title: 'Run a document through a specific team model',
              intro:
                'Pick the team whose model should mask the document — the pipeline routes to that model only.',
              legendMulti: '1 · Choose the team model',
              // A one-entry list here is not a choice the page made, it is a
              // deployment that configured a single team -- so say that rather
              // than inheriting the champion console's "Top-ranked model",
              // which would make this page a silent copy of that one.
              legendSingle: '1 · Team model',
              fixedNote: 'Only team configured',
            }}
          />
        )}
        {view === VIEWS.library && (
          <Library
            config={config}
            runId={routeParam}
            championTeam={championTeam}
            // Routing through the hash rather than local state, so the URL is
            // the source of truth and an opened document can be bookmarked,
            // shared, and reached again after the API restarts.
            onSelect={runId =>
              (window.location.hash = runId ? `#/library/${encodeURIComponent(runId)}` : '#/library')
            }
          />
        )}
      </main>

      <footer>
        <CegedimLogo compact />
        <span>PII Masking Model Arena</span>
        <small>
          <ShieldCheck size={14} /> Benchmark policy {board?.round?.policy || '1.0'} · Scoring weights published in the methodology
        </small>
      </footer>
    </div>
  )
}
