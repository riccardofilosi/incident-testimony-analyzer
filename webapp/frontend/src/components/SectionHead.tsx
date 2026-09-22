import React from 'react'

interface SectionHeadProps {
  roman: string
  kicker: string
  title: string
  deck?: string
  meta: [string, string][]
}

export const SectionHead: React.FC<SectionHeadProps> = ({ roman, kicker, title, deck, meta }) => (
  <div className="sec-head">
    <div className="sec-num"><span className="roman">{roman}</span>—</div>
    <div className="sec-title">
      <div className="kicker">{kicker}</div>
      <h1>{title}</h1>
      {deck && <div className="deck">{deck}</div>}
    </div>
    <div className="sec-meta">
      {meta.map(([k, v], i) => (
        <div key={i}><span className="k">{k}</span>&nbsp;&nbsp;{v}</div>
      ))}
    </div>
  </div>
)

interface StatusProps { s: string }

export const Status: React.FC<StatusProps> = ({ s }) => {
  const map: Record<string, [string, string]> = {
    APPROVED:   ['s-approved',   'approvato'],
    PENDING:    ['s-pending',    'in attesa'],
    PROCESSING: ['s-processing', 'in elaborazione'],
    DRAFT:      ['s-draft',      'bozza'],
    ERROR:      ['s-error',      'errore'],
    REJECTED:   ['s-error',      'rifiutato'],
  }
  const [cls, label] = map[s] || ['s-draft', (s || 'draft').toLowerCase()]
  return (
    <span className={`status ${cls}`}>
      <span className="dot" />{label}
    </span>
  )
}
