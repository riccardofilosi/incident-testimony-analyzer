import React, { useState, useEffect } from 'react';
import { SectionHead } from './SectionHead';

interface Settings {
  api_keys?: string[];
  model?: string;
  n_runs_default?: number;
}

export const Settings: React.FC = () => {
  const [settings, setSettings] = useState<Settings>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'ok' | 'err'>('idle');
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showApiKey, setShowApiKey] = useState(false);
  const [stats, setStats] = useState<any>(null);
  const [resetConfirm, setResetConfirm] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [theme, setTheme] = useState<'dark' | 'light'>(() =>
    typeof document !== 'undefined' && document.body.classList.contains('light') ? 'light' : 'dark'
  );

  const applyTheme = (next: 'dark' | 'light') => {
    setTheme(next);
    if (next === 'light') {
      document.body.classList.add('light');
      document.body.classList.remove('dark');
    } else {
      document.body.classList.add('dark');
      document.body.classList.remove('light');
    }
    try { localStorage.setItem('aqr_theme', next); } catch { /* ignore */ }
  };

  const loadAll = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [s, st] = await Promise.all([
        fetch('http://localhost:8000/api/settings').then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); }),
        fetch('http://localhost:8000/api/stats').then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); }),
      ]);
      // Backend ora risponde con api_keys (lista). Manteniamo fallback su api_key per safety.
      const keys: string[] = Array.isArray(s.api_keys)
        ? s.api_keys.filter((k: string) => typeof k === 'string')
        : (s.api_key ? [s.api_key] : []);
      setSettings({
        api_keys: keys,
        model: s.model || 'gemini-2.5-flash',
        n_runs_default: s.n_runs_default ?? 3,
      });
      setStats(st);
    } catch (err: any) {
      setLoadError(`Impossibile contattare il backend: ${err?.message || 'errore'}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadAll(); }, []);

  const handleSave = async () => {
    setSaving(true);
    setSaveStatus('idle');
    setSaveError(null);
    try {
      const cleaned = {
        ...settings,
        api_keys: (settings.api_keys || []).map(k => k.trim()).filter(k => k.length > 0),
      };
      const res = await fetch('http://localhost:8000/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cleaned),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSaveStatus('ok');
      setTimeout(() => setSaveStatus('idle'), 3000);
    } catch (err: any) {
      setSaveStatus('err');
      setSaveError(err?.message || 'errore');
    } finally {
      setSaving(false);
    }
  };

  const MAX_API_KEYS = 5;
  const addKey = () => setSettings(s => {
    const cur = s.api_keys || [];
    if (cur.length >= MAX_API_KEYS) return s;
    return { ...s, api_keys: [...cur, ''] };
  });
  const removeKey = (idx: number) => setSettings(s => ({
    ...s,
    api_keys: (s.api_keys || []).filter((_, i) => i !== idx),
  }));
  const updateKey = (idx: number, value: string) => setSettings(s => ({
    ...s,
    api_keys: (s.api_keys || []).map((k, i) => (i === idx ? value : k)),
  }));

  const handleReset = async () => {
    setResetting(true);
    try {
      const res = await fetch('http://localhost:8000/api/settings/reset_wiki', { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setResetConfirm(false);
      const st = await fetch('http://localhost:8000/api/stats').then(r => r.json());
      setStats(st);
      alert('Wiki azzerata. Gli archivi locali degli incidenti sono preservati.');
    } catch (err: any) {
      alert(`Errore reset: ${err?.message || 'errore'}`);
    } finally {
      setResetting(false);
    }
  };

  if (loading) return (
    <div className="frame" style={{ padding: '80px 56px', textAlign: 'center' }}>
      <span className="spinner" /> <span className="serif-it" style={{ marginLeft: 8 }}>caricamento impostazioni…</span>
    </div>
  );

  return (
    <div className="frame">
      <SectionHead
        roman="IV" kicker="Sezione  /  Impostazioni"
        title="Configurazione"
        deck="API key, modello d'analisi e regole di consenso."
        meta={[
          ['BACKEND', 'localhost:8000'],
          ['VAULT', 'LLM sicurezza'],
        ]}
      />

      {loadError && (
        <div style={{ borderTop: '1px solid var(--danger)', borderBottom: '1px solid var(--danger)', padding: '12px 14px', color: 'var(--danger)', marginBottom: 24 }}>
          {loadError}
          <button className="btn ghost" style={{ marginLeft: 12, padding: '4px 10px', fontSize: 10 }} onClick={loadAll}>riprova</button>
        </div>
      )}

      {stats && (
        <div className="grid-3" style={{ marginBottom: 32 }}>
          {[
            { label: 'Eventi totali', value: stats.total_incidents },
            { label: 'Approvati', value: stats.approved },
            { label: 'Pagine wiki', value: stats.wiki_pages },
          ].map(s => (
            <div key={s.label} style={{ borderTop: '2px solid var(--ink)', borderBottom: '1px solid var(--rule)', padding: '18px 0' }}>
              <div className="serif" style={{ fontSize: 44, fontWeight: 500, lineHeight: 1, color: 'var(--ink)' }}>{s.value ?? '—'}</div>
              <div className="smallcap" style={{ color: 'var(--ink-soft)', marginTop: 8 }}>{s.label}</div>
            </div>
          ))}
        </div>
      )}

      <div style={{ maxWidth: 820 }}>
        <div className="set-row">
          <div className="lbl">
            <span className="h">00 · aspetto</span>
            Tono dell'interfaccia
            <p>"Scuro" è il default. "Chiaro" inverte la palette per ambienti molto luminosi. Scelta persistita localmente.</p>
          </div>
          <div className="stack">
            <div className="row" style={{ gap: 0, alignSelf: 'flex-start', border: '1px solid var(--rule)', borderRadius: 2 }}>
              {(['dark', 'light'] as const).map(opt => {
                const isActive = theme === opt;
                return (
                  <button
                    key={opt}
                    onClick={() => applyTheme(opt)}
                    className="smallcap"
                    style={{
                      padding: '8px 18px',
                      background: isActive ? 'var(--ink)' : 'transparent',
                      color: isActive ? 'var(--paper)' : 'var(--ink-soft)',
                      border: 0,
                      cursor: 'pointer',
                      fontFamily: 'DM Sans',
                      fontWeight: 600,
                      transition: 'background 0.15s, color 0.15s',
                    }}>
                    {opt === 'dark' ? 'scuro' : 'chiaro'}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        <div className="set-row">
          <div className="lbl">
            <span className="h">01 · chiavi api</span>
            Chiavi Gemini API
            <p>Una o più chiavi (anche da account diversi). Con N&gt;1 la pipeline parallelizza trascrizione ed estrazione, distribuendo le chiamate sulle chiavi disponibili (rotazione + cooldown su 429). Salvate in <span className="mono">settings_ui.json</span>.</p>
          </div>
          <div className="stack">
            {(settings.api_keys || []).length === 0 && (
              <div className="mono" style={{ color: 'var(--gold)', fontSize: 11 }}>
                ⚠ nessuna chiave impostata · le chiamate AI falliranno
              </div>
            )}
            {(settings.api_keys || []).map((k, idx) => (
              <div key={idx} className="row" style={{ gap: 8, alignItems: 'stretch' }}>
                <span
                  className="mono"
                  style={{
                    minWidth: 28, padding: '8px 0',
                    color: 'var(--ink-soft)', fontSize: 11, textAlign: 'right',
                  }}
                >
                  #{String(idx + 1).padStart(2, '0')}
                </span>
                <input
                  className="input"
                  type={showApiKey ? 'text' : 'password'}
                  placeholder="AIza…"
                  value={k}
                  onChange={e => updateKey(idx, e.target.value)}
                  style={{ flex: 1, fontFamily: 'JetBrains Mono, monospace', fontSize: 12 }}
                />
                <button
                  className="btn ghost"
                  style={{ padding: '8px 12px', fontSize: 14, lineHeight: 1 }}
                  title="rimuovi questa chiave"
                  onClick={() => removeKey(idx)}
                >
                  ×
                </button>
              </div>
            ))}
            <div className="row" style={{ gap: 8, alignItems: 'center', justifyContent: 'space-between' }}>
              {(() => {
                const count = (settings.api_keys || []).length;
                const atMax = count >= MAX_API_KEYS;
                return (
                  <button
                    className="btn ghost"
                    style={{
                      padding: '6px 12px', fontSize: 11,
                      opacity: atMax ? 0.4 : 1,
                      cursor: atMax ? 'not-allowed' : 'pointer',
                    }}
                    onClick={addKey}
                    disabled={atMax}
                    title={atMax ? `Massimo ${MAX_API_KEYS} chiavi` : 'Aggiungi una chiave al pool'}
                  >
                    + aggiungi chiave {atMax ? `(max ${MAX_API_KEYS})` : ''}
                  </button>
                );
              })()}
              <div className="row" style={{ gap: 12, alignItems: 'center' }}>
                <span className="mono" style={{ fontSize: 11, color: 'var(--ink-soft)' }}>
                  {(() => {
                    const n = (settings.api_keys || []).filter(k => k.trim().length > 0).length;
                    if (n === 0) return 'nessuna chiave';
                    if (n === 1) return '1 chiave · single-key';
                    return `${n} chiavi · pool parallelo`;
                  })()}
                </span>
                <button
                  className="btn ghost"
                  style={{ padding: '6px 12px', fontSize: 11 }}
                  onClick={() => setShowApiKey(v => !v)}
                >
                  {showApiKey ? 'nascondi' : 'mostra'}
                </button>
              </div>
            </div>
          </div>
        </div>

        <div className="set-row">
          <div className="lbl">
            <span className="h">02 · modello</span>
            Modello AI
            <p>Il modello selezionato viene utilizzato per tutte le operazioni: trascrizione audio, estrazione consenso, giudice di fedeltà, perito d'evento, chat globale. <b>2.5 Flash</b>: trade-off qualità/velocità/costo consolidato. <b>3.5 Flash</b>: più capace su task agentici e di coding, stessa famiglia Flash.</p>
          </div>
          <div className="stack">
            <select
              className="input"
              value={settings.model || 'gemini-2.5-flash'}
              onChange={e => setSettings({ ...settings, model: e.target.value })}
              style={{
                alignSelf: 'flex-start',
                fontFamily: 'JetBrains Mono, monospace',
                fontSize: 13,
                padding: '10px 14px',
              }}>
              <option value="gemini-2.5-flash">gemini-2.5-flash</option>
              <option value="gemini-3.5-flash">gemini-3.5-flash</option>
            </select>
          </div>
        </div>

        <div className="set-row">
          <div className="lbl">
            <span className="h">03 · consenso</span>
            Esecuzioni di consenso per default
            <p>Numero di passaggi indipendenti del modello durante l'analisi di un evento. La risposta finale è la più frequente; ogni divergenza è registrata.</p>
          </div>
          <div className="stack">
            <div className="stepper" style={{ alignSelf: 'flex-start' }}>
              <button onClick={() => setSettings({ ...settings, n_runs_default: Math.max(1, (settings.n_runs_default ?? 3) - 1) })}>−</button>
              <span className="v">{String(settings.n_runs_default ?? 3).padStart(2, '0')}</span>
              <button onClick={() => setSettings({ ...settings, n_runs_default: Math.min(10, (settings.n_runs_default ?? 3) + 1) })}>+</button>
            </div>
          </div>
        </div>

        <div className="set-row">
          <div className="lbl">
            <span className="h">04 · vault</span>
            Apertura vault Obsidian
            <p>La visualizzazione del grafo di conoscenza è disponibile esclusivamente all'interno del vault.</p>
          </div>
          <div className="stack">
            <a href="obsidian://open?vault=LLM sicurezza" className="btn ghost" style={{ alignSelf: 'flex-start' }}>
              Apri vault · LLM sicurezza <span className="arrow">→</span>
            </a>
          </div>
        </div>

        <div className="row" style={{ paddingTop: 22, justifyContent: 'flex-end', gap: 14 }}>
          {saveStatus === 'ok' && <span className="mono" style={{ color: 'var(--green)' }}>✓ salvato</span>}
          {saveStatus === 'err' && <span className="mono" style={{ color: 'var(--danger)' }}>errore · {saveError}</span>}
          <button className="btn" onClick={handleSave} disabled={saving}>
            {saving ? <><span className="spinner" /> Salvataggio…</> : <>Salva impostazioni <span className="arrow">→</span></>}
          </button>
        </div>

        <div className="set-row" style={{ borderBottom: 0, marginTop: 28, borderTop: '2px solid var(--danger)' }}>
          <div className="lbl">
            <span className="h" style={{ color: 'var(--danger)' }}>05 · zona di pericolo</span>
            Azzeramento wiki
            <p>Rimuove tutti gli eventi, le entità e le analisi dalla wiki Obsidian. L'archivio locale degli incidenti viene preservato. L'operazione è irreversibile.</p>
          </div>
          <div>
            {!resetConfirm ? (
              <button className="btn danger" onClick={() => setResetConfirm(true)}>
                Azzeramento integrale
              </button>
            ) : (
              <div className="stack">
                <div className="serif-it" style={{ color: 'var(--danger)' }}>Confermi? L'operazione non è reversibile.</div>
                <div className="row" style={{ gap: 10 }}>
                  <button className="btn ghost" onClick={() => setResetConfirm(false)}>Annulla</button>
                  <button className="btn danger" onClick={handleReset} disabled={resetting}>
                    {resetting ? <><span className="spinner" /> Reset…</> : 'Conferma reset'}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
