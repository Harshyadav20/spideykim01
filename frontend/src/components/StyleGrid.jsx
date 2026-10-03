import React from 'react'

export default function StyleGrid({ styles, value, onPick }) {
  return (
    <div className="style-grid" role="radiogroup" aria-label="Caption style">
      {(styles || []).map((s) => (
        <button
          key={s.id}
          className={`style-tile ${value === s.id ? 'active' : ''}`}
          role="radio"
          aria-checked={value === s.id}
          onClick={() => onPick(s.id)}
          title={s.description}
        >
          <span className="style-emoji" aria-hidden="true">{s.emoji}</span>
          <span>{s.name}</span>
        </button>
      ))}
    </div>
  )
}
