import React, { useState, useRef, useEffect } from 'react';

const ACCEPTED_EXTS = ['.mp3', '.wav', '.m4a', '.ogg', '.txt', '.pdf'];
const ACCEPT_ATTR = ACCEPTED_EXTS.join(',');
const FORMAT_CHIPS = ['MP3', 'WAV', 'M4A', 'OGG', 'PDF', 'TXT'];

function isAccepted(file: File): boolean {
  const name = file.name.toLowerCase();
  return ACCEPTED_EXTS.some(ext => name.endsWith(ext));
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

interface FileUploaderProps {
  onUploadSuccess: (incidentId: string) => void;
}

export const FileUploader: React.FC<FileUploaderProps> = ({ onUploadSuccess }) => {
  const [files, setFiles] = useState<File[]>([]);
  const [rejected, setRejected] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [nRuns, setNRuns] = useState(3);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Carica n_runs_default dalle impostazioni al mount
  useEffect(() => {
    fetch('http://localhost:8000/api/settings')
      .then(r => r.ok ? r.json() : null)
      .then(s => {
        if (s && typeof s.n_runs_default === 'number') {
          setNRuns(Math.max(1, Math.min(9, s.n_runs_default)));
        }
      })
      .catch(() => { /* fallback al default 3 */ });
  }, []);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files) return;
    const all = Array.from(e.target.files);
    const ok = all.filter(isAccepted);
    const ko = all.filter(f => !isAccepted(f)).map(f => f.name);
    setFiles(ok);
    setRejected(ko);
  };

  const handleUpload = async () => {
    if (files.length === 0) return;
    setUploading(true);
    const formData = new FormData();
    files.forEach((file) => formData.append('files', file));
    formData.append('n_runs', nRuns.toString());
    try {
      const response = await fetch('http://localhost:8000/api/incidents/upload', {
        method: 'POST', body: formData,
      });
      if (!response.ok) {
        const e = await response.json().catch(() => ({} as any));
        throw new Error(e?.detail || `HTTP ${response.status}`);
      }
      const data = await response.json();
      onUploadSuccess(data.incident_id);
      setFiles([]);
      setRejected([]);
    } catch (error: any) {
      console.error('Upload failed:', error);
      alert(`Errore durante il caricamento: ${error?.message || 'errore sconosciuto'}`);
    } finally {
      setUploading(false);
    }
  };

  const stampLabel = `Registrazione · BOZZA-${new Date().toISOString().slice(0, 10).replace(/-/g, '')}`;

  return (
    <div className="grid-2">
      <div className="stack">
        <div className="dropzone" onClick={() => fileInputRef.current?.click()}>
          <span className="corner c-tr" />
          <span className="corner c-bl" />
          <input
            type="file" multiple accept={ACCEPT_ATTR} style={{ display: 'none' }}
            ref={fileInputRef} onChange={handleFileChange}
          />
          <div className="dz-stamp">{stampLabel}</div>
          <div className="dz-title">Trascina qui i materiali</div>
          <div className="dz-sub">Audio delle testimonianze, verbali, note in chiaro.</div>
          <div className="dz-formats">
            {FORMAT_CHIPS.map(x => <span key={x} className="chip">{x}</span>)}
          </div>
        </div>

        {rejected.length > 0 && (
          <div style={{ borderTop: '1px solid var(--gold)', borderBottom: '1px solid var(--gold)', padding: '10px 12px', color: 'var(--gold)' }}>
            <div className="smallcap" style={{ marginBottom: 4 }}>Scartati · estensione non supportata</div>
            <ul className="mono" style={{ margin: 0, paddingLeft: 18 }}>
              {rejected.map(n => <li key={n}>{n}</li>)}
            </ul>
          </div>
        )}

        {files.length > 0 && (
          <div className="file-list">
            <div className="between" style={{ padding: '14px 4px', borderBottom: '2px solid var(--ink)' }}>
              <div className="smallcap">Allegati · {files.length}</div>
              <div className="mono muted">{files.reduce((s, f) => s + f.size, 0) > 0 ? fmtSize(files.reduce((s, f) => s + f.size, 0)) : '—'}</div>
            </div>
            {files.map((f, i) => (
              <div className="file-row" key={f.name}>
                <span className="ix">{String(i + 1).padStart(2, '0')}</span>
                <span className="name">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <polyline points="14 2 14 8 20 8" />
                  </svg>
                  {f.name}
                </span>
                <span className="meta">{fmtSize(f.size)}</span>
                <span className="dur">—</span>
                <button className="x" onClick={(e) => { e.stopPropagation(); setFiles(files.filter((_, j) => j !== i)); }}>×</button>
              </div>
            ))}
          </div>
        )}

        <div className="row" style={{ paddingTop: 18, justifyContent: 'space-between' }}>
          <div className="row" style={{ gap: 18 }}>
            <div className="field" style={{ minWidth: 180 }}>
              <label>esecuzioni consenso</label>
              <div className="stepper">
                <button onClick={() => setNRuns(Math.max(1, nRuns - 1))}>−</button>
                <span className="v">{String(nRuns).padStart(2, '0')}</span>
                <button onClick={() => setNRuns(Math.min(9, nRuns + 1))}>+</button>
              </div>
            </div>
          </div>
          <div className="row">
            <button className="btn" onClick={handleUpload} disabled={uploading || files.length === 0}>
              {uploading
                ? <><span className="spinner" /> Elaborazione…</>
                : <>Avvia elaborazione <span className="arrow">→</span></>}
            </button>
          </div>
        </div>
      </div>

      <aside style={{ position: 'sticky', top: 80 }}>
        <div className="aside">
          <div className="smallcap" style={{ color: 'var(--accent)', marginBottom: 6 }}>Promemoria</div>
          <h4>Cosa accade dopo il caricamento</h4>
          <p>Le tracce vengono trascritte, riconciliate e tradotte in un report strutturato. Nessuna informazione viene scartata: ogni divergenza viene annotata a margine.</p>
          <ol>
            <li>Trascrizione e diarizzazione delle voci</li>
            <li>Estrazione entità — persone, linee, macchinari</li>
            <li>{nRuns} esecuzioni indipendenti, consenso a maggioranza</li>
            <li>Generazione report &middot; revisione manuale</li>
          </ol>
          <p style={{ marginTop: 10, color: 'var(--ink-soft)', fontSize: 12 }}>
            <b>Tempo previsto:</b> 3–10 minuti per testimonianza. L'evento
            resta in stato <span className="mono">processing</span> fino al termine;
            non chiudere la pagina o il backend durante l'elaborazione.
          </p>
        </div>
      </aside>
    </div>
  );
};
