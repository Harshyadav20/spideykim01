import React, { useEffect, useMemo, useRef, useState } from 'react'
import { fmtMMSS } from '../services/api.js'

/* ────────────────────────────────────────────────────────────────────────
   V6 — multi-track timeline editor.

   timeline = {
     video: [{id, start, end}]                        // ordered cut list (source time)
     text:  [{id, text, t0, t1, color, y, size}]      // text cards (output time)
     fx:    [{id, kind:'vfx'|'overlay', name, t0, t1, opacity, mode}]
     audio: [{id, kind:'music'|'sfx', name, t0, t1, volume}]
   }
   Video is a sequential cut list (no gaps); the other tracks use
   absolute output time.
   ──────────────────────────────────────────────────────────────────────── */

export const uid = (p = 'e') => p + Math.random().toString(36).slice(2, 8)

export const outTotal = (tl) =>
  (tl?.video || []).reduce((a, s) => a + Math.max(0, s.end - s.start), 0)

export function outToSource(tl, t) {
  let acc = 0
  for (const s of tl?.video || []) {
    const d = s.end - s.start
    if (t <= acc + d) return s.start + (t - acc)
    acc += d
  }
  const segs = tl?.video || []
  return segs.length ? segs[segs.length - 1].end : 0
}

export function sourceToOut(tl, s) {
  let acc = 0
  for (const seg of tl?.video || []) {
    if (s >= seg.start && s <= seg.end) return acc + (s - seg.start)
    if (s < seg.start) return acc
    acc += seg.end - seg.start
  }
  return acc
}

const FX_VFX = ['shake', 'glitch', 'grain', 'vignette', 'flash', 'saturate']
const FX_OVERLAYS_FALLBACK = ['light-leak', 'glitter', 'bokeh-dream', 'neon-pulse']
const MUSIC_FALLBACK = ['chill', 'ambient', 'upbeat']
const SFX_FALLBACK = ['pop', 'whoosh']
const MEME_FITS = [['cover', 'fill'], ['contain', 'fit'], ['band', 'band']]
const TRACK_META = {
  video: { label: '🎬 Video', color: '#3B9EFF' },
  text: { label: '📝 Text', color: '#FFE600' },
  fx: { label: '⚡ FX', color: '#AF52DE' },
  audio: { label: '🎵 Audio', color: '#00E5A0' },
}

export default function TimelineEditor({ timeline, setTimeline, playheadRef,
                                         onSeekOut, onToast, fonts, assets }) {
  // playhead lives in a ref owned by the editor; sample it locally so the
  // whole page doesn't re-render on every video timeupdate
  const [playhead, setPlayhead] = useState(0)
  useEffect(() => {
    const iv = setInterval(() => setPlayhead(playheadRef?.current || 0), 100)
    return () => clearInterval(iv)
  }, [playheadRef])
  const [pps, setPps] = useState(44)                 // pixels per output second
  const [sel, setSel] = useState(null)               // {track, id}
  const laneRef = useRef(null)
  const dragRef = useRef(null)

  const total = outTotal(timeline)
  const width = Math.max(total * pps, 260)
  const playOut = useMemo(() => sourceToOut(timeline, playhead || 0), [timeline, playhead])

  // real inventories (meme pack, VFX sounds) with sane fallbacks when the
  // assets call has not returned yet
  const FX_OVERLAYS = useMemo(
    () => (assets?.overlays || []).filter((o) => o.id !== 'none').map((o) => o.id)
      .concat(assets?.overlays ? [] : FX_OVERLAYS_FALLBACK), [assets])
  const MUSIC = useMemo(
    () => (assets?.music || []).filter((m) => m.id !== 'none').map((m) => m.id)
      .concat(assets?.music ? [] : MUSIC_FALLBACK), [assets])
  const SFX = useMemo(() => {
    const ids = (assets?.sfx || []).map((s) => s.id)
    return ids.length ? ids : SFX_FALLBACK
  }, [assets])
  const SFX_CUES = useMemo(() => (assets?.sfx_cues || []).map((c) => `cue:${c.id}`), [assets])
  const MEMES = useMemo(() => assets?.memes || [], [assets])

  // ------------------------------------------------------------- helpers
  const upd = (track, id, patch) => setTimeline((tl) => ({
    ...tl,
    [track]: (tl[track] || []).map((el) => el.id === id ? { ...el, ...patch } : el),
  }))
  const del = (track, id) => {
    setTimeline((tl) => ({ ...tl, [track]: (tl[track] || []).filter((e) => e.id !== id) }))
    setSel(null)
  }
  const add = (track, el) => {
    setTimeline((tl) => ({ ...tl, [track]: [...(tl[track] || []), el] }))
    setSel({ track, id: el.id })
  }

  const addText = () => add('text', {
    id: uid('t'), text: 'NEW TEXT', t0: Math.min(playOut, Math.max(0, total - 2)),
    t1: Math.min(total, playOut + 2.5), y: 'top', size: 1.1, color: '#FFE600',
  })
  const addFx = () => add('fx', {
    id: uid('f'), kind: 'vfx', name: 'glitch',
    t0: Math.min(playOut, Math.max(0, total - 3)), t1: Math.min(total, playOut + 3),
  })
  const addMusic = () => add('audio', {
    id: uid('m'), kind: 'music', name: MUSIC[0] || 'chill', t0: 0, t1: total, volume: 0.16,
  })
  const addSfx = () => add('audio', {
    id: uid('s'), kind: 'sfx', name: SFX[0] || 'pop',
    t0: Math.min(playOut, Math.max(0, total - 0.5)), t1: Math.min(total, playOut + 1),
  })
  const addMeme = () => {
    if (!MEMES.length) {
      onToast?.('No meme pack yet — open the Meme lab to fetch Vlipsy clips (or drop files in assets/memes/)')
      return
    }
    const dur = 1.6
    add('fx', {
      id: uid('mm'), kind: 'meme', name: MEMES[0].id, fit: 'cover', pos: 'middle',
      anim: 'pop', volume: 0.8, sting: true,
      t0: Math.min(playOut, Math.max(0, total - dur)), t1: Math.min(total, playOut + dur),
    })
  }

  const clampT = (t) => Math.max(0, Math.min(total, Math.round(t * 20) / 20))

  const splitAtPlayhead = () => {
    if (!timeline?.video?.length) return
    let acc = 0
    setTimeline((tl) => {
      const segs = [...tl.video]
      for (let i = 0; i < segs.length; i++) {
        const d = segs[i].end - segs[i].start
        if (playOut > acc + 0.3 && playOut < acc + d - 0.3) {
          const src = segs[i].start + (playOut - acc)
          segs.splice(i, 1,
            { ...segs[i], id: uid('v'), end: src },
            { ...segs[i], id: uid('v'), start: src })
          break
        }
        acc += d
      }
      return { ...tl, video: segs }
    })
  }

  // ------------------------------------------------------------- dragging
  useEffect(() => {
    if (!dragRef.current) return
    const move = (ev) => {
      const d = dragRef.current
      if (!d) return
      const dt = (ev.clientX - d.x0) / pps
      if (d.track === 'video') {
        // the video track is a sequential cut list: edges trim the source in/out
        // point, and reordering is done with the ◀ ▶ buttons (dragging a block
        // sideways would have to shuffle the whole list, so it is ignored)
        if (d.mode === 'move') return
        setTimeline((tl) => ({
          ...tl,
          video: tl.video.map((s) => {
            if (s.id !== d.id) return s
            if (d.mode === 'left') return { ...s, start: Math.min(s.end - 0.3, Math.max(0, d.s0 + dt)) }
            return { ...s, end: Math.max(s.start + 0.3, d.e0 + dt) }
          }),
        }))
      } else {
        setTimeline((tl) => ({
          ...tl,
          [d.track]: tl[d.track].map((el) => {
            if (el.id !== d.id) return el
            if (d.mode === 'move') {
              const len = d.t10 - d.t00
              const t0 = clampT(d.t00 + dt)
              return { ...el, t0, t1: Math.min(total, t0 + len) }
            }
            if (d.mode === 'left') return { ...el, t0: clampT(Math.min(d.t10 - 0.3, d.t00 + dt)) }
            return { ...el, t1: clampT(Math.max(d.t00 + 0.3, d.t10 + dt)) }
          }),
        }))
      }
    }
    const up = () => { dragRef.current = null }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
  }) // re-bind every render: dragRef carries mutable state

  const startDrag = (ev, track, id, mode, el) => {
    ev.stopPropagation()
    ev.preventDefault()
    setSel({ track, id })
    dragRef.current = {
      track, id, mode, x0: ev.clientX,
      t00: el.t0 ?? 0, t10: el.t1 ?? 0,
      s0: el.start ?? 0, e0: el.end ?? 0,
    }
    // force the effect to re-bind with the fresh dragRef
    setDragTick((x) => x + 1)
  }
  const [, setDragTick] = useState(0)

  const seekFromEvent = (ev) => {
    const box = laneRef.current?.getBoundingClientRect()
    if (!box) return
    onSeekOut(Math.max(0, Math.min(total, (ev.clientX - box.left) / pps)))
  }

  // ------------------------------------------------------------- render
  if (!timeline) return null
  const selEl = sel && (timeline[sel.track] || []).find((e) => e.id === sel.id)

  // video blocks laid out sequentially
  let acc = 0
  const videoBlocks = timeline.video.map((s) => {
    const left = acc * pps
    const w = Math.max(6, (s.end - s.start) * pps)
    acc += s.end - s.start
    return { ...s, left, w }
  })

  const ticks = []
  const step = pps < 26 ? 5 : 1
  for (let t = 0; t <= total + 0.01; t += step) ticks.push(t)

  return (
    <div className="tled card">
      <div className="tled-bar">
        <b>🎛 Timeline</b>
        <span className="chip">{total.toFixed(1)}s · {timeline.video.length} cut{timeline.video.length > 1 ? 's' : ''}</span>
        <span className="tled-spacer" />
        <button className="btn tiny" onClick={addText}>+ Text</button>
        <button className="btn tiny" onClick={addFx}>+ FX</button>
        <button className="btn tiny" onClick={addMeme}
                title={MEMES.length ? 'Insert a meme clip at the playhead' : 'Meme pack not installed yet'}>
          + Meme
        </button>
        <button className="btn tiny" onClick={addMusic}>+ Music</button>
        <button className="btn tiny" onClick={addSfx}>+ SFX</button>
        <button className="btn tiny" onClick={splitAtPlayhead} disabled={!timeline.video.length}>✂ Split</button>
        <button className="btn tiny ghost" onClick={() => setPps((p) => Math.max(14, p - 10))}>−</button>
        <button className="btn tiny ghost" onClick={() => setPps((p) => Math.min(160, p + 10))}>+</button>
      </div>

      <div className="tled-scroll" ref={laneRef} onPointerDown={(e) => {
        if (e.target.classList.contains('tled-lane') || e.target.classList.contains('tled-ruler')) seekFromEvent(e)
      }}>
        <div style={{ width }} className="tled-inner">
          {/* ruler */}
          <div className="tled-ruler">
            {ticks.map((t) => (
              <div key={t} className="tled-tick" style={{ left: t * pps }}>
                <span>{t % (step * 5) === 0 || step === 5 ? fmtMMSS(t) : ''}</span>
              </div>
            ))}
          </div>

          {/* video track */}
          <div className="tled-row">
            <div className="tled-gutter">{TRACK_META.video.label}</div>
            <div className="tled-lane">
              {videoBlocks.map((s) => (
                <div key={s.id}
                     className={`tled-blk video ${sel?.id === s.id ? 'sel' : ''}`}
                     style={{ left: s.left, width: s.w, background: TRACK_META.video.color }}
                     onPointerDown={(e) => startDrag(e, 'video', s.id, 'move', s)}>
                  <div className="tled-edge l" onPointerDown={(e) => startDrag(e, 'video', s.id, 'left', s)} />
                  <span className="tled-blk-label">{fmtMMSS(s.start)}–{fmtMMSS(s.end)}</span>
                  <div className="tled-edge r" onPointerDown={(e) => startDrag(e, 'video', s.id, 'right', s)} />
                </div>
              ))}
            </div>
          </div>

          {/* text / fx / audio tracks */}
          {['text', 'fx', 'audio'].map((track) => (
            <div className="tled-row" key={track}>
              <div className="tled-gutter">{TRACK_META[track].label}</div>
              <div className="tled-lane" onPointerDown={(e) => e.target.classList.contains('tled-lane') && seekFromEvent(e)}>
                {(timeline[track] || []).map((el) => {
                  const t0 = Math.max(0, el.t0 || 0), t1 = Math.min(total, el.t1 ?? total)
                  const label = track === 'text' ? el.text :
                    track === 'fx' ? `${el.kind === 'overlay' ? '🌈 ' : el.kind === 'meme' ? '😹 ' : '⚡ '}${el.name}` :
                    `${el.kind === 'music' ? '🎵 ' : '🔔 '}${el.name}`
                  return (
                    <div key={el.id}
                         className={`tled-blk ${track} ${sel?.id === el.id ? 'sel' : ''}`}
                         style={{ left: t0 * pps, width: Math.max(8, (t1 - t0) * pps), background: TRACK_META[track].color }}
                         onPointerDown={(e) => startDrag(e, track, el.id, 'move', { t0, t1 })}>
                      <div className="tled-edge l" onPointerDown={(e) => startDrag(e, track, el.id, 'left', { t0, t1 })} />
                      <span className="tled-blk-label">{label}</span>
                      <div className="tled-edge r" onPointerDown={(e) => startDrag(e, track, el.id, 'right', { t0, t1 })} />
                    </div>
                  )
                })}
              </div>
            </div>
          ))}

          {/* playhead */}
          <div className="tled-playhead" style={{ left: playOut * pps }} />
        </div>
      </div>

      {/* inspector */}
      {selEl ? (
        <div className="tled-inspect">
          {sel.track === 'video' && (
            <>
              <b>Cut</b>
              <label>in <input type="number" step="0.1" value={selEl.start}
                onChange={(e) => upd('video', selEl.id, { start: Math.max(0, Math.min(selEl.end - 0.3, +e.target.value || 0)) })} /></label>
              <label>out <input type="number" step="0.1" value={selEl.end}
                onChange={(e) => upd('video', selEl.id, { end: Math.max(selEl.start + 0.3, +e.target.value || 0) })} /></label>
              <span className="chip">{(selEl.end - selEl.start).toFixed(1)}s</span>
              <div className="tled-spacer" />
              <button className="btn tiny" onClick={() => setTimeline((tl) => {
                const i = tl.video.findIndex((s) => s.id === selEl.id)
                if (i > 0) { const v = [...tl.video]; [v[i - 1], v[i]] = [v[i], v[i - 1]]; return { ...tl, video: v } }
                return tl
              })}>◀ move</button>
              <button className="btn tiny" onClick={() => setTimeline((tl) => {
                const i = tl.video.findIndex((s) => s.id === selEl.id)
                if (i < tl.video.length - 1) { const v = [...tl.video]; [v[i + 1], v[i]] = [v[i], v[i + 1]]; return { ...tl, video: v } }
                return tl
              })}>move ▶</button>
              <button className="btn tiny danger"
                      onClick={() => timeline.video.length > 1 && del('video', selEl.id)}>🗑</button>
            </>
          )}
          {sel.track === 'text' && (
            <>
              <b>Text</b>
              <input className="text-input" value={selEl.text}
                     onChange={(e) => upd('text', selEl.id, { text: e.target.value })} />
              <select value={selEl.font || ''} onChange={(e) => upd('text', selEl.id, { font: e.target.value })}>
                <option value="">style font</option>
                {(fonts || []).map((f) => <option key={f} value={f}>{f.replace(/\.ttf$/i, '')}</option>)}
              </select>
              <select value={selEl.anim || 'fade'} onChange={(e) => upd('text', selEl.id, { anim: e.target.value })}>
                {['fade', 'pop', 'slide', 'glow'].map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
              <select value={selEl.y || 'top'} onChange={(e) => upd('text', selEl.id, { y: e.target.value })}>
                <option value="top">top</option><option value="middle">middle</option><option value="bottom">bottom</option>
              </select>
              <select value={selEl.color || '#FFE600'} onChange={(e) => upd('text', selEl.id, { color: e.target.value })}>
                {['#FFE600', '#FFFFFF', '#FF3B30', '#00E5A0', '#3B9EFF', '#FF6FB5'].map((c) => (
                  <option key={c} value={c}>{c}</option>))}
              </select>
              <label>size <input type="range" min="0.6" max="2" step="0.1" value={selEl.size || 1}
                onChange={(e) => upd('text', selEl.id, { size: +e.target.value })} /></label>
              <label>spacing <input type="range" min="0" max="24" step="1" value={selEl.spacing || 0}
                onChange={(e) => upd('text', selEl.id, { spacing: +e.target.value })} /></label>
              <label>rotate <input type="range" min="-30" max="30" step="1" value={selEl.rotate || 0}
                onChange={(e) => upd('text', selEl.id, { rotate: +e.target.value })} /></label>
              <label title="background box">
                <input type="checkbox" checked={!!selEl.box}
                       onChange={(e) => upd('text', selEl.id, { box: e.target.checked })} /> box
              </label>
              <div className="tled-spacer" />
              <button className="btn tiny danger" onClick={() => del('text', selEl.id)}>🗑</button>
            </>
          )}
          {sel.track === 'fx' && (
            <>
              <b>FX</b>
              <select value={selEl.kind || 'vfx'}
                      onChange={(e) => upd('fx', selEl.id, {
                        kind: e.target.value,
                        name: e.target.value === 'vfx' ? (FX_VFX[0] || 'glitch')
                          : e.target.value === 'meme' ? (MEMES[0]?.id || '')
                          : (FX_OVERLAYS[0] || 'light-leak'),
                      })}>
                <option value="vfx">effect</option>
                <option value="overlay">overlay</option>
                <option value="meme">😹 meme</option>
              </select>

              {(selEl.kind || 'vfx') === 'meme' ? (
                MEMES.length ? (
                  <select value={selEl.name} onChange={(e) => upd('fx', selEl.id, { name: e.target.value })}>
                    {MEMES.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                  </select>
                ) : (
                  <input className="text-input" placeholder="meme id (pack empty)"
                         value={selEl.name || ''}
                         onChange={(e) => upd('fx', selEl.id, { name: e.target.value })} />
                )
              ) : (
                <select value={selEl.name} onChange={(e) => upd('fx', selEl.id, { name: e.target.value })}>
                  {((selEl.kind || 'vfx') === 'vfx' ? FX_VFX : FX_OVERLAYS).map((n) => (
                    <option key={n} value={n}>{n}</option>))}
                </select>
              )}

              {selEl.kind === 'overlay' && (
                <label>opacity <input type="range" min="0.1" max="1" step="0.05" value={selEl.opacity || 0.5}
                  onChange={(e) => upd('fx', selEl.id, { opacity: +e.target.value })} /></label>
              )}

              {selEl.kind === 'meme' && (
                <>
                  <label>fit
                    <select value={selEl.fit || 'cover'} onChange={(e) => upd('fx', selEl.id, { fit: e.target.value })}>
                      {MEME_FITS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                  </label>
                  <label>place
                    <select value={selEl.pos || 'middle'} onChange={(e) => upd('fx', selEl.id, { pos: e.target.value })}>
                      <option value="top">top</option><option value="middle">middle</option><option value="bottom">bottom</option>
                    </select>
                  </label>
                  <label>anim
                    <select value={selEl.anim || 'pop'} onChange={(e) => upd('fx', selEl.id, { anim: e.target.value })}>
                      <option value="pop">pop-in</option><option value="none">none</option>
                    </select>
                  </label>
                  <label>vol <input type="range" min="0" max="1.2" step="0.05" value={selEl.volume ?? 0.8}
                    onChange={(e) => upd('fx', selEl.id, { volume: +e.target.value })} /></label>
                  <label title="Play the meme sting when this insert lands">
                    <input type="checkbox" checked={selEl.sting !== false}
                           onChange={(e) => upd('fx', selEl.id, { sting: e.target.checked })} /> sting
                  </label>
                </>
              )}
              <div className="tled-spacer" />
              <button className="btn tiny danger" onClick={() => del('fx', selEl.id)}>🗑</button>
            </>
          )}
          {sel.track === 'audio' && (
            <>
              <b>Audio</b>
              <select value={selEl.kind} onChange={(e) => upd('audio', selEl.id, { kind: e.target.value, name: e.target.value === 'music' ? (MUSIC[0] || 'chill') : (SFX[0] || 'pop') })}>
                <option value="music">music</option><option value="sfx">sfx</option>
              </select>
              <select value={selEl.name} onChange={(e) => upd('audio', selEl.id, { name: e.target.value })}>
                {selEl.kind === 'music'
                  ? MUSIC.map((n) => <option key={n} value={n}>{n}</option>)
                  : (
                    <>
                      <optgroup label="cues (resolved at render)">
                        {SFX_CUES.map((n) => <option key={n} value={n}>{n}</option>)}
                      </optgroup>
                      <optgroup label="sounds">
                        {SFX.map((n) => <option key={n} value={n}>{n}</option>)}
                      </optgroup>
                    </>
                  )}
              </select>
              {selEl.kind === 'music' && (
                <label>vol <input type="range" min="0.05" max="0.6" step="0.01" value={selEl.volume || 0.16}
                  onChange={(e) => upd('audio', selEl.id, { volume: +e.target.value })} /></label>
              )}
              {selEl.kind === 'sfx' && (
                <label>vol <input type="range" min="0.05" max="1" step="0.05" value={selEl.volume || 0.5}
                  onChange={(e) => upd('audio', selEl.id, { volume: +e.target.value })} /></label>
              )}
              <div className="tled-spacer" />
              <button className="btn tiny danger" onClick={() => del('audio', selEl.id)}>🗑</button>
            </>
          )}
        </div>
      ) : (
        <div className="tled-inspect muted">
          Drag block edges to trim · drag blocks to move · click the ruler to seek · ✂ Split cuts at the playhead
        </div>
      )}
    </div>
  )
}
