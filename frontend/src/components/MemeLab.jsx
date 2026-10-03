import React, { useEffect, useMemo, useState } from 'react'
import { api, fmtBytes } from '../services/api.js'

/* ────────────────────────────────────────────────────────────────────────
   🎬 Meme lab — the packs panel.

   One place for everything that arrives as a *pack*:
     • meme presets  (Vlipsy clips + Impact text + stings, one click each)
     • meme clip inserts (click a clip → drops onto the timeline at the playhead)
     • VFX sounds (Pixabay / offline kit; click → placed on the audio track)
     • install state + how to fetch the packs (they are never committed)
   ──────────────────────────────────────────────────────────────────────── */

const TABS = [
  ['presets', '⚡ Presets'],
  ['memes', '😹 Memes'],
  ['sounds', '🔔 Sounds'],
]

const cueLabel = (c) => `${c.resolved ? '' : '⚠ '}${c.name}`

export default function MemeLab({
  assets, options, set, notify, hasTimeline,
  onApplyPreset, onAddMeme, onAddSound, onPacksChanged,
}) {
  const [tab, setTab] = useState('presets')
  const [filter, setFilter] = useState('')
  const [packs, setPacks] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => { api.packs().then(setPacks).catch(() => {}) }, [])

  const presets = assets?.meme_presets || []
  const memes = assets?.memes || []
  const sfx = assets?.sfx || []
  const cues = assets?.sfx_cues || []
  const packInfo = packs?.packs || assets?.packs || {}

  const matches = (item) => {
    if (!filter.trim()) return true
    const q = filter.toLowerCase()
    return `${item.id} ${item.name} ${item.category || ''} ${(item.tags || []).join(' ')}`
      .toLowerCase().includes(q)
  }

  const soundsByCategory = useMemo(() => {
    const groups = {}
    for (const s of sfx.filter(matches)) {
      const key = s.category || 'other'
      ;(groups[key] = groups[key] || []).push(s)
    }
    return groups
  }, [sfx, filter])

  const refresh = async () => {
    setBusy(true)
    try {
      await api.refreshAssets()
      const [p, a] = await Promise.all([api.packs(), onPacksChanged?.()])
      if (p) setPacks(p)
      if (a) notify?.(`Packs re-scanned — ${a.memes?.length || 0} memes, ${a.sfx?.length || 0} sounds`, 'success')
    } catch (e) {
      notify?.(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const buildOffline = async () => {
    setBusy(true)
    try {
      const res = await api.buildVfxPack()
      notify?.(res.status === 'building' ? 'Building the offline VFX kit…' : 'VFX kit ready', 'info')
      // the build runs in the background on the server — poll briefly
      for (let i = 0; i < 40; i++) {
        await new Promise((r) => setTimeout(r, 1500))
        const st = await api.packBuildState().catch(() => null)
        if (!st?.running) break
      }
      await refresh()
      notify?.('Offline VFX kit is ready', 'success')
    } catch (e) {
      notify?.(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const copy = (text) => {
    navigator.clipboard?.writeText(text).then(
      () => notify?.('Command copied', 'success'),
      () => notify?.(text, 'info'))
  }

  const PackRow = ({ info }) => (
    <div className={`pack-row ${info?.installed ? 'ok' : 'empty'}`}>
      <span className="pack-dot" aria-hidden="true" />
      <div className="pack-text">
        <b>{info?.label || 'Pack'}</b>
        <span className="muted">
          {info?.installed
            ? `${info.count} file(s)${info.active != null && info.active < info.count
                ? ` · ${info.active} active` : ''} · ${fmtBytes(info.bytes)} · ${info.source}`
            : 'not installed'}
        </span>
        {!info?.installed && info?.note && <span className="pack-note">{info.note}</span>}
      </div>
      {info?.install && (
        <button className="btn ghost tiny" title={info.install}
                onClick={() => copy(info.install)}>copy cmd</button>
      )}
    </div>
  )

  return (
    <div className="panel card memelab">
      <div className="panel-head">
        <h3>😂 Meme lab</h3>
        <span className="muted">{memes.length} clips · {sfx.length} sounds</span>
        <button className="btn ghost tiny" onClick={refresh} disabled={busy}
                title="Re-scan assets/ after fetching packs">↻</button>
      </div>

      <div className="seg wrap memelab-tabs">
        {TABS.map(([id, label]) => (
          <button key={id} className={tab === id ? 'active' : ''}
                  onClick={() => setTab(id)}>{label}</button>
        ))}
        <input className="text-input memelab-filter" placeholder="filter…" value={filter}
               aria-label="Filter assets" onChange={(e) => setFilter(e.target.value)} />
      </div>

      {tab === 'presets' && (
        <div className="preset-grid">
          {presets.filter(matches).map((p) => {
            const needs = p.requires === 'memes' && memes.length === 0
            return (
              <button key={p.id} className={`preset-card ${needs ? 'needs' : ''}`}
                      title={p.description + (needs ? ' — install the meme pack first' : '')}
                      onClick={() => onApplyPreset?.(p)}>
                <span className="preset-emoji">{p.emoji || '✨'}</span>
                <span className="preset-name">{p.name}</span>
                <span className="preset-desc">{p.description}</span>
                <span className="preset-tags">
                  {(p.tags || []).map((t) => <em key={t}>{t}</em>)}
                  {needs && <em className="warn">needs meme pack</em>}
                </span>
              </button>
            )
          })}
          {!presets.length && <p className="muted pad">No presets found — is assets/templates/memes.json present?</p>}
        </div>
      )}

      {tab === 'memes' && (
        <>
          {memes.length === 0 ? (
            <div className="pack-empty">
              <p className="muted">
                No meme clips yet. Fetch them from <b>vlipsy.com</b> (or drop your own
                MP4/GIF files into <code>assets/memes/</code>):
              </p>
              <code className="cmd">VLIPSY_API_KEY=… python3 scripts/fetch_packs.py --memes --sounds</code>
              <div className="row-actions">
                <button className="btn tiny" onClick={() => copy('VLIPSY_API_KEY=… python3 scripts/fetch_packs.py --memes --sounds')}>
                  copy command
                </button>
                <button className="btn tiny ghost" onClick={refresh} disabled={busy}>I dropped files in — rescan</button>
              </div>
              <p className="muted small">
                No key? Right-click any clip on vlipsy.com → copy the media URL and put it in
                <code> scripts/packs.example.json</code>, then run
                <code> python3 scripts/fetch_packs.py --urls scripts/packs.example.json</code>.
              </p>
            </div>
          ) : (
            <div className="asset-grid">
              {memes.filter(matches).map((m) => (
                <button key={m.id} className="asset-tile" title={`${m.name} — insert at the playhead`}
                        onClick={() => onAddMeme?.(m.id)}>
                  <video src={m.url} muted loop preload="metadata"
                         onMouseEnter={(e) => e.currentTarget.play().catch(() => {})}
                         onMouseLeave={(e) => { e.currentTarget.pause(); e.currentTarget.currentTime = 0 }} />
                  <span className="asset-name">{m.name}</span>
                </button>
              ))}
            </div>
          )}
        </>
      )}

      {tab === 'sounds' && (
        <>
          <div className="cue-row">
            <span className="muted small">Auto-SFX cue:</span>
            {cues.map((c) => (
              <button key={c.id} className={`vfx-chip ${c.resolved ? '' : 'off'}`}
                      title={c.description || c.name}
                      onClick={() => {
                        const sid = c.resolved
                        if (!sid) return notify?.(`No sound installed for “${c.id}” — build or fetch the VFX pack`, 'error')
                        onAddSound?.(sid, c.id)
                      }}>{cueLabel(c)}</button>
            ))}
          </div>
          <div className="seg">
            {['auto', 'vfx', 'builtin', 'none'].map((v) => (
              <button key={v} className={(options.sfx_pack || 'auto') === v ? 'active' : ''}
                      title={`Auto-SFX resolves against: ${v}`}
                      onClick={() => set({ sfx_pack: v })}>{v}</button>
            ))}
          </div>
          <div className="divider" />
          {sfx.length === 0 && (
            <div className="pack-empty">
              <p className="muted">
                No sounds yet. Generate the free offline VFX kit (no network), or fetch
                real sound design from <b>pixabay.com/sound-effects</b>:
              </p>
              <button className="btn tiny primary" onClick={buildOffline} disabled={busy}>
                {busy ? 'building…' : '⚡ Build offline VFX kit'}
              </button>
              <code className="cmd">python3 scripts/fetch_packs.py --sound</code>
            </div>
          )}
          {Object.entries(soundsByCategory).map(([cat, items]) => (
            <div key={cat} className="sound-cat">
              <div className="sound-cat-head">
                <b>{cat}</b>
                <span className="muted small">{items.length}</span>
              </div>
              <div className="vfx-chips">
                {items.map((s) => (
                  <button key={`${s.pack}-${s.id}`} className="vfx-chip"
                          title={`${s.source}${s.license ? ' · ' + s.license : ''} — place at the playhead`}
                          onClick={() => onAddSound?.(s.id)}>
                    {s.id === s.name ? s.id : s.name}
                    {s.pack !== 'vfx' && <em className="chip-pack">{s.pack}</em>}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </>
      )}

      <div className="divider" />
      <div className="pack-status">
        <PackRow info={packInfo.vfx_sfx} />
        <PackRow info={packInfo.memes} />
        {memes.length === 0 && packInfo.vfx_sfx?.installed && (
          <button className="btn tiny ghost wide" onClick={buildOffline} disabled={busy}>
            {busy ? 'building…' : '↻ Rebuild the offline VFX kit'}
          </button>
        )}
      </div>
      {!hasTimeline && (
        <p className="muted small">Select a clip first — meme inserts and sounds are placed on its timeline.</p>
      )}
    </div>
  )
}
