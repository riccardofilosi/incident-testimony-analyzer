import React, { useState, useRef, useEffect } from 'react';
import { SectionHead } from './SectionHead';
import { useAudioInput } from '../hooks/useAudioInput';

type ChatMsg = { role: 'user' | 'assistant' | 'system'; content: string };

export const GlobalChat: React.FC = () => {
  const [message, setMessage] = useState('');
  const [history, setHistory] = useState<ChatMsg[]>([]);
  const [loading, setLoading] = useState(false);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const audio = useAudioInput((text) => setMessage(prev => (prev ? prev + ' ' + text : text)));

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [history, loading]);

  const handleSend = async () => {
    if (!message) return;
    const msg = message;
    setMessage('');
    setErrorBanner(null);
    setHistory(prev => [...prev, { role: 'user', content: msg }]);
    setLoading(true);
    try {
      const res = await fetch('http://localhost:8000/api/chat/global', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg })
      });
      if (res.status === 422) {
        const body = await res.json().catch(() => ({} as any));
        const reason = body?.detail?.reason || body?.detail || '';
        if (reason === 'off_topic') {
          setHistory(prev => [...prev, {
            role: 'system',
            content: "Domanda fuori ambito. Rispondo solo a domande su sicurezza, incidenti, macchinari, procedure o wiki Acqua Riva."
          }]);
          return;
        }
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setHistory(prev => [...prev, { role: 'assistant', content: data.reply }]);
    } catch (err: any) {
      console.error(err);
      setErrorBanner(`Errore di rete: ${err?.message || 'verifica che il backend sia attivo'}`);
    } finally {
      setLoading(false);
    }
  };

  const handleKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  return (
    <div className="frame">
      <SectionHead
        roman="II" kicker="Sezione  /  Database"
        title="Interrogazione al database"
        deck="Domanda in linguaggio naturale all'archivio degli eventi: il sistema risponde solo su fatti documentati."
        meta={[
          ['CONTESTO', 'archivio + wiki'],
          ['MODELLO', 'Gemini'],
        ]}
      />

      {errorBanner && (
        <div style={{ borderTop: '1px solid var(--danger)', borderBottom: '1px solid var(--danger)', padding: '10px 12px', color: 'var(--danger)', marginBottom: 18 }} className="between">
          <span>{errorBanner}</span>
          <button onClick={() => setErrorBanner(null)} style={{ background: 'transparent', border: 0, color: 'var(--danger)', cursor: 'pointer' }}>×</button>
        </div>
      )}

      <div className="perito" style={{ gridTemplateColumns: '1fr' }}>
        <div className="perito-main">
          <div style={{ flex: 1, overflowY: 'auto' }}>
            {history.length === 0 && (
              <div className="msg">
                <div className="who">DB &middot; pronto</div>
                <div className="body">
                  Sono il perito del database degli eventi critici di Acqua Riva. Posso rispondere su sicurezza, incidenti, macchinari e procedure documentate nella wiki interna.
                </div>
              </div>
            )}
            {history.map((m, i) => {
              if (m.role === 'system') {
                return (
                  <div key={i} className="msg sys">
                    <div className="who">SISTEMA</div>
                    <div className="body">{m.content}</div>
                  </div>
                );
              }
              const isUser = m.role === 'user';
              return (
                <div key={i} className={'msg' + (isUser ? ' you' : '')}>
                  <div className="who">{isUser ? 'Operatore' : 'Db'}</div>
                  <div className="body">{m.content}</div>
                </div>
              );
            })}
            {loading && (
              <div className="msg">
                <div className="who">Db · …</div>
                <div className="body"><span className="spinner" /> <span className="serif-it">elaborazione risposta…</span></div>
              </div>
            )}
            <div ref={endRef} />
          </div>

          <div className="compose">
            <textarea
              rows={2}
              value={message}
              onChange={e => setMessage(e.target.value)}
              onKeyDown={handleKey}
              disabled={audio.isRecording || audio.isTranscribing}
              placeholder={audio.isRecording ? `Registrazione in corso… ${audio.elapsed}s` : 'Domanda al database… (oppure usa il microfono)'}
            />
            {audio.status === 'transcribing' && (
              <div className="serif-it" style={{ color: 'var(--ink-mute)', marginTop: 6, fontSize: 12 }}>
                <span className="spinner" /> trascrizione audio in corso…
              </div>
            )}
            {audio.error && (
              <div className="mono" style={{ color: 'var(--danger)', marginTop: 6, fontSize: 11 }}>
                microfono · {audio.error}
              </div>
            )}
            <div className="row" style={{ gap: 8 }}>
              <button
                type="button"
                className={'btn ghost' + (audio.isRecording ? ' danger' : '')}
                disabled={!audio.supported || audio.isTranscribing || loading}
                title={!audio.supported ? 'Microfono non disponibile' : (audio.isRecording ? `Stop (${audio.elapsed}s)` : 'Registra audio')}
                onClick={() => audio.isRecording ? audio.stop() : audio.start()}
              >
                {audio.isRecording ? `■ Stop ${audio.elapsed}s` : '🎙 Registra'}
              </button>
              <button className="btn" onClick={handleSend} disabled={!message || loading || audio.isTranscribing}>
                Invia <span className="arrow">→</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
