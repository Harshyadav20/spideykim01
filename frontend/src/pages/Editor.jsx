import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, fmtMMSS } from '../services/api.js'
import ClipCard from '../components/ClipCard.jsx'
import Timeline from '../components/Timeline.jsx'
import VideoPlayer from '../components/VideoPlayer.jsx'
import StyleGrid from '../components/StyleGrid.jsx'
import EffectsControls from '../components/EffectsControls.jsx'
import RenderPanel from '../components/RenderPanel.jsx'
import TranscriptPanel from '../components/TranscriptPanel.jsx'
import CommandBox from '../components/CommandBox.jsx'
import TimelineEditor, { outTotal, outToSource, sourceToOut } from '../components/TimelineEditor.jsx'
import StyleGen from '../components/StyleGen.jsx'
import MemeLab from '../components/MemeLab.jsx'

export const DEFAULT_OPTIONS = {
  style: 'viral',
  captions: true,
  caption_animation: '',       // '' -> style default
  font_scale: 1.0,
  caption_position: '',        // '' -> style default
  uppercase: null,             // null -> style default
  zoom: '',                    // '' -> style default
  aspect: '',                  // '' -> style default
  crop_x: 0.5,
  remove_silence: false,
  watermark: '',
  music: null,                 // null -> style default, 'none' to disable
  music_volume: 0.16,
  sfx: true,
  sfx_pack: 'auto',            // auto | builtin | vfx | memes | none
  meme_preset: null,           // last applied meme preset (recorded for renders)
  vfx: null,                   // null -> style default, [] to disable all
  vfx_intensity: 0.5,
  overlay: null,               // null -> style default, 'none' to disable
  overlay_opacity: 0.5,
  overlay_mode: 'screen',
  resolution: '1080',
  fast: false,
}

const STAGES = [
  ['probe', 'Reading video'],
  ['audio', 'Silence map'],
  ['transcribe', 'Transcription'],
  ['ai', 'Clip finding'],
  ['done', 'Complete'],
]

export default function Editor({ id, notify, onBack }) {
  const [project, setProject] = useState(null)
  const [analysis, setAnalysis] = useState(null)
  const [running, setRunning] = useState(false)
  const [clips, setClips] = useState([])
  const [selected, setSelected] = useState(null)   // working copy of the clip
  const [options, setOptions] = useState(DEFAULT_OPTIONS)
  const [styles, setStyles] = useState({ styles: [] })
  const [assets, setAssets] = useState(null)
  const [seekTo, setSeekTo] = useState(null)       // seconds -> player seeks
  const [time, setTime] = useState(0)              // current playhead
  const playheadRef = useRef(0)                    // source time from the player (no re-render)
  const [timeline, setTimeline] = useState(null)   // V6 multi-track timeline
  const [renderSignal, setRenderSignal] = useState(0)
  const pollRef = useRef(null)

  const set = useCallback((patch) => setOptions((o) => ({ ...o, ...patch })), [])

  const loadProject = useCallback(async () => {
    const p = await api.project(id)
    setProject(p)
    const a = await api.analysis(id)
    setAnalysis(a.analysis)
    setRunning(a.running)
    setClips(a.analysis?.clips || [])
  }, [id])

  useEffect(() => {
    loadProject().catch((e) => notify(e.message, 'error'))
    api.styles().then(setStyles).catch(() => {})
    api.assets().then(setAssets).catch(() => {})
  }, [loadProject, notify])

  // kick off analysis automatically for fresh uploads
  useEffect(() => {
    if (!project || running || analysis) return
    if (project.status === 'ready') {
      api.analyze(id).then(() => setRunning(true)).catch(() => {})
    }
  }, [project, running, analysis, id])

  // poll while analyzing
  useEffect(() => {
    if (!running) return undefined
    pollRef.current = setInterval(async () => {
      try {
        const a = await api.analysis(id)
        setRunning(a.running)
        setProject(a.project)
        if (!a.running) {
          setAnalysis(a.analysis)
          setClips(a.analysis?.clips || [])
          if (a.project?.status === 'error') notify('Analysis failed: ' + a.project.error, 'error')
          else notify(`Analysis complete — ${a.analysis?.clips?.length || 0} clips suggested`, 'success')
        }
      } catch { /* keep polling */ }
    }, 1500)
    return () => clearInterval(pollRef.current)
  }, [running, id, notify])

  const pickClip = useCallback((c) => {
    setSelected({ ...c })
    setSeekTo(c.start)
    // the timeline (V6) edits one clip at a time: seed it on first selection and
    // re-seed when a different clip is picked, so cuts never cross clips
    if (!timeline || timeline._clipId !== c.id) {
      if (timeline) notify('Timeline reset for the new clip')
      setTimeline({
        _clipId: c.id,
        video: [{ id: 'v1', start: c.start, end: c.end }],
        text: [], fx: [], audio: [],
      })
    }
  }, [timeline, notify])

  const updateClip = (patch) => setSelected((s) => (s ? { ...s, ...patch } : s))

  const addManualClip = () => {
    const start = Math.max(0, Math.round((selected?.end ?? time ?? 0) * 10) / 10)
    const end = Math.min(project.duration, start + 30)
    if (end - start < 3) {
      notify('You are too close to the end of the video to add a clip', 'error')
      return
    }
    api.addClip(id, { start, end, title: 'Manual clip' }).then((c) => {
      setClips((cl) => [c, ...cl])
      pickClip(c)
      notify('Manual clip added', 'success')
    }).catch((e) => notify(e.message, 'error'))
  }

  const deleteClip = (clip) => {
    if (clip.source !== 'manual') return
    api.deleteClip(id, clip.id)
      .then(() => {
        setClips((cl) => cl.filter((c) => c.id !== clip.id))
        setSelected((s) => (s?.id === clip.id ? null : s))
        notify('Clip removed', 'success')
      })
      .catch((e) => notify(e.message, 'error'))
  }

  const reanalyze = () => {
    setAnalysis(null)
    api.analyze(id).then(() => { setRunning(true); notify('Re-running analysis…') })
      .catch((e) => notify(e.message, 'error'))
  }

  const activeStyle = useMemo(
    () => styles.styles.find((s) => s.id === options.style) || styles.styles[0] || {},
    [styles, options.style])

  const reloadAssets = useCallback(() => api.assets().then((a) => { setAssets(a); return a }).catch(() => null), [])

  // ---------------- meme pack: presets, inserts, stings ----------------
  const nid = (p) => p + Math.random().toString(36).slice(2, 8)

  /** Timeline for the current selection, created on demand (meme actions need one). */
  const ensureTimeline = useCallback(() => {
    if (timeline) {
      return {
        ...timeline,
        video: timeline.video.map((s) => ({ ...s })),
        text: timeline.text.map((e) => ({ ...e })),
        fx: timeline.fx.map((e) => ({ ...e })),
        audio: timeline.audio.map((e) => ({ ...e })),
      }
    }
    const base = selected || clips[0]
    if (!base) return null
    return { _clipId: base.id, video: [{ id: 'v1', start: base.start, end: base.end }],
             text: [], fx: [], audio: [] }
  }, [timeline, selected, clips])

  const pickMemeForPreset = useCallback((preset) => {
    const memes = assets?.memes || []
    if (!memes.length) return null
    const keys = (preset?.meme?.match || []).map((k) => k.toLowerCase())
    const hit = memes.find((m) => {
      const hay = `${m.id} ${m.name} ${(m.tags || []).join(' ')}`.toLowerCase()
      return keys.some((k) => hay.includes(k))
    })
    return (hit || memes[0]).id
  }, [assets])

  /** One-click meme preset: style + text + meme insert + sting + FX. */
  const applyMemePreset = useCallback((preset, windowed = null) => {
    if (!preset) return
    let tl = ensureTimeline()
    if (!tl) {
      notify('Pick a clip first — meme presets are placed on its timeline', 'error')
      return
    }
    const total = outTotal(tl)
    const play = sourceToOut(tl, playheadRef.current || 0)
    const memeId = pickMemeForPreset(preset)
    const spec = preset.meme || null
    const dur = Math.max(0.4, Number(spec?.duration || (windowed ? windowed.t1 - windowed.t0 : 1.6)))
    const t0 = windowed?.t0 ?? spec?.t0 ?? Math.min(play, Math.max(0, total - dur))
    const t1 = windowed?.t1 ?? spec?.t1 ?? Math.min(total, t0 + dur)
    const notes = []

    if (spec && memeId) {
      const every = windowed?.every
      if (every) {
        for (let t = t0; t + dur <= t1 + 0.01; t += every) {
          tl.fx.push({ id: nid('mm'), kind: 'meme', name: memeId, t0: t,
                       t1: Math.min(total, t + dur),
                       fit: spec.fit || 'cover', pos: spec.pos || 'middle',
                       anim: spec.anim || 'pop', volume: spec.volume ?? 0.8,
                       sting: spec.sting !== false })
        }
      } else {
        tl.fx.push({ id: nid('mm'), kind: 'meme', name: memeId, t0, t1,
                     fit: spec.fit || 'cover', pos: spec.pos || 'middle',
                     anim: spec.anim || 'pop', volume: spec.volume ?? 0.8,
                     sting: spec.sting !== false })
      }
      notes.push(`😹 ${memeId} @ ${t0.toFixed(1)}s`)
    } else if (spec) {
      notify('No meme clips installed — fetch the Vlipsy pack (Meme lab) or drop MP4s into assets/memes/',
             'error')
    }

    for (const t of preset.text || []) {
      const td = Number(t.duration || 2.5)
      tl.text.push({ id: nid('t'), text: t.text || 'TEXT', t0: t0, t1: Math.min(total, t0 + td),
                     y: t.y || 'top', size: t.size || 1.1, color: t.color || '#FFFFFF',
                     box: !!t.box, anim: t.anim || 'pop' })
    }
    for (const f of preset.fx || []) {
      const fd = Number(f.duration || 1)
      tl.fx.push({ id: nid('f'), kind: f.kind || 'vfx', name: f.name, t0,
                   t1: Math.min(total, t0 + fd), opacity: f.opacity ?? 0.5 })
    }
    // stings: hook lands on frame one, reaction/meme on the insert, outro at the end
    const cues = preset.sfx || []
    for (const cue of cues) {
      const when = cue === 'hook' ? 0
        : cue === 'outro' ? Math.max(0, total - 1.2)
        : cue === 'meme' || cue === 'reaction' ? t0 : null
      if (when === null) continue                 // punch/text/transition ride the auto-SFX
      tl.audio.push({ id: nid('s'), kind: 'sfx', name: `cue:${cue}`, t0: when,
                      t1: Math.min(total, when + 1.2), volume: cue === 'reaction' ? 0.35 : 0.5 })
    }

    setTimeline(tl)
    setOptions((o) => ({ ...o, sfx: true, meme_preset: preset.id, ...(preset.options || {}) }))
    notify(`⚡ ${preset.name} applied${notes.length ? ' — ' + notes.join(' · ') : ''}`, 'success')
  }, [ensureTimeline, pickMemeForPreset, notify, playheadRef])

  /** Insert one meme clip at the playhead (click in the Meme lab). */
  const addMemeClip = useCallback((memeId, extra = {}) => {
    const tl = ensureTimeline()
    if (!tl) {
      notify('Pick a clip first', 'error')
      return
    }
    const total = outTotal(tl)
    const play = sourceToOut(tl, playheadRef.current || 0)
    const dur = Number(extra.duration || 1.6)
    const t0 = Math.min(play, Math.max(0, total - dur))
    setTimeline({
      ...tl,
      fx: [...tl.fx, { id: nid('mm'), kind: 'meme', name: memeId, t0,
                       t1: Math.min(total, t0 + dur), fit: extra.fit || 'cover',
                       pos: extra.pos || 'middle', anim: extra.anim || 'pop',
                       volume: extra.volume ?? 0.8, sting: true }],
    })
    setOptions((o) => ({ ...o, sfx: true }))
    notify(`😹 ${memeId} inserted at ${t0.toFixed(1)}s${total - t0 < dur ? ' (trimmed to the end)' : ''}`, 'success')
  }, [ensureTimeline, notify, playheadRef])

  /** Place a sound (id or cue) on the audio track at the playhead. */
  const addSound = useCallback((name, cue) => {
    const tl = ensureTimeline()
    if (!tl) {
      notify('Pick a clip first', 'error')
      return
    }
    const total = outTotal(tl)
    const play = sourceToOut(tl, playheadRef.current || 0)
    const t0 = Math.min(play, Math.max(0, total - 0.4))
    setTimeline({
      ...tl,
      audio: [...tl.audio, { id: nid('s'), kind: 'sfx', name: cue ? `cue:${cue}` : name,
                             t0, t1: Math.min(total, t0 + 1.2), volume: 0.5 }],
    })
    notify(`🔔 ${cue ? `${cue} sting (${name})` : name} at ${t0.toFixed(1)}s — drag it on the audio track`, 'success')
  }, [ensureTimeline, notify, playheadRef])

  // ---------------- V7: apply an intent result to timeline + options ----------------
  const onCommand = (res) => {
    let patch = res.patch || {}
    const actions = [...(res.actions || [])]
    // naming a preset ("make it deep fried") should place it, not just set a field
    if (patch.meme_preset && !actions.some((a) => a.op === 'add_meme')) {
      actions.push({ op: 'add_meme', preset: patch.meme_preset })
    }

    // auto-init a timeline from the best clip if none exists yet
    let tl = timeline
    if (!tl && actions.some((a) => a.op !== 'analyze')) {
      const base = patch.clip_id
        ? clips.find((c) => c.id === patch.clip_id)
        : (selected || clips[0])
      if (base) {
        tl = {
          _clipId: base.id,
          video: [{ id: 'v1', start: base.start, end: base.end }],
          text: [], fx: [], audio: [],
        }
      }
    }
    if (tl) {
      tl = {
        ...tl,
        video: tl.video.map((s) => ({ ...s })),
        text: tl.text.map((e) => ({ ...e })),
        fx: tl.fx.map((e) => ({ ...e })),
        audio: tl.audio.map((e) => ({ ...e })),
      }
      const nid = (p) => p + Math.random().toString(36).slice(2, 8)
      for (const a of actions) {
        if (a.op === 'add_text') {
          tl.text.push({ id: nid('t'), text: a.text, t0: a.t0, t1: a.t1,
                         color: a.color, y: a.y || 'top', size: a.size || 1 })
        } else if (a.op === 'add_fx') {
          tl.fx.push({ id: nid('f'), kind: a.kind || 'vfx', name: a.name,
                       t0: a.t0, t1: a.t1, opacity: 0.5 })
        } else if (a.op === 'add_audio') {
          tl.audio.push({ id: nid('m'), kind: a.kind || 'music', name: a.name,
                          t0: a.t0, t1: a.t1, volume: 0.16 })
        } else if (a.op === 'trim' && tl.video.length) {
          if (a.edge === 'start') {
            tl.video[0] = { ...tl.video[0],
              start: Math.min(tl.video[0].end - 0.5, tl.video[0].start + (a.seconds || 0)) }
          } else {
            const last = tl.video.length - 1
            tl.video[last] = { ...tl.video[last],
              end: Math.max(tl.video[last].start + 0.5, tl.video[last].end - (a.seconds || 0)) }
          }
        } else if (a.op === 'split' && tl.video.length) {
          let acc = 0
          for (let i = 0; i < tl.video.length; i++) {
            const d = tl.video[i].end - tl.video[i].start
            if (a.at > acc + 0.3 && a.at < acc + d - 0.3) {
              const src = tl.video[i].start + (a.at - acc)
              tl.video.splice(i, 1, { ...tl.video[i], end: src },
                              { ...tl.video[i], id: nid('v'), start: src })
              break
            }
            acc += d
          }
        } else if (a.op === 'render') {
          setRenderSignal((s) => s + 1)
        } else if (a.op === 'analyze') {
          reanalyze()
        } else if (a.op === 'clear_memes') {
          tl.fx = tl.fx.filter((e) => e.kind !== 'meme')
        } else if (a.op === 'add_meme') {
          const preset = (assets?.meme_presets || []).find((p) => p.id === a.preset)
          const memeId = pickMemeForPreset(preset || { id: a.preset, meme: { match: [] } })
          if (!memeId) {
            notify('No meme clips installed — run the meme fetch (Meme lab) or drop MP4s in assets/memes/', 'error')
          } else {
            const spec = preset?.meme || {}
            const dur = Number(spec.duration || 1.6)
            const start = a.t0 ?? sourceToOut(tl, playheadRef.current || 0)
            const end = a.t1 ?? Math.min(outTotal(tl), start + dur)
            const pushes = []
            if (a.every) {
              for (let t = start; t + dur <= end + 0.01; t += a.every) pushes.push(t)
            } else {
              pushes.push(Math.min(start, Math.max(0, outTotal(tl) - dur)))
            }
            for (const t of pushes) {
              tl.fx.push({ id: nid('mm'), kind: 'meme', name: memeId, t0: t,
                           t1: Math.min(outTotal(tl), t + dur),
                           fit: spec.fit || 'cover', pos: spec.pos || 'middle',
                           anim: spec.anim || 'pop', volume: spec.volume ?? 0.8,
                           sting: spec.sting !== false })
            }
            const cues = preset?.sfx || ['meme']
            for (const cue of cues) {
              const when = cue === 'hook' ? 0 : cue === 'meme' || cue === 'reaction' ? pushes[0] : null
              if (when === null) continue
              tl.audio.push({ id: nid('s'), kind: 'sfx', name: `cue:${cue}`, t0: when,
                              t1: Math.min(outTotal(tl), when + 1.2), volume: 0.5 })
            }
            patch = { meme_preset: a.preset, sfx: true, ...(preset?.options || {}), ...patch }
          }
        }
      }
      setTimeline(tl)
    }

    // patch -> options (and clip switch)
    setOptions((o) => {
      const next = { ...o }
      for (const [k, v] of Object.entries(patch)) {
        if (k === 'clip_id' || v === undefined) continue
        next[k] = v
      }
      return next
    })
    if (patch.clip_id) {
      const c = clips.find((x) => x.id === patch.clip_id)
      if (c) pickClip(c)
    }
    notify(res.reply || res.message || 'Command applied',
           actions.length || Object.keys(patch).length ? 'success' : 'info')
  }

  if (!project) {
    return (
      <main className="editor">
        <div className="empty-state">
          <div className="spinner" aria-hidden="true" />
          <p>Loading project…</p>
        </div>
      </main>
    )
  }

  const stageIdx = STAGES.findIndex(([k]) => k === project.stage)
  const analyzing = running || project.status === 'analyzing'

  return (
    <main className="editor">
      <div className="editor-head">
        <button className="btn ghost" onClick={onBack}>← Projects</button>
        <h2 className="editor-title" title={project.name}>{project.name}</h2>
        <span className="chip">{fmtMMSS(project.duration)} · {project.width}×{project.height}</span>
        {analyzing && <span className="chip analyzing">Analyzing…</span>}
        {!analyzing && analysis && (
          <button className="btn tiny secondary" onClick={reanalyze} title="Run the clip finder again">
            ⟳ Re-analyze
          </button>
        )}
        <span className="spacer" />
        {analysis && (
          <span className="chip" title="Speech-to-text and clip-finding engines used">
            {analysis.stt_engine} · {analysis.engine}
          </span>
        )}
      </div>

      {analyzing && (
        <div className="card stages" aria-live="polite">
          {STAGES.map(([k, label], i) => (
            <div key={k} className={`stage ${i < stageIdx ? 'done' : ''} ${i === stageIdx ? 'active' : ''}`}>
              <span className="stage-dot">{i < stageIdx ? '✓' : i + 1}</span> {label}
            </div>
          ))}
          {project.stage_detail && <span className="muted stage-detail">{project.stage_detail}</span>}
        </div>
      )}

      {project.status === 'error' && (
        <div className="error-banner" role="alert">Analysis error: {project.error}</div>
      )}

      <div className="editor-grid">
        {/* ---------------- left: clips ---------------- */}
        <section className="panel card clips-panel">
          <div className="panel-head">
            <h3>Suggested clips</h3>
            <span className="muted">{clips.length ? `${clips.length} found` : ''}</span>
            <button className="btn tiny secondary" onClick={addManualClip}
                    title="Add a 30s clip at the playhead">+ Manual</button>
          </div>

          {clips.length === 0 && !analyzing && (
            <div className="muted pad">
              No clips yet.{analysis ? ' Re-run analysis or add a manual clip.' : ''}
            </div>
          )}

          <div className="clip-list">
            {clips.map((c, i) => (
              <ClipCard
                key={c.id}
                clip={c}
                rank={i + 1}
                active={selected?.id === c.id}
                onSelect={() => pickClip(c)}
                onDelete={c.source === 'manual' ? () => deleteClip(c) : undefined}
              />
            ))}
          </div>
        </section>

        {/* ---------------- center: player + timeline ---------------- */}
        <section className="panel card center-panel">
          <VideoPlayer
            project={project}
            selected={selected}
            seekTo={seekTo}
            onSeekHandled={() => setSeekTo(null)}
            aspect={options.aspect || activeStyle.aspect || 'crop'}
            cropX={options.crop_x ?? 0.5}
            onTimeUpdate={(t) => { setTime(t); playheadRef.current = t }}
            onSetStart={() => selected && updateClip({ start: Math.max(0, Math.min(time, selected.end - 3)) })}
            onSetEnd={() => selected && updateClip({ end: Math.min(project.duration, Math.max(time, selected.start + 3)) })}
            onNudgeEnd={(d) => selected && updateClip({ end: Math.max(selected.start + 3, Math.min(project.duration, selected.end + d)) })}
          />
          <Timeline
            project={project}
            analysis={analysis}
            selected={selected}
            time={time}
            onSelect={updateClip}
            onSeek={(t) => setSeekTo(t)}
          />

          {selected && (
            <div className="clip-nudge">
              <label htmlFor="clip-start">Start</label>
              <input id="clip-start" type="number" step="0.1" min="0" max={project.duration}
                     value={Number(selected.start.toFixed(1))}
                     onChange={(e) => updateClip({ start: Math.max(0, Math.min(+e.target.value, selected.end - 3)) })} />
              <label htmlFor="clip-end">End</label>
              <input id="clip-end" type="number" step="0.1" min="0" max={project.duration}
                     value={Number(selected.end.toFixed(1))}
                     onChange={(e) => updateClip({ end: Math.min(project.duration, Math.max(+e.target.value, selected.start + 3)) })} />
              <span className="chip">{(selected.end - selected.start).toFixed(1)}s</span>
              {selected.title && <span className="muted">{selected.title}</span>}
            </div>
          )}

          {selected && (
            <TimelineEditor timeline={timeline} setTimeline={setTimeline}
                            playheadRef={playheadRef}
                            onSeekOut={(t) => setSeekTo(outToSource(timeline, t))}
                            onToast={(m) => notify(m)}
                            assets={assets}
                            fonts={assets?.fonts} />
          )}

          <CommandBox projectId={id} onResult={onCommand} disabled={!analysis}
                      total={timeline ? outTotal(timeline) : undefined} />
        </section>

        {/* ---------------- right: properties ---------------- */}
        <section className="props">
          <div className="panel card">
            <div className="panel-head"><h3>Style</h3></div>
            <StyleGrid styles={styles.styles} value={options.style}
                       onPick={(styleId) => set({ style: styleId })} />
            {activeStyle.description && <p className="muted style-desc">{activeStyle.description}</p>}
            <StyleGen onGenerated={async (s) => {
              const fresh = await api.styles().catch(() => null)
              if (fresh) setStyles(fresh)
              set({ style: s.id })
              notify(`New style “${s.name}” created (${s.source})`, 'success')
            }} />
          </div>

          <MemeLab
            assets={assets}
            options={options}
            set={set}
            notify={notify}
            hasTimeline={!!timeline}
            onApplyPreset={applyMemePreset}
            onAddMeme={addMemeClip}
            onAddSound={addSound}
            onPacksChanged={reloadAssets}
          />

          <EffectsControls
            options={options}
            set={set}
            style={activeStyle}
            assets={assets}
            hasTranscript={!!analysis?.transcript?.length}
            onReset={() => setOptions({ ...DEFAULT_OPTIONS, style: options.style })}
          />

          <RenderPanel
            projectId={id}
            project={project}
            selected={selected}
            options={options}
            set={set}
            notify={notify}
            timeline={timeline}
            renderSignal={renderSignal}
          />
        </section>
      </div>

      {analysis?.transcript?.length > 0 && (
        <TranscriptPanel analysis={analysis} selected={selected} onSeek={(t) => setSeekTo(t)} />
      )}
    </main>
  )
}
