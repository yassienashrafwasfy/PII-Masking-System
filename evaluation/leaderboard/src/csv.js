/**
 * Turning a table into a downloaded file.
 *
 * Two views export CSV -- the ranking table and the per-entity breakdown --
 * and they were about to grow two copies of the same quoting and object-URL
 * dance. Quoting in particular is the part worth having in one place: a model
 * id or a QA reason string containing a comma or a quote silently corrupts
 * every column after it, and that is invisible until someone opens the file.
 */

/** Quote one cell so commas, quotes and newlines survive the round trip. */
function cell(value) {
  return `"${String(value ?? '').replace(/"/g, '""')}"`
}

export function toCsv(header, rows) {
  return [header, ...rows].map(row => row.map(cell).join(',')).join('\n')
}

/**
 * Hand `text` to the browser as a download named `filename`.
 *
 * The object URL is revoked immediately after the synthetic click, which is
 * safe: the click has already handed the blob to the download manager.
 */
export function download(filename, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8;' }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

/** Today, as the date stamp every exported filename carries. */
export function stamp() {
  return new Date().toISOString().slice(0, 10)
}
