/**
 * Clipper AI — browser-less UI smoke test.
 *
 * Renders the real React app inside jsdom against a running backend and walks
 * the dashboard → editor flow, failing on any JavaScript error or broken arrow.
 * Clip-dependent checks are skipped (with a warning) when the project has no
 * analysis yet, so this also works on a backend without speech-to-text.
 *
 * Usage:
 *   npm run build                      # once, so the API serves the SPA
 *   cd backend && PYTHONPATH=vendor python3 -m uvicorn app.main:app --port 8000
 *   cd frontend && npm run smoke
 *
 * Overrides: API=http://host:port  BUNDLE=/path/to/static-dir
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { build } from 'esbuild'
import { fileURLToPath } from 'node:url'

// jsdom 30 needs Node >= 22.22 — fail with a readable message instead of a
// stack trace from deep inside undici.
let JSDOM, VirtualConsole
try {
  ({ JSDOM, VirtualConsole } = await import('jsdom'))
} catch (err) {
  console.error(`✖ The UI smoke test needs Node 22.22+ (running ${process.version}).`)
  console.error(`  ${String(err).split('\n')[0]}`)
  process.exit(1)
}

const here = path.dirname(fileURLToPath(import.meta.url))
const frontendDir = path.resolve(here, '..')
const repoRoot = path.resolve(frontendDir, '..')

const API = process.env.API || 'http://127.0.0.1:8000'
const STATIC_DIR = process.env.BUNDLE || path.join(repoRoot, 'backend', 'static')
const INDEX = path.join(STATIC_DIR, 'index.html')

const wait = (ms) => new Promise((r) => setTimeout(r, ms))

/** Poll until `predicate` is true (CI runners are slower than laptops). */
async function waitFor(predicate, { timeout = 15000, interval = 100 } = {}) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    try {
      if (predicate()) return true
    } catch { /* keep waiting */ }
    await wait(interval)
  }
  return false
}

const errors = []
let skipped = 0

const fail = (msg) => errors.push(msg)
const check = (label, cond) => {
  console.log(`${cond ? '✅' : '❌'} ${label}`)
  if (!cond) fail(`assertion failed: ${label}`)
}
const skip = (label) => {
  skipped += 1
  console.log(`⏭️  ${label} (needs an analyzed project)`)
}

if (!fs.existsSync(INDEX)) {
  console.error(`✖ ${INDEX} not found — run "npm run build" first.`)
  process.exit(1)
}

// Seed a project from the bundled sample so the editor walkthrough has data.
const list = await fetch(`${API}/api/projects`).then((r) => r.json()).catch(() => [])
if (!Array.isArray(list) || list.length === 0) {
  const res = await fetch(`${API}/api/sample`, { method: 'POST' }).catch(() => null)
  if (res?.ok) {
    console.log('ℹ️  created a project from the bundled sample')
  } else {
    const body = res ? await res.text().catch(() => '') : 'server unreachable'
    console.log(`ℹ️  /api/sample → ${res ? res.status : 'no response'}: ${body.slice(0, 160)}`)
    console.log('   (clip-level checks will be skipped)')
  }
}

// The published SPA is an ES module, which jsdom cannot execute — bundle the
// same sources to an IIFE for the test run.
const bundle = await build({
  entryPoints: [path.join(frontendDir, 'src', 'main.jsx')],
  bundle: true,
  format: 'iife',
  define: { 'process.env.NODE_ENV': '"production"' },
  outfile: path.join(os.tmpdir(), 'clipper-ai-smoke.js'),
  logLevel: 'warning',
})
const bundlePath = bundle.outputFiles?.[0]?.path
  || path.join(os.tmpdir(), 'clipper-ai-smoke.js')

const virtualConsole = new VirtualConsole()
virtualConsole.on('jsdomError', (e) => {
  if (!/Not implemented/.test(e.message)) fail(`jsdomError: ${e.message}`)
})
virtualConsole.on('error', (...args) => fail(`console.error: ${args.join(' ')}`))

const dom = new JSDOM(fs.readFileSync(INDEX, 'utf8'), {
  url: `${API}/`,
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  virtualConsole,
})
const { window } = dom
window.fetch = (url, opts) => fetch(new URL(url, API).href, opts)
window.addEventListener('error', (e) => fail(`window.error: ${e.message}`))
window.addEventListener('unhandledrejection', (e) => fail(`unhandled rejection: ${e.reason}`))

const $ = (sel) => window.document.querySelector(sel)
const $$ = (sel) => [...window.document.querySelectorAll(sel)]
const bodyText = () => window.document.body.textContent || ''
const click = (el) => el.dispatchEvent(
  new window.MouseEvent('click', { bubbles: true, cancelable: true }))

const script = window.document.createElement('script')
script.textContent = fs.readFileSync(bundlePath, 'utf8')
window.document.body.appendChild(script)

// Wait for the first data-driven render (health badges + project list).
const loaded = await waitFor(
  () => window.document.querySelector('.engine-badges .badge')
    && window.document.querySelector('.project-card, .empty-state, .skeleton-card'))
if (!loaded) console.log('⚠️  the dashboard was still loading after 15s — continuing anyway')

// ─────────────────────────────────────────────────────── dashboard
check('brand renders "Clipper AI"', /Clipper\s*AI/.test($('.brand-name')?.textContent || ''))
check('hero headline renders', /scroll-stopping shorts/.test(bodyText()))
check('engine badges come from /api/health', $$('.engine-badges .badge').length >= 3)
check('uploader shows the configured size limit', /up to \d+ MB/.test($('.uploader-sub')?.textContent || ''))
check('feature strip renders', $$('.feature').length === 4)
const cardCount = $$('.project-card').length
check(cardCount ? 'projects list renders' : 'empty state renders (no project could be created)',
  cardCount > 0 || !!$('.empty-state'))

if (!cardCount) {
  console.log('\nNo project available — stopping after the dashboard checks.')
  report()
}

// ─────────────────────────────────────────────────────── editor
click($$('.project-card')[0])
if (!await waitFor(() => window.document.querySelector('.editor-grid'))) {
  console.log('⚠️  the editor did not render within 15s')
}
await waitFor(() => window.document.querySelector('.style-tile'))
check('editor renders', !!$('.editor-grid'))
check('player + crop guide render', !!$('.player-box video') && !!$('.crop-guide'))
check('timeline renders', !!$('.timeline-track'))
check('style presets render', $$('.style-tile').length >= 7)
check('effects panel renders', /Captions & effects/.test(bodyText()))
check('render panel renders', /Export/.test(bodyText()))
check('keyboard hints render', $$('.kbd-hints kbd').length >= 5)
check('AI command box renders', !!$('.command-box'))

const clips = $$('.clip-card')
if (clips.length) {
  click(clips[0])
  await waitFor(() => window.document.querySelector('.clip-card.active'))
  check('selecting a clip highlights it', !!$('.clip-card.active'))
  check('clip window appears on the timeline', !!$('.tl-clip'))
  check('render button shows the clip length', /Render \d+s clip/.test($('.render-row .btn.primary')?.textContent || ''))
} else {
  skip('clip selection')
  skip('clip window on the timeline')
  skip('render button label')
}

click($$('.style-tile')[1])
await waitFor(() => window.document.querySelectorAll('.style-tile')[1]?.classList.contains('active'))
check('style switching works', $$('.style-tile')[1].classList.contains('active'))

// ─────────────────────────────────────────────────────── meme lab (packs)
await waitFor(() => window.document.querySelector('.memelab'))
check('meme lab renders', !!$('.memelab'))
check('meme presets come from the API', $$('.preset-card').length >= 5)
check('pack status rows render', $$('.pack-row').length >= 2)

const tabs = $$('.memelab-tabs button')
click(tabs[1])                                   // memes tab
await waitFor(() => window.document.querySelector('.asset-grid, .pack-empty'))
check('meme tab renders its inventory', !!$('.asset-grid') || !!$('.pack-empty'))

click(tabs[2])                                   // sounds tab
await waitFor(() => window.document.querySelector('.sound-cat, .pack-empty'))
check('sound tab lists the installed packs', $$('.sound-cat .vfx-chip').length >= 5)
check('auto-SFX cues are shown with what they resolve to',
  $$('.cue-row .vfx-chip').length >= 3)

click(tabs[0])
await waitFor(() => window.document.querySelector('.preset-card'))
if (clips.length) {
  click($$('.preset-card')[0])                   // apply the first preset
  check('applying a preset updates the timeline or explains why not',
    await waitFor(() => window.document.querySelector('.toast'), { timeout: 8000 }))
  check('meme timeline elements render', !!$('.tled-blk.fx') || !!$('.toast'))
} else {
  skip('meme preset application')
}

const examples = $$('.example-chip')
if (examples.length && !examples[0].disabled) {
  click(examples[0])
  const toasted = await waitFor(() => window.document.querySelector('.toast'), { timeout: 10000 })
  check('AI command returns a toast', toasted && $$('.toast').length > 0)
} else {
  skip('AI command')
}

click($('.editor-head .btn.ghost'))
await waitFor(() => window.document.querySelector('.dashboard'))
check('navigates back to the dashboard', !!$('.dashboard'))

report()

function report() {
  const suffix = skipped ? ` (${skipped} skipped)` : ''
  console.log(`\n${errors.length ? '❌' : '✅'} smoke test finished with ${errors.length} error(s)${suffix}`)
  errors.slice(0, 20).forEach((e) => console.log(`   • ${e}`))
  process.exit(errors.length ? 1 : 0)
}
