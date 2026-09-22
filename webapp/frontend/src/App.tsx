import { useState, useEffect, useRef } from 'react'
import { FileUploader } from './components/FileUploader'
import { IncidentDetail } from './components/IncidentDetail'
import { GlobalChat } from './components/GlobalChat'
import { Settings } from './components/Settings'
import { SectionHead, Status } from './components/SectionHead'
import { SplashOverlay } from './components/splash/SplashOverlay'

const SPLASH_KEY = 'aqr_splash_seen_v3'
const initialSplashActive = (() => {
  if (typeof window === 'undefined') return false
  const replay = new URLSearchParams(window.location.search).has('replay')
  if (replay) return true
  try { return window.localStorage.getItem(SPLASH_KEY) !== '1' } catch { return true }
})()

type Tab = 'upload' | 'chat' | 'archive' | 'settings'

const NAV: { id: Tab; num: string; tag: string; label: string }[] = [
  { id: 'upload',   num: 'I',   tag: '01', label: 'Nuovo evento' },
  { id: 'chat',     num: 'II',  tag: '02', label: 'Interrogazione database' },
  { id: 'archive',  num: 'III', tag: '03', label: 'Archivio eventi' },
  { id: 'settings', num: 'IV',  tag: '04', label: 'Impostazioni' },
]

function App() {
  const [splashActive, setSplashActive] = useState<boolean>(initialSplashActive)
  const [activeTab, setActiveTab] = useState<Tab>('upload')
  const [incidents, setIncidents] = useState<any[]>([])
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(null)
  const [archFilter, setArchFilter] = useState<string>('TUTTI')
  const [archSearch, setArchSearch] = useState<string>('')
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null)
  const [poolStatus, setPoolStatus] = useState<any>(null)
  const [toast, setToast] = useState<string | null>(null)
  const prevPoolRef = useRef<any>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    let cancelled = false
    const ping = async () => {
      try {
        const ctrl = new AbortController()
        const timeout = setTimeout(() => ctrl.abort(), 3000)
        const res = await fetch('http://localhost:8000/api/health', { signal: ctrl.signal })
        clearTimeout(timeout)
        if (!cancelled) setBackendOnline(res.ok)
      } catch {
        if (!cancelled) setBackendOnline(false)
      }
    }
    ping()
    const id = setInterval(ping, 10000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  useEffect(() => {
    let cancelled = false
    const fetchPool = async () => {
      try {
        const res = await fetch('http://localhost:8000/api/pool/status')
        if (!res.ok) return
        const data = await res.json()
        if (cancelled) return
        // Notifica toast quando una chiave entra in cooldown rispetto al poll precedente
        const prev = prevPoolRef.current
        if (prev && data?.keys?.length) {
          const newlyDown = data.keys.filter((k: any, i: number) => {
            const pk = prev.keys?.[i]
            return pk && pk.available && !k.available
          })
          if (newlyDown.length > 0) {
            const idx = newlyDown[0].idx + 1
            const remaining = data.available
            const total = data.total
            setToast(`Chiave #${idx} esaurita. Continuo con ${remaining}/${total} chiavi attive.`)
            setTimeout(() => setToast(null), 6000)
          }
        }
        prevPoolRef.current = data
        setPoolStatus(data)
        try { (window as any).__poolSize = data?.available || data?.total || 1 } catch { /* ignore */ }
      } catch {
        // silent
      }
    }
    fetchPool()
    const id = setInterval(fetchPool, 8000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  const fetchIncidents = async () => {
    try {
      const res = await fetch('http://localhost:8000/api/incidents')
      const data = await res.json()
      setIncidents(data)
      const hasProcessing = data.some((i: any) => i.status === 'PROCESSING')
      if (hasProcessing && !selectedIncidentId && !pollRef.current) {
        pollRef.current = setInterval(fetchIncidents, 5000)
      } else if ((!hasProcessing || selectedIncidentId) && pollRef.current) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
    } catch (err) {
      console.error('Failed to fetch incidents', err)
    }
  }

  useEffect(() => {
    fetchIncidents()
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [selectedIncidentId])

  const processingCount = incidents.filter(i => i.status === 'PROCESSING').length
  const activeItem = NAV.find(n => n.id === activeTab) || NAV[0]
  const today = new Date().toLocaleDateString('it-IT', { weekday: 'long', day: '2-digit', month: 'long', year: 'numeric' })

  const filteredArchive = incidents.filter(inc => {
    if (archFilter !== 'TUTTI' && inc.status !== archFilter) return false
    if (archSearch.trim()) {
      const q = archSearch.toLowerCase()
      // Cerca solo per ID (incident_id o event_id)
      const hay = `${inc.incident_id} ${inc.event_id || ''}`.toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })

  const handleDeleteIncident = async (e: React.MouseEvent, incidentId: string) => {
    e.stopPropagation()
    const ok = window.confirm(`Eliminare definitivamente l'evento ${incidentId}?\n\nQuesta operazione cancella la cartella locale e tutti i file associati.`)
    if (!ok) return
    try {
      const res = await fetch(`http://localhost:8000/api/incidents/${incidentId}`, { method: 'DELETE' })
      if (!res.ok) {
        const err = await res.json().catch(() => ({} as any))
        alert(`Errore eliminazione: ${err?.detail || res.status}`)
        return
      }
      fetchIncidents()
    } catch (err: any) {
      alert(`Errore di rete: ${err?.message || 'verifica backend'}`)
    }
  }

  return (
    <>
    {splashActive && (
      <>
        {/* Hide the real crest-title while splash is active so its wordmark
            visually "becomes" the crest when it lands. */}
        <style>{`.crest-title { opacity: 0 !important; }`}</style>
        <SplashOverlay onComplete={() => {
          setSplashActive(false)
          try { window.localStorage.setItem(SPLASH_KEY, '1') } catch { /* ignore */ }
        }} />
      </>
    )}
    {toast && (
      <div style={{
        position: 'fixed', top: 18, right: 18, zIndex: 9999,
        background: 'var(--paper)', border: '1px solid var(--gold)',
        padding: '12px 18px', color: 'var(--ink)', fontFamily: 'DM Sans, sans-serif',
        fontSize: 13, maxWidth: 360, boxShadow: '0 4px 16px rgba(0,0,0,0.12)',
      }}>
        <div className="smallcap" style={{ color: 'var(--gold)', marginBottom: 4 }}>Quota chiave</div>
        {toast}
      </div>
    )}
    <div className="app">
      {/* SIDEBAR */}
      <aside className="rail">
        <div className="crest">
          <div className="crest-title">
            <span className="crest-wordmark-anchor">Acqua&nbsp;Riva</span>{' '}
            <span className="serif-it" style={{ fontSize: 14, color: 'var(--ink-soft)', fontWeight: 400 }}>s.p.a.</span>
            <br />
            <span className="serif-it" style={{ fontSize: 13, color: 'var(--ink-soft)' }}>reportistica sicurezza</span>
          </div>
          <div className="crest-sub">Stab. di Frosinone</div>
        </div>

        <div className="rail-section">
          <div className="rail-h">Atti</div>
          <nav>
            {NAV.map(item => {
              const isActive = activeTab === item.id && !selectedIncidentId
              const showBadge = item.id === 'archive' && processingCount > 0
              return (
                <button key={item.id}
                  className={'nav-item' + (isActive ? ' active' : '')}
                  onClick={() => {
                    setActiveTab(item.id)
                    setSelectedIncidentId(null)
                    if (item.id === 'archive') fetchIncidents()
                  }}>
                  <span className="num-tag">{item.tag}</span>
                  <span>{item.label}</span>
                  {showBadge
                    ? <span className="badge" title={`${processingCount} in elaborazione`}>{processingCount}</span>
                    : <span style={{ color: 'var(--ink-mute)', fontSize: 12 }}>›</span>}
                </button>
              )
            })}
          </nav>
        </div>

        <div className="rail-foot">
          <div className="seal">
            <span className={'dot' + (backendOnline === false ? ' offline' : '')} />
            backend &middot; {backendOnline === false ? 'offline' : (backendOnline === null ? 'connessione…' : 'online')}
          </div>
          {poolStatus && poolStatus.configured && (
            <div className="mono" style={{ fontSize: 10, letterSpacing: '0.08em', lineHeight: 1.7, marginTop: 6,
                                            color: poolStatus.available === 0 ? 'var(--danger)' :
                                                   poolStatus.available < poolStatus.total ? 'var(--gold)' :
                                                   'var(--green)' }}
                 title={poolStatus.keys?.map((k: any) => `#${k.idx+1} ${k.fingerprint} ${k.available ? 'OK' : `cooldown ${k.cooldown_seconds_left}s`}`).join('\n')}>
              ● {poolStatus.available}/{poolStatus.total} chiavi · {poolStatus.model}
            </div>
          )}
          {poolStatus && !poolStatus.configured && (
            <div className="mono" style={{ fontSize: 10, color: 'var(--danger)', marginTop: 6 }}>
              ● nessuna chiave configurata
            </div>
          )}
          <div className="mono" style={{ fontSize: 10, letterSpacing: '0.08em', lineHeight: 1.7 }}>
            vault · LLM sicurezza
          </div>
          <a href="obsidian://open?vault=LLM sicurezza"
             className="btn ghost"
             style={{ marginTop: 14, width: '100%', justifyContent: 'center', padding: '8px 12px', fontSize: 11 }}>
            Apri vault Obsidian <span className="arrow">→</span>
          </a>
        </div>
      </aside>

      {/* MAIN */}
      <main className="main">
        <div className="masthead">
          <div className="l">Acqua Riva &middot; Sez. {activeItem.num}</div>
          <div className="c">Sistema di reportistica sugli eventi critici di stabilimento</div>
          <div className="r">{today}</div>
        </div>
        <div className="double-rule" />

        {selectedIncidentId ? (
          <IncidentDetail
            incidentId={selectedIncidentId}
            onBack={() => { setSelectedIncidentId(null); fetchIncidents() }}
          />
        ) : (
          <>
            {activeTab === 'upload' && (
              <div className="frame">
                <SectionHead
                  roman="I" kicker="Sezione  /  Caricamento"
                  title="Nuovo evento critico"
                  deck="Audio, PDF o note in chiaro: l'estrazione costruisce un'ontologia tabellare con N esecuzioni indipendenti per consenso."
                  meta={[
                    ['DATA', new Date().toLocaleDateString('it-IT', { day: '2-digit', month: 'long', year: 'numeric' })],
                    ['STAB.', 'Frosinone'],
                  ]}
                />
                <FileUploader onUploadSuccess={(id) => {
                  setSelectedIncidentId(id)
                  setActiveTab('archive')
                  fetchIncidents()
                }} />
              </div>
            )}

            {activeTab === 'chat' && <GlobalChat />}
            {activeTab === 'settings' && <Settings />}

            {activeTab === 'archive' && (
              <div className="frame">
                <SectionHead
                  roman="III" kicker="Sezione  /  Archivio"
                  title="Archivio eventi critici"
                  deck="Cronologia degli eventi registrati. Documentazione integrale nel vault Obsidian."
                  meta={[
                    ['TOTALE', String(incidents.length)],
                    ['APPROVATI', String(incidents.filter(r => r.status === 'APPROVED').length)],
                    ['IN ATTESA', String(incidents.filter(r => r.status !== 'APPROVED').length)],
                  ]}
                />

                <div className="between" style={{ marginBottom: 18 }}>
                  <div className="row">
                    {['TUTTI', 'APPROVED', 'PENDING', 'PROCESSING'].map(f => (
                      <button key={f}
                        onClick={() => setArchFilter(f)}
                        className="smallcap"
                        style={{
                          background: 'transparent', border: 0, cursor: 'pointer',
                          padding: '6px 0',
                          borderBottom: archFilter === f ? '2px solid var(--accent)' : '2px solid transparent',
                          color: archFilter === f ? 'var(--accent)' : 'var(--ink-soft)',
                          fontFamily: 'DM Sans', fontWeight: 600,
                        }}>
                        {f === 'TUTTI' ? 'tutti' : f.toLowerCase()}
                      </button>
                    ))}
                  </div>
                  <div className="row">
                    <span className="mono muted">ID:</span>
                    <input className="input" style={{ maxWidth: 240, padding: '6px 10px', fontFamily: 'JetBrains Mono, monospace' }}
                      placeholder="INC-YYYYMMDD-NNN"
                      value={archSearch}
                      onChange={e => setArchSearch(e.target.value)} />
                    <button className="btn ghost" style={{ padding: '8px 14px', fontSize: 11 }} onClick={fetchIncidents}>
                      aggiorna
                    </button>
                  </div>
                </div>

                {filteredArchive.length === 0 ? (
                  <div style={{ padding: '60px 0', textAlign: 'center', color: 'var(--ink-mute)' }}
                       className="serif-it">
                    Nessun evento {archFilter !== 'TUTTI' ? `nello stato ${archFilter.toLowerCase()}` : 'registrato'}.
                  </div>
                ) : (
                  <table className="arch">
                    <thead>
                      <tr>
                        <th style={{ width: 36 }}>n°</th>
                        <th>evento</th>
                        <th style={{ width: 220 }}>cronologia</th>
                        <th style={{ width: 130, textAlign: 'right' }}>stato</th>
                        <th style={{ width: 36 }} aria-label="azioni" />
                      </tr>
                    </thead>
                    <tbody>
                      {filteredArchive.map((inc, i) => {
                        const dt = inc.created_at
                          ? new Date(inc.created_at).toLocaleDateString('it-IT', { day: '2-digit', month: 'short', year: 'numeric' })
                          : '—'
                        return (
                          <tr key={inc.incident_id} onClick={() => setSelectedIncidentId(inc.incident_id)}>
                            <td className="arch-num">{String(filteredArchive.length - i).padStart(3, '0')}</td>
                            <td>
                              <div className="arch-id">{inc.event_id || inc.incident_id}</div>
                              {inc.event_id && (
                                <div className="mono" style={{ color: 'var(--ink-mute)', marginTop: 6 }}>{inc.incident_id}</div>
                              )}
                            </td>
                            <td className="arch-meta">
                              <div><b>{dt}</b></div>
                              {inc.operator && <div>{inc.operator}</div>}
                              {inc.input_files?.length > 0 && <div>{inc.input_files.length} allegati</div>}
                            </td>
                            <td style={{ textAlign: 'right' }}>
                              <Status s={inc.status} />
                              <div className="mono" style={{ color: 'var(--ink-mute)', marginTop: 10 }}>→</div>
                            </td>
                            <td style={{ textAlign: 'right', verticalAlign: 'top', paddingTop: 18 }}>
                              <button
                                onClick={(e) => handleDeleteIncident(e, inc.incident_id)}
                                title={`Elimina ${inc.incident_id}`}
                                style={{
                                  background: 'transparent', border: 0, cursor: 'pointer',
                                  color: 'var(--ink-mute)', fontSize: 18, padding: '0 6px',
                                  lineHeight: 1, transition: 'color 0.15s',
                                }}
                                onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--danger)')}
                                onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--ink-mute)')}>
                                ×
                              </button>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            )}

          </>
        )}
      </main>
    </div>
    </>
  )
}

export default App
