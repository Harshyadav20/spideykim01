import React, { useRef, useState } from 'react'
import { uploadFile, fmtBytes } from '../services/api.js'

const FORMATS = ['mp4', 'mov', 'mkv', 'webm', 'm4v', 'mp3', 'wav', 'm4a']
const OK_EXT = /\.(mp4|mov|mkv|webm|m4v|avi|mp3|m4a|wav|aac|flac)$/i

export default function VideoUploader({ onUploaded, maxUploadMb = 2048, notify }) {
  const [drag, setDrag] = useState(false)
  const [progress, setProgress] = useState(null)
  const [error, setError] = useState('')
  const inputRef = useRef(null)

  const send = async (file) => {
    setError('')
    if (!file) return
    if (!OK_EXT.test(file.name)) {
      setError('Please choose a video or audio file (mp4, mov, mkv, webm, mp3…)')
      return
    }
    if (file.size > maxUploadMb * 1024 * 1024) {
      setError(`That file is ${fmtBytes(file.size)} — the limit here is ${maxUploadMb} MB.`)
      return
    }
    setProgress(0)
    try {
      const p = await uploadFile(file, setProgress)
      notify?.(`“${file.name}” uploaded — analysis starting`, 'success')
      onUploaded(p)
    } catch (e) {
      setError(e.message)
      setProgress(null)
    }
  }

  return (
    <div
      className={`uploader ${drag ? 'drag' : ''} ${progress !== null ? 'busy' : ''}`}
      role="button"
      tabIndex={0}
      aria-label="Upload a video or audio file"
      onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); send(e.dataTransfer.files?.[0]) }}
      onClick={() => progress === null && inputRef.current?.click()}
      onKeyDown={(e) => {
        if ((e.key === 'Enter' || e.key === ' ') && progress === null) {
          e.preventDefault()
          inputRef.current?.click()
        }
      }}
    >
      <input ref={inputRef} type="file" accept="video/*,audio/*" hidden
             onChange={(e) => send(e.target.files?.[0])} />

      {progress === null ? (
        <>
          <div className="uploader-icon" aria-hidden="true">📥</div>
          <div className="uploader-title">Drop your long video here</div>
          <div className="uploader-sub">
            or click to browse — up to {maxUploadMb} MB
          </div>
          <div className="uploader-formats" aria-hidden="true">
            {FORMATS.map((f) => <span key={f} className="chip">{f}</span>)}
          </div>
        </>
      ) : (
        <div className="uploader-progress" aria-live="polite">
          <div className="spinner" aria-hidden="true" />
          <div className="uploader-title">Uploading… {Math.round(progress * 100)}%</div>
          <div className="bar" style={{ width: '100%' }}>
            <div className="bar-fill" style={{ width: `${progress * 100}%` }} />
          </div>
          <div className="uploader-sub">Keep this tab open while the file transfers.</div>
        </div>
      )}

      {error && <div className="error-banner small" role="alert">{error}</div>}
    </div>
  )
}
