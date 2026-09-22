"""
╔══════════════════════════════════════════════════════════════╗
║   SISTEMA ANALISI TESTIMONIANZE — Acqua Riva S.p.A. · v2    ║
║   Smart Factory · Sapienza 2025/26 · Ontologia-First         ║
║                                                              ║
║   USO: python analisi_testimonianze.py                       ║
║   Produce: report individuali · report riassuntivo · note    ║
╚══════════════════════════════════════════════════════════════╝
"""

# ==============================================================
# ████  CONFIGURAZIONE — MODIFICA SOLO QUESTA SEZIONE  ████
# ==============================================================

API_KEY    = ""  # Inserire chiave via UI (Impostazioni) o nel file .env

BASE_DIR   = r"."

FILES_INPUT = [
    "Stefano.MP3",
    "giusy.MP3",
    "Nicola.MP3",
]

N_RUNS     = 5
OUTPUT_DIR = "output_reports"

# Numero di trascrizioni indipendenti per audio (per stabilizzare lo score qualità).
# Il LLM di trascrizione non è deterministico: più campioni → media stabile dei marker.
# Costo: moltiplica per N le chiamate LLM di trascrizione (audio-only).
N_TRASCRIZIONI_QUALITA = 3

MODELLO_EXTRACTOR = "gemini-3.5-flash"
MODELLO_JUDGE     = "gemini-3.5-flash"

SOGLIE = {
    "completezza_min":   50.0,
    "coerenza_min":      75.0,
    "faithfulness_min":   7.0,
    "hallucination_max": 10.0,
    "qualita_audio_min": 70.0,
}

MAX_RETRY   = 20
ATTESA_BASE = 30

ONTOLOGIA_PATH = None
SCHEMA_PATH    = None

# ==============================================================
# ████  FINE CONFIGURAZIONE  ████
# ==============================================================

import subprocess, sys

def _install(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])

for _pkg in ["google-genai", "openpyxl", "pandas", "pydub", "soundfile", "PyPDF2"]:
    try:
        __import__(_pkg.replace("-", "_").split("_")[0])
    except ImportError:
        print(f"  Installazione {_pkg}...")
        _install(_pkg)

import os, re, json, time, copy, random, statistics, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field as _field
from datetime import datetime
from openpyxl import load_workbook
from openpyxl.styles import Alignment, PatternFill
from google import genai
from pydub import AudioSegment
from pydub.effects import normalize

from client_pool import ClientPool, init_pool, get_pool, is_quota_error

BASE_DIR       = os.path.abspath(BASE_DIR)
OUTPUT_DIR     = os.path.join(BASE_DIR, OUTPUT_DIR)
ONTOLOGIA_PATH = ONTOLOGIA_PATH or os.path.join(BASE_DIR, "Nuova_Ontologia_Tabellare.xlsx")
SCHEMA_PATH    = SCHEMA_PATH    or os.path.join(BASE_DIR, "Nuovo_schema_Report.xlsx")
os.makedirs(OUTPUT_DIR, exist_ok=True)

FORMATI_AUDIO = {".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac"}
FORMATI_TESTO = {".txt", ".pdf"}

# Runtime globals -- initialized by main() or run_pipeline()
client = None
ONTOLOGIA = None
GUIDA_ONTOLOGIA = ""
SCHEMA_JSON_BASE = {}
DATI_PROSODICI = {}

# ==============================================================
# SEZIONE 1 -- API
# ==============================================================

_print_lock = threading.Lock()

def _tprint(msg):
    """Print thread-safe per zone parallele."""
    with _print_lock:
        print(msg)


def _carica_chiavi():
    """Carica la lista di chiavi Gemini.
    Priorità: GEMINI_API_KEYS (CSV) > GEMINI_API_KEY > API_KEY costante."""
    keys_csv = os.environ.get("GEMINI_API_KEYS", "").strip()
    if keys_csv:
        keys = [k.strip() for k in keys_csv.split(",") if k.strip()]
        if keys:
            return keys
    single = os.environ.get("GEMINI_API_KEY") or API_KEY
    return [single] if single else []


def _inizializza_pool(api_key_principale: str | None = None):
    """Inizializza il pool globale. Se viene passata api_key_principale e
    GEMINI_API_KEYS non è impostata, usa solo quella (back-compat con run_pipeline)."""
    keys_csv = os.environ.get("GEMINI_API_KEYS", "").strip()
    if keys_csv:
        keys = [k.strip() for k in keys_csv.split(",") if k.strip()]
    elif api_key_principale:
        keys = [api_key_principale]
    else:
        keys = _carica_chiavi()
    if not keys:
        raise ValueError("Nessuna chiave Gemini configurata (GEMINI_API_KEYS, GEMINI_API_KEY o API_KEY).")
    cooldown_base = float(os.environ.get("GEMINI_COOLDOWN_BASE_S", "60"))
    return init_pool(keys, cooldown_base_s=cooldown_base)


def _test_connessione_pool(pool, modello, log_fn=None) -> list[int]:
    """Testa OGNI chiave del pool. Marca le chiavi morte in cooldown.
    Ritorna la lista degli indici di chiavi che hanno risposto OK.
    Solleva RuntimeError solo se TUTTE falliscono.

    log_fn: funzione opzionale per log (es. _logp). Default: print thread-safe.
    """
    log = log_fn or _tprint
    keys_ok: list[int] = []
    keys_failed: list[tuple[int, str]] = []
    for _ in range(pool.size):
        idx, c = pool.acquire()
        try:
            c.models.generate_content(model=modello, contents="OK")
            keys_ok.append(idx)
            pool.release(idx, mark_quota_hit=False)
        except Exception as e:
            if is_quota_error(e):
                pool.release(idx, mark_quota_hit=True)
                keys_failed.append((idx, "quota/429"))
            else:
                pool.release(idx, mark_quota_hit=False)
                keys_failed.append((idx, str(e)[:120]))
    log(f"Connection test: {len(keys_ok)}/{pool.size} chiavi OK · {len(keys_failed)} fallite")
    for idx, msg in keys_failed:
        log(f"  Chiave #{idx}: {msg}")
    if not keys_ok:
        detail = "; ".join(f"#{i}={m}" for i, m in keys_failed) or "nessun dettaglio"
        raise RuntimeError(
            f"Tutte le {pool.size} chiavi Gemini sono inutilizzabili al test di connessione. "
            f"Dettagli: {detail}. Verifica le chiavi in Impostazioni o attendi che la quota si resetti."
        )
    return keys_ok


def _banner(titolo):
    print(f"\n{'█'*62}\n  {titolo}\n{'█'*62}")

def _sep():
    print("─" * 62)

def chiama_gemini(contenuti, modello=None, client_override=None):
    """Chiama Gemini con retry su quota.

    - Se client_override è fornito, usa SOLO quel client (necessario per workflow
      che dipendono da file uploadati su una chiave specifica).
    - Altrimenti usa il pool globale (rotazione automatica su 429) se inizializzato,
      con fallback al `client` globale per back-compat.
    """
    from google.genai import types
    modello = modello or MODELLO_EXTRACTOR
    cfg = types.GenerateContentConfig(max_output_tokens=65536, temperature=0.1)
    pool = get_pool()

    for tentativo in range(1, MAX_RETRY + 1):
        try:
            if client_override is not None:
                return client_override.models.generate_content(
                    model=modello, contents=contenuti, config=cfg
                )
            if pool is not None:
                with pool.lease() as c:
                    return c.models.generate_content(
                        model=modello, contents=contenuti, config=cfg
                    )
            # Fallback: client globale legacy.
            return client.models.generate_content(
                model=modello, contents=contenuti, config=cfg
            )
        except Exception as e:
            if is_quota_error(e) and tentativo < MAX_RETRY:
                # Con il pool il cooldown è già stato applicato dalla lease.
                # Aggiungiamo un piccolo backoff supplementare solo se NON c'è pool
                # (altrimenti la prossima lease attende automaticamente).
                if pool is None and client_override is None:
                    jitter = random.uniform(0, 10)
                    attesa = ATTESA_BASE * (2 ** (tentativo - 1)) + jitter
                    print(f"  Quota -- tentativo {tentativo}/{MAX_RETRY}. Attesa {attesa:.0f}s...")
                    time.sleep(attesa)
                elif client_override is not None:
                    # Override fissa la chiave: backoff locale.
                    jitter = random.uniform(0, 10)
                    attesa = ATTESA_BASE * (2 ** (tentativo - 1)) + jitter
                    _tprint(f"  Quota su chiave fissa -- tentativo {tentativo}/{MAX_RETRY}. "
                            f"Attesa {attesa:.0f}s...")
                    time.sleep(attesa)
                # Se pool attivo senza override: prossimo giro userà un'altra chiave.
            else:
                raise

# ==============================================================
# SEZIONE 2 -- HELPER
# ==============================================================

def pulisci_testo(t):
    if t is None: return None
    return str(t).strip()

def normalizza_valore(valore):
    if isinstance(valore, list):
        return "; ".join(str(v) for v in valore)
    testo = str(valore).strip()
    if testo.startswith("[") and testo.endswith("]"):
        try:
            lst = json.loads(testo)
            if isinstance(lst, list):
                return "; ".join(str(v) for v in lst)
        except json.JSONDecodeError:
            pass
    return testo

def estrai_json_sicuro(testo):
    testo = re.sub(r"```(?:json)?", "", testo).replace("```", "").strip()
    try:
        return json.loads(testo)
    except json.JSONDecodeError:
        pass
    inizio = testo.find("{")
    if inizio == -1:
        raise ValueError(f"Nessun JSON trovato:\n{testo[:300]}")
    profondita, fine = 0, -1
    in_stringa, escape_next = False, False
    for i, ch in enumerate(testo[inizio:], start=inizio):
        if escape_next: escape_next = False; continue
        if ch == "\\": escape_next = True; continue
        if ch == '"' and not escape_next: in_stringa = not in_stringa
        if not in_stringa:
            if ch == "{": profondita += 1
            elif ch == "}":
                profondita -= 1
                if profondita == 0: fine = i + 1; break
    if fine == -1:
        raise ValueError("JSON non bilanciato.")
    return json.loads(testo[inizio:fine])

def costruisci_id_evento(data_str, tipo="incidente"):
    MESI = {"gennaio":"01","febbraio":"02","marzo":"03","aprile":"04",
            "maggio":"05","giugno":"06","luglio":"07","agosto":"08",
            "settembre":"09","ottobre":"10","novembre":"11","dicembre":"12"}
    PREFISSI = {"incidente":"INC","near miss":"NM","guasto":"GU","manutenzione":"MAN"}
    tipo_low  = str(tipo).lower()
    prefisso  = next((v for k,v in PREFISSI.items() if k in tipo_low), "INC")
    data_low  = str(data_str).lower().strip()
    for nome, num in MESI.items():
        if nome in data_low:
            m_g = re.search(r"(\d{1,2})", data_low)
            m_y = re.search(r"(\d{4})", data_low)
            gg   = m_g.group(1).zfill(2) if m_g else "XX"
            yyyy = m_y.group(1) if m_y else "XXXX"
            return f"{prefisso}-{gg}{num}{yyyy}"
    m = re.search(r"(\d{1,2})[/\-](\d{2})[/\-](\d{4})", data_low)
    if m: return f"{prefisso}-{m.group(1).zfill(2)}{m.group(2)}{m.group(3)}"
    return f"{prefisso}-XXXXXXXX"

FILL_DISCORDANTE  = PatternFill(start_color="FFD966", end_color="FFD966", fill_type="solid")
FILL_TRONCAMENTO  = PatternFill(start_color="FFB347", end_color="FFB347", fill_type="solid")

def _norm_campo(nome):
    return re.sub(r'\s*\([^)]+\)\s*$', '', str(nome)).strip()

def _e_troncato_risposta(resp):
    try:
        return resp.candidates[0].finish_reason.name == "MAX_TOKENS"
    except Exception:
        return False

def scrivi_cella(ws, row, col, valore):
    valore_str = str(valore).strip() if valore is not None else ""
    finale = "N/D" if (not valore_str or valore_str.lower() in
                       ("n/d","null","none","n/a","")) else normalizza_valore(valore)
    c = ws.cell(row=row, column=col)
    c.value = finale
    c.alignment = Alignment(wrap_text=True, vertical="top")
    if isinstance(finale, str) and finale.startswith("[⚠ INCOMPLETO]"):
        c.fill = FILL_TRONCAMENTO
    return finale

def leggi_colonne_foglio(ws, row_header):
    mappa = {}
    for col in range(1, ws.max_column + 1):
        val = ws.cell(row=row_header, column=col).value
        if val:
            mappa[str(val).strip()] = col
    return mappa

def _leggi_mappa_entita_colonne(ws, row_sezioni, row_header):
    cur_ent = None
    mappa = {}
    for col in range(1, ws.max_column + 1):
        ent_val = ws.cell(row=row_sezioni, column=col).value
        if ent_val and str(ent_val).strip():
            cur_ent = str(ent_val).strip().title()
        campo_val = ws.cell(row=row_header, column=col).value
        if campo_val and cur_ent:
            campo = str(campo_val).strip()
            if cur_ent not in mappa:
                mappa[cur_ent] = {}
            mappa[cur_ent][campo] = col
    return mappa

def _blocco_dict(json_onto, entita):
    block = json_onto.get(entita, {})
    if isinstance(block, list):
        block = block[0] if (block and isinstance(block[0], dict)) else {}
    return block if isinstance(block, dict) else {}

SCHEMA_INFO = {
    "🏭 Evento":        {"row_header": 5, "row_data": 7, "entita": ["Evento"]},
    "📍 Spaziale":      {"row_header": 5, "row_data": 7,
                         "entita": ["Stabilimento","Reparto","Linea","Macchinario","Componente"]},
    "👤 Persona":       {"row_header": 5, "row_data": 7, "entita": ["Persona"]},
    "⚙️ Causa":        {"row_header": 5, "row_data": 7,
                         "entita": ["Contesto Operativo","Fattore Ambientale",
                                    "Causa Tecnica","Fattore Umano"]},
    "💥 Impatto":       {"row_header": 5, "row_data": 7,
                         "entita": ["Danno Infrastrutturale","Conseguenza Immediata",
                                    "Perdite Stimate"]},
    "✅ Azione":        {"row_header": 5, "row_data": 7, "entita": ["Azione"]},
    "🎙 Testimonianza": {"row_header": 5, "row_data": 7, "entita": ["Testimonianza"]},
    "🚚 Fornitore":     {"row_header": 5, "row_data": 7, "entita": ["Fornitore"]},
    "📜 Normativo":     {"row_header": 5, "row_data": 7, "entita": ["Riferimento Normativo"]},
}
def scrivi_report_multifoglio(template_path, json_onto, audio_sorgente,
                               n_run, id_evento, metriche_testimonianza=None,
                               discordanze_inter=None, testimoni_lista=None):
    wb  = load_workbook(template_path)
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    disc_lookup = {}
    if discordanze_inter:
        for d in discordanze_inter:
            campo = d.get("campo", "")
            disc_lookup[campo] = d
            disc_lookup[_norm_campo(campo)] = d
            attr = campo.split(".")[-1] if "." in campo else campo
            disc_lookup.setdefault(attr, d)
            disc_lookup.setdefault(_norm_campo(attr), d)
    for sheet_name, info in SCHEMA_INFO.items():
        if sheet_name not in wb.sheetnames:
            continue
        ws        = wb[sheet_name]
        row_h     = info["row_header"]
        row_start = info["row_data"]
        mappa_col = leggi_colonne_foglio(ws, row_h)
        entita_list = info["entita"]
        ws.cell(row=2, column=1).value = id_evento
        ws.cell(row=2, column=3).value = audio_sorgente
        ws.cell(row=2, column=5).value = str(n_run)
        ws.cell(row=2, column=7).value = now
        if sheet_name == "👤 Persona":
            persone = json_onto.get("Persona", [])
            if not isinstance(persone, list): persone = [persone]
            for i, persona in enumerate(persone):
                if not isinstance(persona, dict): persona = {}
                row = row_start + i
                ruolo         = persona.get("Ruolo", {}) or {}
                turno         = persona.get("Turno", {}) or {}
                coinvolgimento= persona.get("Coinvolgimento", {}) or {}
                prognosi      = persona.get("Prognosi", {}) or {}
                if not isinstance(ruolo, dict):          ruolo = {}
                if not isinstance(turno, dict):          turno = {}
                if not isinstance(coinvolgimento, dict): coinvolgimento = {}
                if not isinstance(prognosi, dict):       prognosi = {}
                persona_flat = {}
                persona_flat.update(ruolo)
                persona_flat.update(turno)
                persona_flat.update(coinvolgimento)
                persona_flat.update(prognosi)
                persona_flat.update({k: v for k, v in persona.items()
                                     if k not in ("Ruolo","Turno","Coinvolgimento","Prognosi")})
                for nome_col, col_idx in mappa_col.items():
                    if nome_col == "ID_Persona":
                        val = genera_id_persona(persona)
                    else:
                        val = (persona_flat.get(nome_col) or
                               persona_flat.get(nome_col.replace("_"," ")))
                    scrivi_cella(ws, row, col_idx, val)
            continue
        if sheet_name == "✅ Azione":
            azioni = json_onto.get("Azione", [])
            if not isinstance(azioni, list): azioni = [azioni]
            for i, azione in enumerate(azioni):
                if not isinstance(azione, dict): azione = {}
                row = row_start + i
                for nome_col, col_idx in mappa_col.items():
                    val = (f"AZ-{i+1:03d}" if nome_col == "ID_Azione"
                           else (azione.get(nome_col) or azione.get(nome_col.replace("_"," "))))
                    scrivi_cella(ws, row, col_idx, val)
            continue
        if sheet_name == "🎙 Testimonianza":
            if testimoni_lista:
                for t_idx, test_data in enumerate(testimoni_lista):
                    t_row = row_start + t_idx
                    for nome_col, col_idx in mappa_col.items():
                        val = test_data.get(nome_col, "N/D")
                        scrivi_cella(ws, t_row, col_idx, val)
                continue
            test_raw = json_onto.get("Testimonianza", {})
            if isinstance(test_raw, list):
                testimoni = [t for t in test_raw if isinstance(t, dict)] or [{}]
            else:
                testimoni = [test_raw if isinstance(test_raw, dict) else {}]
            met = metriche_testimonianza or {}
            for t_idx, test_data in enumerate(testimoni):
                t_row = row_start + t_idx
                for nome_col, col_idx in mappa_col.items():
                    if nome_col == "ID_Testimone":
                        val = test_data.get("ID_Testimone", f"T-{t_idx+1:03d}")
                    elif nome_col == "File_Audio":
                        val = audio_sorgente
                    elif nome_col in ("Completezza_Campi_%","Coerenza_Inter_Run_%",
                                      "Campi_Discordanti","Qualità_Trascrizione"):
                        val = met.get(nome_col, "N/D")
                    else:
                        val = (test_data.get(nome_col) or test_data.get(nome_col.replace("_"," ")))
                    scrivi_cella(ws, t_row, col_idx, val)
            continue
        if sheet_name == "📍 Spaziale":
            mappa_ent = _leggi_mappa_entita_colonne(ws, row_h - 2, row_h)
            CAMPI_EXC = {"Stato_Report", "Stato Report"}
            for entita in entita_list:
                block    = _blocco_dict(json_onto, entita)
                ent_cols = mappa_ent.get(entita, {})
                for campo, col_idx in ent_cols.items():
                    if campo in CAMPI_EXC: continue
                    if campo == "ID_Evento":
                        scrivi_cella(ws, row_start, col_idx, id_evento); continue
                    val = block.get(campo) or block.get(campo.replace("_"," "))
                    disc_c = disc_lookup.get(campo) or disc_lookup.get(f"{entita}.{campo}")
                    if disc_c:
                        imp  = disc_c.get("impatto_sicurezza","?")
                        nota = disc_c.get("spiegazione","")
                        parti = [f"{n}: «{v}»" for n, v in disc_c.get("valori",{}).items()
                                 if str(v).lower() not in ("n/d","null","none","n/a","")]
                        val = f"[DISC {imp}]\n"+"\n".join(parti)+f"\n-> {nota}"
                    scrivi_cella(ws, row_start, col_idx, val)
                    if disc_c:
                        ws.cell(row=row_start, column=col_idx).fill = FILL_DISCORDANTE
            continue
        CAMPI_ESCLUSI_EXCEL = {"Stato_Report", "Stato Report"}
        row = row_start
        for nome_col, col_idx in mappa_col.items():
            if nome_col in CAMPI_ESCLUSI_EXCEL:
                continue
            if nome_col == "ID_Evento":
                scrivi_cella(ws, row, col_idx, id_evento); continue
            val = None
            for entita in entita_list:
                block = _blocco_dict(json_onto, entita)
                val   = block.get(nome_col) or block.get(nome_col.replace("_"," "))
                if val: break
            disc_info = disc_lookup.get(nome_col) or disc_lookup.get(_norm_campo(nome_col))
            if not disc_info:
                for entita in entita_list:
                    disc_info = (disc_lookup.get(f"{entita}.{nome_col}") or
                                 disc_lookup.get(f"{entita}.{_norm_campo(nome_col)}"))
                    if disc_info: break
            if disc_info:
                imp   = disc_info.get("impatto_sicurezza", "?")
                nota  = disc_info.get("spiegazione", "")
                parti = [f"{n}: «{v}»" for n, v in disc_info.get("valori", {}).items()
                         if str(v).lower() not in ("n/d","null","none","n/a","")]
                val = f"[DISC {imp}]\n" + "\n".join(parti) + f"\n-> {nota}"
            scrivi_cella(ws, row, col_idx, val)
            if disc_info:
                ws.cell(row=row, column=col_idx).fill = FILL_DISCORDANTE
    return wb


# ==============================================================
# SEZIONE 3 -- ONTOLOGIA
# ==============================================================

def carica_ontologia(percorso):
    wb  = load_workbook(percorso, read_only=True)
    ws  = wb["Ontologia Tabellare"]
    ont = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        n      = row[0]
        entita = pulisci_testo(row[1])
        if not entita or not str(n).isdigit(): continue
        attr_raw  = pulisci_testo(row[2]) or ""
        attributi = [a.strip() for a in re.split(r"\s*·\s*|,|;", attr_raw) if a.strip()]
        ont[entita] = {"id": int(n), "attributi": attributi,
                       "attr_raw": attr_raw,
                       "relazione": pulisci_testo(row[3]) or "-",
                       "origine":   pulisci_testo(row[4]) or "-",
                       "destinazione": pulisci_testo(row[5]) or "-"}
    wb.close()
    return ont

CONDENSATI = {
    # storici
    "Descrizione_Narrativa","Modalità_Accadimento","Anomalie_Pre_Evento",
    "Stato_ambiente_di_lavoro","Modalità_Cedimento","Stato_Fisico_Stimato",
    "Fattore_Organizzativo","Descrizione_Danno","Descrizione",
    "Perdita_Economica_Totale","Costo_Stimato","Costo_Fermo_Orario",
    "Costo_Ricambi","Perdita_Produzione_Stimata",
    # aggiunti: campi inferenziali (classificazioni ontologiche e stime qualitative)
    "Tipo_Guasto","Difetto_Materiale","Livello_Attenzione",
    "Sistema_Controllo","Componente_Ceduto",
}

CAMPI_TESTUALI = CONDENSATI | {"Nota", "Note", "Descrizione_Breve"}
VOCALI_IT = frozenset("aeiouàèéìíîòóùú")
PUNTEGGIATURA_FINALE = frozenset('.!?,:;)\'"»-')

def _trova_troncamenti(j):
    sospetti = []
    def _scan(obj, percorso):
        if isinstance(obj, dict):
            for k, v in obj.items():
                p = f"{percorso}.{k}" if percorso else k
                if isinstance(v, str):
                    v_s = v.strip()
                    is_desc = (k in CAMPI_TESTUALI or
                               any(kw in k for kw in ("Descrizione","Narrativa","Modalità","Nota")))
                    if (is_desc and len(v_s) > 25
                            and v_s.lower() not in ("n/d","null","none","n/a","")
                            and v_s[-1] not in PUNTEGGIATURA_FINALE
                            and v_s[-1] not in VOCALI_IT
                            and v_s[-1].isalpha()):
                        sospetti.append((p, v_s))
                else:
                    _scan(v, p)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _scan(item, f"{percorso}[{i}]")
    _scan(j, "")
    return sospetti

def _marca_troncamenti(j):
    troncati = _trova_troncamenti(j)
    if not troncati: return 0
    percorsi = {p for p, _ in troncati}
    def _applica(obj, cur):
        if isinstance(obj, dict):
            for k in list(obj.keys()):
                p = f"{cur}.{k}" if cur else k
                if isinstance(obj[k], str) and p in percorsi:
                    obj[k] = "[⚠ INCOMPLETO] " + obj[k]
                else:
                    _applica(obj[k], p)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _applica(item, f"{cur}[{i}]")
    _applica(j, "")
    return len(troncati)

_KW_IMMEDIATA  = {"emergenz","arresto","118","soccor","medicaz","trasport","isolamento",
                   "allerta","messa in sicurezza","capoturno","notific","responsabil",
                   "chiamat","pulsante","evacuaz","primo soccorso","allontanament"}
_KW_CORRETTIVA = {"sostituz","ripristin","riparaz","cambio","rimozion","install",
                   "documentazion","trasmission","asl","inail","ripulizz",
                   "analisi tecnica","identific","indagin","verifica causa"}
_KW_PREVENTIVA = {"ispezion","procedur","formazion","revisione","audit",
                   "aggiornament","pianific","prevent","futur","estesa",
                   "controllo periodico","monitoragg","valutazion rischio"}

def _postprocess_azioni(j):
    for az in (j.get("Azione") or []):
        if not isinstance(az, dict): continue
        if str(az.get("Tipo_Azione","")).lower() not in ("n/d","null","none","n/a",""): continue
        desc = str(az.get("Descrizione","")).lower()
        if any(kw in desc for kw in _KW_IMMEDIATA):
            az["Tipo_Azione"] = "Immediata"
        elif any(kw in desc for kw in _KW_PREVENTIVA):
            az["Tipo_Azione"] = "Preventiva"
        else:
            az["Tipo_Azione"] = "Correttiva"

def _postprocess_tipo_danno(j):
    inf = j.get("Danno Infrastrutturale", {})
    if not isinstance(inf, dict): return
    if str(inf.get("Tipo_Danno","")).lower() not in ("n/d","null","none","n/a",""): return
    ev   = j.get("Evento", {}) or {}
    caus = j.get("Causa Tecnica", {}) or {}
    testo = " ".join([str(inf.get("Descrizione_Danno","")),
                      str(ev.get("Descrizione_Narrativa","")),
                      str(caus.get("Modalità_Cedimento",""))]).lower()
    tipi = []
    if any(kw in testo for kw in ["plc","elettric","cortocircuito","quadro","scintill"]):
        tipi.append("Elettrico")
    if any(kw in testo for kw in ["rottura","frattura","esplosione","cedimento","spezzat",
                                    "scoppio","supporto","stella","meccanico"]):
        tipi.append("Meccanico")
    if tipi: inf["Tipo_Danno"] = ", ".join(tipi)

def _postprocess_persona_coinvolgimento(persona):
    coin = persona.get("Coinvolgimento")
    if not isinstance(coin, dict): coin = {}
    if str(coin.get("Tipo_Coinvolgimento","")).lower() not in ("n/d","null","none","n/a",""): return
    prognosi = persona.get("Prognosi") or {}
    giorni   = str(prognosi.get("Giorni_Prognosi","")).strip().lower()
    lesione  = str(prognosi.get("Tipo_Lesione","")).strip().lower()
    stato    = str(persona.get("Stato_Post_Evento","") or coin.get("Stato_Post_Evento","")).lower()
    if giorni not in ("n/d","null","none","n/a","") or lesione not in ("n/d","null","none","n/a",""):
        coin["Tipo_Coinvolgimento"] = "Infortunato"
    elif "illeso" in stato or "illesa" in stato:
        coin["Tipo_Coinvolgimento"] = "Illeso"
    elif any(kw in stato for kw in ["soccors","emergenz","pulsante","arresto"]):
        coin["Tipo_Coinvolgimento"] = "Primo_Soccorso"
    else:
        coin["Tipo_Coinvolgimento"] = "Testimone"
    persona["Coinvolgimento"] = coin

def _postprocess_componente_nome(j):
    comp = j.get("Componente", {})
    if not isinstance(comp, dict): return
    nome_comp = str(comp.get("Nome","")).strip()
    if nome_comp.lower() in ("n/d","null","none","n/a",""): return
    _AZIENDE = ["s.p.a","s.r.l","srl","spa","acqua riva","acquariva","riva"]
    _GEO     = ["frosinone","napoli","roma","milano","torino","firenze"]
    if any(kw in nome_comp.lower() for kw in _AZIENDE) or nome_comp.lower() in _GEO:
        comp["Nome"] = "N/D"

def applica_postprocessing(j):
    _postprocess_azioni(j)
    _postprocess_tipo_danno(j)
    _postprocess_componente_nome(j)
    persone = j.get("Persona", [])
    if isinstance(persone, list):
        for p in persone:
            if isinstance(p, dict): _postprocess_persona_coinvolgimento(p)
    elif isinstance(persone, dict):
        _postprocess_persona_coinvolgimento(persone)

KEYPAD = {
    'A':2,'B':2,'C':2,'D':3,'E':3,'F':3,'G':4,'H':4,'I':4,
    'J':5,'K':5,'L':5,'M':6,'N':6,'O':6,'P':7,'Q':7,'R':7,'S':7,
    'T':8,'U':8,'V':8,'W':9,'X':9,'Y':9,'Z':9
}

def nome_a_codice_telefonico(nome):
    return ''.join(str(KEYPAD[c]) for c in str(nome).upper() if c in KEYPAD) or '000'

def genera_id_persona(persona_dict):
    nome    = (persona_dict.get('Nome') or persona_dict.get('Nome_Completo') or '') 
    cognome = (persona_dict.get('Cognome') or '')
    nome    = nome    if nome    and nome.lower()    not in ("n/d","null","none","n/a") else ""
    cognome = cognome if cognome and cognome.lower() not in ("n/d","null","none","n/a") else ""
    nome_full = f"{nome} {cognome}".strip()
    if not nome_full:
        import hashlib
        seed = str(id(persona_dict)) + str(len(persona_dict))
        return "P-ANON-" + hashlib.md5(seed.encode()).hexdigest()[:6].upper()
    return f"P-{nome_a_codice_telefonico(nome_full)}"

ENTITA_EVENTO = {
    "Evento","Stabilimento","Reparto","Linea","Macchinario","Componente",
    "Contesto Operativo","Fattore Ambientale","Causa Tecnica","Fattore Umano",
    "Danno Infrastrutturale","Conseguenza Immediata","Perdite Stimate",
    "Fornitore","Riferimento Normativo"
}

def get_entita_prefix(campo):
    m = re.match(r"^([^\.\[]+)", campo)
    return m.group(1).strip() if m else campo

def normalizza_per_confronto(v):
    if not v or str(v).lower().strip() in ("n/d","null","none","n/a",""): return "n/d"
    v = str(v).lower().strip()
    v = re.sub(r"^(circa\s+(le?\s+)?|intorno\s+alle?\s+)", "", v)
    v = v.replace(";",",").replace(" ,",",").replace(", ",",")
    v = re.sub(r"(\d)\.(\d{3})", r"", v)
    v = re.sub(r"(\d+)\s*(unità|pezzi|ore|€|euro|kg|litri|bar)", r"", v)
    v = re.sub(r"^linea\s+(\d+)$", r"", v)
    v = re.sub(r"plc\s+", "", v)
    v = re.sub(r"\s+", " ", v)
    return v.strip()

def crea_guida_ontologica(ont):
    lines = ["="*60,"  ONTOLOGIA EVENTI INDUSTRIALI -- GUIDA ESTRAZIONE","="*60,
             "","  [DIRETTO]    -> SOLO valori esplicitamente dichiarati. N/D se non detto.",
             "  [CONDENSATO] -> sintetizza, inferenza consentita se motivata.",""]
    for nome, info in sorted(ont.items(), key=lambda x: x[1]["id"]):
        lines.append(f"[{info['id']:02d}] {nome.upper()}")
        if info["relazione"] != "-":
            lines.append(f"     {info['relazione']} ({info['origine']} -> {info['destinazione']})")
        for attr in info["attributi"]:
            tipo = "[CONDENSATO]" if attr in CONDENSATI else "[DIRETTO]  "
            lines.append(f"     . {attr}: {tipo}")
        lines.append("")
    lines.append("="*60)
    return "\n".join(lines)

def crea_schema_json(ont):
    ENTITA_PERSONA_NESTED = {"Ruolo","Turno","Coinvolgimento","Prognosi"}
    ENTITA_ARRAY = {"Azione"}
    schema = {}
    nested = {}
    for nome, info in sorted(ont.items(), key=lambda x: x[1]["id"]):
        if nome in ENTITA_PERSONA_NESTED:
            nested[nome] = {a: "N/D" for a in info["attributi"]}
        elif nome == "Persona":
            template_persona = {"Nome":"N/D","Cognome":"N/D"}
            for a in info["attributi"]: template_persona[a] = "N/D"
            template_persona["Ruolo"]         = {}
            template_persona["Turno"]         = {}
            template_persona["Coinvolgimento"]= {}
            template_persona["Prognosi"]      = {}
            schema["Persona"] = [template_persona]
        elif nome in ENTITA_ARRAY:
            schema[nome] = [{a: "N/D" for a in info["attributi"]}]
        elif nome not in ENTITA_PERSONA_NESTED:
            schema[nome] = {a: "N/D" for a in info["attributi"]}
    if "Persona" in schema and nested:
        for p in schema["Persona"]:
            for entita_n, template_n in nested.items():
                p[entita_n] = dict(template_n)
    return schema


# ==============================================================
# SEZIONE 4 -- INGESTION + VALIDAZIONE
# ==============================================================

PROMPT_VALIDAZIONE = """Analizza il testo e rispondi SOLO con JSON:
{"e_contesto_critico": true/false, "categoria": "incidente|guasto|near_miss|manutenzione|altro", "motivazione": "breve spiegazione"}
Contesto critico = incidenti, infortuni, guasti, near miss, emergenze produttive.
TESTO:\n"""

def valida_contesto(testo):
    try:
        resp = chiama_gemini(PROMPT_VALIDAZIONE + testo[:3000])
        return estrai_json_sicuro(resp.text)
    except Exception:
        return {"e_contesto_critico": True, "categoria": "sconosciuto", "motivazione": "errore validazione"}

def leggi_testo_file(percorso):
    ext = os.path.splitext(percorso)[1].lower()
    if ext == ".txt":
        with open(percorso, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    if ext == ".pdf":
        try:
            import PyPDF2
            with open(percorso, "rb") as f:
                r = PyPDF2.PdfReader(f)
                return "\n".join(p.extract_text() or "" for p in r.pages)
        except Exception as e:
            print(f"  PDF non leggibile: {e}")
            return ""
    return ""

def pulisci_audio(percorso_input):
    nome_base = os.path.splitext(percorso_input)[0]
    output    = f"{nome_base}_pulito.mp3"
    dim_kb    = os.path.getsize(percorso_input) / 1024
    if dim_kb < 10:
        print(f"  File troppo piccolo ({dim_kb:.1f} KB) -- saltato.")
        return None, 0.0
    try:
        audio = AudioSegment.from_file(percorso_input)
        audio = normalize(audio)
        audio = audio.high_pass_filter(300).low_pass_filter(3400)
        banda = audio.high_pass_filter(800).low_pass_filter(1200)
        audio = audio.overlay(banda + 3)
        audio.export(output, format="mp3", bitrate="128k")
        snr = round(20 * (audio.dBFS + 60) / 60, 1)
        return output, snr
    except Exception as e:
        print(f"  Pulizia audio fallita ({e}) -- uso originale")
        return percorso_input, 0.0

def analizza_prosodia(percorso_audio, n_parole=0):
    try:
        import librosa, numpy as np
        y, sr = librosa.load(percorso_audio, mono=True)
        duration = librosa.get_duration(y=y, sr=sr)
        velocita = round(n_parole / max(duration, 1), 2)
        f0, voiced_flag, _ = librosa.pyin(y, fmin=80.0, fmax=400.0)
        if voiced_flag is not None and voiced_flag.any():
            f0_voiced = f0[voiced_flag]
        else:
            f0_voiced = np.array([float("nan")])
        pitch_mean = round(float(np.nanmean(f0_voiced)), 1)
        pitch_std  = round(float(np.nanstd(f0_voiced)), 1)
        rms = librosa.feature.rms(y=y)[0]
        energy_mean = round(float(np.mean(rms)), 4)
        energy_std  = round(float(np.std(rms)), 4)
        return (f"Durata: {round(duration)}s | Velocita: {velocita} p/s | "
                f"Pitch medio: {pitch_mean} Hz (s={pitch_std} Hz) | "
                f"Energia: {energy_mean} (s={energy_std})")
    except Exception as e:
        return f"Analisi prosodica non disponibile: {e}"


# ==============================================================
# SEZIONE 5 -- TRASCRIZIONE
# ==============================================================

PROMPT_TRASCRIZIONE = """Trascrivi fedelmente tutto l'audio, parola per parola.
Regole:
- non riassumere, non omettere nulla
- Parola non udibile -> [INCOMPRENSIBILE]
- Parola incerta -> parola[?]
- Pausa >3s -> [PAUSA]
- SIGLE / ACRONIMI: se il parlante effettua lo spelling lettera per lettera
  (es. "i... n... a... i... l" oppure "i-n-a-i-l"), trascrivi la sigla
  come UNA SOLA parola in maiuscolo (es. "INAIL"). Non marcare le singole
  lettere come [INCOMPRENSIBILE] o [?]: lo spelling e' una resa lecita della
  sigla intera ed equivale alla parola compatta.

GLOSSARIO SIGLE ITALIANE NOTE (sicurezza sul lavoro / enti / normativa):
- INAIL  (Istituto Nazionale Assicurazione contro Infortuni sul Lavoro)
- INPS   (Istituto Nazionale Previdenza Sociale)
- ASL / USL / ATS (Azienda Sanitaria Locale / Unita' Sanitaria Locale)
- RSPP   (Responsabile del Servizio di Prevenzione e Protezione)
- ASPP   (Addetto al Servizio di Prevenzione e Protezione)
- RLS    (Rappresentante dei Lavoratori per la Sicurezza)
- DPI    (Dispositivi di Protezione Individuale)
- DVR    (Documento di Valutazione dei Rischi)
- SPP    (Servizio di Prevenzione e Protezione)
- HSE    (Health, Safety, Environment)
- HACCP  (Hazard Analysis and Critical Control Points)
- OSHA   (Occupational Safety and Health Administration)
- D.Lgs. 81/2008  (Testo Unico sulla Sicurezza sul Lavoro)
- ISO 45001 (norma sui sistemi di gestione della salute e sicurezza)

REGOLA SIGLE: se lo spelling lettera per lettera, o la pronuncia compatta della
sigla, e' foneticamente compatibile (anche solo in modo approssimato) con UNA
delle sigle del glossario qui sopra, trascrivila come quella sigla nota. Es:
se senti "alianl" / "anail" / "inail" / "i n a i l" -> scrivi INAIL.
Preferisci SEMPRE la sigla del glossario rispetto a una sigla inventata.

GLOSSARIO TERMINI DOMINIO ACQUA RIVA (azienda, prodotti, processi):
- Acqua Riva (nome dell'azienda, SEMPRE scritto con due parole separate;
  non unirlo mai in una sola parola)
- Acqua Riva S.p.A. / Acqua Riva Societa' per Azioni
- isobarico (processo di riempimento sotto pressione, con UNA sola lettera
  "b"; non raddoppiare la consonante)
- Acqua Riva e' uno stabilimento di imbottigliamento bevande (acqua naturale,
  acqua frizzante).

REGOLA DOMINIO: se foneticamente il parlante pronuncia il nome dell'azienda
come se fosse una parola unica (es. "akwariva", "akuariva"), trascrivilo
SEMPRE come "Acqua Riva" (due parole). Se senti la parola che indica il
processo di riempimento pronunciata con doppia "b", trascrivila con UNA
sola "b": "isobarico"."""


# ==============================================================
# SEZIONE 6 -- N-RUN ESTRAZIONE ONTOLOGICA
# ==============================================================

def appiattisci_json(j):
    piatto = {}
    PERSONA_NESTED = {"Ruolo","Turno","Coinvolgimento","Prognosi"}
    for entita, dati in j.items():
        if isinstance(dati, dict):
            for k, v in dati.items():
                piatto[f"{entita}.{k}"] = str(v).strip() if v else "N/D"
        elif isinstance(dati, list):
            for i, item in enumerate(dati):
                if not isinstance(item, dict): continue
                for k, v in item.items():
                    if entita == "Persona" and k in PERSONA_NESTED:
                        if isinstance(v, dict):
                            for sk, sv in v.items():
                                piatto[f"Persona[{i}].{k}.{sk}"] = str(sv).strip() if sv else "N/D"
                    else:
                        piatto[f"{entita}[{i}].{k}"] = str(v).strip() if v else "N/D"
    return piatto

CAMPI_NON_AUDIO = {
    "Num_Serie","Data_Installazione","Data_Ultima_Manutenzione",
    "Numero_Guasti_Storici","ID_Pezzo","Codice_Posizione",
    "Perdita_Economica_Totale","Costo_Ricambi","Costo_Fermo_Orario",
    "Costo_Stimato","Articolo","Paese_Origine",
    "Livello_Illuminazione","Rumore_Ambientale_dB","Temperatura",
    "Numero_Ore_Consecutive",
}

def calcola_completezza(j, solo_audio=False):
    piatto = appiattisci_json(j)
    if not piatto: return 0.0
    if solo_audio:
        piatto = {k: v for k, v in piatto.items()
                  if not any(na in k for na in CAMPI_NON_AUDIO)}
    compilati = sum(1 for v in piatto.values()
                    if v and v.lower() not in ("n/d","null","none","n/a",""))
    return round(compilati / max(len(piatto),1) * 100, 2)

def valuta_discordanze_semantiche(candidati, piatti, nome_file=""):
    if not candidati: return [], {}
    confronti = {}
    for campo in candidati:
        valori = [p.get(campo, "N/D") for p in piatti]
        sig = [v for v in valori if str(v).lower() not in ("n/d","null","none","n/a","")]
        if len(set(sig)) >= 2:
            confronti[campo] = valori
    if not confronti: return [], {}
    prompt = (f"Analizza le discrepanze tra {len(piatti)} estrazioni indipendenti "
              f"dello stesso audio (\"{nome_file}\").\n\n"
              f"Per ogni campo, determina se i valori sono:\n"
              f"- STILISTICI: stessa informazione con phrasing diverso -> NON discordante\n"
              f"- SEMANTICI: fatti genuinamente contraddittori tra i run -> discordante\n\n"
              f"Rispondi SOLO con JSON:\n"
              f"{{\"valutazioni\": [{{\"campo\": \"nome.campo\", \"discordante\": true, "
              f"\"motivazione\": \"breve spiegazione\"}}]}}\n"
              f"Includi SOLO i campi discordanti (discordante: true).\n\n"
              f"CONFRONTI TRA RUN:\n"
              f"{json.dumps(confronti, ensure_ascii=False, indent=2)[:5000]}")
    try:
        resp = chiama_gemini(prompt, modello=MODELLO_JUDGE)
        dati = estrai_json_sicuro(resp.text)
        disc_campi  = [v["campo"] for v in dati.get("valutazioni", []) if v.get("discordante")]
        motivazioni = {v["campo"]: v.get("motivazione","")
                       for v in dati.get("valutazioni",[]) if v.get("discordante")}
        return disc_campi, motivazioni
    except Exception as e:
        print(f"  Valutazione semantica intra-run ({e}) -- uso confronto stringa")
        return candidati, {c: "confronto stringa" for c in candidati}

def calcola_coerenza(runs, nome_file=""):
    if len(runs) < 2: return 100.0, [], {}
    piatti = [appiattisci_json(j) for j in runs]
    tutti  = set().union(*[p.keys() for p in piatti])
    campi_evento = [c for c in tutti if get_entita_prefix(c) in ENTITA_EVENTO]
    candidati = []
    for c in campi_evento:
        valori_norm = {normalizza_per_confronto(p.get(c,"")) for p in piatti}
        valori_sig  = {v for v in valori_norm if v != "n/d"}
        if len(valori_sig) > 1: candidati.append(c)
    disc, motivazioni = valuta_discordanze_semantiche(candidati, piatti, nome_file)
    base = max(len(campi_evento), 1)
    return round((1 - len(disc)/base)*100, 2), disc, motivazioni

def calcola_stabilita(runs):
    piatti = [appiattisci_json(j) for j in runs]
    tutti  = set().union(*[p.keys() for p in piatti])
    result = {}
    for c in tutti:
        valori_raw  = [p.get(c, "N/D") for p in piatti]
        valori_norm = [normalizza_per_confronto(v) for v in valori_raw]
        e_evento    = get_entita_prefix(c) in ENTITA_EVENTO
        stabile_norm = len(set(valori_norm)) == 1
        result[c] = {"valori":valori_raw,"stabile":stabile_norm,
                     "e_evento":e_evento,"critico":not stabile_norm and e_evento}
    return result

def costruisci_consensus(runs):
    piatti = [appiattisci_json(j) for j in runs]
    tutti  = set().union(*[p.keys() for p in piatti])
    consensus_p = {}
    for campo in tutti:
        valori = [p.get(campo,"N/D") for p in piatti]
        sig    = [v for v in valori if v.lower() not in ("n/d","null","none","n/a","")]
        consensus_p[campo] = max(set(sig), key=sig.count) if sig else "N/D"
    result = copy.deepcopy(runs[0])
    def aggiorna(obj, pref=""):
        if isinstance(obj, dict):
            for k in list(obj.keys()):
                kp = f"{pref}.{k}" if pref else k
                if kp in consensus_p: obj[k] = consensus_p[kp]
                elif isinstance(obj[k], (dict,list)): aggiorna(obj[k], kp)
        elif isinstance(obj, list):
            for i, item in enumerate(obj): aggiorna(item, f"{pref}[{i}]")
    for entita, dati in result.items(): aggiorna(dati, entita)
    return result

def calcola_score_run(run, tutti_runs):
    comp = calcola_completezza(run, solo_audio=True)
    piatti      = [appiattisci_json(r) for r in tutti_runs]
    piatto_run  = appiattisci_json(run)
    tutti_campi = set().union(*[p.keys() for p in piatti])
    concordanti = 0
    for campo in tutti_campi:
        val_run = normalizza_per_confronto(piatto_run.get(campo, "N/D"))
        altri   = [normalizza_per_confronto(p.get(campo, "N/D"))
                   for p in piatti if p is not piatto_run]
        maggioranza = max(set(altri), key=altri.count) if altri else "n/d"
        if val_run == maggioranza: concordanti += 1
    accordo = concordanti / max(len(tutti_campi), 1) * 100
    return round(0.6 * comp + 0.4 * accordo, 2)

def seleziona_miglior_run(runs):
    if len(runs) == 1: return runs[0], 1, calcola_score_run(runs[0], runs)
    scores = [calcola_score_run(r, runs) for r in runs]
    idx    = scores.index(max(scores))
    print(f"    Run selezionato: Run {idx+1} (score {scores[idx]:.1f})")
    return runs[idx], idx + 1, scores[idx]

def calcola_discordanze_inter_testimonianza(risultati):
    if len(risultati) < 2: return []
    nomi   = list(risultati.keys())
    piatti = {nome: appiattisci_json(dati["consensus"]) for nome, dati in risultati.items()}
    tutti_campi  = set().union(*[p.keys() for p in piatti.values()])
    campi_evento = [c for c in tutti_campi if get_entita_prefix(c) in ENTITA_EVENTO]
    candidati = {}
    for campo in campi_evento:
        valori = {nome: piatti[nome].get(campo, "N/D") for nome in nomi}
        sig = {v for v in valori.values() if str(v).lower() not in ("n/d","null","none","n/a","")}
        if len(sig) > 1: candidati[campo] = valori
    if not candidati:
        print("  Nessuna discordanza candidata tra le testimonianze")
        return []
    print(f"  Campi candidati inter-testimonianza: {len(candidati)} -- valutazione semantica...")
    prompt = (f"Sei un perito industriale esperto. Analizza {len(nomi)} testimonianze dello STESSO incidente: "
              f"{', '.join(nomi)}.\n\n"
              f"Per ogni campo: COMPATIBILI (stessa sostanza) -> non discordante; "
              f"GENUINAMENTE DISCORDANTI -> discordante.\n\n"
              f"Rispondi SOLO con JSON:\n"
              f"{{\"discordanze\": [{{\"campo\": \"Entita.attributo\", \"discordante\": true, "
              f"\"valori\": {{\"file1\": \"val1\"}}, \"spiegazione\": \"...\", "
              f"\"impatto_sicurezza\": \"CRITICO\"}}]}}\n"
              f"impatto_sicurezza: CRITICO/RILEVANTE/INFORMATIVO\n\n"
              f"CAMPI DA VALUTARE:\n"
              f"{json.dumps(candidati, ensure_ascii=False, indent=2)[:6000]}")
    try:
        resp = chiama_gemini(prompt, modello=MODELLO_JUDGE)
        dati = estrai_json_sicuro(resp.text)
        disc = [d for d in dati.get("discordanze", []) if d.get("discordante")]
        print(f"  Discordanze genuine: {len(disc)} su {len(candidati)} candidati")
        return disc
    except Exception as e:
        print(f"  Discordanze inter-testimonianza: errore ({e}) -- uso candidati grezzi")
        return [{"campo":c,"discordante":True,"valori":v,
                 "spiegazione":"Valori divergenti -- verifica manuale",
                 "impatto_sicurezza":"RILEVANTE"} for c,v in candidati.items()]

def costruisci_prompt_individuale(trascrizione, nome_file, n_run):
    schema = json.dumps(SCHEMA_JSON_BASE, ensure_ascii=False, indent=2)
    return (f"Sei un perito industriale certificato. Stai analizzando la trascrizione "
            f"di UN SOLO testimone: {nome_file}. Run {n_run}.\n\n"
            f"{'='*60}\nGUIDA ESTRAZIONE -- ONTOLOGIA EVENTI INDUSTRIALI\n{'='*60}\n"
            f"L'estrazione e' guidata ESCLUSIVAMENTE dall'ontologia sotto.\n\n"
            f"{GUIDA_ONTOLOGIA}\n\n"
            f"{'_'*60}\nREGOLE ASSOLUTE\n{'_'*60}\n"
            f"[DIRETTO] -> SOLO valori esplicitamente dichiarati. MAI inventare.\n"
            f"[CONDENSATO] -> inferenza logica ammessa se motivata.\n\n"
            f"VOCABOLARI CONTROLLATI:\n"
            f"  Tipo_Evento: Incidente/Near Miss/Guasto/Manutenzione\n"
            f"  Gravita': Critico/Alto/Medio/Basso\n"
            f"  Tipo_Guasto: Meccanico/Elettrico/Idraulico/Software/Materiale\n"
            f"  Tipo_Danno: Elettrico/Meccanico/Strutturale\n"
            f"  Tipo_Coinvolgimento: Infortunato/Illeso/Testimone/Primo_Soccorso\n"
            f"  Tipo_Azione: Immediata/Correttiva/Preventiva\n\n"
            f"PERSONA -- REGOLA CRITICA: solo il NARRATORE.\n"
            f"Crea UN SOLO oggetto Persona: la persona che sta parlando.\n"
            f"NON creare oggetti per persone citate nel racconto.\n\n"
            f"AZIONE -- OBBLIGATORIA, almeno una voce.\n"
            f"Includi SEMPRE: arresti emergenza, chiamate 118, medicazioni, notifiche.\n\n"
            f"Rispondi SOLO con JSON puro -- zero testo, zero backtick.\n"
            f"Schema da compilare:\n{schema}\n\n"
            f"{'_'*60}\nTRASCRIZIONE\n{'_'*60}\n{trascrizione}\n")

def esegui_faithfulness(trascrizione, report_json):
    # Lista campi CONDENSATI passata al giudice (deduzioni motivate ammesse)
    condensati_str = ", ".join(sorted(CONDENSATI))

    prompt = (
        f"Sei un giudice esperto in valutazione AI per la sicurezza industriale.\n"
        f"Valuta la fedelta' del report rispetto alla trascrizione originale.\n\n"

        f"--- REGOLE GENERALI ---\n"
        f"[DIRETTO]    -> segnala come allucinazione ogni valore NON ESPLICITO nella trascrizione.\n"
        f"[CONDENSATO] -> inferenze logiche motivate sono ACCETTABILI, NON contestarle.\n"
        f"Campi CONDENSATO (deduzioni ammesse): {condensati_str}.\n\n"

        f"PERSONA: contiene SOLO il narratore. Non segnalare assenza di altri.\n"
        f"Verifica omissioni critiche: lesioni narratore, causa tecnica, azioni emergenza.\n\n"

        f"--- WHITELIST METADATI DI SISTEMA (IGNORA SEMPRE) ---\n"
        f"I seguenti campi NON sono estratti dal LLM ma popolati dalla pipeline a monte\n"
        f"(librosa per la prosodia, filesystem per il nome file). NON contestarli mai\n"
        f"come allucinazioni anche se non compaiono nel testo; riportali eventualmente\n"
        f"in 'falsi_positivi_scartati':\n"
        f"  - Testimonianza.Note_Prosodiche e ogni suo sotto-campo\n"
        f"    (Durata, Velocita_Eloquio, Pitch_medio, Pitch, Energia, ecc.)\n"
        f"  - Testimonianza.File_Audio\n"
        f"  - Testimonianza.Lingua\n"
        f"  - Testimonianza.Modalita_Racconto / Modalità_Racconto\n"
        f"  - Testimonianza.Score_Affidabilita / Score_Affidabilità\n"
        f"  - Testimonianza.Livello_Dettaglio\n\n"

        f"--- CONTROLLO SEVERO: marker [INCOMPRENSIBILE] e NOME[?] ---\n"
        f"Se la trascrizione contiene marker [INCOMPRENSIBILE] o NOME[?] (es. 'Giu[?]'),\n"
        f"l'extractor DEVE lasciare il marker oppure scrivere N/D.\n"
        f"NON e' ammesso completare arbitrariamente un nome marcato [?] (es. 'Giu[?]' -> 'Giusy')\n"
        f"a meno che il nome completo non compaia ESPLICITAMENTE in altra parte della trascrizione.\n"
        f"Qualunque completamento arbitrario va segnalato come allucinazione DIRETTO CRITICA.\n"
        f"Esempi:\n"
        f"  - trascrizione: 'mi chiamo Giu[?] Ferrara' + report Persona.Nome='Giusy'  -> ALLUCINAZIONE\n"
        f"  - trascrizione: 'Giu[?]... e io Giusy Ferrara firmo' + report 'Giusy'    -> OK (supportato)\n\n"

        f"TRASCRIZIONE:\n{trascrizione}\n\n"
        f"REPORT JSON:\n{json.dumps(report_json, ensure_ascii=False, indent=2)[:8000]}\n\n"

        f"Rispondi SOLO con JSON puro, senza testo aggiuntivo:\n"
        f"{{\n"
        f"  \"faithfulness_score\": 0-10,\n"
        f"  \"allucinazioni\": [{{\"campo\": \"...\", \"problema\": \"...\", \"tipo_campo\": \"DIRETTO\"}}],\n"
        f"  \"n_allucinazioni\": N,\n"
        f"  \"falsi_positivi_scartati\": [{{\"campo\": \"Testimonianza.File_Audio\", \"motivo\": \"metadato di sistema\"}}],\n"
        f"  \"omissioni\": [{{\"campo_atteso\": \"...\", \"info_mancante\": \"...\", \"gravita\": \"CRITICA\"}}],\n"
        f"  \"n_omissioni\": N,\n"
        f"  \"note\": \"...\"\n"
        f"}}"
    )
    try:
        dati = None
        for _t in range(1, 3):
            try:
                resp = chiama_gemini(prompt, modello=MODELLO_JUDGE)
                dati = estrai_json_sicuro(resp.text); break
            except Exception as _e:
                if "bilanciato" in str(_e) and _t < 2: time.sleep(5); continue
                raise
        if dati is None: raise ValueError("Faithfulness: tutti i tentativi falliti")

        # Safety net lato Python: rimuovi qualunque allucinazione che cada nel whitelist
        # (caso in cui il giudice non rispetti l'istruzione del prompt).
        METADATA_WHITELIST_PREFIX = "Testimonianza."
        METADATA_WHITELIST_KEYS = {
            "Testimonianza.Note_Prosodiche",
            "Testimonianza.File_Audio",
            "Testimonianza.Lingua",
            "Testimonianza.Modalita_Racconto",
            "Testimonianza.Modalità_Racconto",
            "Testimonianza.Score_Affidabilita",
            "Testimonianza.Score_Affidabilità",
            "Testimonianza.Livello_Dettaglio",
        }
        def _is_whitelisted(campo):
            if not isinstance(campo, str): return False
            if campo in METADATA_WHITELIST_KEYS: return True
            # Tutto cio' che inizia con "Testimonianza.Note_Prosodiche" o "Testimonianza.<metadato>"
            for k in METADATA_WHITELIST_KEYS:
                if campo.startswith(k + ".") or campo.startswith(k):
                    return True
            return False

        scartati = list(dati.get("falsi_positivi_scartati") or [])
        nuove_allu = []
        for a in (dati.get("allucinazioni") or []):
            campo = a.get("campo") if isinstance(a, dict) else None
            if _is_whitelisted(campo):
                scartati.append({"campo": campo, "motivo": "metadato di sistema (auto-scarto)"})
            else:
                nuove_allu.append(a)
        dati["allucinazioni"] = nuove_allu
        dati["falsi_positivi_scartati"] = scartati
        dati["n_allucinazioni"] = len(nuove_allu)

        piatto = appiattisci_json(report_json)
        dati["tasso_allucinazione_pct"] = round(
            dati.get("n_allucinazioni",0) / max(len(piatto),1) * 100, 2)
        dati["omissioni"]   = dati.get("omissioni", [])
        dati["n_omissioni"] = dati.get("n_omissioni", 0)
        return dati
    except Exception as e:
        print(f"  Faithfulness check fallito: {e}")
        return {"faithfulness_score":-1,"allucinazioni":[],"n_allucinazioni":0,
                "tasso_allucinazione_pct":0,"note":"errore","omissioni":[],"n_omissioni":0,
                "falsi_positivi_scartati":[]}


# ==============================================================
# SEZIONE 9 -- NOTA CRITICA
# ==============================================================

def semaforo(valore, soglia, inverso=False):
    if valore is None or valore < 0: return "?"
    ok   = valore >= soglia if not inverso else valore <= soglia
    warn = valore >= soglia*0.8 if not inverso else valore <= soglia*1.2
    return "OK" if ok else ("WARN" if warn else "ERR")


def _estrai_snippet_marker(testo, ctx=60):
    """Estrae frammenti di testo attorno ai marker [INCOMPRENSIBILE] / parola[?].
    Ritorna lista di dict {tipo, parola, snippet}."""
    if not testo: return []
    out = []
    pattern = re.compile(r"\[INCOMPRENSIBILE\]|(\S+?)\[\?\]")
    flat = testo.replace("\n", " ").replace("\r", " ")
    while "  " in flat: flat = flat.replace("  ", " ")
    for m in pattern.finditer(flat):
        idx = m.start(); end = m.end()
        a = max(0, idx - ctx); b = min(len(flat), end + ctx)
        snippet = flat[a:b].strip()
        if a > 0:
            sp = snippet.find(" ")
            if sp > 0 and sp < 12: snippet = snippet[sp+1:]
            snippet = "..." + snippet
        if b < len(flat):
            sp = snippet.rfind(" ")
            if sp > 0 and len(snippet) - sp < 12: snippet = snippet[:sp]
            snippet = snippet + "..."
        if m.group(0) == "[INCOMPRENSIBILE]":
            out.append({"tipo": "incomprensibile", "parola": None, "snippet": snippet})
        else:
            out.append({"tipo": "incerto", "parola": m.group(1), "snippet": snippet})
    return out


def genera_nota_critica(risultati, metriche_qualita, soglie, n_runs,
                         report_ind, report_rias, discordanze_inter=None,
                         output_dir=None):
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    linee = []
    W = 70

    ont_base = os.path.basename(ONTOLOGIA_PATH) if ONTOLOGIA_PATH else "N/D"
    linee += [
        "=" * W,
        "  NOTA CRITICA -- SISTEMA ANALISI TESTIMONIANZE v2",
        f"  Generato: {now}",
        "=" * W, "",
        "CONFIGURAZIONE ESECUZIONE",
        "-" * W,
        f"  N run per file    : {n_runs}",
        f"  File processati   : {len(risultati)}",
        f"  Run totali        : {len(risultati) * n_runs}",
        f"  Modello extractor : {MODELLO_EXTRACTOR}",
        f"  Modello judge     : {MODELLO_JUDGE}",
        f"  Ontologia         : {ont_base}",
        "", "SOGLIE DI ACCETTAZIONE",
        "-" * W,
        f"  Completezza      >= {soglie['completezza_min']}%",
        f"  Coerenza inter-run >= {soglie['coerenza_min']}%  (solo campi evento)",
        f"  Faithfulness     >= {soglie['faithfulness_min']}/10",
        f"  Hallucination    <= {soglie['hallucination_max']}%",
        f"  Qualita' audio    >= {soglie['qualita_audio_min']}/100",
        "", "=" * W,
        "  METRICHE PER FILE",
        "=" * W,
    ]

    giudizi_globali = []

    for nome, dati in risultati.items():
        m       = dati["metriche"]
        comp    = m["Completezza_Campi_%"]
        comp_md = m.get("Completezza_Campi_Media_%", comp)
        coe     = m["Coerenza_Inter_Run_%"]
        faith   = m["faithfulness_score"]
        h_rate  = m["Hallucination_Rate_%"]
        q_aud   = m["score_qualita_audio"]
        n_inst  = m["n_campi_instabili"]
        n_all   = m["n_allucinazioni"]

        s1 = semaforo(comp,   soglie["completezza_min"])
        s2 = semaforo(coe,    soglie["coerenza_min"])
        s3 = semaforo(faith,  soglie["faithfulness_min"])
        s6 = semaforo(h_rate, soglie["hallucination_max"], inverso=True)
        s5 = semaforo(q_aud,  soglie["qualita_audio_min"])

        flag_ok = all([
            comp   >= soglie["completezza_min"],
            coe    >= soglie["coerenza_min"],
            faith  >= soglie["faithfulness_min"] or faith < 0,
            h_rate <= soglie["hallucination_max"],
        ])
        giudizi_globali.append(flag_ok)

        linee += [
            "", f"FILE: {nome}",
            "-" * W,
            f"  Run selezionato       : Run {m.get('run_scelto','?')} "
            f"(score {m.get('score_run_scelto', 0.0):.1f})",
            f"  [{s1}] Completezza (run scelto): {comp}%",
            f"       Media tra {len(m.get('completezze_per_run', {}))} run: {comp_md}%",
            f"       Per run: {m.get('completezze_per_run', {})}",
            f"  [{s2}] Coerenza inter-run    : {coe}%  ({m['Campi_Discordanti']} campi discordanti)",
            f"  [{s3}] Faithfulness (giudice): {faith}/10",
            f"       {m.get('faithfulness_note', '')}",
            f"  [{s6}] Hallucination rate    : {h_rate}%  ({n_all} allucinazioni)",
            f"       Stabilita' campi       : {n_inst} campi evento critici "
            f"({m.get('n_campi_instabili_tot', n_inst)} totali)",
            f"  [{s5}] Qualita' trascrizione  : {m['Qualità_Trascrizione']}",
        ]

        q = metriche_qualita.get(nome, {}) if metriche_qualita else {}
        n_trasc = q.get("n_trascrizioni_qualita", 0)
        idx_scelto = q.get("idx_trascrizione_scelta", 1)
        if n_trasc:
            linee.append(f"  Trascrizione operativa  : run {idx_scelto} di {n_trasc} (testo usato per estrazione)")

        trasc_complete = q.get("trascrizioni_complete") or []
        ni_per_run = q.get("incomprensibili_per_run") or []
        nc_per_run = q.get("incerti_per_run") or []
        if trasc_complete and (sum(ni_per_run) + sum(nc_per_run) > 0):
            linee.append("")
            linee.append(f"  PARTI NON BEN COMPRESE (causa penalita' qualita' audio):")
            for k, t in enumerate(trasc_complete, start=1):
                snippets = _estrai_snippet_marker(t)
                if not snippets: continue
                for s in snippets[:5]:
                    tag = "[INCOMPRENSIBILE]" if s["tipo"] == "incomprensibile" else f"{s['parola']}[?]"
                    linee.append(f"    - run {k} · {tag}: \"{s['snippet']}\"")
                if len(snippets) > 5:
                    linee.append(f"    - run {k} · ...e altri {len(snippets)-5} marker")

        if m.get("campi_discordanti_lista"):
            linee.append(f"\n  CAMPI DISCORDANTI TRA I {n_runs} RUN:")
            motivazioni = m.get("campi_discordanti_motivazioni", {})
            for c in m["campi_discordanti_lista"][:10]:
                mot = motivazioni.get(c, "")
                linee.append(f"    - {c}" + (f": {mot}" if mot else ""))
            if len(m["campi_discordanti_lista"]) > 10:
                linee.append(f"    ... e altri {len(m['campi_discordanti_lista'])-10}")

        if m.get("allucinazioni"):
            linee.append(f"\n  ALLUCINAZIONI RILEVATE DAL GIUDICE:")
            for a in m["allucinazioni"][:5]:
                linee.append(f"    - [{a.get('tipo_campo','?')}] {a.get('campo','?')}: {a.get('problema','')}")
            if len(m["allucinazioni"]) > 5:
                linee.append(f"    ... e altre {len(m['allucinazioni'])-5}")

        if m.get("omissioni"):
            linee.append(f"\n  OMISSIONI DI SICUREZZA (info nell'audio ma assenti dal report):")
            for o in m["omissioni"][:5]:
                grav = o.get("gravita", "?")
                linee.append(f"    [!][{grav}] {o.get('campo_atteso','?')}: {o.get('info_mancante','')}")

        if m.get("campi_instabili_lista"):
            linee.append(f"\n  CAMPI CRITICI (variano tra run, verificare manualmente):")
            runs_list = dati.get("runs")
            for c in m["campi_instabili_lista"]:
                if runs_list:
                    valori = [appiattisci_json(r).get(c, "N/D") for r in runs_list]
                    linee.append(f"    - {c}: {valori}")
                else:
                    linee.append(f"    - {c}")

        linee.append(f"\n  GIUDIZIO: {'APPROVATO' if flag_ok else 'REVISIONE RACCOMANDATA'}")

    # -- Valutazione critica complessiva ----------------------------------
    linee += ["", "=" * W, "  VALUTAZIONE CRITICA COMPLESSIVA", "=" * W, ""]

    n_ok  = sum(giudizi_globali)
    n_warn = len(giudizi_globali) - n_ok
    comp_med  = statistics.mean([d["metriche"]["Completezza_Campi_%"] for d in risultati.values()])
    comp_med_run = statistics.mean([d["metriche"].get("Completezza_Campi_Media_%",
                                                       d["metriche"]["Completezza_Campi_%"])
                                     for d in risultati.values()])
    coe_med   = statistics.mean([d["metriche"]["Coerenza_Inter_Run_%"] for d in risultati.values()])
    faith_med = statistics.mean([d["metriche"]["faithfulness_score"] for d in risultati.values()
                                  if d["metriche"]["faithfulness_score"] >= 0] or [0])
    h_med     = statistics.mean([d["metriche"]["Hallucination_Rate_%"] for d in risultati.values()])

    linee += [
        f"  File approvati / totali : {n_ok} / {len(giudizi_globali)}",
        f"  Completezza glob. (run scelti): {round(comp_med,2)}%",
        f"  Completezza media tra run    : {round(comp_med_run,2)}%",
        f"  Coerenza media glob.    : {round(coe_med,2)}%",
        f"  Faithfulness media      : {round(faith_med,2)}/10",
        f"  Hallucination rate med. : {round(h_med,2)}%",
        "",
    ]

    punti_forza = []
    if faith_med >= soglie["faithfulness_min"]:
        punti_forza.append(f"Alta fedelta' del report (faithfulness medio {round(faith_med,2)}/10).")
    if h_med <= soglie["hallucination_max"]:
        punti_forza.append(f"Basso tasso di allucinazione ({round(h_med,2)}%).")
    if coe_med >= soglie["coerenza_min"]:
        punti_forza.append(f"Buona stabilita' tra run (coerenza media {round(coe_med,2)}%).")

    criticita = []
    if comp_med < soglie["completezza_min"]:
        criticita.append(f"Completezza media insufficiente ({round(comp_med,2)}% < "
                          f"{soglie['completezza_min']}%): valutare raccolta dati aggiuntiva.")
    if coe_med < soglie["coerenza_min"]:
        criticita.append(f"Coerenza inter-run bassa ({round(coe_med,2)}%): aumentare N_RUNS.")
    total_inst = sum(d["metriche"]["n_campi_instabili"] for d in risultati.values())
    if total_inst > 0:
        criticita.append(f"{total_inst} campi instabili: verificare manualmente prima dell'approvazione.")
    total_all = sum(d["metriche"]["n_allucinazioni"] for d in risultati.values())
    if total_all > 0:
        criticita.append(f"{total_all} potenziali allucinazioni: verificare i campi segnalati.")

    raccomandazioni = [
        "Aumentare N_RUNS a 5+ per report ad alto impatto.",
        "Verificare manualmente TUTTI i campi [DIRETTO] prima dell'approvazione.",
        f"I campi [CONDENSATO] contengono inferenze del modello: richiedono validazione esperta.",
    ]
    if n_warn > 0:
        raccomandazioni.append(f"{n_warn} file sotto soglia: considerare nuove testimonianze.")

    linee += ["PUNTI DI FORZA:", "-" * W]
    linee += [f"  + {p}" for p in punti_forza] if punti_forza else ["  (nessuno rilevato)"]
    linee += ["", "CRITICITA':", "-" * W]
    linee += [f"  - {c}" for c in criticita] if criticita else ["  (nessuna critica rilevata)"]
    linee += ["", "RACCOMANDAZIONI:", "-" * W]
    linee += [f"  -> {r}" for r in raccomandazioni]

    # -- Discordanze inter-testimonianza ----------------------------------
    linee += ["", "=" * W, "  DISCORDANZE TRA TESTIMONIANZE", "=" * W]

    if discordanze_inter:
        nomi_testimoni = list(risultati.keys())
        linee += [
            f"  Testimoni confrontati: {', '.join(nomi_testimoni)}",
            f"  Discordanze totali: {len(discordanze_inter)}",
            "-" * W,
        ]
        critiche  = [d for d in discordanze_inter if d.get("impatto_sicurezza") == "CRITICO"]
        rilevanti = [d for d in discordanze_inter if d.get("impatto_sicurezza") == "RILEVANTE"]
        info      = [d for d in discordanze_inter
                     if d.get("impatto_sicurezza") not in ("CRITICO", "RILEVANTE")]

        for gruppo, etichetta in [
            (critiche,  "[CRITICHE] fatti di sicurezza contraddittori"),
            (rilevanti, "[RILEVANTI] dinamica evento discordante"),
            (info,      "[INFORMATIVE] discrepanze minori"),
        ]:
            if not gruppo:
                continue
            linee.append(f"\n  {etichetta} -- {len(gruppo)} campo/i:")
            for d in gruppo:
                linee.append(f"\n    CAMPO: {d['campo']}")
                for nome_t, val_t in d.get("valori", {}).items():
                    if str(val_t).lower() not in ("n/d", "null", "none", "n/a", ""):
                        linee.append(f"      - {nome_t}: '{val_t}'")
                linee.append(f"      -> {d.get('spiegazione','')}")
    else:
        linee += ["  Nessuna discordanza rilevata tra le testimonianze.", ""]

    # -- File prodotti ----------------------------------------------------
    linee += ["", "=" * W, "  FILE PRODOTTI", "=" * W]
    for nome, path in report_ind.items():
        linee.append(f"  REPORT INDIVIDUALE [{nome}]: {path}")
    if report_rias:
        linee.append(f"  REPORT RIASSUNTIVO: {report_rias}")

    linee += ["", "=" * W,
              "  AVVERTENZA LEGALE",
              "-" * W,
              "  Questo report e' generato da un sistema AI e deve essere",
              "  validato da un esperto umano prima dell'uso ufficiale.",
              "  I campi [DIRETTO] contengono solo info esplicitamente dichiarate.",
              "  I campi [CONDENSATO] includono inferenze del modello.",
              "=" * W]

    return "\n".join(linee)


# ==============================================================
# PIPELINE RESULT
# ==============================================================

@dataclass
class PipelineResult:
    risultati: dict = _field(default_factory=dict)
    discordanze: list = _field(default_factory=list)
    metriche_qualita: dict = _field(default_factory=dict)
    trascrizioni: dict = _field(default_factory=dict)
    dati_prosodici: dict = _field(default_factory=dict)
    output_dir: str = ""
    report_ind_paths: dict = _field(default_factory=dict)
    report_rias_path: str = ""
    riassuntivo_json: dict = _field(default_factory=dict)
    nota_critica: str = ""
    trascrizioni_testo: str = ""
    processing_summary: dict = _field(default_factory=dict)
    processing_log: str = ""

    def get_consensus_json(self, filename: str) -> dict:
        return self.risultati.get(filename, {}).get("consensus", {})

    def get_metriche(self, filename: str) -> dict:
        return self.risultati.get(filename, {}).get("metriche", {})


# ==============================================================
# HELPER -- passi pipeline condivisi
# ==============================================================

def _esegui_ingestion(files_input, base_dir):
    elaborati = {}
    for f_orig in files_input:
        percorso_assoluto = os.path.join(base_dir, f_orig) if not os.path.isabs(f_orig) else f_orig
        if not os.path.exists(percorso_assoluto):
            print(f"  Non trovato: {percorso_assoluto}"); continue
        ext = os.path.splitext(f_orig)[1].lower()
        print(f"\n  {f_orig}")
        if ext in FORMATI_AUDIO:
            out, snr = pulisci_audio(percorso_assoluto)
            if out:
                print(f"  Audio accettato -- SNR stimato: {snr} dB")
                elaborati[f_orig] = {"tipo": "audio", "percorso": out, "snr": snr}
        elif ext in FORMATI_TESTO:
            testo = leggi_testo_file(percorso_assoluto)
            if not testo.strip():
                print("  File vuoto -- saltato."); continue
            val = valida_contesto(testo)
            if val.get("e_contesto_critico"):
                print(f"  Testo critico: {val.get('categoria')} -- {val.get('motivazione')}")
                elaborati[f_orig] = {"tipo":"testo","percorso":percorso_assoluto,
                                     "testo_grezzo":testo,"snr":None}
            else:
                print(f"  RIFIUTATO -- {val.get('motivazione')}")
        else:
            print(f"  Formato non supportato: {ext}")
    return elaborati


def _classifica_qualita_audio(avg_ni, avg_nc):
    """Score continuo: ogni [INCOMPRENSIBILE] vale -15, ogni [?] vale -8 (mediati
    su N trascrizioni). Evita il salto secco 100->70 al primo marker.
    Categoria: 90+ OTTIMA, 70-89 ACCETTABILE, 40-69 SCARSA, <40 CRITICA."""
    penalty = avg_ni * 15.0 + avg_nc * 8.0
    score = max(10, min(100, round(100 - penalty)))
    if   score >= 90: cat = "OTTIMA"
    elif score >= 70: cat = "ACCETTABILE"
    elif score >= 40: cat = "SCARSA"
    else:             cat = "CRITICA"
    return cat, score


def _seleziona_migliore_trascrizione(trascrizioni):
    """Sceglie la trascrizione con minor 'peso marker' (stessa formula di penalita'
    di _classifica_qualita_audio: n_incomp*15 + n_incerti*8). Tiebreak: piu' lunga
    in parole. Ritorna (testo_scelto, idx_1based)."""
    if len(trascrizioni) == 1:
        return trascrizioni[0], 1
    def _score(t):
        ni = t.count("[INCOMPRENSIBILE]")
        nc = t.count("[?]")
        n_parole = len(t.split())
        return (ni * 15 + nc * 8, -n_parole)
    idx = min(range(len(trascrizioni)), key=lambda i: _score(trascrizioni[i]))
    return trascrizioni[idx], idx + 1


def _trascrivi_singolo_file(nome_orig, info, base_dir):
    """Trascrive un singolo file usando una chiave dedicata dal pool per tutto
    il ciclo (upload + N trascrizioni). Ritorna (testo, metriche_dict, prosodia)
    oppure (None, None, None) se scartato.

    Eseguibile in parallelo (chiamate I/O bound, ogni thread ha la sua chiave).
    """
    pool = get_pool()
    tag = f"[{nome_orig}]"

    if info["tipo"] == "audio":
        # Acquisiamo UN client e lo usiamo per tutto: upload e N transcrizioni.
        # Il file uploadato è legato alla chiave dell'upload → niente cross-key.
        if pool is None:
            # Fallback: usa global client (modalità legacy).
            local_client = client
            release_idx = None
        else:
            release_idx, local_client = pool.acquire()

        try:
            _tprint(f"\n  {tag} upload audio...")
            fc = local_client.files.upload(file=info["percorso"])
            while fc.state.name == "PROCESSING":
                time.sleep(2)
                fc = local_client.files.get(name=fc.name)

            trascrizioni = []
            for k in range(1, N_TRASCRIZIONI_QUALITA + 1):
                try:
                    resp = chiama_gemini(
                        [PROMPT_TRASCRIZIONE, fc],
                        client_override=local_client,
                    )
                    t = resp.text or ""
                    trascrizioni.append(t)
                    ni_k = t.count("[INCOMPRENSIBILE]"); nc_k = t.count("[?]")
                    _tprint(f"  {tag} trascr {k}/{N_TRASCRIZIONI_QUALITA}: "
                            f"{len(t.split())} parole · {ni_k} incomp · {nc_k} incerti")
                except Exception as e:
                    _tprint(f"  {tag} trascr {k} fallita: {e}")
        finally:
            if pool is not None and release_idx is not None:
                pool.release(release_idx, mark_quota_hit=False)

        if not trascrizioni:
            _tprint(f"  {tag} nessuna trascrizione disponibile -- saltato")
            return None, None, None

        testo, idx_scelto = _seleziona_migliore_trascrizione(trascrizioni)
        if len(trascrizioni) > 1:
            ni_s = testo.count("[INCOMPRENSIBILE]"); nc_s = testo.count("[?]")
            _tprint(f"  {tag} trascr operativa: run {idx_scelto}/{len(trascrizioni)} "
                    f"({ni_s} incomp, {nc_s} incerti)")

        n_parole_temp = len(testo.split())
        prosodia = analizza_prosodia(info["percorso"], n_parole_temp)
        _tprint(f"  {tag} prosodia: {prosodia}")

        percorso_pulito = info["percorso"]
        nome_orig_check = nome_orig if not os.path.isabs(nome_orig) else os.path.basename(nome_orig)
        if (percorso_pulito != os.path.join(base_dir, nome_orig_check)
                and os.path.exists(percorso_pulito) and "_pulito" in percorso_pulito):
            try:
                os.remove(percorso_pulito)
            except OSError:
                pass

        val_audio = valida_contesto(testo)
        if not val_audio.get("e_contesto_critico"):
            _tprint(f"  {tag} RIFIUTATO dopo trascrizione -- {val_audio.get('motivazione')}")
            return None, None, None
        _tprint(f"  {tag} contesto critico confermato: {val_audio.get('categoria')}")
    else:
        testo = info.get("testo_grezzo", "")
        trascrizioni = [testo]
        idx_scelto = 1
        prosodia = {}

    marker_ni = [t.count("[INCOMPRENSIBILE]") for t in trascrizioni]
    marker_nc = [t.count("[?]") for t in trascrizioni]
    avg_ni = sum(marker_ni) / len(marker_ni)
    avg_nc = sum(marker_nc) / len(marker_nc)
    livello, score = _classifica_qualita_audio(avg_ni, avg_nc)
    np_p = testo.count("[PAUSA]"); n_parole = len(testo.split())

    metr = {
        "score_qualita": score, "livello_qualita": livello,
        "incomprensibili": round(avg_ni, 2), "incerti": round(avg_nc, 2),
        "incomprensibili_per_run": marker_ni, "incerti_per_run": marker_nc,
        "trascrizioni_complete": trascrizioni[:],
        "n_trascrizioni_qualita": len(trascrizioni),
        "idx_trascrizione_scelta": idx_scelto,
        "pause": np_p, "n_parole": n_parole,
        "snr": info.get("snr"),
        "Qualità_Trascrizione": (
            f"{score}/100 -- {livello} "
            f"({avg_ni:.1f} incomprensibili, {avg_nc:.1f} incerti, media su {len(trascrizioni)} trascr.)"
        )
    }
    _tprint(f"  {tag} {n_parole} parole | qualita': {livello} "
            f"(media: {avg_ni:.1f} incomp, {avg_nc:.1f} incerti su {len(trascrizioni)} trascr.)")
    return testo, metr, prosodia


def _esegui_trascrizione(files_elaborati, base_dir):
    """Trascrive ogni audio N_TRASCRIZIONI_QUALITA volte e media i marker per ottenere
    uno score audio stabile tra esecuzioni. La migliore (min marker pesati) viene usata
    come testo operativo per l'estrazione successiva.

    Se il pool ha >1 chiave, i FILE vengono trascritti in parallelo (un thread per file).
    Le N trascrizioni per file restano sequenziali sulla stessa chiave perché il file
    Gemini uploadato è legato a quella chiave.
    """
    contenuti, metriche, prosodici = {}, {}, {}
    pool = get_pool()
    max_workers = pool.size if pool is not None else 1
    max_workers = max(1, min(max_workers, len(files_elaborati)))

    if max_workers == 1 or len(files_elaborati) <= 1:
        # Cammino sequenziale (back-compat / debug più semplice).
        for nome_orig, info in files_elaborati.items():
            testo, m, p = _trascrivi_singolo_file(nome_orig, info, base_dir)
            if testo is not None:
                contenuti[nome_orig] = testo
                metriche[nome_orig] = m
                if p:
                    prosodici[nome_orig] = p
        return contenuti, metriche, prosodici

    _tprint(f"  [Pool] Trascrizione parallela con {max_workers} worker su {len(files_elaborati)} file")
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="trascr") as ex:
        future_map = {
            ex.submit(_trascrivi_singolo_file, nome, info, base_dir): nome
            for nome, info in files_elaborati.items()
        }
        for fut in as_completed(future_map):
            nome = future_map[fut]
            try:
                testo, m, p = fut.result()
            except Exception as e:
                _tprint(f"  [{nome}] trascrizione fallita con eccezione: {e}")
                continue
            if testo is not None:
                contenuti[nome] = testo
                metriche[nome] = m
                if p:
                    prosodici[nome] = p
    return contenuti, metriche, prosodici


def _esegui_run_singolo(nome_orig, testo, run_n, n_runs):
    """Esegue un singolo run di estrazione (con retry interni su MAX_TOKENS / JSON).
    Ritorna il dict JSON estratto, oppure None se fallito.
    Pensata per essere chiamata da ThreadPoolExecutor — usa il pool globale via chiama_gemini.
    """
    tag = f"[{nome_orig} run {run_n}/{n_runs}]"
    for tentativo in range(1, 4):
        try:
            resp = chiama_gemini(costruisci_prompt_individuale(testo, nome_orig, run_n))
            if _e_troncato_risposta(resp):
                if tentativo < 3:
                    _tprint(f"  {tag} MAX_TOKENS retry {tentativo}/3")
                    time.sleep(2); continue
            j = estrai_json_sicuro(resp.text)
            troncati_str = _trova_troncamenti(j)
            if troncati_str and tentativo < 3:
                _tprint(f"  {tag} {len(troncati_str)} troncati retry {tentativo}/3")
                time.sleep(2); continue
            _tprint(f"  {tag} OK completezza {calcola_completezza(j)}%")
            return j
        except Exception as e:
            msg = str(e)
            if ("bilanciato" in msg or "JSON" in msg) and tentativo < 3:
                _tprint(f"  {tag} JSON troncato retry {tentativo}/3")
                time.sleep(2); continue
            _tprint(f"  {tag} ERRORE: {msg}")
            return None
    return None


def _aggrega_runs_testimonianza(nome_orig, testo, runs, runs_falliti,
                                metriche_qualita, dati_prosodici, n_runs):
    """Aggregazione sequenziale post-parallelismo: consensus, coerenza, faithfulness, ecc.
    Ritorna il dict completo per `risultati[nome_orig]`."""
    completezze              = [calcola_completezza(j) for j in runs]
    completezze_audio        = [calcola_completezza(j, solo_audio=True) for j in runs]
    coerenza, disc, disc_mot = calcola_coerenza(runs, nome_orig)
    stabilita                = calcola_stabilita(runs)
    instabili                = [c for c, v in stabilita.items() if v["critico"]]
    miglior_run, run_scelto, score_scelto = seleziona_miglior_run(runs)
    consensus                = costruisci_consensus(runs)
    if nome_orig in dati_prosodici:
        for _j in (consensus, miglior_run):
            if isinstance(_j.get("Testimonianza"), dict):
                _j["Testimonianza"]["Note_Prosodiche"] = dati_prosodici[nome_orig]
    faith = esegui_faithfulness(testo, miglior_run)
    q     = metriche_qualita.get(nome_orig, {})
    idx_scelto = max(1, min(run_scelto, len(completezze))) - 1
    comp_run_scelto       = round(completezze[idx_scelto], 2)
    comp_audio_run_scelto = round(completezze_audio[idx_scelto], 2)
    comp_media            = round(statistics.mean(completezze), 2)
    comp_audio_media      = round(statistics.mean(completezze_audio), 2)
    res = {
        "runs": runs, "consensus": miglior_run,
        "run_scelto": run_scelto, "score_run_scelto": score_scelto,
        "runs_falliti": runs_falliti, "n_runs_richiesti": n_runs,
        "metriche": {
            "Completezza_Campi_%":           comp_run_scelto,
            "Completezza_Audio_%":           comp_audio_run_scelto,
            "Completezza_Campi_Media_%":     comp_media,
            "Completezza_Audio_Media_%":     comp_audio_media,
            "completezze_per_run":           dict(zip(range(1, n_runs+1), completezze)),
            "run_scelto":                    run_scelto,
            "score_run_scelto":              score_scelto,
            "Coerenza_Inter_Run_%":          coerenza,
            "Campi_Discordanti":             len(disc),
            "campi_discordanti_lista":       disc,
            "campi_discordanti_motivazioni": disc_mot,
            "faithfulness_score":            faith.get("faithfulness_score", -1),
            "faithfulness_note":             faith.get("note", ""),
            "allucinazioni":                 faith.get("allucinazioni", []),
            "n_campi_instabili":             len(instabili),
            "n_campi_instabili_tot":         len([c for c,v in stabilita.items() if not v["stabile"]]),
            "campi_instabili_lista":         instabili,
            "Qualità_Trascrizione":          q.get("Qualità_Trascrizione", "N/D"),
            "score_qualita_audio":           q.get("score_qualita", 0) or 0,
            "incomprensibili":               q.get("incomprensibili", 0),
            "Hallucination_Rate_%":          faith.get("tasso_allucinazione_pct", 0),
            "n_allucinazioni":               faith.get("n_allucinazioni", 0),
            "omissioni":                     faith.get("omissioni", []),
            "n_omissioni":                   faith.get("n_omissioni", 0),
        }
    }
    m = res["metriche"]
    _tprint(f"  [{nome_orig}] Completezza {m['Completezza_Campi_%']}% | "
            f"Coerenza {m['Coerenza_Inter_Run_%']}% | "
            f"Faithfulness {m['faithfulness_score']}/10 | "
            f"Hallucination {m['Hallucination_Rate_%']}%")
    return res


def _esegui_estrazione(contenuti_testuali, metriche_qualita, dati_prosodici, n_runs):
    """Esegue estrazione ontologica. Se il pool ha >1 chiave, parallelizza i singoli
    run (file, run_n) usando ThreadPoolExecutor. L'aggregazione per testimonianza
    (consensus, coerenza, faith, ...) resta sequenziale dopo che tutti i run sono pronti.
    """
    pool = get_pool()
    max_workers = pool.size if pool is not None else 1
    n_tasks = len(contenuti_testuali) * n_runs
    max_workers = max(1, min(max_workers, n_tasks))

    # Raccolta runs grezzi (mantengo l'ordine run_n usando index).
    runs_grezzi: dict[str, list] = {nome: [None] * n_runs for nome in contenuti_testuali}
    runs_falliti_cnt: dict[str, int] = {nome: 0 for nome in contenuti_testuali}

    tasks = [
        (nome, testo, run_n)
        for nome, testo in contenuti_testuali.items()
        for run_n in range(1, n_runs + 1)
    ]

    if max_workers == 1:
        for nome, testo, run_n in tasks:
            j = _esegui_run_singolo(nome, testo, run_n, n_runs)
            if j is not None:
                runs_grezzi[nome][run_n - 1] = j
            else:
                runs_falliti_cnt[nome] += 1
    else:
        _tprint(f"  [Pool] Estrazione parallela con {max_workers} worker su {n_tasks} run")
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="estraz") as ex:
            future_map = {
                ex.submit(_esegui_run_singolo, nome, testo, run_n, n_runs): (nome, run_n)
                for nome, testo, run_n in tasks
            }
            for fut in as_completed(future_map):
                nome, run_n = future_map[fut]
                try:
                    j = fut.result()
                except Exception as e:
                    _tprint(f"  [{nome} run {run_n}] eccezione non gestita: {e}")
                    j = None
                if j is not None:
                    runs_grezzi[nome][run_n - 1] = j
                else:
                    runs_falliti_cnt[nome] += 1

    # Aggregazione sequenziale per ciascuna testimonianza.
    risultati = {}
    for nome_orig, testo in contenuti_testuali.items():
        runs = [r for r in runs_grezzi[nome_orig] if r is not None]
        if not runs:
            _tprint(f"  [{nome_orig}] nessun run valido -- saltato")
            continue
        _tprint(f"  [{nome_orig}] Run validi: {len(runs)}/{n_runs}")
        risultati[nome_orig] = _aggrega_runs_testimonianza(
            nome_orig, testo, runs, runs_falliti_cnt[nome_orig],
            metriche_qualita, dati_prosodici, n_runs,
        )
    return risultati


def _genera_report_ind(risultati, schema_path, n_runs, output_dir):
    paths = {}
    for nome_orig, dati in risultati.items():
        # Applica postprocessing/troncamenti DIRETTAMENTE sul consensus,
        # cosi' i campi derivati (Tipo_Azione, Tipo_Danno, Coinvolgimento,
        # marker [INCOMPLETO]) sono visibili anche nel JSON che il frontend legge.
        cons_orig = dati.get("consensus")
        if isinstance(cons_orig, dict):
            applica_postprocessing(cons_orig); _marca_troncamenti(cons_orig)
            ev_orig = cons_orig.get("Evento", {}) if isinstance(cons_orig.get("Evento"), dict) else {}
            id_evento_orig = costruisci_id_evento(ev_orig.get("Data","N/D"), ev_orig.get("Tipo_Evento","incidente"))
            if isinstance(cons_orig.get("Evento"), dict):
                cons_orig["Evento"]["ID_Evento"] = id_evento_orig

        j = copy.deepcopy(dati["consensus"])
        m = dati["metriche"]
        persone_raw = j.get("Persona", [])
        if isinstance(persone_raw, list) and len(persone_raw) > 1:
            j["Persona"] = persone_raw[:1]
        elif isinstance(persone_raw, dict):
            j["Persona"] = [persone_raw]
        ev = j.get("Evento", {}) if isinstance(j.get("Evento"), dict) else {}
        id_evento = costruisci_id_evento(ev.get("Data","N/D"), ev.get("Tipo_Evento","incidente"))
        if "Evento" in j and isinstance(j["Evento"], dict):
            j["Evento"]["ID_Evento"] = id_evento
        met_test = {"Completezza_Campi_%":f"{m['Completezza_Campi_%']}%",
                    "Coerenza_Inter_Run_%":f"{m['Coerenza_Inter_Run_%']}%",
                    "Campi_Discordanti":str(m["Campi_Discordanti"]),
                    "Qualità_Trascrizione":m["Qualità_Trascrizione"]}
        wb   = scrivi_report_multifoglio(schema_path, j, nome_orig, n_runs, id_evento, met_test)
        nome_p = os.path.splitext(nome_orig)[0].replace(" ","_")
        path   = os.path.join(output_dir, f"REPORT_IND_{nome_p}.xlsx")
        wb.save(path); paths[nome_orig] = path
        print(f"  Salvato: {path}")
    return paths


def _genera_report_rias(risultati, dati_prosodici, discordanze_inter, schema_path, output_dir):
    if not risultati: return None
    try:
        sintesi_json = "\n\n".join(
            f"=== CONSENSUS {nome} ===\n{json.dumps(dati, ensure_ascii=False, indent=2)}"
            for nome, dati in {n: d["consensus"] for n,d in risultati.items()}.items()
        )
        prompt_r = (f"Sei il perito responsabile. Integra i report JSON dei singoli testimoni "
                    f"in un unico report definitivo consolidato.\n\n"
                    f"{GUIDA_ONTOLOGIA}\n\n"
                    f"Valori CONCORDANTI -> usa il valore comune. "
                    f"COMPLEMENTARI -> integra. DISCORDANTI -> [DISC: valA / valB].\n"
                    f"PERSONA: unifica per Nome+Cognome. NON duplicare.\n"
                    f"AZIONE: unifica tutte. Rimuovi duplicati.\n\n"
                    f"Rispondi SOLO con JSON puro:\n"
                    f"{json.dumps(SCHEMA_JSON_BASE, ensure_ascii=False, indent=2)}\n\n"
                    f"JSON CONSENSUS PER TESTIMONE:\n{sintesi_json}\n")
        j_rias = None
        for _t in range(1, 4):
            try:
                resp_rias = chiama_gemini(prompt_r)
                j_rias    = estrai_json_sicuro(resp_rias.text); break
            except Exception as _e:
                if "bilanciato" in str(_e) and _t < 3: time.sleep(8); continue
                else: raise
        if j_rias is None: raise ValueError("Riassuntivo fallito")
        if isinstance(j_rias, list):
            j_rias = j_rias[0] if j_rias and isinstance(j_rias[0], dict) else {}
        comp_rias = calcola_completezza(j_rias)
        ev_rias   = j_rias.get("Evento",{}) if isinstance(j_rias.get("Evento"),dict) else {}
        id_rias   = costruisci_id_evento(ev_rias.get("Data","N/D"),ev_rias.get("Tipo_Evento","incidente"))
        if "Evento" in j_rias and isinstance(j_rias["Evento"], dict):
            j_rias["Evento"]["ID_Evento"] = id_rias
        persone_rias_list = []
        azioni_rias_list = []
        for nome_t, dati_t in risultati.items():
            persone_t = dati_t["consensus"].get("Persona", [])
            if isinstance(persone_t, list) and persone_t: persone_rias_list.append(copy.deepcopy(persone_t[0]))
            elif isinstance(persone_t, dict): persone_rias_list.append(copy.deepcopy(persone_t))
            
            azioni_t = dati_t["consensus"].get("Azione", [])
            if isinstance(azioni_t, list): azioni_rias_list.extend(copy.deepcopy(azioni_t))
            elif isinstance(azioni_t, dict): azioni_rias_list.append(copy.deepcopy(azioni_t))
            
        j_rias["Persona"] = persone_rias_list
        if azioni_rias_list:
            # deduplicate actions superficially by description if needed, or just keep them
            j_rias["Azione"] = azioni_rias_list
            
        applica_postprocessing(j_rias); _marca_troncamenti(j_rias)
        testimoni_rias = []
        for t_idx, (nome_t, dati_t) in enumerate(risultati.items()):
            m_t = dati_t["metriche"]; j_t = dati_t["consensus"]
            test_t = j_t.get("Testimonianza", {})
            if not isinstance(test_t, dict): test_t = {}
            testimoni_rias.append({
                "ID_Testimone": f"T-{t_idx+1:03d}", "File_Audio": nome_t,
                "Lingua": test_t.get("Lingua","Italiano"),
                "Modalità_Racconto": test_t.get("Modalità_Racconto","N/D"),
                "Score_Affidabilità": test_t.get("Score_Affidabilità","N/D"),
                "Livello_Dettaglio": test_t.get("Livello_Dettaglio","N/D"),
                "Note_Prosodiche": dati_prosodici.get(nome_t,"N/D"),
                "Completezza_Campi_%": f"{m_t['Completezza_Campi_%']}%",
                "Coerenza_Inter_Run_%": f"{m_t['Coerenza_Inter_Run_%']}%",
                "Campi_Discordanti": str(m_t["Campi_Discordanti"]),
                "Qualità_Trascrizione": m_t["Qualità_Trascrizione"],
            })
        j_rias["Testimonianza"] = testimoni_rias
        wb_rias = scrivi_report_multifoglio(
            schema_path, j_rias, "; ".join(risultati.keys()), 0, id_rias,
            {"Completezza_Campi_%": f"{comp_rias}%"},
            discordanze_inter=discordanze_inter, testimoni_lista=testimoni_rias)
        path_rias = os.path.join(output_dir, "REPORT_RIASSUNTIVO.xlsx")
        wb_rias.save(path_rias)
        print(f"  Completezza riassuntivo: {comp_rias}%\n  Salvato: {path_rias}")
        return path_rias, j_rias
    except Exception as e:
        print(f"  Report riassuntivo fallito: {e}"); return None, None


def _genera_trascrizioni_testo(contenuti_testuali, discordanze_inter):
    W = 70; sep = "=" * W; dsh = "-" * W
    righe = [sep, "  TRASCRIZIONI TESTIMONIANZE -- GUIDA ALL'OPERATORE",
             f"  Generato: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
             sep, "", "  Usare per verificare i campi in GIALLO nel REPORT_RIASSUNTIVO.xlsx.", ""]
    if discordanze_inter:
        righe += [sep, "  CAMPI DISCORDANTI -- RICHIEDONO DECISIONE DELL'OPERATORE", sep, ""]
        for i, d in enumerate(discordanze_inter, 1):
            righe.append(f"  {i}. {d.get('campo','')} [{d.get('impatto_sicurezza','?')}]")
            for t, v in d.get("valori",{}).items(): righe.append(f"       . {t}: {v}")
            if d.get("spiegazione"): righe.append(f"       -> {d['spiegazione']}")
            righe.append("")
    else:
        righe += [sep, "  Nessuna discordanza rilevata.", sep, ""]
    righe += [sep, "  TRASCRIZIONI ORIGINALI", sep]
    for nome, testo in contenuti_testuali.items():
        righe += ["", sep, f"  TESTIMONIANZA: {nome}", dsh, "", testo.strip(), ""]
    righe.append(sep)
    return "\n".join(righe)


def salva_report_per_run(risultati: dict, nome_file: str,
                         valida_run_dir: str, schema_path: str) -> int:
    """Genera un Excel per ognuna delle N run del file indicato (per validazione metriche)."""
    os.makedirs(valida_run_dir, exist_ok=True)
    dati = risultati.get(nome_file, {})
    runs = dati.get("runs", [])
    if not runs:
        return 0
    ev0 = runs[0].get("Evento", {}) if isinstance(runs[0].get("Evento"), dict) else {}
    id_evento = costruisci_id_evento(ev0.get("Data", "N/D"), ev0.get("Tipo_Evento", "incidente"))
    nome_p = os.path.splitext(nome_file)[0].replace(" ", "_")
    for i, run_json in enumerate(runs, start=1):
        run_copy = copy.deepcopy(run_json)
        applica_postprocessing(run_copy)
        out_path = os.path.join(valida_run_dir, f"RUN_{i:02d}_{nome_p}.xlsx")
        wb = scrivi_report_multifoglio(schema_path, run_copy, nome_file, i, id_evento)
        wb.save(out_path)
        print(f"  [valida_run] Salvato: {out_path}")
    return len(runs)


# ==============================================================
# run_pipeline() -- entry point programmatico (usato da Streamlit)
# ==============================================================

def run_pipeline(files_input: list, api_key: str, base_dir: str,
                 ontology_path: str = None, schema_path: str = None,
                 n_runs: int = 5, output_dir: str = None,
                 model: str = None,
                 api_keys: list | None = None) -> "PipelineResult":
    """Esegue la pipeline in modo programmatico e ritorna PipelineResult."""
    global client, ONTOLOGIA, GUIDA_ONTOLOGIA, SCHEMA_JSON_BASE, DATI_PROSODICI
    global MODELLO_EXTRACTOR, MODELLO_JUDGE

    if model:
        MODELLO_EXTRACTOR = model
        MODELLO_JUDGE     = model

    import time as _time
    pipeline_start = _time.time()

    base_dir = os.path.abspath(base_dir)
    ont_path = ontology_path or os.path.join(base_dir, "Nuova_Ontologia_Tabellare.xlsx")
    sch_path = schema_path   or os.path.join(base_dir, "Nuovo_schema_Report.xlsx")
    out_dir  = output_dir    or os.path.join(base_dir, "output_reports")
    os.makedirs(out_dir, exist_ok=True)

    log_lines = []
    def _logp(msg):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        log_lines.append(line)
        print(f"  {msg}")

    _logp(f"Pipeline start | base_dir={base_dir} | n_runs={n_runs} | output_dir={out_dir}")

    # Inizializza pool di chiavi.
    # Priorità: api_keys (lista esplicita dal caller) > GEMINI_API_KEYS env > api_key singola.
    if api_keys:
        cooldown_base = float(os.environ.get("GEMINI_COOLDOWN_BASE_S", "60"))
        pool = init_pool(api_keys, cooldown_base_s=cooldown_base)
    else:
        pool = _inizializza_pool(api_key_principale=api_key)
    # Test connessione: prova OGNI chiave, marca le morte in cooldown, procede se almeno una è viva.
    keys_ok = _test_connessione_pool(pool, MODELLO_EXTRACTOR, log_fn=_logp)
    # `client` globale (per back-compat) punta alla prima chiave VIVA, non a quella di indice 0.
    client = pool._clients[keys_ok[0]]
    ONTOLOGIA        = carica_ontologia(ont_path)
    GUIDA_ONTOLOGIA  = crea_guida_ontologica(ONTOLOGIA)
    SCHEMA_JSON_BASE = crea_schema_json(ONTOLOGIA)
    _logp(f"Ontologia caricata: {len(ONTOLOGIA)} entità")

    _banner("INGESTION")
    files_elab = _esegui_ingestion(files_input, base_dir)
    _logp(f"Ingestion: {len(files_elab)}/{len(files_input)} file accettati")
    if not files_elab: raise ValueError("Nessun file valido da processare.")

    _banner("TRASCRIZIONE")
    cont_test, metr_qual, dat_pros = _esegui_trascrizione(files_elab, base_dir)
    DATI_PROSODICI = dat_pros
    _logp(f"Trascrizione: {len(cont_test)} testimonianze")
    if not cont_test: raise ValueError("Nessuna trascrizione valida prodotta.")

    _banner(f"ESTRAZIONE ONTOLOGICA -- {n_runs} RUN")
    estraz_t0 = _time.time()
    risultati = _esegui_estrazione(cont_test, metr_qual, dat_pros, n_runs)
    estraz_elapsed = _time.time() - estraz_t0
    _logp(f"Estrazione: {len(risultati)} file in {estraz_elapsed:.1f}s")
    if not risultati: raise ValueError("Nessun risultato estratto.")

    _banner("DISCORDANZE INTER-TESTIMONIANZA")
    discordanze = calcola_discordanze_inter_testimonianza(risultati)
    _logp(f"Discordanze rilevate: {len(discordanze)}")
    for _i, _d in enumerate(discordanze, 1):
        _vt = list((_d.get('valori') or {}).keys())
        _logp(f"  [{_i}] {_d.get('campo')} · impatto={_d.get('impatto_sicurezza')} · testimoni={_vt}")

    _banner("REPORT INDIVIDUALI")
    rep_ind = _genera_report_ind(risultati, sch_path, n_runs, out_dir)

    _banner("REPORT RIASSUNTIVO")
    rep_rias_path, rep_rias_json = _genera_report_rias(risultati, dat_pros, discordanze, sch_path, out_dir)

    # Salvataggio N run separate per validazione metriche (per-incident)
    valida_run_dir = os.path.join(out_dir, "valida_run")
    runs_saved_per_file = {}
    for nome_file in risultati.keys():
        try:
            n = salva_report_per_run(risultati, nome_file, valida_run_dir, sch_path)
            runs_saved_per_file[nome_file] = n
            _logp(f"Validazione: salvati {n} report Excel per {nome_file}")
        except Exception as e:
            runs_saved_per_file[nome_file] = 0
            _logp(f"Validazione: ERRORE salvataggio run per {nome_file}: {e}")

    nota = genera_nota_critica(risultati, metr_qual, SOGLIE, n_runs,
                               rep_ind, rep_rias_path, discordanze_inter=discordanze)
    with open(os.path.join(out_dir, "NOTA_CRITICA.txt"), "w", encoding="utf-8") as f:
        f.write(nota)

    trasc_testo = _genera_trascrizioni_testo(cont_test, discordanze)
    with open(os.path.join(out_dir, "TRASCRIZIONI.txt"), "w", encoding="utf-8") as f:
        f.write(trasc_testo)

    # Costruzione processing_summary (dati derivati dai risultati)
    files_summary = []
    for nome_file, dati in risultati.items():
        runs = dati.get("runs", []) if isinstance(dati, dict) else []
        runs_completed = len(runs)
        runs_failed = dati.get("runs_falliti", 0) if isinstance(dati, dict) else 0
        runs_total = dati.get("n_runs_richiesti", runs_completed + runs_failed) if isinstance(dati, dict) else runs_completed + runs_failed
        consensus_run_index = dati.get("run_scelto") if isinstance(dati, dict) else None
        files_summary.append({
            "file": nome_file,
            "runs_total": runs_total,
            "runs_completed": runs_completed,
            "runs_failed": runs_failed,
            "consensus_run_index": consensus_run_index,
            "consensus_strategy": dati.get("consensus_strategy", "consenso multi-run") if isinstance(dati, dict) else None,
            "valida_run_files_saved": runs_saved_per_file.get(nome_file, 0),
            "errors": dati.get("errors", []) if isinstance(dati, dict) else [],
        })

    total_elapsed = _time.time() - pipeline_start
    processing_summary = {
        "started_at": datetime.fromtimestamp(pipeline_start).isoformat(),
        "completed_at": datetime.now().isoformat(),
        "total_elapsed_sec": round(total_elapsed, 2),
        "n_runs_requested": n_runs,
        "files": files_summary,
        "discordanze_count": len(discordanze),
        "valida_run_dir": valida_run_dir,
    }
    _logp(f"Pipeline completata in {total_elapsed:.1f}s")

    processing_log = "\n".join(log_lines)
    with open(os.path.join(out_dir, "processing_log.txt"), "w", encoding="utf-8") as f:
        f.write(processing_log)

    return PipelineResult(
        risultati=risultati, discordanze=discordanze, metriche_qualita=metr_qual,
        trascrizioni=cont_test, dati_prosodici=dat_pros, output_dir=out_dir,
        report_ind_paths=rep_ind, report_rias_path=rep_rias_path or "",
        riassuntivo_json=rep_rias_json or {},
        nota_critica=nota, trascrizioni_testo=trasc_testo,
        processing_summary=processing_summary, processing_log=processing_log,
    )


# ==============================================================
# main() -- entry point CLI (comportamento originale)
# ==============================================================

def main():
    global client, ONTOLOGIA, GUIDA_ONTOLOGIA, SCHEMA_JSON_BASE, DATI_PROSODICI

    _banner("AVVIO SISTEMA")
    print(f"  Base dir   : {BASE_DIR}")
    print(f"  Output dir : {OUTPUT_DIR}")
    print(f"  N runs     : {N_RUNS}")
    print(f"  Files      : {FILES_INPUT}")

    for _p, _n in [(ONTOLOGIA_PATH,"Nuova_Ontologia_Tabellare.xlsx"),
                   (SCHEMA_PATH,"Nuovo_schema_Report.xlsx")]:
        if not os.path.exists(_p):
            print(f"\nFile non trovato: {_p}\nMetti '{_n}' in: {BASE_DIR}"); sys.exit(1)

    pool = _inizializza_pool(api_key_principale=API_KEY)
    try:
        keys_ok = _test_connessione_pool(pool, MODELLO_EXTRACTOR)
        client = pool._clients[keys_ok[0]]
        print(f"\nGemini connesso -- {MODELLO_EXTRACTOR} "
              f"({len(keys_ok)}/{pool.size} chiave/i utilizzabili)")
    except Exception as e:
        print(f"\nErrore API: {e}"); sys.exit(1)

    _banner("CARICAMENTO ONTOLOGIA")
    ONTOLOGIA        = carica_ontologia(ONTOLOGIA_PATH)
    GUIDA_ONTOLOGIA  = crea_guida_ontologica(ONTOLOGIA)
    SCHEMA_JSON_BASE = crea_schema_json(ONTOLOGIA)
    _n_attr_tot = sum(len(e["attributi"]) for e in ONTOLOGIA.values())
    print(f"  Ontologia: {len(ONTOLOGIA)} entita', {_n_attr_tot} attributi")

    _banner("INGESTION INPUT")
    FILES_ELABORATI = _esegui_ingestion(FILES_INPUT, BASE_DIR)
    print(f"\n  File accettati: {len(FILES_ELABORATI)}/{len(FILES_INPUT)}")
    if not FILES_ELABORATI:
        print("  Nessun file da processare."); sys.exit(1)

    _banner("TRASCRIZIONE / LETTURA")
    CONTENUTI_TESTUALI, METRICHE_QUALITA, DATI_PROSODICI = _esegui_trascrizione(
        FILES_ELABORATI, BASE_DIR)
    if not CONTENUTI_TESTUALI:
        print("  Nessuna trascrizione valida."); sys.exit(1)

    _banner(f"ESTRAZIONE ONTOLOGICA -- {N_RUNS} RUN PER FILE")
    RISULTATI = _esegui_estrazione(CONTENUTI_TESTUALI, METRICHE_QUALITA, DATI_PROSODICI, N_RUNS)
    if not RISULTATI:
        print("  Nessun risultato."); sys.exit(1)

    _banner("DISCORDANZE INTER-TESTIMONIANZA")
    DISCORDANZE_INTER = calcola_discordanze_inter_testimonianza(RISULTATI)
    if DISCORDANZE_INTER:
        critiche  = [d for d in DISCORDANZE_INTER if d.get("impatto_sicurezza") == "CRITICO"]
        rilevanti = [d for d in DISCORDANZE_INTER if d.get("impatto_sicurezza") == "RILEVANTE"]
        print(f"  CRITICHE: {len(critiche)}  |  RILEVANTI: {len(rilevanti)}  |  TOTALE: {len(DISCORDANZE_INTER)}")
    else:
        print("  Nessuna discordanza inter-testimonianza rilevata")

    _banner("GENERAZIONE REPORT INDIVIDUALI")
    REPORT_IND_PATHS = _genera_report_ind(RISULTATI, SCHEMA_PATH, N_RUNS, OUTPUT_DIR)

    _banner("GENERAZIONE REPORT RIASSUNTIVO")
    REPORT_RIAS_PATH, _ = _genera_report_rias(RISULTATI, DATI_PROSODICI, DISCORDANZE_INTER,
                                           SCHEMA_PATH, OUTPUT_DIR)

    _banner("GENERAZIONE NOTA CRITICA")
    nota = genera_nota_critica(RISULTATI, METRICHE_QUALITA, SOGLIE, N_RUNS,
                               REPORT_IND_PATHS, REPORT_RIAS_PATH, discordanze_inter=DISCORDANZE_INTER)
    nota_path = os.path.join(OUTPUT_DIR, "NOTA_CRITICA.txt")
    with open(nota_path, "w", encoding="utf-8") as f: f.write(nota)
    print(f"  Salvato: {nota_path}")

    _banner("GENERAZIONE TRASCRIZIONI")
    trasc_testo = _genera_trascrizioni_testo(CONTENUTI_TESTUALI, DISCORDANZE_INTER)
    trasc_path  = os.path.join(OUTPUT_DIR, "TRASCRIZIONI.txt")
    with open(trasc_path, "w", encoding="utf-8") as f: f.write(trasc_testo)
    print(f"  Salvato: {trasc_path}")

    _banner("PACCHETTO PRODOTTO")
    tutti_i_file = list(REPORT_IND_PATHS.values())
    if REPORT_RIAS_PATH: tutti_i_file.append(REPORT_RIAS_PATH)
    tutti_i_file += [nota_path, trasc_path]
    for p in tutti_i_file:
        dim = os.path.getsize(p)/1024 if os.path.exists(p) else 0
        print(f"  {os.path.basename(p):45s} {dim:.0f} KB")
    print(f"\n  Tutto in: {OUTPUT_DIR}")
    print(f"  Pipeline completata in un solo avvio.")


if __name__ == "__main__":
    main()
