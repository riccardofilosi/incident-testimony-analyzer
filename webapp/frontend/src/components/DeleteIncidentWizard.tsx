import React, { useEffect, useState } from 'react';

interface Props {
  incidentId: string;
  eventId: string;
  onClose: () => void;
  onCompleted: () => void;
}

type Preview = {
  event_id: string;
  wiki_root: string;
  event_page: string;
  event_page_exists: boolean;
  archive_dir: string;
  archive_files: string[];
  entity_pages: Array<{
    type: string;
    name: string;
    path: string;
    abs_path: string;
    conteggio_eventi: number | null;
    is_orphan_after_delete: boolean;
  }>;
  index_has_row: boolean;
  log_path: string;
  incident_status?: string;
  incident_dir?: string;
};

type Step = 1 | 2 | 3 | 4;

export const DeleteIncidentWizard: React.FC<Props> = ({ incidentId, eventId, onClose, onCompleted }) => {
  const [step, setStep] = useState<Step>(1);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [loading, setLoading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmedSteps, setConfirmedSteps] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (step === 2 && !preview) {
      loadPreview();
    }
  }, [step]);

  const loadPreview = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`http://localhost:8000/api/incidents/${incidentId}/deletion_preview`);
      if (!res.ok) {
        const e = await res.json().catch(() => ({} as any));
        throw new Error(e?.detail || `HTTP ${res.status}`);
      }
      setPreview(await res.json());
    } catch (err: any) {
      setError(err?.message || 'Errore caricamento anteprima');
    } finally {
      setLoading(false);
    }
  };

  const handleFinalDelete = async () => {
    setDeleting(true);
    setError(null);
    try {
      const res = await fetch(`http://localhost:8000/api/incidents/${incidentId}`, { method: 'DELETE' });
      if (!res.ok) {
        const e = await res.json().catch(() => ({} as any));
        throw new Error(e?.detail || `HTTP ${res.status}`);
      }
      onCompleted();
    } catch (err: any) {
      setError(err?.message || 'Errore eliminazione');
      setDeleting(false);
    }
  };

  const copyChecklist = () => {
    if (!preview) return;
    const lines: string[] = [];
    lines.push(`# Procedura di eliminazione manuale evento ${preview.event_id}`);
    lines.push(`Vault Obsidian: ${preview.wiki_root}`);
    lines.push('');

    let n = 1;
    if (preview.entity_pages.length > 0) {
      lines.push(`## ${n++}. Aggiorna pagine entità (rimuovi riferimenti all'evento)`);
      preview.entity_pages.forEach((e) => {
        lines.push(`- [ ] ${e.path}`);
        lines.push(`     • Apri il file, rimuovi righe/link che contengono "${preview.event_id}"`);
        if (e.conteggio_eventi !== null) {
          lines.push(`     • Decrementa frontmatter conteggio_eventi: ${e.conteggio_eventi} → ${e.conteggio_eventi - 1}`);
        }
        if (e.is_orphan_after_delete) {
          lines.push(`     • ATTENZIONE: questa entità rimarrebbe a 0 eventi (orfana). Valuta se eliminarla.`);
        }
      });
      lines.push('');
    }

    if (preview.event_page_exists) {
      lines.push(`## ${n++}. Elimina pagina evento`);
      lines.push(`- [ ] Cancella file: ${preview.event_page.replace(preview.wiki_root, '').replace(/^[\\/]/, '')}`);
      lines.push('');
    }

    if (preview.archive_files.length > 0) {
      lines.push(`## ${n++}. Elimina archivio file`);
      lines.push(`- [ ] Cancella cartella: archivio/${preview.event_id}/ (${preview.archive_files.length} file)`);
      lines.push('');
    }

    if (preview.index_has_row) {
      lines.push(`## ${n++}. Aggiorna index.md`);
      lines.push(`- [ ] Rimuovi la riga della tabella eventi che contiene "${preview.event_id}"`);
      lines.push(`- [ ] Decrementa "Incidenti approvati" nelle statistiche`);
      lines.push('');
    }

    lines.push(`## ${n++}. Aggiorna log.md`);
    lines.push(`- [ ] Aggiungi riga: ## [${new Date().toISOString().slice(0,16).replace('T',' ')}] ELIMINAZIONE | ${preview.event_id} — motivo: ...`);
    lines.push('');

    lines.push(`## ${n++}. (Opzionale) Rigenera analisi/pattern_ricorrenti.md`);
    lines.push(`- [ ] Rigenera manualmente o riapprovando un evento esistente`);

    navigator.clipboard.writeText(lines.join('\n')).then(
      () => alert('Checklist copiata negli appunti'),
      () => alert('Impossibile copiare. Selezionala manualmente.')
    );
  };

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-6" onClick={onClose}>
      <div className="bg-slate-900 border border-slate-700 rounded-2xl w-full max-w-3xl max-h-[90vh] flex flex-col shadow-2xl" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="px-7 py-5 border-b border-slate-800 flex items-center justify-between shrink-0">
          <div>
            <h3 className="text-xl font-bold text-white">Elimina Evento Approvato</h3>
            <p className="text-xs text-slate-500 mt-1">Step {step} di 4 — {eventId}</p>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-200 text-2xl leading-none">×</button>
        </div>

        {/* Stepper */}
        <div className="px-7 py-3 border-b border-slate-800 flex items-center gap-2 shrink-0">
          {[1, 2, 3, 4].map((s) => (
            <div key={s} className={`flex-1 h-1.5 rounded-full ${s <= step ? 'bg-emerald-500' : 'bg-slate-800'}`} />
          ))}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto px-7 py-6">
          {error && (
            <div className="mb-4 px-4 py-3 bg-red-500/10 border border-red-500/30 text-red-300 text-sm rounded-xl">
              {error}
            </div>
          )}

          {/* Step 1 - Conferma intent */}
          {step === 1 && (
            <div className="space-y-5">
              <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-5 text-amber-100 text-sm leading-relaxed">
                <p className="font-bold mb-2">⚠️ Attenzione: questa è una procedura manuale guidata</p>
                <p>L'evento è già stato approvato e <strong>pubblicato sulla wiki Obsidian</strong>. Per eliminarlo correttamente devi:</p>
                <ol className="list-decimal pl-5 mt-3 space-y-1">
                  <li>Pulire <strong>manualmente</strong> i riferimenti dalla wiki seguendo la guida del prossimo step</li>
                  <li>Solo alla fine: confermare l'eliminazione della cartella locale dell'incidente</li>
                </ol>
                <p className="mt-3 text-amber-300 text-xs">Il sistema NON modifica automaticamente la wiki: ogni passo è sotto il tuo controllo.</p>
              </div>
              <div className="bg-slate-950 border border-slate-800 rounded-xl p-4">
                <p className="text-xs uppercase font-bold text-slate-500 tracking-wide mb-2">Evento da eliminare</p>
                <p className="text-white font-mono text-sm">{eventId}</p>
              </div>
            </div>
          )}

          {/* Step 2 - Anteprima impatto */}
          {step === 2 && (
            <div className="space-y-4">
              {loading && (
                <div className="flex items-center justify-center py-10 text-slate-500">
                  <div className="w-6 h-6 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin mr-3" />
                  Scansione wiki in corso...
                </div>
              )}
              {preview && !loading && (
                <>
                  <p className="text-xs text-slate-500">
                    Vault: <span className="font-mono text-slate-400">{preview.wiki_root}</span>
                  </p>

                  <Section title="Pagina evento" empty={!preview.event_page_exists ? 'Non trovata' : null}>
                    {preview.event_page_exists && (
                      <p className="font-mono text-xs text-slate-300 break-all">{preview.event_page}</p>
                    )}
                  </Section>

                  <Section title={`Archivio file (${preview.archive_files.length})`} empty={preview.archive_files.length === 0 ? 'Nessun file archiviato' : null}>
                    <ul className="text-xs text-slate-400 space-y-0.5 max-h-32 overflow-auto">
                      {preview.archive_files.map((f) => <li key={f} className="font-mono">• {f}</li>)}
                    </ul>
                  </Section>

                  <Section title={`Pagine entità con riferimenti (${preview.entity_pages.length})`} empty={preview.entity_pages.length === 0 ? 'Nessuna entità collegata trovata' : null}>
                    <div className="space-y-2">
                      {preview.entity_pages.map((e) => (
                        <div key={e.path} className="bg-slate-950 border border-slate-800 rounded-lg p-3 text-xs">
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <p className="text-slate-200 font-mono break-all">{e.path}</p>
                              <p className="text-slate-500 mt-0.5">
                                Tipo: <span className="text-slate-400">{e.type}</span>
                                {e.conteggio_eventi !== null && (
                                  <> · conteggio_eventi: <span className="text-slate-400">{e.conteggio_eventi}</span> → <span className="text-amber-400">{e.conteggio_eventi - 1}</span></>
                                )}
                              </p>
                            </div>
                            {e.is_orphan_after_delete && (
                              <span className="px-2 py-0.5 bg-red-500/15 text-red-300 border border-red-500/30 rounded text-[10px] font-bold uppercase shrink-0">
                                orfana
                              </span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </Section>

                  <Section title="Index" empty={!preview.index_has_row ? 'Evento non presente in index.md' : null}>
                    {preview.index_has_row && (
                      <p className="text-xs text-slate-400">L'index contiene una riga per <span className="font-mono text-slate-300">{preview.event_id}</span></p>
                    )}
                  </Section>
                </>
              )}
            </div>
          )}

          {/* Step 3 - Guida */}
          {step === 3 && preview && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-sm text-slate-300">Spunta ogni passo dopo averlo eseguito in Obsidian.</p>
                <button onClick={copyChecklist} className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-bold">
                  📋 Copia checklist
                </button>
              </div>

              <ChecklistStep id="open-vault" confirmed={confirmedSteps} setConfirmed={setConfirmedSteps}
                title="Apri il vault Obsidian">
                <p className="text-xs text-slate-400">
                  Vault: <span className="font-mono">{preview.wiki_root}</span>
                </p>
                <a href="obsidian://open?vault=LLM sicurezza" className="inline-block mt-2 text-emerald-400 hover:text-emerald-300 text-xs underline">
                  💎 Apri Obsidian
                </a>
              </ChecklistStep>

              {preview.entity_pages.length > 0 && (
                <ChecklistStep id="entity-cleanup" confirmed={confirmedSteps} setConfirmed={setConfirmedSteps}
                  title={`Pulisci ${preview.entity_pages.length} pagine entità`}>
                  <p className="text-xs text-slate-400 mb-2">
                    Per ogni pagina: rimuovi righe/link contenenti <code className="text-emerald-400">{preview.event_id}</code>;
                    decrementa <code className="text-emerald-400">conteggio_eventi</code> nel frontmatter.
                  </p>
                  <ul className="text-[11px] text-slate-300 space-y-1 max-h-40 overflow-auto bg-slate-950 border border-slate-800 rounded-lg p-2">
                    {preview.entity_pages.map((e) => (
                      <li key={e.path} className="font-mono break-all">
                        • {e.path}
                        {e.is_orphan_after_delete && <span className="ml-2 text-red-400">[orfana — valuta eliminazione]</span>}
                      </li>
                    ))}
                  </ul>
                </ChecklistStep>
              )}

              {preview.event_page_exists && (
                <ChecklistStep id="event-page" confirmed={confirmedSteps} setConfirmed={setConfirmedSteps}
                  title="Elimina pagina evento">
                  <p className="text-xs text-slate-400 font-mono break-all">eventi/{preview.event_id}.md</p>
                </ChecklistStep>
              )}

              {preview.archive_files.length > 0 && (
                <ChecklistStep id="archive" confirmed={confirmedSteps} setConfirmed={setConfirmedSteps}
                  title="Elimina cartella archivio">
                  <p className="text-xs text-slate-400 font-mono">archivio/{preview.event_id}/ ({preview.archive_files.length} file)</p>
                </ChecklistStep>
              )}

              {preview.index_has_row && (
                <ChecklistStep id="index" confirmed={confirmedSteps} setConfirmed={setConfirmedSteps}
                  title="Aggiorna index.md">
                  <p className="text-xs text-slate-400">
                    Rimuovi la riga della tabella eventi che contiene <code className="text-emerald-400">{preview.event_id}</code> e decrementa il conteggio "Incidenti approvati".
                  </p>
                </ChecklistStep>
              )}

              <ChecklistStep id="log" confirmed={confirmedSteps} setConfirmed={setConfirmedSteps}
                title="Aggiungi riga in log.md">
                <p className="text-xs text-slate-400">
                  Es: <code className="text-emerald-400">## [{new Date().toISOString().slice(0,16).replace('T',' ')}] ELIMINAZIONE | {preview.event_id} — motivo: ...</code>
                </p>
              </ChecklistStep>

              <ChecklistStep id="pattern" confirmed={confirmedSteps} setConfirmed={setConfirmedSteps}
                title="(Opzionale) Rigenera analisi/pattern_ricorrenti.md">
                <p className="text-xs text-slate-400">
                  Si rigenera automaticamente alla prossima approvazione di un evento. Puoi anche modificarla manualmente.
                </p>
              </ChecklistStep>
            </div>
          )}

          {/* Step 4 - Conferma finale */}
          {step === 4 && (
            <div className="space-y-4">
              <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-xl p-5">
                <p className="text-emerald-200 text-sm">
                  Hai dichiarato di aver completato la pulizia manuale della wiki Obsidian.
                  Cliccando il pulsante qui sotto, il sistema cancellerà <strong>solo la cartella locale</strong> dell'incidente
                  ({preview?.incident_dir || incidentId}).
                </p>
              </div>
              <div className="bg-slate-950 border border-slate-800 rounded-xl p-4">
                <p className="text-xs uppercase font-bold text-slate-500 tracking-wide mb-2">Cartella locale che verrà eliminata</p>
                <p className="font-mono text-xs text-slate-300 break-all">{preview?.incident_dir || `incidents/${incidentId}/`}</p>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-7 py-4 border-t border-slate-800 flex items-center justify-between shrink-0">
          <button
            onClick={() => step > 1 ? setStep((step - 1) as Step) : onClose()}
            className="px-4 py-2 text-slate-400 hover:text-slate-200 text-sm font-medium"
          >
            {step === 1 ? 'Annulla' : 'Indietro'}
          </button>
          {step < 4 ? (
            <button
              onClick={() => setStep((step + 1) as Step)}
              disabled={step === 2 && (loading || !preview)}
              className="px-5 py-2 bg-emerald-500 text-white rounded-lg text-sm font-bold hover:bg-emerald-600 disabled:opacity-50 transition-colors"
            >
              Avanti →
            </button>
          ) : (
            <button
              onClick={handleFinalDelete}
              disabled={deleting}
              className="px-5 py-2 bg-red-500 text-white rounded-lg text-sm font-bold hover:bg-red-600 disabled:opacity-50 transition-colors"
            >
              {deleting ? 'Eliminazione...' : 'Elimina cartella locale'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

const Section: React.FC<{ title: string; empty: string | null; children?: React.ReactNode }> = ({ title, empty, children }) => (
  <div className="bg-slate-950 border border-slate-800 rounded-xl p-4">
    <p className="text-xs uppercase font-bold text-slate-500 tracking-wide mb-2">{title}</p>
    {empty ? <p className="text-xs text-slate-600 italic">{empty}</p> : children}
  </div>
);

const ChecklistStep: React.FC<{
  id: string;
  title: string;
  confirmed: Record<string, boolean>;
  setConfirmed: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
  children?: React.ReactNode;
}> = ({ id, title, confirmed, setConfirmed, children }) => (
  <label className={`flex items-start gap-3 p-3 rounded-xl border cursor-pointer transition-colors ${confirmed[id] ? 'border-emerald-500/40 bg-emerald-500/5' : 'border-slate-800 bg-slate-950 hover:border-slate-700'}`}>
    <input
      type="checkbox"
      checked={!!confirmed[id]}
      onChange={(e) => setConfirmed((p) => ({ ...p, [id]: e.target.checked }))}
      className="mt-0.5 w-4 h-4 accent-emerald-500"
    />
    <div className="flex-1 min-w-0">
      <p className={`text-sm font-bold ${confirmed[id] ? 'text-emerald-300' : 'text-slate-200'}`}>{title}</p>
      {children && <div className="mt-1">{children}</div>}
    </div>
  </label>
);
