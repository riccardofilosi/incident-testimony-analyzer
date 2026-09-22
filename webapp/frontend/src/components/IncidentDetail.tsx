import React, { useState, useEffect, useRef } from 'react';
import { DeleteIncidentWizard } from './DeleteIncidentWizard';
import { Status } from './SectionHead';
import { useAudioInput } from '../hooks/useAudioInput';

type Tab = 'riepilogo' | 'trascrizioni' | 'discordanze' | 'note';

interface IncidentDetailProps {
  incidentId: string;
  onBack: () => void;
}

function readError(json: any): string {
  if (!json) return 'Errore sconosciuto';
  const d = json.detail ?? json.error ?? json.message;
  if (typeof d === 'string') return d;
  if (d && typeof d === 'object') return JSON.stringify(d);
  return 'Errore sconosciuto';
}

function flattenDeep(obj: any, prefix = ''): Array<{ key: string; value: string }> {
  const rows: Array<{ key: string; value: string }> = [];
  if (obj === null || obj === undefined) return rows;
  if (typeof obj !== 'object') { rows.push({ key: prefix || 'value', value: String(obj) }); return rows; }
  if (Array.isArray(obj)) {
    if (obj.length === 0) { rows.push({ key: prefix, value: '' }); }
    else { obj.forEach((item, i) => { const k = prefix ? `${prefix}.${i}` : String(i); rows.push(...flattenDeep(item, k)); }); }
    return rows;
  }
  const entries = Object.entries(obj);
  if (entries.length === 0) { rows.push({ key: prefix, value: '' }); return rows; }
  for (const [k, v] of entries) {
    const fullKey = prefix ? `${prefix}.${k}` : k;
    if (v === null || v === undefined) rows.push({ key: fullKey, value: '' });
    else if (typeof v === 'object') rows.push(...flattenDeep(v, fullKey));
    else rows.push({ key: fullKey, value: String(v) });
  }
  return rows;
}

function applyEdits(original: any, edits: Record<string, string>): any {
  const result = original === undefined || original === null ? {} : JSON.parse(JSON.stringify(original));
  for (const [key, val] of Object.entries(edits)) {
    const parts = key.split('.');
    let obj: any = result;
    for (let i = 0; i < parts.length - 1; i++) {
      const p = parts[i];
      const nextP = parts[i + 1];
      const nextIsIndex = /^\d+$/.test(nextP);
      if (obj[p] === undefined || obj[p] === null) obj[p] = nextIsIndex ? [] : {};
      obj = obj[p];
    }
    obj[parts[parts.length - 1]] = val;
  }
  return result;
}

type NotaToken =
  | { t: 'h1'; text: string }
  | { t: 'h2'; text: string }
  | { t: 'file_open'; name: string }
  | { t: 'file_close' }
  | { t: 'metric'; status: 'OK' | 'X' | '!'; label: string; value: string }
  | { t: 'metric_sub'; text: string }
  | { t: 'list_header'; kind: 'discordanti' | 'allucinazioni' | 'omissioni' | 'critici' | 'forza' | 'criticita' | 'raccomandazioni' | 'altro'; text: string }
  | { t: 'bullet_field'; field: string; rest: string }
  | { t: 'callout'; level: string; field: string; rest: string }
  | { t: 'disc_field'; name: string }
  | { t: 'disc_value'; witness: string; value: string }
  | { t: 'disc_note'; text: string }
  | { t: 'strength'; text: string }
  | { t: 'crit'; text: string }
  | { t: 'rec'; text: string }
  | { t: 'verdict'; outcome: string }
  | { t: 'file_path'; label: string; path: string }
  | { t: 'operativa'; text: string }
  | { t: 'snippet'; runNum: number; tipo: 'incomprensibile' | 'incerto'; parola?: string; text: string }
  | { t: 'p'; text: string };

function tokenizeNota(text: string): NotaToken[] {
  const lines = text.split('\n');
  const tokens: NotaToken[] = [];
  const isH1Sep = (s: string) => /^={6,}\s*$/.test(s);
  const isH2Sep = (s: string) => /^-{6,}\s*$/.test(s);
  let inFile = false;
  let currentSection: string = '';
  let currentList: string = '';

  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];
    const next = lines[i + 1] || '';

    if (isH1Sep(ln)) continue;
    if (isH2Sep(ln)) continue;

    if (isH1Sep(next)) {
      if (inFile) { tokens.push({ t: 'file_close' }); inFile = false; }
      const titleParts = [ln];
      let j = i + 2;
      while (j < lines.length && !isH1Sep(lines[j]) && !isH2Sep(lines[j + 1] || '') && lines[j].trim() !== '') {
        titleParts.push(lines[j]); j++;
      }
      const title = titleParts.join(' ').trim();
      tokens.push({ t: 'h1', text: title });
      currentSection = title.toUpperCase();
      currentList = '' as any;
      i = j - 1;
      while (i + 1 < lines.length && isH1Sep(lines[i + 1])) i++;
      continue;
    }

    if (isH2Sep(next)) {
      const title = ln.trim();
      const fileMatch = title.match(/^FILE:\s*(.+)$/i);
      if (fileMatch) {
        if (inFile) tokens.push({ t: 'file_close' });
        tokens.push({ t: 'file_open', name: fileMatch[1].trim() });
        inFile = true;
        currentList = '';
        i++;
        continue;
      }
      tokens.push({ t: 'h2', text: title });
      const up = title.toUpperCase();
      if (up.startsWith('PUNTI DI FORZA')) currentList = 'forza';
      else if (up.startsWith('CRITICITA')) currentList = 'criticita';
      else if (up.startsWith('RACCOMANDAZIONI')) currentList = 'raccomandazioni';
      else currentList = '';
      i++;
      continue;
    }

    const trimmed = ln.trim();
    if (trimmed === '') continue;

    const verdictMatch = trimmed.match(/^GIUDIZIO:\s*(.+)$/i);
    if (verdictMatch) {
      tokens.push({ t: 'verdict', outcome: verdictMatch[1].trim() });
      continue;
    }

    const filePathMatch = trimmed.match(/^(REPORT [A-Z_ ]+(?:\[[^\]]+\])?):\s*(.+\.(?:xlsx|txt|pdf|json))$/i);
    if (filePathMatch) {
      tokens.push({ t: 'file_path', label: filePathMatch[1].trim(), path: filePathMatch[2].trim() });
      continue;
    }

    const calloutMatch = trimmed.match(/^\[!\]\[([A-Z]+)\]\s*(.+?)(?::\s*(.*))?$/);
    if (calloutMatch) {
      tokens.push({ t: 'callout', level: calloutMatch[1], field: calloutMatch[2].trim(), rest: (calloutMatch[3] || '').trim() });
      continue;
    }

    const metricMatch = trimmed.match(/^\[(OK|X|!)\]\s*([^:]+?)\s*:\s*(.+)$/);
    if (metricMatch && inFile) {
      tokens.push({ t: 'metric', status: metricMatch[1] as 'OK' | 'X' | '!', label: metricMatch[2].trim(), value: metricMatch[3].trim() });
      continue;
    }

    if (inFile && /^(Per run|Stabilita|Run selezionato)/i.test(trimmed)) {
      tokens.push({ t: 'metric_sub', text: trimmed });
      continue;
    }

    const opMatch = trimmed.match(/^Trascrizione operativa\s*:\s*(.+)$/i);
    if (opMatch && inFile) {
      tokens.push({ t: 'operativa', text: opMatch[1].trim() });
      continue;
    }

    if (inFile && /^PARTI NON BEN COMPRESE/i.test(trimmed)) {
      tokens.push({ t: 'list_header', kind: 'altro', text: 'Parti non ben comprese' });
      currentList = 'snippets' as any;
      continue;
    }

    const snipMatch = trimmed.match(/^-\s*run\s+(\d+)\s*·\s*(?:\[INCOMPRENSIBILE\]|(\S+?)\[\?\])\s*:\s*"(.+)"\s*$/);
    if (snipMatch && (currentList as any) === 'snippets') {
      tokens.push({
        t: 'snippet',
        runNum: Number(snipMatch[1]),
        tipo: snipMatch[2] ? 'incerto' : 'incomprensibile',
        parola: snipMatch[2],
        text: snipMatch[3],
      });
      continue;
    }

    const listHdr = trimmed.match(/^(CAMPI DISCORDANTI[^:]*|ALLUCINAZIONI[^:]*|OMISSIONI[^:]*|CAMPI CRITICI[^:]*)(?::.*)?$/i);
    if (listHdr) {
      const u = listHdr[1].toUpperCase();
      const kind: any = u.startsWith('CAMPI DISCORDANTI') ? 'discordanti'
        : u.startsWith('ALLUCINAZIONI') ? 'allucinazioni'
        : u.startsWith('OMISSIONI') ? 'omissioni'
        : 'critici';
      tokens.push({ t: 'list_header', kind, text: listHdr[1].trim() });
      currentList = kind;
      continue;
    }

    const discFieldMatch = trimmed.match(/^CAMPO:\s*(.+)$/i);
    if (discFieldMatch && currentSection.includes('DISCORDANZE TRA TESTIMONIANZE')) {
      tokens.push({ t: 'disc_field', name: discFieldMatch[1].trim() });
      continue;
    }

    const discValueMatch = trimmed.match(/^-\s*([^:]+?)\.(?:MP3|mp3|WAV|wav|m4a|mp4|txt):\s*['"](.*)['"]$/);
    if (discValueMatch && currentSection.includes('DISCORDANZE TRA TESTIMONIANZE')) {
      tokens.push({ t: 'disc_value', witness: discValueMatch[1].trim(), value: discValueMatch[2] });
      continue;
    }

    const discValueMatch2 = trimmed.match(/^-\s*(\S+\.(?:MP3|mp3|WAV|wav|m4a|mp4|txt)):\s*(.+)$/);
    if (discValueMatch2 && currentSection.includes('DISCORDANZE TRA TESTIMONIANZE')) {
      let v = discValueMatch2[2].trim();
      if ((v.startsWith("'") && v.endsWith("'")) || (v.startsWith('"') && v.endsWith('"'))) v = v.slice(1, -1);
      tokens.push({ t: 'disc_value', witness: discValueMatch2[1].trim(), value: v });
      continue;
    }

    const discNoteMatch = trimmed.match(/^->\s*(.+)$/);
    if (discNoteMatch && currentSection.includes('DISCORDANZE TRA TESTIMONIANZE')) {
      tokens.push({ t: 'disc_note', text: discNoteMatch[1].trim() });
      continue;
    }

    if (currentList === 'forza' && trimmed.startsWith('+ ')) {
      tokens.push({ t: 'strength', text: trimmed.slice(2).trim() });
      continue;
    }
    if (currentList === 'criticita' && trimmed.startsWith('- ')) {
      tokens.push({ t: 'crit', text: trimmed.slice(2).trim() });
      continue;
    }
    if (currentList === 'raccomandazioni' && trimmed.startsWith('-> ')) {
      tokens.push({ t: 'rec', text: trimmed.slice(3).trim() });
      continue;
    }

    const bulletFieldMatch = trimmed.match(/^-\s+(.+?):\s*(.*)$/);
    if (bulletFieldMatch && (currentList === 'discordanti' || currentList === 'allucinazioni' || currentList === 'critici')) {
      let field = bulletFieldMatch[1].trim();
      let rest = bulletFieldMatch[2].trim();
      const tagMatch = field.match(/^\[(DIRETTO|CONDENSATO)\]\s*(.+)$/);
      if (tagMatch) field = `[${tagMatch[1]}] ${tagMatch[2]}`;
      tokens.push({ t: 'bullet_field', field, rest });
      continue;
    }

    if (trimmed.startsWith('Testimoni confrontati') || trimmed.startsWith('Discordanze totali') || /^\[(CRITICHE|RILEVANTI|INFORMATIVE)\]/.test(trimmed)) {
      tokens.push({ t: 'p', text: trimmed });
      continue;
    }

    tokens.push({ t: 'p', text: ln });
  }
  if (inFile) tokens.push({ t: 'file_close' });
  return tokens;
}

const STATUS_ICON: Record<string, string> = { OK: '●', X: '✗', '!': '⚠' };
const STATUS_COLOR: Record<string, string> = { OK: 'var(--green)', X: 'var(--danger)', '!': 'var(--gold)' };
const LEVEL_COLOR: Record<string, string> = { CRITICA: 'var(--danger)', ALTA: 'var(--gold)', MEDIA: 'var(--ink-soft)', INFORMATIVO: 'var(--ink-soft)' };

function renderNotaCritica(text: string): React.ReactNode {
  if (!text || !text.trim()) return null;
  const tokens = tokenizeNota(text);

  const elements: React.ReactNode[] = [];
  let fileBuf: React.ReactNode[] = [];
  let inFile = false;
  let currentFileName = '';
  let currentVerdict: string | null = null;
  let discCard: { name: string; values: { witness: string; value: string }[]; note: string } | null = null;
  let listBuf: React.ReactNode[] = [];
  let listKind: string = '';
  let listKey = 0;

  const flushList = () => {
    if (!listBuf.length) return;
    const isLong = listBuf.length > 5;
    const headerColor = listKind === 'allucinazioni' || listKind === 'omissioni' ? 'var(--danger)' : 'var(--gold)';
    const node = (
      <div key={`l-${listKey++}`} style={{ marginTop: 6, marginBottom: 12 }}>
        {isLong && listKind === 'critici' ? (
          <details>
            <summary style={{ cursor: 'pointer', color: headerColor, fontFamily: 'JetBrains Mono, monospace', fontSize: 10, letterSpacing: '0.15em', textTransform: 'uppercase', padding: '4px 0' }}>
              Mostra {listBuf.length} campi varianti per run
            </summary>
            <div style={{ marginTop: 8 }}>{listBuf}</div>
          </details>
        ) : listBuf}
      </div>
    );
    (inFile ? fileBuf : elements).push(node);
    listBuf = [];
    listKind = '';
  };

  const flushDisc = () => {
    if (!discCard) return;
    elements.push(
      <div key={`d-${listKey++}`} style={{
        border: '1px solid var(--rule)', borderLeft: '3px solid var(--danger)',
        padding: '10px 14px', marginTop: 10, marginBottom: 4,
        background: 'color-mix(in srgb, var(--danger) 3%, transparent)'
      }}>
        <div className="mono" style={{ fontSize: 12, fontWeight: 700, color: 'var(--ink)', marginBottom: 8 }}>{discCard.name}</div>
        <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 8 }}>
          <tbody>
            {discCard.values.map((v, i) => (
              <tr key={i} style={{ borderTop: i === 0 ? 0 : '1px dotted var(--rule)' }}>
                <td className="mono" style={{ padding: '4px 8px 4px 0', color: 'var(--ink-soft)', fontSize: 11, width: 130, verticalAlign: 'top' }}>{v.witness}</td>
                <td className="serif" style={{ padding: '4px 0', color: 'var(--ink)', fontSize: 14 }}>{v.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {discCard.note && (
          <div className="serif-it" style={{ fontSize: 13, color: 'var(--ink-soft)', lineHeight: 1.5, paddingTop: 6, borderTop: '1px solid var(--rule-soft)' }}>{discCard.note}</div>
        )}
      </div>
    );
    discCard = null;
  };

  const flushFile = () => {
    if (!inFile) return;
    flushList();
    elements.push(
      <div key={`f-${listKey++}`} style={{
        border: '1px solid var(--rule)', borderLeft: '3px solid var(--gold)',
        padding: '14px 18px', marginTop: 14, marginBottom: 4,
        background: 'color-mix(in srgb, var(--gold) 3%, transparent)'
      }}>
        <div className="between" style={{ alignItems: 'center', marginBottom: 10, paddingBottom: 8, borderBottom: '1px solid var(--rule-soft)' }}>
          <div className="serif" style={{ fontSize: 18, fontWeight: 500, color: 'var(--ink)' }}>{currentFileName}</div>
          {currentVerdict && (
            <span style={{
              fontFamily: 'DM Sans', fontSize: 11, fontWeight: 600, letterSpacing: '0.1em',
              padding: '4px 10px', borderRadius: 2,
              color: /APPROV/i.test(currentVerdict) ? 'var(--green)' : 'var(--danger)',
              background: `color-mix(in srgb, ${/APPROV/i.test(currentVerdict) ? 'var(--green)' : 'var(--danger)'} 10%, transparent)`,
              border: `1px solid ${/APPROV/i.test(currentVerdict) ? 'var(--green)' : 'var(--danger)'}`,
            }}>{currentVerdict.toUpperCase()}</span>
          )}
        </div>
        {fileBuf}
      </div>
    );
    fileBuf = [];
    inFile = false;
    currentFileName = '';
    currentVerdict = null;
  };

  for (let i = 0; i < tokens.length; i++) {
    const tk = tokens[i];
    if (tk.t === 'h1') {
      flushDisc(); flushList(); flushFile();
      elements.push(
        <h3 key={`h1-${i}`} className="serif" style={{
          fontSize: 24, fontWeight: 500, color: 'var(--ink)',
          marginTop: elements.length === 0 ? 0 : 32, marginBottom: 14,
          paddingBottom: 8, borderBottom: '1px solid var(--accent)'
        }}>{tk.text}</h3>
      );
      continue;
    }
    if (tk.t === 'h2') {
      flushDisc(); flushList();
      const target = inFile ? fileBuf : elements;
      target.push(
        <h4 key={`h2-${i}`} className="smallcap" style={{
          color: 'var(--accent)', marginTop: 18, marginBottom: 8,
          letterSpacing: '0.15em', fontSize: 11, fontWeight: 600
        }}>{tk.text}</h4>
      );
      continue;
    }
    if (tk.t === 'file_open') {
      flushFile();
      inFile = true;
      currentFileName = tk.name;
      continue;
    }
    if (tk.t === 'file_close') {
      flushFile();
      continue;
    }
    if (tk.t === 'verdict') {
      if (inFile) currentVerdict = tk.outcome;
      else elements.push(
        <div key={`v-${i}`} className="serif" style={{ fontSize: 16, fontWeight: 500, color: /APPROV/i.test(tk.outcome) ? 'var(--green)' : 'var(--danger)', marginTop: 8 }}>
          GIUDIZIO: {tk.outcome}
        </div>
      );
      continue;
    }
    if (tk.t === 'metric') {
      const target = inFile ? fileBuf : elements;
      target.push(
        <div key={`m-${i}`} style={{ display: 'flex', alignItems: 'baseline', gap: 10, padding: '4px 0' }}>
          <span style={{ fontSize: 14, color: STATUS_COLOR[tk.status], width: 16, textAlign: 'center', lineHeight: 1 }}>{STATUS_ICON[tk.status]}</span>
          <span className="smallcap" style={{ color: 'var(--ink-soft)', fontSize: 10, minWidth: 180 }}>{tk.label}</span>
          <span className="mono" style={{ color: 'var(--ink)', fontSize: 13, fontWeight: 600 }}>{tk.value}</span>
        </div>
      );
      continue;
    }
    if (tk.t === 'metric_sub') {
      const target = inFile ? fileBuf : elements;
      target.push(
        <div key={`ms-${i}`} className="mono" style={{ color: 'var(--ink-mute)', fontSize: 11, paddingLeft: 26, marginTop: 2, marginBottom: 4 }}>{tk.text}</div>
      );
      continue;
    }
    if (tk.t === 'operativa') {
      flushList();
      const target = inFile ? fileBuf : elements;
      target.push(
        <div key={`op-${i}`} style={{ marginTop: 8, marginBottom: 4, padding: '6px 10px', borderLeft: '2px solid var(--accent)', background: 'color-mix(in srgb, var(--accent) 6%, transparent)' }}>
          <span className="smallcap" style={{ color: 'var(--accent)', fontSize: 10, letterSpacing: '0.15em', marginRight: 8 }}>→ trascrizione operativa</span>
          <span className="mono" style={{ fontSize: 12, color: 'var(--ink-2)', fontWeight: 600 }}>{tk.text}</span>
        </div>
      );
      continue;
    }
    if (tk.t === 'snippet') {
      const isIncomp = tk.tipo === 'incomprensibile';
      const accent = isIncomp ? 'var(--danger)' : 'var(--gold)';
      listBuf.push(
        <div key={`sn-${i}`} style={{ borderLeft: `2px solid ${accent}`, paddingLeft: 12, marginBottom: 8, paddingTop: 4, paddingBottom: 4 }}>
          <div className="mono" style={{ fontSize: 11, fontWeight: 700, color: accent, marginBottom: 4, letterSpacing: '0.05em' }}>
            RUN {tk.runNum} · {isIncomp ? '[INCOMPRENSIBILE]' : `parola incerta "${tk.parola}"`}
          </div>
          <div className="serif" style={{ fontSize: 14, lineHeight: 1.6, color: 'var(--ink-2)', fontStyle: 'italic' }}>
            {renderTrascrizioneMarkata(tk.text)}
          </div>
        </div>
      );
      continue;
    }
    if (tk.t === 'list_header') {
      flushList();
      const color = tk.kind === 'allucinazioni' || tk.kind === 'omissioni' ? 'var(--danger)' : 'var(--gold)';
      const target = inFile ? fileBuf : elements;
      target.push(
        <div key={`lh-${i}`} className="smallcap" style={{ color, marginTop: 14, marginBottom: 6, letterSpacing: '0.15em', fontSize: 10, fontWeight: 700 }}>{tk.text}</div>
      );
      listKind = tk.kind;
      continue;
    }
    if (tk.t === 'bullet_field') {
      const valLooksLikeArray = /^\[.*\]$/.test(tk.rest);
      let pills: string[] | null = null;
      if (valLooksLikeArray) {
        try {
          const parsed = JSON.parse(tk.rest.replace(/'/g, '"'));
          if (Array.isArray(parsed)) pills = parsed.map(String);
        } catch { pills = null; }
      }
      listBuf.push(
        <div key={`bf-${i}`} style={{ borderLeft: '1px solid var(--rule-soft)', paddingLeft: 12, paddingBottom: 6, paddingTop: 4, marginBottom: 4 }}>
          <div className="mono" style={{ fontSize: 11, fontWeight: 700, color: 'var(--ink)', marginBottom: 4 }}>{tk.field}</div>
          {pills ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {pills.map((p, k) => (
                <span key={k} className="mono" style={{
                  fontSize: 11, padding: '2px 6px',
                  background: /^N\/D$/i.test(p) ? 'color-mix(in srgb, var(--ink-mute) 12%, transparent)' : 'color-mix(in srgb, var(--accent) 10%, transparent)',
                  color: /^N\/D$/i.test(p) ? 'var(--ink-mute)' : 'var(--ink-2)',
                  borderRadius: 2, border: '1px solid var(--rule)'
                }}>{p}</span>
              ))}
            </div>
          ) : (
            <div className="serif" style={{ fontSize: 13.5, color: 'var(--ink-2)', lineHeight: 1.5 }}>{tk.rest}</div>
          )}
        </div>
      );
      continue;
    }
    if (tk.t === 'callout') {
      const color = LEVEL_COLOR[tk.level] || 'var(--ink-soft)';
      const target = inFile ? fileBuf : elements;
      target.push(
        <div key={`c-${i}`} style={{
          borderLeft: `3px solid ${color}`,
          background: `color-mix(in srgb, ${color} 5%, transparent)`,
          padding: '8px 12px', marginTop: 6, marginBottom: 6
        }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 4 }}>
            <span style={{ fontFamily: 'DM Sans', fontSize: 9, fontWeight: 700, letterSpacing: '0.15em', color: 'var(--bg)', background: color, padding: '2px 6px', borderRadius: 2 }}>{tk.level}</span>
            <span className="mono" style={{ fontSize: 11, fontWeight: 700, color: 'var(--ink)' }}>{tk.field}</span>
          </div>
          {tk.rest && <div className="serif" style={{ fontSize: 13.5, color: 'var(--ink-2)', lineHeight: 1.5 }}>{tk.rest}</div>}
        </div>
      );
      continue;
    }
    if (tk.t === 'disc_field') {
      flushDisc();
      discCard = { name: tk.name, values: [], note: '' };
      continue;
    }
    if (tk.t === 'disc_value') {
      if (!discCard) discCard = { name: '?', values: [], note: '' };
      discCard.values.push({ witness: tk.witness, value: tk.value });
      continue;
    }
    if (tk.t === 'disc_note') {
      if (discCard) { discCard.note = tk.text; flushDisc(); }
      continue;
    }
    if (tk.t === 'strength') {
      elements.push(
        <div key={`st-${i}`} style={{ display: 'flex', gap: 10, padding: '3px 0' }}>
          <span style={{ color: 'var(--green)', fontSize: 16, lineHeight: 1.4, width: 14 }}>✓</span>
          <span className="serif" style={{ fontSize: 14, color: 'var(--ink-2)', lineHeight: 1.5 }}>{tk.text}</span>
        </div>
      );
      continue;
    }
    if (tk.t === 'crit') {
      elements.push(
        <div key={`cr-${i}`} style={{ display: 'flex', gap: 10, padding: '3px 0' }}>
          <span style={{ color: 'var(--danger)', fontSize: 16, lineHeight: 1.4, width: 14 }}>✗</span>
          <span className="serif" style={{ fontSize: 14, color: 'var(--ink-2)', lineHeight: 1.5 }}>{tk.text}</span>
        </div>
      );
      continue;
    }
    if (tk.t === 'rec') {
      elements.push(
        <div key={`re-${i}`} style={{ display: 'flex', gap: 10, padding: '3px 0' }}>
          <span style={{ color: 'var(--accent)', fontSize: 16, lineHeight: 1.4, width: 14 }}>→</span>
          <span className="serif" style={{ fontSize: 14, color: 'var(--ink-2)', lineHeight: 1.5 }}>{tk.text}</span>
        </div>
      );
      continue;
    }
    if (tk.t === 'file_path') {
      elements.push(
        <div key={`fp-${i}`} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '6px 10px', background: 'color-mix(in srgb, var(--ink) 4%, transparent)', marginBottom: 4 }}>
          <span style={{ fontSize: 13 }}>📄</span>
          <span className="smallcap" style={{ fontSize: 10, color: 'var(--ink-soft)', minWidth: 220 }}>{tk.label}</span>
          <span className="mono" style={{ fontSize: 11, color: 'var(--ink-soft)', wordBreak: 'break-all' }}>{tk.path}</span>
        </div>
      );
      continue;
    }
    if (tk.t === 'p') {
      const target = inFile ? fileBuf : elements;
      const txt = tk.text.replace(/^\s+/, '').trimEnd();
      if (!txt) continue;
      target.push(
        <div key={`p-${i}`} className="serif" style={{ fontSize: 14, color: 'var(--ink-2)', lineHeight: 1.6, marginBottom: 4 }}>{txt}</div>
      );
      continue;
    }
  }
  flushList(); flushDisc(); flushFile();
  return elements;
}

function renderTrascrizioneMarkata(testo: string): React.ReactNode {
  if (!testo) return null;
  const tokens: Array<React.ReactNode> = [];
  const re = /\[INCOMPRENSIBILE\]|\[PAUSA\]|(\S+?)\[\?\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(testo)) !== null) {
    if (m.index > last) tokens.push(<span key={`t-${i++}`}>{testo.slice(last, m.index)}</span>);
    if (m[0] === '[INCOMPRENSIBILE]') {
      tokens.push(
        <span key={`m-${i++}`} title="parola non udibile" className="mono" style={{
          background: 'color-mix(in srgb, var(--danger) 18%, transparent)',
          color: 'var(--danger)', padding: '1px 6px', borderRadius: 3,
          fontSize: 12, fontWeight: 600, margin: '0 2px', verticalAlign: 'baseline',
        }}>[INCOMPRENSIBILE]</span>
      );
    } else if (m[0] === '[PAUSA]') {
      tokens.push(
        <span key={`m-${i++}`} title="pausa > 3s" className="mono" style={{
          background: 'color-mix(in srgb, var(--ink-soft) 14%, transparent)',
          color: 'var(--ink-soft)', padding: '1px 6px', borderRadius: 3,
          fontSize: 12, margin: '0 2px',
        }}>[PAUSA]</span>
      );
    } else if (m[1]) {
      tokens.push(
        <span key={`m-${i++}`} title="parola incerta" style={{
          background: 'color-mix(in srgb, var(--gold) 18%, transparent)',
          color: 'var(--gold)', padding: '1px 4px', borderRadius: 3,
          fontWeight: 600, margin: '0 1px',
        }}>{m[1]}<span className="mono" style={{ fontSize: 11, marginLeft: 2 }}>[?]</span></span>
      );
    }
    last = re.lastIndex;
  }
  if (last < testo.length) tokens.push(<span key={`t-${i++}`}>{testo.slice(last)}</span>);
  return tokens;
}

const METRIC_KEYS = {
  completezza: 'Completezza_Campi_%',
  coerenza: 'Coerenza_Inter_Run_%',
  faithfulness: 'faithfulness_score',
  hallucination: 'Hallucination_Rate_%',
  audio: 'score_qualita_audio',
} as const;

function metricColor(val: number, inverted = false) {
  if (inverted) return val <= 10 ? 'var(--green)' : val <= 20 ? 'var(--gold)' : 'var(--danger)';
  return val >= 80 ? 'var(--green)' : val >= 60 ? 'var(--gold)' : 'var(--danger)';
}

const MetricCell: React.FC<{ label: string; raw: number; fmt: (v: number) => string; inverted?: boolean; colorScale?: number }> = ({ label, raw, fmt, inverted = false, colorScale }) => {
  const colorVal = colorScale ? raw * colorScale : raw;
  return (
    <div style={{ minWidth: 90 }}>
      <div className="serif" style={{ fontSize: 24, fontWeight: 500, color: metricColor(colorVal, inverted), lineHeight: 1 }}>{fmt(raw)}</div>
      <div className="smallcap" style={{ color: 'var(--ink-mute)', marginTop: 6 }}>{label}</div>
    </div>
  );
};

export const IncidentDetail: React.FC<IncidentDetailProps> = ({ incidentId, onBack }) => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<Tab>('riepilogo');

  const SUMMARY_KEY = '__summary__';
  const [edits, setEdits] = useState<Record<string, Record<string, string>>>({});
  const [selectedReport, setSelectedReport] = useState<string>(SUMMARY_KEY);
  const [savingDraft, setSavingDraft] = useState(false);

  const [resolutions, setResolutions] = useState<Record<string, string>>({});
  const [selectedWitness, setSelectedWitness] = useState('');

  const [showApproveModal, setShowApproveModal] = useState(false);
  const [showRejectModal, setShowRejectModal] = useState(false);
  const [operatorName, setOperatorName] = useState('');
  const [rejectReason, setRejectReason] = useState('');
  const [approving, setApproving] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [regenLoading, setRegenLoading] = useState(false);

  const [chatMessage, setChatMessage] = useState('');
  const [chatHistory, setChatHistory] = useState<any[]>([]);
  const [chatLoading, setChatLoading] = useState(false);
  const [proposedChanges, setProposedChanges] = useState<any[]>([]);
  const [chatError, setChatError] = useState<string | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const audio = useAudioInput((text) => setChatMessage(prev => (prev ? prev + ' ' + text : text)));

  const [showDeleteWizard, setShowDeleteWizard] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [runsExpanded, setRunsExpanded] = useState(false);
  const [runsFiles, setRunsFiles] = useState<Array<{ name: string; size: number; modified: string; download_url: string }>>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [peritoExpanded, setPeritoExpanded] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchData = async () => {
    try {
      const res = await fetch(`http://localhost:8000/api/incidents/${incidentId}`);
      const json = await res.json();
      setData(json);
      if (json.state?.discordance_resolutions) setResolutions(json.state.discordance_resolutions);
      // Aggiorna chat history SOLO se cambia davvero (evita re-render inutili
      // ogni 3s durante il polling di wiki_update_status -> evita auto-scroll).
      if (json.chat_history?.length) {
        setChatHistory(prev => (prev.length === json.chat_history.length ? prev : json.chat_history));
      }
      const wikiInFlight = json.state?.wiki_update_status === 'pending'
        || json.state?.wiki_update_status === 'running';
      if (json.state?.status === 'PROCESSING' || wikiInFlight) {
        if (!pollRef.current) pollRef.current = setInterval(fetchData, 3000);
      } else {
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [incidentId]);

  // Scroll della chat alla fine SOLO sui nuovi messaggi, e solo dentro il
  // container chat (block: 'nearest'). Evita di rubare lo scroll della pagina.
  const prevChatLenRef = useRef(0);
  useEffect(() => {
    if (chatHistory.length > prevChatLenRef.current) {
      chatEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    prevChatLenRef.current = chatHistory.length;
  }, [chatHistory]);

  useEffect(() => {
    if (!selectedWitness && data?.result?.trascrizioni) {
      const first = Object.keys(data.result.trascrizioni)[0];
      if (first) setSelectedWitness(first);
    }
  }, [data]);

  const getSummaryJson = () => data?.draft || data?.result?.riassuntivo_json || {};
  const getWitnessConsensus = (witness: string) =>
    data?.witness_drafts?.[witness] || data?.result?.risultati?.[witness]?.consensus || {};
  const getReportJson = (key: string) => key === SUMMARY_KEY ? getSummaryJson() : getWitnessConsensus(key);

  const handleSaveDraft = async () => {
    setSavingDraft(true);
    try {
      const updatedSummary = applyEdits(getSummaryJson(), edits[SUMMARY_KEY] || {});
      const witnessDrafts: Record<string, any> = {};
      const witnessList = Object.keys(data?.result?.risultati || {});
      for (const w of witnessList) {
        const witnessEdits = edits[w];
        if (witnessEdits && Object.keys(witnessEdits).length > 0) witnessDrafts[w] = applyEdits(getWitnessConsensus(w), witnessEdits);
        else if (data?.witness_drafts?.[w]) witnessDrafts[w] = data.witness_drafts[w];
      }
      await fetch(`http://localhost:8000/api/incidents/${incidentId}/save_draft`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ draft_json: updatedSummary, witness_drafts: Object.keys(witnessDrafts).length > 0 ? witnessDrafts : undefined }),
      });
      setEdits({});
      await fetchData();
    } finally { setSavingDraft(false); }
  };

  const handleApprove = async () => {
    if (!operatorName.trim()) return;
    setApproving(true);
    try {
      const updated = applyEdits(getSummaryJson(), edits[SUMMARY_KEY] || {});
      const res = await fetch(`http://localhost:8000/api/incidents/${incidentId}/approve`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ operator: operatorName, approved_json: updated }),
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        alert(readError(e)); return;
      }
      setShowApproveModal(false);
      setEdits({});
      await fetchData();
    } finally { setApproving(false); }
  };

  const handleReject = async () => {
    if (!rejectReason.trim()) return;
    setRejecting(true);
    try {
      await fetch(`http://localhost:8000/api/incidents/${incidentId}/reject`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ operator: operatorName || 'Sistema', reason: rejectReason }),
      });
      setShowRejectModal(false);
      await fetchData();
    } finally { setRejecting(false); }
  };

  const handleRegenWiki = async () => {
    setRegenLoading(true);
    try {
      const res = await fetch(`http://localhost:8000/api/incidents/${incidentId}/regenerate_wiki`, { method: 'POST' });
      if (res.ok) alert('Wiki rigenerata con successo');
      else { const e = await res.json().catch(() => ({})); alert(readError(e)); }
    } finally { setRegenLoading(false); }
  };

  const handleSendChat = async (forcedMsg?: string) => {
    const raw = forcedMsg ?? chatMessage;
    if (!raw.trim() || chatLoading) return;
    const msg = raw.trim();
    setChatMessage('');
    setChatError(null);
    setChatHistory(prev => [...prev, { role: 'user', content: msg }]);
    setChatLoading(true);
    setProposedChanges([]);
    try {
      const res = await fetch(`http://localhost:8000/api/chat/${incidentId}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg }),
      });
      if (res.status === 422) {
        const body = await res.json().catch(() => ({} as any));
        const reason = body?.detail?.reason || body?.detail || '';
        if (reason === 'off_topic') {
          setChatHistory(prev => [...prev, { role: 'system', content: 'Domanda fuori ambito. Rispondo solo a domande su sicurezza, incidenti, macchinari, procedure o wiki Acqua Riva.' }]);
          return;
        }
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      setChatHistory(d.history);
      if (d.proposed_changes?.length) setProposedChanges(d.proposed_changes);
    } catch (err: any) { setChatError(err?.message || 'Errore di rete'); }
    finally { setChatLoading(false); }
  };

  const handleToggleRuns = async () => {
    const next = !runsExpanded;
    setRunsExpanded(next);
    if (next && runsFiles.length === 0 && !runsLoading) {
      setRunsLoading(true);
      setRunsError(null);
      try {
        const res = await fetch(`http://localhost:8000/api/incidents/${incidentId}/runs_files`);
        if (!res.ok) {
          const e = await res.json().catch(() => ({}));
          throw new Error(readError(e));
        }
        const data = await res.json();
        setRunsFiles(data.files || []);
        if (!data.exists) {
          setRunsError('Cartella validazione non ancora creata. Attendi il completamento della pipeline.');
        } else if ((data.files || []).length === 0) {
          setRunsError('Nessun file trovato nella cartella di validazione.');
        }
      } catch (err: any) {
        setRunsError(err?.message || 'Errore nel caricamento dei file');
      } finally {
        setRunsLoading(false);
      }
    }
  };

  const fmtFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  };

  const handleDownloadLog = () => {
    window.open(`http://localhost:8000/api/incidents/${incidentId}/processing_log`, '_blank');
  };

  const handleQuickDelete = async () => {
    const ok = window.confirm(`Eliminare l'incidente ${incidentId}?\n\nQuesto cancella SOLO la cartella locale.`);
    if (!ok) return;
    setDeleting(true);
    try {
      const res = await fetch(`http://localhost:8000/api/incidents/${incidentId}`, { method: 'DELETE' });
      if (!res.ok) { const e = await res.json().catch(() => ({})); alert(readError(e)); return; }
      onBack();
    } finally { setDeleting(false); }
  };

  const applyProposedChange = (change: any) => {
    setEdits(prev => ({
      ...prev,
      [SUMMARY_KEY]: { ...(prev[SUMMARY_KEY] || {}), [change.campo]: change.valore_proposto }
    }));
    setSelectedReport(SUMMARY_KEY);
    setProposedChanges(prev => prev.filter(c => c.campo !== change.campo));
  };

  const handleSaveResolution = async (idx: string, value: string) => {
    const newRes = { ...resolutions, [idx]: value };
    setResolutions(newRes);
    const disc = (data?.result?.discordanze || [])[Number(idx)];
    if (disc?.campo) {
      setEdits(prev => ({
        ...prev,
        [SUMMARY_KEY]: { ...(prev[SUMMARY_KEY] || {}), [disc.campo]: value }
      }));
    }
    await fetch(`http://localhost:8000/api/incidents/${incidentId}/resolve_discordance`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ resolutions: newRes }),
    });
  };

  if (loading) return (
    <div className="frame" style={{ padding: '80px 56px', textAlign: 'center' }}>
      <span className="spinner" /> <span className="serif-it" style={{ marginLeft: 8 }}>caricamento evento…</span>
    </div>
  );

  const status = data?.state?.status ?? 'DRAFT';
  const result = data?.result;
  const witnesses: string[] = Object.keys(result?.risultati || {});
  const discordanze = result?.discordanze || [];
  const trascrizioni: Record<string, string> = result?.trascrizioni || {};
  const notaCritica = result?.nota_critica || '';
  const isPending = status === 'PENDING';
  const isApproved = status === 'APPROVED';
  const isProcessing = status === 'PROCESSING';
  const wikiStatus: string | undefined = data?.state?.wiki_update_status;
  const wikiInFlight = wikiStatus === 'pending' || wikiStatus === 'running';
  const wikiError: string | undefined = data?.state?.wiki_update_error;
  const currentReportEdits = edits[selectedReport] || {};
  const rows = flattenDeep(getReportJson(selectedReport));
  const dirtyCount = Object.values(edits).reduce((acc, m) => acc + Object.keys(m).length, 0);
  const dirtyByReport: Record<string, number> = Object.fromEntries(
    Object.entries(edits).map(([k, v]) => [k, Object.keys(v).length])
  );

  const eventId = data?.state?.event_id || incidentId;
  const approvedAt = data?.state?.approved_at
    ? new Date(data.state.approved_at).toLocaleDateString('it-IT', { day: '2-digit', month: 'short', year: 'numeric' })
    : '—';
  const createdAt = data?.state?.created_at
    ? new Date(data.state.created_at).toLocaleDateString('it-IT', { day: '2-digit', month: 'short', year: 'numeric' })
    : '—';

  return (
    <div className={'frame' + (peritoExpanded ? ' frame--wide' : '')}>
      {/* Header riga indietro */}
      <div className="row" style={{ paddingBottom: 14, marginBottom: 14, borderBottom: '1px solid var(--rule)' }}>
        <button className="btn ghost" style={{ padding: '8px 14px', fontSize: 11 }} onClick={onBack}>
          <span className="arrow" style={{ marginRight: 6 }}>←</span> Torna all'archivio
        </button>
        <span className="mono muted" style={{ marginLeft: 'auto' }}>
          report &middot; {incidentId} {data?.state?.version ? `· v${data.state.version}` : ''}
        </span>
      </div>

      {/* Eyebrow + H1 + deck + meta */}
      <div>
        <div className="kicker">Report evento &middot; {eventId !== incidentId ? eventId : incidentId}</div>
        <h1 style={{ margin: '8px 0 6px', fontFamily: "'Newsreader', serif", fontWeight: 500, fontSize: 64, lineHeight: 0.96, letterSpacing: '-0.02em', color: 'var(--ink)' }}>
          {eventId}
        </h1>
        {result?.titolo_evento && (
          <div className="serif-it" style={{ fontSize: 20, color: 'var(--ink-2)', maxWidth: 760, marginTop: 8 }}>
            {result.titolo_evento}
          </div>
        )}
        <div className="mono" style={{ color: 'var(--ink-mute)', marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--rule)', display: 'flex', gap: 24, flexWrap: 'wrap' }}>
          <span>caricato · <b style={{ color: 'var(--ink-2)' }}>{createdAt}</b></span>
          {isApproved && <span>approvato · <b style={{ color: 'var(--ink-2)' }}>{approvedAt}</b></span>}
          {witnesses.length > 0 && <span>testimonianze · <b style={{ color: 'var(--ink-2)' }}>{witnesses.length}</b></span>}
          {data?.state?.model_used && <span>modello · <b style={{ color: 'var(--ink-2)' }}>{data.state.model_used}</b></span>}
          {data?.state?.operator && <span>operatore · <b style={{ color: 'var(--ink-2)' }}>{data.state.operator}</b></span>}
          <span style={{ marginLeft: 'auto' }}><Status s={status} /></span>
        </div>
      </div>

      {wikiInFlight && (
        <div style={{
          marginTop: 18, padding: '12px 16px',
          borderTop: '1px solid var(--gold)', borderBottom: '1px solid var(--gold)',
          color: 'var(--gold)', display: 'flex', alignItems: 'center', gap: 12,
        }}>
          <span className="spinner" />
          <span className="serif-it">Aggiornamento del vault Obsidian in corso… (estrazione entità via LLM, può durare 1–3 min)</span>
        </div>
      )}
      {wikiStatus === 'error' && wikiError && (
        <div style={{
          marginTop: 18, padding: '12px 16px',
          borderTop: '1px solid var(--danger)', borderBottom: '1px solid var(--danger)',
          color: 'var(--danger)',
        }}>
          <span className="smallcap">Errore aggiornamento wiki</span>
          <div className="mono" style={{ marginTop: 6 }}>{wikiError}</div>
        </div>
      )}
      {wikiStatus === 'ok' && (
        <div style={{
          marginTop: 18, padding: '10px 16px',
          borderTop: '1px solid var(--green)', borderBottom: '1px solid var(--green)',
          color: 'var(--green)',
        }} className="mono">
          ✓ wiki aggiornata
          {data?.state?.wiki_update_elapsed_sec ? ` · ${data.state.wiki_update_elapsed_sec}s` : ''}
          {data?.state?.model_used ? ` · modello ${data.state.model_used}` : ''}
          {data?.state?.wiki_update_completed_at ? ` · ${new Date(data.state.wiki_update_completed_at).toLocaleString('it-IT')}` : ''}
        </div>
      )}

      {isProcessing && (() => {
        const nFiles = data?.state?.input_files?.length || 1;
        const nRuns = data?.state?.n_runs || 3;
        // Stima ETA basata su pool size (fetch sincrono dello stato pool via state)
        // Fallback semplice se non disponibile: 90s/file + 25s/run, diviso per parallelismo stimato (3)
        const poolSize = (window as any).__poolSize || 1;
        const etaSec = Math.ceil(((nFiles / Math.max(poolSize,1)) * 90) + ((nFiles * nRuns / Math.max(poolSize,1)) * 25));
        const etaMin = Math.max(1, Math.round(etaSec / 60));
        const startedAt = data?.state?.created_at ? new Date(data.state.created_at).getTime() : Date.now();
        const elapsedSec = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
        const elapsedMin = Math.floor(elapsedSec / 60);
        const elapsedSecRem = elapsedSec % 60;
        return (
          <div style={{ padding: '80px 0', textAlign: 'center' }}>
            <div className="spinner" style={{ width: 32, height: 32, borderWidth: 2 }} />
            <h3 className="serif" style={{ fontSize: 28, fontWeight: 500, marginTop: 18, color: 'var(--ink)' }}>Pipeline in esecuzione</h3>
            <p className="serif-it" style={{ color: 'var(--ink-soft)', marginTop: 8 }}>
              L'analisi AI è in corso. La pagina si aggiorna automaticamente ogni 3 secondi.
            </p>
            <p className="mono" style={{ color: 'var(--gold)', marginTop: 12, fontSize: 12 }}>
              tempo trascorso · {elapsedMin}m {elapsedSecRem}s · stima totale · ~{etaMin} min · {poolSize} chiave/i in parallelo
            </p>
            {data?.state?.input_files?.length > 0 && (
              <p className="mono muted" style={{ marginTop: 14 }}>file · {data.state.input_files.join(' · ')}</p>
            )}
          </div>
        );
      })()}

      {!isProcessing && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: peritoExpanded ? '1fr' : '2.6fr 1fr',
          gap: peritoExpanded ? 0 : 48,
          marginTop: 34
        }}>
          {/* Sinistra — sezioni report (rimossa dal DOM in modalità Perito espanso) */}
          {!peritoExpanded && (
          <div>
            <div className="tabs">
              {(['riepilogo', 'trascrizioni', 'discordanze', 'note'] as Tab[]).map(tab => {
                const labels: Record<Tab, string> = {
                  riepilogo: '§ I Riepilogo',
                  trascrizioni: '§ II Trascrizioni',
                  discordanze: `§ III Discordanze${discordanze.length > 0 ? ` (${discordanze.length})` : ''}`,
                  note: '§ IV Note critiche',
                };
                return (
                  <button key={tab} className={activeTab === tab ? 'active' : ''} onClick={() => setActiveTab(tab)}>
                    {labels[tab]}
                  </button>
                );
              })}
            </div>

            {/* ── RIEPILOGO ── */}
            {activeTab === 'riepilogo' && (
              <div style={{ paddingTop: 28 }}>
                {discordanze.length > 0 && (
                  <div style={{
                    marginBottom: 24, padding: '10px 14px',
                    borderTop: '1px solid var(--gold)', borderBottom: '1px solid var(--gold)',
                    color: 'var(--gold)',
                  }} className="mono">
                    ⚠ {discordanze.length} discordanze tra testimoni — risolvile tutte nel tab §III Discordanze prima di approvare.
                  </div>
                )}
                {witnesses.length > 0 && (
                  <div style={{ marginBottom: 28 }}>
                    <div className="smallcap" style={{ color: 'var(--ink-soft)', marginBottom: 12 }}>Metriche qualità per testimone</div>
                    <div className="stack">
                      {witnesses.map(w => {
                        const m = result.risultati[w]?.metriche || {};
                        const audioQ = m[METRIC_KEYS.audio];
                        const compl = m[METRIC_KEYS.completezza];
                        const coer = m[METRIC_KEYS.coerenza];
                        const faith = m[METRIC_KEYS.faithfulness];
                        const hall = m[METRIC_KEYS.hallucination];
                        return (
                          <div key={w} style={{ borderTop: '1px solid var(--rule)', paddingTop: 14 }}>
                            <div className="mono" style={{ color: 'var(--ink-soft)', marginBottom: 10 }}>{w}</div>
                            <div className="row" style={{ gap: 28 }}>
                              {audioQ !== undefined && audioQ !== null && <MetricCell label="Audio" raw={Number(audioQ)} fmt={v => `${v.toFixed(0)}%`} />}
                              {compl !== undefined && compl !== null && <MetricCell label="Completezza" raw={Number(compl)} fmt={v => `${v.toFixed(0)}%`} />}
                              {coer !== undefined && coer !== null && <MetricCell label="Coerenza" raw={Number(coer)} fmt={v => `${v.toFixed(0)}%`} />}
                              {faith !== undefined && faith !== null && <MetricCell label="Faithfulness" raw={Number(faith)} fmt={v => `${v.toFixed(1)}/10`} colorScale={10} />}
                              {hall !== undefined && hall !== null && <MetricCell label="Allucinazioni" raw={Number(hall)} fmt={v => `${v.toFixed(1)}%`} inverted />}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                <div>
                  <div className="between" style={{ marginBottom: 12 }}>
                    <div className="smallcap" style={{ color: 'var(--ink-soft)' }}>Dati estratti</div>
                    {dirtyCount > 0 && (
                      <span className="mono" style={{ color: 'var(--gold)' }}>{dirtyCount} campi modificati</span>
                    )}
                  </div>

                  <div className="row" style={{ marginBottom: 14 }}>
                    {[SUMMARY_KEY, ...witnesses].map(rk => {
                      const dirty = dirtyByReport[rk] || 0;
                      const active = selectedReport === rk;
                      return (
                        <button key={rk} onClick={() => setSelectedReport(rk)} className="smallcap"
                          style={{
                            background: 'transparent', border: 0, cursor: 'pointer', padding: '6px 0',
                            borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
                            color: active ? 'var(--accent)' : 'var(--ink-soft)',
                            fontFamily: 'DM Sans', fontWeight: 600,
                            display: 'inline-flex', alignItems: 'center', gap: 6,
                          }}>
                          {rk === SUMMARY_KEY ? 'riassuntivo' : rk}
                          {dirty > 0 && <span className="mono" style={{ color: 'var(--gold)' }}>·{dirty}</span>}
                        </button>
                      );
                    })}
                  </div>

                  <p className="serif-it" style={{ color: 'var(--ink-soft)', fontSize: 13, marginBottom: 14 }}>
                    {selectedReport === SUMMARY_KEY
                      ? 'Report consenso: i dati di questo report verranno pubblicati sulla wiki all\'approvazione.'
                      : 'Report individuale del testimone. Le modifiche vengono salvate ma il riassuntivo è quello pubblicato in wiki.'}
                  </p>

                  {rows.length === 0 ? (
                    <div className="serif-it" style={{ color: 'var(--ink-mute)', padding: '40px 0', textAlign: 'center', borderTop: '1px solid var(--rule)' }}>
                      Nessun dato disponibile per questo report.
                    </div>
                  ) : (
                    <table className="kv">
                      <thead>
                        <tr><th style={{ width: '40%' }}>Campo</th><th>Valore</th></tr>
                      </thead>
                      <tbody>
                        {rows.map(({ key, value }) => {
                          const editedVal = currentReportEdits[key];
                          const current = editedVal !== undefined ? editedVal : value;
                          const dirty = editedVal !== undefined;
                          const len = (current || '').length;
                          const newlines = (current || '').split('\n').length;
                          const dynRows = Math.min(8, Math.max(1, Math.max(newlines, Math.ceil(len / 80))));
                          return (
                            <tr key={key} className={dirty ? 'dirty' : ''}>
                              <td className="k">{key}</td>
                              <td>
                                <textarea
                                  value={current}
                                  onChange={e => setEdits(p => ({ ...p, [selectedReport]: { ...(p[selectedReport] || {}), [key]: e.target.value } }))}
                                  rows={dynRows}
                                />
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  )}

                  {dirtyCount > 0 && (
                    <div className="row" style={{ marginTop: 16, justifyContent: 'flex-end' }}>
                      <button className="btn" onClick={handleSaveDraft} disabled={savingDraft}>
                        {savingDraft ? <><span className="spinner" /> Salvataggio…</> : `Salva bozza · ${dirtyCount} campi`}
                      </button>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* ── TRASCRIZIONI ── */}
            {activeTab === 'trascrizioni' && (
              <div style={{ paddingTop: 28 }}>
                {witnesses.length === 0 ? (
                  <div className="serif-it" style={{ color: 'var(--ink-mute)', padding: '40px 0', textAlign: 'center' }}>
                    Nessuna trascrizione disponibile.
                  </div>
                ) : (
                  <>
                    <div className="row" style={{ marginBottom: 18 }}>
                      {witnesses.map(w => (
                        <button key={w} onClick={() => setSelectedWitness(w)} className="smallcap"
                          style={{
                            background: 'transparent', border: 0, cursor: 'pointer', padding: '6px 0',
                            borderBottom: selectedWitness === w ? '2px solid var(--accent)' : '2px solid transparent',
                            color: selectedWitness === w ? 'var(--accent)' : 'var(--ink-soft)',
                            fontFamily: 'DM Sans', fontWeight: 600,
                          }}>
                          {w}
                        </button>
                      ))}
                    </div>
                    {selectedWitness && (
                      <div style={{ borderTop: '2px solid var(--ink)', borderBottom: '1px solid var(--rule)', padding: '18px 0' }}>
                        <div className="mono" style={{ color: 'var(--ink-soft)', marginBottom: 12 }}>{selectedWitness}</div>
                        <p className="serif" style={{ fontSize: 16, lineHeight: 1.6, color: 'var(--ink-2)', whiteSpace: 'pre-wrap', margin: 0 }}>
                          {trascrizioni[selectedWitness] || 'Trascrizione non disponibile.'}
                        </p>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {/* ── DISCORDANZE ── */}
            {activeTab === 'discordanze' && (
              <div style={{ paddingTop: 28 }}>
                {discordanze.length === 0 ? (
                  <div style={{ textAlign: 'center', padding: '60px 0' }}>
                    <div className="serif" style={{ fontSize: 28, color: 'var(--green)' }}>nessuna discordanza</div>
                    <div className="serif-it" style={{ color: 'var(--ink-soft)', marginTop: 8 }}>
                      Tutte le testimonianze sono coerenti tra loro.
                    </div>
                  </div>
                ) : (
                  <div className="stack">
                    {discordanze.map((d: any, idx: number) => {
                      const impatto = d.impatto_sicurezza || 'INFORMATIVO';
                      const vals = Object.entries(d.valori || {}) as [string, string][];
                      const currentRes = resolutions[String(idx)];
                      const witnessValues = vals.map(([, v]) => String(v));
                      const impColor = impatto === 'CRITICO' ? 'var(--danger)' : impatto === 'RILEVANTE' ? 'var(--gold)' : 'var(--ink-soft)';
                      return (
                        <div key={idx} style={{ borderTop: '2px solid var(--ink)', borderBottom: '1px solid var(--rule)', paddingBottom: 16 }}>
                          <div className="between" style={{ paddingTop: 14 }}>
                            <div>
                              <div className="serif" style={{ fontSize: 18, fontWeight: 500, color: 'var(--ink)' }}>{d.campo}</div>
                              <div className="serif-it" style={{ color: 'var(--ink-soft)', fontSize: 14, marginTop: 4 }}>{d.spiegazione}</div>
                            </div>
                            <span className="status" style={{ color: impColor }}><span className="dot" />{impatto}</span>
                          </div>
                          <div className="smallcap" style={{ color: 'var(--ink-mute)', marginTop: 18, marginBottom: 10 }}>Valori per testimone</div>
                          <div className="stack" style={{ marginTop: 0 }}>
                            {vals.map(([witness, val]) => {
                              const selected = currentRes === String(val);
                              return (
                                <button key={witness} onClick={() => handleSaveResolution(String(idx), String(val))}
                                  style={{
                                    width: '100%', textAlign: 'left',
                                    border: `1px solid ${selected ? 'var(--accent)' : 'var(--rule)'}`,
                                    background: selected ? 'color-mix(in srgb, var(--accent) 8%, transparent)' : 'transparent',
                                    padding: '12px 14px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between',
                                  }}>
                                  <span className="serif" style={{ fontSize: 15, color: selected ? 'var(--accent)' : 'var(--ink-2)' }}>
                                    {String(val) || <em className="muted">vuoto</em>}
                                  </span>
                                  <span className="mono" style={{ color: 'var(--ink-mute)' }}>{witness}</span>
                                </button>
                              );
                            })}
                            <input className="input"
                              placeholder="Valore personalizzato…"
                              value={currentRes && !witnessValues.includes(currentRes) ? currentRes : ''}
                              onChange={e => handleSaveResolution(String(idx), e.target.value)} />
                          </div>
                          {currentRes && (
                            <div className="mono" style={{ color: 'var(--green)', marginTop: 10 }}>
                              risoluzione · <b>{currentRes}</b>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {/* ── NOTE ── */}
            {activeTab === 'note' && (
              <div style={{ paddingTop: 28 }} className="stack">
                <div style={{ borderTop: '2px solid var(--ink)', borderBottom: '1px solid var(--rule)', padding: '18px 0' }}>
                  <div className="between">
                    <div className="kicker">Processo di estrazione</div>
                    <button className="btn ghost" style={{ padding: '6px 12px', fontSize: 10 }} onClick={handleDownloadLog}>
                      ⬇ scarica log
                    </button>
                  </div>
                  {result?.processing_summary?.files?.length ? (
                    <div className="stack" style={{ marginTop: 14 }}>
                      {result.processing_summary.files.map((f: any, i: number) => (
                        <div key={i} style={{ borderTop: '1px solid var(--rule-soft)', paddingTop: 12 }}>
                          <div className="serif" style={{ fontSize: 15, fontWeight: 500, color: 'var(--ink)' }}>{f.file}</div>
                          <div className="row" style={{ gap: 28, marginTop: 10 }}>
                            <div><div className="mono" style={{ color: 'var(--green)', fontSize: 16, fontWeight: 600 }}>{f.runs_completed ?? '—'}/{f.runs_total ?? '—'}</div><div className="smallcap" style={{ color: 'var(--ink-mute)' }}>completate</div></div>
                            <div><div className="mono" style={{ color: (f.runs_failed ?? 0) > 0 ? 'var(--danger)' : 'var(--ink-2)', fontSize: 16, fontWeight: 600 }}>{f.runs_failed ?? 0}</div><div className="smallcap" style={{ color: 'var(--ink-mute)' }}>fallite</div></div>
                          </div>
                          {f.consensus_strategy && (
                            <div className="mono muted" style={{ marginTop: 10 }}>
                              consenso · {f.consensus_strategy}
                              {f.consensus_run_index !== undefined && f.consensus_run_index !== null && ` · run #${f.consensus_run_index}`}
                            </div>
                          )}
                          {f.errors?.length > 0 && (
                            <div style={{ marginTop: 10, padding: '10px 12px', borderLeft: '2px solid var(--danger)' }}>
                              <div className="smallcap" style={{ color: 'var(--danger)' }}>Errori non bloccanti · {f.errors.length}</div>
                              <ul className="mono" style={{ margin: '6px 0 0', paddingLeft: 18, color: 'var(--ink-soft)' }}>
                                {f.errors.slice(0, 5).map((e: string, j: number) => <li key={j}>{e}</li>)}
                                {f.errors.length > 5 && <li className="muted">… altri {f.errors.length - 5}</li>}
                              </ul>
                            </div>
                          )}
                        </div>
                      ))}
                      {result.processing_summary.total_elapsed_sec && (
                        <div className="mono muted right" style={{ marginTop: 8 }}>
                          tempo totale pipeline · <b style={{ color: 'var(--ink-2)' }}>{Number(result.processing_summary.total_elapsed_sec).toFixed(1)}s</b>
                        </div>
                      )}
                    </div>
                  ) : (
                    <p className="serif-it" style={{ color: 'var(--ink-soft)', marginTop: 10 }}>
                      Riassunto tecnico non disponibile. Il log dettagliato è comunque scaricabile.
                    </p>
                  )}
                </div>

                {notaCritica ? (
                  <div style={{ borderTop: '2px solid var(--accent)', borderBottom: '1px solid var(--rule)', padding: '18px 0' }}>
                    <div className="kicker" style={{ color: 'var(--accent)' }}>Nota critica di sicurezza</div>
                    <div style={{ marginTop: 14 }}>
                      {renderNotaCritica(notaCritica)}
                    </div>
                  </div>
                ) : (
                  <div className="serif-it" style={{ color: 'var(--ink-mute)', padding: '20px 0', textAlign: 'center', borderTop: '1px solid var(--rule)' }}>
                    Nessuna nota critica registrata.
                  </div>
                )}
              </div>
            )}
          </div>

          )}

          {/* Destra — marginalia & azioni (a tutta larghezza in modalità espansa) */}
          <aside style={{
            borderLeft: peritoExpanded ? 'none' : '1px solid var(--rule)',
            paddingLeft: peritoExpanded ? 0 : 32
          }}>
            {!peritoExpanded && (
              <>
                <div className="aside" style={{ borderTop: 0, paddingTop: 0 }}>
                  <div className="kicker">Stato</div>
                  <div className="serif" style={{ fontSize: 18, marginTop: 6, color: 'var(--ink)' }}>
                    <Status s={status} />
                  </div>
                  {data?.state?.operator && (
                    <div className="mono muted" style={{ marginTop: 10, lineHeight: 1.7 }}>
                      operatore · <b style={{ color: 'var(--ink-2)' }}>{data.state.operator}</b><br />
                      {data?.state?.input_files?.length > 0 && <>file · <b style={{ color: 'var(--ink-2)' }}>{data.state.input_files.length}</b></>}
                    </div>
                  )}
                </div>

                {result?.titolo_evento && (
                  <div className="aside">
                    <div className="smallcap" style={{ color: 'var(--ink-soft)' }}>Evento</div>
                    <div className="serif" style={{ fontSize: 16, marginTop: 6, color: 'var(--ink-2)' }}>{result.titolo_evento}</div>
                  </div>
                )}
              </>
            )}

            {/* Perito dell'evento — promosso SOPRA le Azioni per visibilità */}
            <div className="aside" style={{
              borderLeft: '3px solid var(--gold)',
              paddingLeft: peritoExpanded ? 16 : 16,
              marginLeft: peritoExpanded ? 0 : -19,
              background: 'linear-gradient(to right, rgba(212,175,55,0.04), transparent 60%)'
            }}>
              <div className="row" style={{ alignItems: 'center', gap: 8, justifyContent: 'space-between' }}>
                <div className="row" style={{ alignItems: 'center', gap: 8 }}>
                  <h3 className="serif" style={{ fontSize: 22, fontWeight: 500, color: 'var(--ink)', margin: 0 }}>
                    Perito dell'evento
                  </h3>
                  <span className="mono" style={{
                    fontSize: 9, padding: '2px 6px', background: 'var(--gold)',
                    color: 'var(--bg)', borderRadius: 2, letterSpacing: 1, fontWeight: 600
                  }}>AI</span>
                </div>
                <div className="row" style={{ gap: 6 }}>
                  {isApproved && (
                    <button className="btn ghost"
                      style={{ padding: '4px 10px', fontSize: 11, whiteSpace: 'nowrap' }}
                      onClick={handleRegenWiki} disabled={regenLoading}
                      title="Rigenera la pagina Obsidian con i dati correnti">
                      {regenLoading ? 'rigenera…' : '↻ rigenera wiki'}
                    </button>
                  )}
                  <button className="btn ghost"
                    style={{ padding: '4px 10px', fontSize: 11, whiteSpace: 'nowrap' }}
                    onClick={() => setPeritoExpanded(v => !v)}
                    title={peritoExpanded ? 'Riduci la chat alla sidebar' : 'Espandi la chat a tutta pagina'}>
                    {peritoExpanded ? '⤡ riduci' : '⤢ espandi'}
                  </button>
                </div>
              </div>
              <p className="serif-it" style={{ marginTop: 6, fontSize: 13, color: 'var(--ink-soft)', lineHeight: 1.5 }}>
                Interroga il tuo perito virtuale su questo incidente. Riassume, confronta versioni dei testimoni, segnala lacune, e <b>propone correzioni applicabili con un click</b>.
              </p>

              {chatHistory.length === 0 && (
                <div style={{ marginTop: 14 }}>
                  <div className="smallcap" style={{ color: 'var(--ink-mute)', fontSize: 9, marginBottom: 8 }}>
                    Esempi · clic per usare
                  </div>
                  <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
                    {[
                      'Riassumi le cause tecniche',
                      'Quali sono le discordanze tra testimoni?',
                      'I DPI erano adeguati?',
                      'Suggerisci modifiche al report',
                    ].map(prompt => (
                      <button key={prompt} className="btn ghost"
                        style={{ fontSize: 11, padding: '5px 10px' }}
                        onClick={() => handleSendChat(prompt)}>
                        {prompt}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div style={{
                marginTop: 14,
                maxHeight: peritoExpanded ? 'min(75vh, 800px)' : 'min(480px, 50vh)',
                overflowY: 'auto',
                borderTop: '1px solid var(--rule-soft)',
                paddingTop: 12
              }}>
                {chatHistory.length === 0 && (
                  <div className="serif-it" style={{ color: 'var(--ink-mute)', fontSize: 13 }}>
                    Nessuna conversazione ancora. Usa un esempio o scrivi una domanda…
                  </div>
                )}
                {chatHistory.map((m, i) => {
                  if (m.role === 'system') {
                    return (
                      <div key={i} className="chat-msg-c chat-msg-c--sys">
                        <div className="chat-msg-c__who">sistema</div>
                        <div className="chat-msg-c__body">{m.content}</div>
                      </div>
                    );
                  }
                  const isUser = m.role === 'user';
                  return (
                    <div key={i} className={'chat-msg-c' + (isUser ? ' chat-msg-c--user' : '')}>
                      <div className="chat-msg-c__who">{isUser ? 'tu' : 'perito'}</div>
                      <div className="chat-msg-c__body">{m.content}</div>
                    </div>
                  );
                })}
                {chatError && <div className="mono" style={{ color: 'var(--danger)', marginTop: 8 }}>errore · {chatError}</div>}
                {chatLoading && <div className="serif-it" style={{ color: 'var(--ink-mute)', marginTop: 8 }}><span className="spinner" /> elaborazione…</div>}
                {proposedChanges.length > 0 && (
                  <div style={{ marginTop: 14, borderTop: '1px solid var(--gold)', paddingTop: 12 }}>
                    <div className="smallcap" style={{ color: 'var(--gold)', marginBottom: 10 }}>Modifiche proposte</div>
                    <div className="stack">
                      {proposedChanges.map((c, i) => (
                        <div key={i} style={{ borderTop: '1px dotted var(--rule)', paddingTop: 10, paddingBottom: 4 }}>
                          <div className="mono" style={{ color: 'var(--ink-2)', fontSize: 11, fontWeight: 600, marginBottom: 8 }}>{c.campo}</div>
                          {c.valore_attuale && (
                            <div style={{
                              borderLeft: '2px solid var(--danger)', paddingLeft: 8, marginBottom: 6,
                              background: 'color-mix(in srgb, var(--danger) 5%, transparent)'
                            }}>
                              <div className="smallcap" style={{ color: 'var(--danger)', fontSize: 9, marginBottom: 2 }}>attuale</div>
                              <div className="serif" style={{ color: 'var(--ink)', fontSize: 13, lineHeight: 1.4 }}>{String(c.valore_attuale)}</div>
                            </div>
                          )}
                          <div style={{
                            borderLeft: '2px solid var(--green)', paddingLeft: 8,
                            background: 'color-mix(in srgb, var(--green) 6%, transparent)'
                          }}>
                            <div className="smallcap" style={{ color: 'var(--green)', fontSize: 9, marginBottom: 2 }}>proposto</div>
                            <div className="serif" style={{ color: 'var(--ink)', fontSize: 14, lineHeight: 1.4, fontWeight: 500 }}>{String(c.valore_proposto)}</div>
                          </div>
                          {c.motivazione && <div className="serif-it" style={{ color: 'var(--ink-soft)', fontSize: 12, marginTop: 6 }}>{c.motivazione}</div>}
                          <button className="btn ghost" style={{ marginTop: 10, padding: '6px 12px', fontSize: 10, width: '100%', justifyContent: 'center' }}
                            onClick={() => applyProposedChange(c)}>
                            Applica modifica
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                <div ref={chatEndRef} />
              </div>

              {audio.status === 'transcribing' && (
                <div className="serif-it" style={{ color: 'var(--ink-mute)', marginTop: 8, fontSize: 12 }}>
                  <span className="spinner" /> trascrizione audio in corso…
                </div>
              )}
              {audio.error && (
                <div className="mono" style={{ color: 'var(--danger)', marginTop: 8, fontSize: 11 }}>
                  microfono · {audio.error}
                </div>
              )}

              <div style={{ marginTop: 14, display: 'flex', gap: 6, alignItems: 'stretch' }}>
                <input className="input" style={{ flex: 1, fontSize: 13 }}
                  placeholder="Chiedi al perito… (Enter per inviare)"
                  value={chatMessage}
                  disabled={audio.isRecording || audio.isTranscribing}
                  onChange={e => setChatMessage(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSendChat()} />
                <button
                  type="button"
                  className={'btn ghost' + (audio.isRecording ? ' danger' : '')}
                  style={{ padding: '8px 12px', fontSize: 13, minWidth: 44 }}
                  disabled={!audio.supported || audio.isTranscribing || chatLoading}
                  title={!audio.supported ? 'Microfono non disponibile' : (audio.isRecording ? `Stop (${audio.elapsed}s)` : 'Registra audio')}
                  onClick={() => audio.isRecording ? audio.stop() : audio.start()}
                >
                  {audio.isRecording ? `■ ${audio.elapsed}s` : '🎙'}
                </button>
                <button className="btn" style={{ padding: '8px 14px', fontSize: 11 }}
                  onClick={() => handleSendChat()} disabled={!chatMessage.trim() || chatLoading || audio.isTranscribing}>
                  Invia <span className="arrow">→</span>
                </button>
              </div>
            </div>

            {!peritoExpanded && (
              <div className="aside">
                <div className="smallcap" style={{ color: 'var(--ink-soft)' }}>Azioni</div>
                <div className="stack" style={{ marginTop: 12 }}>
                  {!isProcessing && result && (
                    <div style={{ width: '100%' }}>
                      <button className="btn ghost" style={{ width: '100%', justifyContent: 'center' }}
                        onClick={handleToggleRuns}>
                        {runsExpanded ? '▼ File di validazione' : '▸ File di validazione'}
                        {runsExpanded && runsFiles.length > 0 && (
                          <span className="mono" style={{ marginLeft: 8, color: 'var(--ink-mute)', fontSize: 11 }}>
                            · {runsFiles.length}
                          </span>
                        )}
                      </button>
                      {runsExpanded && (
                        <div style={{ marginTop: 10, border: '1px solid var(--rule)', padding: 10, background: 'var(--paper)' }}>
                          {runsLoading && (
                            <div className="serif-it" style={{ color: 'var(--ink-mute)', fontSize: 12 }}>
                              <span className="spinner" /> caricamento…
                            </div>
                          )}
                          {runsError && !runsLoading && (
                            <div className="mono" style={{ color: 'var(--gold)', fontSize: 11 }}>
                              {runsError}
                            </div>
                          )}
                          {!runsLoading && !runsError && runsFiles.length > 0 && (
                            <div className="stack" style={{ gap: 6 }}>
                              {runsFiles.map(f => (
                                <a key={f.name}
                                   href={`http://localhost:8000${f.download_url}`}
                                   download={f.name}
                                   className="mono"
                                   style={{
                                     fontSize: 11,
                                     padding: '6px 8px',
                                     borderBottom: '1px solid var(--rule)',
                                     color: 'var(--ink)',
                                     textDecoration: 'none',
                                     display: 'flex',
                                     justifyContent: 'space-between',
                                     alignItems: 'center',
                                     gap: 8,
                                   }}
                                   title={`Scarica ${f.name}`}>
                                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {f.name}
                                  </span>
                                  <span style={{ color: 'var(--ink-mute)', flexShrink: 0 }}>
                                    {fmtFileSize(f.size)} ↓
                                  </span>
                                </a>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                  {!isProcessing && dirtyCount > 0 && (
                    <button className="btn" style={{ width: '100%', justifyContent: 'center', background: 'var(--gold)', borderColor: 'var(--gold)' }}
                      onClick={handleSaveDraft} disabled={savingDraft}>
                      {savingDraft ? 'Salvataggio…' : `Salva bozza · ${dirtyCount}`}
                    </button>
                  )}
                  {!isProcessing && isPending && (
                    <>
                      <button className="btn" style={{ width: '100%', justifyContent: 'center' }}
                        onClick={() => setShowApproveModal(true)}>
                        Approva e pubblica <span className="arrow">→</span>
                      </button>
                      <button className="btn danger" style={{ width: '100%', justifyContent: 'center' }}
                        onClick={() => setShowRejectModal(true)}>
                        Rifiuta
                      </button>
                    </>
                  )}
                  {!isProcessing && isApproved && (
                    <>
                      <button className="btn ghost" style={{ width: '100%', justifyContent: 'center' }}
                        onClick={handleRegenWiki} disabled={regenLoading}>
                        {regenLoading ? 'rigenera…' : 'Rigenera wiki'}
                      </button>
                      <a className="btn ghost" style={{ width: '100%', justifyContent: 'center' }}
                         href="obsidian://open?vault=LLM sicurezza">
                        Apri in Obsidian <span className="arrow">→</span>
                      </a>
                      <button className="btn danger" style={{ width: '100%', justifyContent: 'center' }}
                        onClick={() => setShowDeleteWizard(true)}>
                        Elimina evento (wizard)
                      </button>
                    </>
                  )}
                  {!isApproved && (
                    <button className="btn danger" style={{ width: '100%', justifyContent: 'center' }}
                      onClick={handleQuickDelete} disabled={deleting}>
                      {deleting ? 'Eliminazione…' : 'Elimina'}
                    </button>
                  )}
                </div>
              </div>
            )}
          </aside>
        </div>
      )}

      {/* Approve modal */}
      {showApproveModal && (
        <div className="modal-backdrop" onClick={() => setShowApproveModal(false)}>
          <div className="modal-panel" onClick={e => e.stopPropagation()}>
            <div className="kicker">Approvazione</div>
            <h3 className="serif" style={{ fontSize: 28, fontWeight: 500, margin: '6px 0 12px', color: 'var(--ink)' }}>Approva evento</h3>
            <p className="serif-it" style={{ color: 'var(--ink-soft)', marginBottom: 18 }}>
              Inserisci il tuo nome. La wiki verrà aggiornata automaticamente con i dati approvati.
            </p>
            <div className="field" style={{ marginBottom: 18 }}>
              <label>Operatore</label>
              <input className="input" autoFocus placeholder="es. wert"
                value={operatorName}
                onChange={e => setOperatorName(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleApprove()} />
            </div>
            <div className="row" style={{ justifyContent: 'flex-end' }}>
              <button className="btn ghost" onClick={() => setShowApproveModal(false)}>Annulla</button>
              <button className="btn" onClick={handleApprove} disabled={!operatorName.trim() || approving}>
                {approving ? <><span className="spinner" /> Approvazione…</> : <>Conferma <span className="arrow">→</span></>}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reject modal */}
      {showRejectModal && (
        <div className="modal-backdrop" onClick={() => setShowRejectModal(false)}>
          <div className="modal-panel" onClick={e => e.stopPropagation()}>
            <div className="kicker" style={{ color: 'var(--danger)' }}>Rifiuto</div>
            <h3 className="serif" style={{ fontSize: 28, fontWeight: 500, margin: '6px 0 12px', color: 'var(--ink)' }}>Rifiuta evento</h3>
            <p className="serif-it" style={{ color: 'var(--ink-soft)', marginBottom: 18 }}>
              Specifica il motivo del rifiuto per l'audit trail.
            </p>
            <div className="field" style={{ marginBottom: 18 }}>
              <label>Motivo</label>
              <textarea className="input" autoFocus rows={4}
                placeholder="motivo del rifiuto…"
                value={rejectReason}
                onChange={e => setRejectReason(e.target.value)} />
            </div>
            <div className="row" style={{ justifyContent: 'flex-end' }}>
              <button className="btn ghost" onClick={() => setShowRejectModal(false)}>Annulla</button>
              <button className="btn danger" onClick={handleReject} disabled={!rejectReason.trim() || rejecting}>
                {rejecting ? <><span className="spinner" /> Rifiuto…</> : 'Conferma rifiuto'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showDeleteWizard && (
        <DeleteIncidentWizard
          incidentId={incidentId}
          eventId={eventId}
          onClose={() => setShowDeleteWizard(false)}
          onCompleted={() => { setShowDeleteWizard(false); onBack(); }}
        />
      )}
    </div>
  );
};
