/**
 * The illustrative snippet behind the leaderboard's hover preview.
 *
 * No API the arena talks to carries a per-model masked document: the board
 * endpoint serves metrics only, and the console's side-by-side viewer diffs a
 * document *you* uploaded, which no leaderboard row has. So the preview cannot
 * show a model's real output, and this file does not pretend otherwise -- the
 * row that renders it prints a note saying exactly that.
 *
 * What it can do honestly is replay the numbers QA already published. Every
 * board row carries `per_entity_recall`, so which spans a given model catches
 * here is a pure function of that model's own reported recall for that entity
 * type. A model that QA measured as weak on dates visibly leaves the date
 * unmasked. The text is invented; the behaviour over it is not.
 */

// Synthetic, and deliberately so -- see the repo's SECURITY.md. The shape
// follows the Belgian clinical documents the benchmark actually uses: a
// national number (INSZ/NISS), a prescriber number (RIZIV/INAMI), a mobile in
// national format, and a Dutch-language body.
export const SAMPLE_TEXT =
  'Verslag 14/03/2026 — patiënt Jan Peeters, INSZ 85.07.30-123.45, ' +
  'wonende Lindestraat 42, 2018 Antwerpen, tel. 0475 22 88 91. ' +
  'Voorschrijver dr. Els Vermeulen (RIZIV 1-84036-21-004).'

/**
 * Character spans of the PII in SAMPLE_TEXT, with the entity label QA reports
 * recall under. Offsets are absolute into SAMPLE_TEXT and must stay sorted and
 * non-overlapping -- `segments()` below walks them in one pass and relies on
 * both. `assertSpansAlign()` in this module is the guard for that.
 */
export const SAMPLE_SPANS = [
  { label: 'DATE', text: '14/03/2026' },
  { label: 'NAME', text: 'Jan Peeters' },
  { label: 'INSZ_NISS', text: '85.07.30-123.45' },
  { label: 'ADDRESS', text: 'Lindestraat 42, 2018 Antwerpen' },
  { label: 'PHONE_NUMBER', text: '0475 22 88 91' },
  { label: 'NAME', text: 'Els Vermeulen' },
  { label: 'RIZIV_INAMI', text: '1-84036-21-004' },
].map(span => {
  // Derived from the text rather than hand-written, so editing SAMPLE_TEXT
  // above cannot silently leave the offsets pointing at the wrong characters.
  const start = SAMPLE_TEXT.indexOf(span.text)
  return { ...span, start, end: start + span.text.length }
})

/**
 * A model is shown catching an entity when its published recall for that
 * entity clears this bar. It is a display threshold for one illustrative
 * sentence, not a QA gate and not a pass/fail line -- the real gates live in
 * the methodology modal.
 *
 * 0.5 because the interesting thing to show is the *typical* case: above a
 * coin flip the model usually catches this kind of span, below it usually does
 * not.
 */
const CATCH_THRESHOLD = 0.5

function recallFor(model, label) {
  const entity = model?.per_entity_recall?.[label]
  if (entity && entity.available && typeof entity.value === 'number') return entity.value
  // Not every board reports every entity type. Falling back to the row's own
  // headline score (0-100) keeps a strong model looking strong instead of
  // making an unreported label read as a miss for everyone.
  return typeof model?.score === 'number' ? model.score / 100 : null
}

/** The replacement token a caught span is rendered as. */
export function tokenFor(label) {
  return `[${label}]`
}

/**
 * Split SAMPLE_TEXT into the alternating plain/PII pieces the preview types
 * out, deciding per span whether `model` catches it.
 *
 * Returns segments of `{ kind: 'text' | 'masked' | 'missed', text, label }`,
 * where `text` is what should appear on screen -- already the token for a
 * caught span, still the raw PII for a missed one.
 */
export function segments(model) {
  const pieces = []
  let cursor = 0
  SAMPLE_SPANS.forEach(span => {
    if (span.start > cursor) {
      pieces.push({ kind: 'text', text: SAMPLE_TEXT.slice(cursor, span.start) })
    }
    const recall = recallFor(model, span.label)
    const caught = recall === null ? true : recall >= CATCH_THRESHOLD
    pieces.push({
      kind: caught ? 'masked' : 'missed',
      text: caught ? tokenFor(span.label) : span.text,
      label: span.label,
      recall,
    })
    cursor = span.end
  })
  if (cursor < SAMPLE_TEXT.length) {
    pieces.push({ kind: 'text', text: SAMPLE_TEXT.slice(cursor) })
  }
  return pieces
}

/**
 * SAMPLE_TEXT split into plain runs and untouched PII runs, for the "before"
 * side. Model-independent by definition -- the original is the same document
 * whoever is about to mask it.
 */
export function originalSegments() {
  const pieces = []
  let cursor = 0
  SAMPLE_SPANS.forEach(span => {
    if (span.start > cursor) {
      pieces.push({ kind: 'text', text: SAMPLE_TEXT.slice(cursor, span.start) })
    }
    pieces.push({ kind: 'pii', text: span.text, label: span.label })
    cursor = span.end
  })
  if (cursor < SAMPLE_TEXT.length) {
    pieces.push({ kind: 'text', text: SAMPLE_TEXT.slice(cursor) })
  }
  return pieces
}

/** How many characters the finished preview is, for the typewriter's clock. */
export function maskedLength(pieces) {
  return pieces.reduce((total, piece) => total + piece.text.length, 0)
}

/**
 * Throws if any span failed to locate itself in SAMPLE_TEXT, or if two spans
 * overlap. Called once at module load: a bad edit to the text or the span list
 * should fail loudly in development rather than render a scrambled sentence.
 */
function assertSpansAlign() {
  let previousEnd = 0
  SAMPLE_SPANS.forEach(span => {
    if (span.start < 0) {
      throw new Error(`maskingSample: "${span.text}" is not present in SAMPLE_TEXT`)
    }
    if (span.start < previousEnd) {
      throw new Error(`maskingSample: span "${span.text}" overlaps the one before it`)
    }
    previousEnd = span.end
  })
}

assertSpansAlign()
