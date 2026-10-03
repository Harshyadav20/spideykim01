import React from 'react'

function Row({ label, children, hint }) {
  return (
    <div className="ctl-row">
      <div className="ctl-label">
        <span>{label}</span>
        {hint && <span className="ctl-hint">{hint}</span>}
      </div>
      <div className="ctl-body">{children}</div>
    </div>
  )
}

const ANIMATIONS = [
  ['pop', 'Pop'], ['highlight', 'Highlight'], ['bounce', 'Bounce'],
  ['typewriter', 'Typewriter'], ['kinetic', 'Kinetic'], ['slide', 'Slide'],
  ['wave', 'Wave'], ['punch', 'Punch'], ['neon', 'Neon'],
  ['shake', 'Shake'], ['blur', 'Blur-in'], ['none', 'Static'],
]

const VFX = [
  ['shake', '🎥 Shake'], ['glitch', '🌐 Glitch'], ['grain', '🎞️ Grain'],
  ['vignette', '🕳️ Vignette'], ['flash', '⚡ Flash'],
]

export default function EffectsControls({ options, set, style, assets, hasTranscript, onReset }) {
  const anim = options.caption_animation || style.animation || 'pop'
  const position = options.caption_position || style.position
  const zoom = options.zoom || style.zoom
  const aspect = options.aspect || style.aspect

  // null -> follow the style's recipe, [] -> nothing
  const vfxList = options.vfx == null ? (style.vfx || []) : options.vfx
  const overlay = options.overlay == null ? (style.overlay || null) : options.overlay

  return (
    <div className="panel card">
      <div className="panel-head">
        <h3>Captions &amp; effects</h3>
        {onReset && (
          <button className="btn ghost tiny" onClick={onReset} title="Back to this style's defaults">
            Reset
          </button>
        )}
      </div>

      <Row label="Captions" hint={hasTranscript ? undefined : 'needs transcript'}>
        <button className={`switch ${options.captions ? 'on' : ''}`}
                aria-pressed={options.captions}
                onClick={() => set({ captions: !options.captions })}>
          {options.captions ? 'ON' : 'OFF'}
        </button>
      </Row>

      <Row label="Animation" hint={options.caption_animation ? undefined : `style: ${style.animation || 'pop'}`}>
        <div className="seg wrap">
          {ANIMATIONS.map(([id, label]) => (
            <button key={id} className={anim === id ? 'active' : ''}
                    aria-pressed={anim === id}
                    onClick={() => set({ caption_animation: id })}>{label}</button>
          ))}
        </div>
      </Row>

      <Row label={`Font size — ${Math.round(options.font_scale * 100)}%`}>
        <input type="range" min="0.6" max="1.6" step="0.05" value={options.font_scale}
               aria-label="Caption font size"
               onChange={(e) => set({ font_scale: +e.target.value })} />
      </Row>

      <Row label="Position" hint={options.caption_position ? undefined : `style: ${style.position}`}>
        <div className="seg">
          {['bottom', 'middle', 'top'].map((p) => (
            <button key={p} className={position === p ? 'active' : ''}
                    aria-pressed={position === p}
                    onClick={() => set({ caption_position: p })}>{p}</button>
          ))}
        </div>
      </Row>

      <Row label="Uppercase" hint={options.uppercase == null ? `style: ${style.uppercase ? 'yes' : 'no'}` : undefined}>
        <div className="seg">
          {[['', 'style'], ['yes', 'YES'], ['no', 'lower']].map(([v, l]) => (
            <button key={l}
                    className={`${v === '' ? options.uppercase == null : options.uppercase === (v === 'yes') ? 'active' : ''}`}
                    onClick={() => set({ uppercase: v === '' ? null : v === 'yes' })}>{l}</button>
          ))}
        </div>
      </Row>

      <div className="divider" />

      <Row label="Zoom" hint={options.zoom ? undefined : `style: ${style.zoom}`}>
        <div className="seg">
          {['punch', 'slow', 'none'].map((z) => (
            <button key={z} className={zoom === z ? 'active' : ''}
                    aria-pressed={zoom === z}
                    onClick={() => set({ zoom: z })}>{z}</button>
          ))}
        </div>
      </Row>

      <Row label="9:16 framing" hint={options.aspect ? undefined : `style: ${style.aspect}`}>
        <div className="seg">
          {[['crop', 'crop'], ['blur', 'blur bars'], ['fit', 'fit']].map(([v, l]) => (
            <button key={v} className={aspect === v ? 'active' : ''}
                    aria-pressed={aspect === v}
                    onClick={() => set({ aspect: v })}>{l}</button>
          ))}
        </div>
      </Row>

      {aspect === 'crop' && (
        <Row label={`Crop focus X — ${Math.round((options.crop_x ?? 0.5) * 100)}%`}>
          <input type="range" min="0" max="1" step="0.05" value={options.crop_x ?? 0.5}
                 aria-label="Horizontal crop focus"
                 onChange={(e) => set({ crop_x: +e.target.value })} />
        </Row>
      )}

      <Row label="Remove silence" hint="cuts dead air, retimes captions">
        <button className={`switch ${options.remove_silence ? 'on' : ''}`}
                aria-pressed={options.remove_silence}
                onClick={() => set({ remove_silence: !options.remove_silence })}>
          {options.remove_silence ? 'ON' : 'OFF'}
        </button>
      </Row>

      <div className="divider" />

      <Row label="VFX" hint={options.vfx == null ? `style: ${(style.vfx || []).join(', ') || 'none'}` : undefined}>
        <div className="vfx-chips">
          {VFX.map(([id, label]) => {
            const on = vfxList.includes(id)
            return (
              <button key={id} className={`vfx-chip ${on ? 'on' : ''}`}
                      aria-pressed={on}
                      onClick={() => {
                        const base = [...vfxList]
                        set({ vfx: on ? base.filter((v) => v !== id) : [...base, id] })
                      }}>{label}</button>
            )
          })}
        </div>
      </Row>

      <Row label={`VFX intensity — ${Math.round((options.vfx_intensity ?? 0.5) * 100)}%`}>
        <input type="range" min="0.1" max="1" step="0.05" value={options.vfx_intensity ?? 0.5}
               aria-label="VFX intensity"
               onChange={(e) => set({ vfx_intensity: +e.target.value })} />
      </Row>

      <Row label="Overlay" hint={options.overlay == null ? `style: ${style.overlay || 'none'}` : undefined}>
        <select value={options.overlay ?? ''} aria-label="Overlay loop"
                onChange={(e) => set({ overlay: e.target.value === '' ? null : e.target.value })}>
          <option value="">style default</option>
          <option value="none">none</option>
          {(assets?.overlays || []).filter((o) => o.id !== 'none').map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}{o.kind === 'stock' ? ' (stock)' : ''}
            </option>
          ))}
        </select>
      </Row>

      {overlay && overlay !== 'none' && (
        <>
          <Row label={`Overlay opacity — ${Math.round((options.overlay_opacity ?? 0.5) * 100)}%`}>
            <input type="range" min="0.1" max="1" step="0.05" value={options.overlay_opacity ?? 0.5}
                   aria-label="Overlay opacity"
                   onChange={(e) => set({ overlay_opacity: +e.target.value })} />
          </Row>
          <Row label="Blend">
            <div className="seg">
              {['screen', 'lighten', 'overlay', 'softlight'].map((m) => (
                <button key={m} className={(options.overlay_mode || 'screen') === m ? 'active' : ''}
                        aria-pressed={(options.overlay_mode || 'screen') === m}
                        onClick={() => set({ overlay_mode: m })}>{m}</button>
              ))}
            </div>
          </Row>
        </>
      )}

      <div className="divider" />

      <Row label="Music" hint={options.music == null ? `style: ${style.music || 'none'}` : undefined}>
        <select value={options.music ?? ''} aria-label="Background music"
                onChange={(e) => set({ music: e.target.value === '' ? null : e.target.value })}>
          <option value="">style default</option>
          {(assets?.music || []).map((m) => (
            <option key={m.id} value={m.id}>{m.name}</option>
          ))}
        </select>
      </Row>

      <Row label={`Music volume — ${Math.round((options.music_volume ?? 0.16) * 100)}%`}>
        <input type="range" min="0" max="0.5" step="0.02" value={options.music_volume ?? 0.16}
               aria-label="Music volume"
               onChange={(e) => set({ music_volume: +e.target.value })} />
      </Row>

      <Row label="Auto SFX" hint="hook sting · zoom punches · meme cut-ins">
        <button className={`switch ${options.sfx ? 'on' : ''}`}
                aria-pressed={options.sfx}
                onClick={() => set({ sfx: !options.sfx })}>
          {options.sfx ? 'ON' : 'OFF'}
        </button>
      </Row>

      <Row label="SFX pack"
           hint={options.sfx_pack && options.sfx_pack !== 'auto'
             ? `pinned: ${options.sfx_pack}`
             : 'auto — VFX pack first, built-ins as backup'}>
        <div className="seg">
          {['auto', 'vfx', 'builtin', 'none'].map((v) => (
            <button key={v} className={(options.sfx_pack || 'auto') === v ? 'active' : ''}
                    aria-pressed={(options.sfx_pack || 'auto') === v}
                    title={v === 'vfx' ? 'Pixabay / offline VFX pack (assets/sfx/vfx)'
                      : v === 'builtin' ? 'the bundled whoosh + pop'
                      : v === 'none' ? 'no sound effects at all' : 'whatever is installed'}
                    onClick={() => set({ sfx_pack: v })}>{v}</button>
          ))}
        </div>
      </Row>

      {(assets?.sfx?.length || 0) > 0 && (
        <Row label="Cue resolution" hint="what the auto-SFX will actually play">
          <div className="vfx-chips">
            {(assets?.sfx_cues || []).map((c) => (
              <span key={c.id} className={`cue-chip ${c.resolved ? 'ok' : 'missing'}`}
                    title={c.description || c.name}>
                {c.id} → {c.resolved || 'none'}
              </span>
            ))}
          </div>
        </Row>
      )}

      <Row label="Watermark text">
        <input type="text" className="text-input" placeholder="@yourhandle"
               aria-label="Watermark text"
               value={options.watermark} onChange={(e) => set({ watermark: e.target.value })} />
      </Row>
    </div>
  )
}
