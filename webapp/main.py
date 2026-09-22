import os
import re
import sys
import json
import time
import uuid
import shutil
import yaml
from pathlib import Path
from datetime import datetime, date
from typing import List, Optional, Dict, Any

# Forza UTF-8 su stdout/stderr (Windows usa cp1252 di default e crasha sui caratteri unicode)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Sopprime il rumore di uvicorn access log (es. "INFO: 127.0.0.1 - GET /api/health 200")
# Le richieste importanti sono già loggate manualmente con prefissi [pipeline] [wiki-bg] [Pool].
import logging as _logging
_logging.getLogger("uvicorn.access").setLevel(_logging.WARNING)

from config import BASE_DIR, INCIDENTS_DIR, WIKI_DIR, GEMINI_API_KEY, ONTOLOGIA_PATH, SCHEMA_PATH, GEMINI_MODEL_CHAT, CHAT_DOMAIN_SYSTEM_PROMPT, SUPPORTED_GEMINI_MODELS
from state_manager import StateManager
from analisi_testimonianze import run_pipeline, salva_report_per_run
from wiki_updater import WikiUpdater, scan_event_links

app = FastAPI(title="Analisi Testimonianze API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

sm = StateManager(INCIDENTS_DIR)
SETTINGS_PATH = BASE_DIR / "settings_ui.json"
MAX_API_KEYS = 5

# -----------------
# Models
# -----------------
class ApproveRequest(BaseModel):
    operator: str
    approved_json: Dict[str, Any]
    notes: Optional[str] = ""

class RejectRequest(BaseModel):
    operator: str
    reason: str

class ChatMessageRequest(BaseModel):
    message: str

class SaveDraftRequest(BaseModel):
    draft_json: Dict[str, Any]
    witness_drafts: Optional[Dict[str, Dict[str, Any]]] = None

class ResolveDiscordanceRequest(BaseModel):
    resolutions: Dict[str, str]

class SettingsRequest(BaseModel):
    api_key: Optional[str] = None              # legacy (back-compat con vecchi client)
    api_keys: Optional[List[str]] = None       # nuovo formato: lista chiavi per il pool
    model: Optional[str] = None
    n_runs_default: Optional[int] = None
    ontology_path: Optional[str] = None
    schema_path: Optional[str] = None

# -----------------
# Helpers
# -----------------
def _load_settings() -> dict:
    defaults = {
        "api_keys": [GEMINI_API_KEY] if GEMINI_API_KEY else [],
        "model": GEMINI_MODEL_CHAT,
        "n_runs_default": 3,
    }
    if SETTINGS_PATH.exists():
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            # Migrazione: vecchio api_key singolo -> api_keys lista
            if "api_keys" not in saved and saved.get("api_key"):
                saved["api_keys"] = [saved["api_key"]]
            saved.pop("api_key", None)
            for k in ("ontology_path", "schema_path"):
                saved.pop(k, None)
            if saved.get("model") and saved["model"] not in SUPPORTED_GEMINI_MODELS:
                saved.pop("model", None)
            if isinstance(saved.get("api_keys"), list):
                saved["api_keys"] = [str(k).strip() for k in saved["api_keys"] if str(k).strip()]
            defaults.update(saved)
        except Exception:
            pass
    return defaults

def _get_api_keys() -> List[str]:
    """Lista chiavi effettive: settings_ui.json > GEMINI_API_KEYS env > GEMINI_API_KEY."""
    keys = _load_settings().get("api_keys") or []
    if keys:
        return keys
    env_csv = os.environ.get("GEMINI_API_KEYS", "").strip()
    if env_csv:
        return [k.strip() for k in env_csv.split(",") if k.strip()]
    if GEMINI_API_KEY:
        return [GEMINI_API_KEY]
    return []

def _get_api_key() -> str:
    """Prima chiave disponibile (per chat e operazioni non parallele)."""
    keys = _get_api_keys()
    return keys[0] if keys else ""

def _get_model() -> str:
    """Modello Gemini effettivo: settings_ui.json se presente, altrimenti default config."""
    m = _load_settings().get("model")
    if m in SUPPORTED_GEMINI_MODELS:
        return m
    return GEMINI_MODEL_CHAT

def _get_model_chat() -> str:
    return _get_model()

# ----- Pool multi-chiave per chat/wiki -----
from client_pool import init_pool as _init_pool, get_pool as _get_pool, is_quota_error as _is_quota_error

def _ensure_pool():
    """Crea/aggiorna il pool globale se la lista chiavi è cambiata.
    Ritorna il pool corrente, o None se nessuna chiave configurata."""
    keys = _get_api_keys()
    if not keys:
        return None
    pool = _get_pool()
    if pool is None or list(getattr(pool, "_keys", [])) != keys:
        cooldown_base = float(os.environ.get("GEMINI_COOLDOWN_BASE_S", "60"))
        pool = _init_pool(keys, cooldown_base_s=cooldown_base)
    return pool

def _chat_generate(prompt, model: Optional[str] = None, config=None):
    """Chiama Gemini generate_content tramite il pool (rotazione + cooldown su 429).
    Solleva HTTPException(500) se nessuna chiave è configurata."""
    from google.genai import types
    cfg = config if config is not None else types.GenerateContentConfig(
        max_output_tokens=8192, temperature=0.2
    )
    mdl = model or _get_model_chat()
    pool = _ensure_pool()
    if pool is None:
        raise HTTPException(status_code=500, detail="Nessuna chiave Gemini configurata. Inseriscila in Impostazioni.")
    if pool.size == 1:
        return pool.primary_client.models.generate_content(model=mdl, contents=prompt, config=cfg)
    
    last_err = None
    for _ in range(pool.size):
        try:
            with pool.lease() as c:
                return c.models.generate_content(model=mdl, contents=prompt, config=cfg)
        except Exception as e:
            if getattr(e, "code", None) == 429 or "429" in str(e) or "503" in str(e) or "exhausted" in str(e).lower():
                last_err = e
                continue
            raise e
    raise last_err or Exception("All keys exhausted")

# Inizializza il pool a module-load (esegue sia su `uvicorn main:app` che su import diretto).
# Logica idempotente: se nessuna chiave configurata, pool resta None.
try:
    _initial_pool = _ensure_pool()
    if _initial_pool is None:
        print("[startup] Nessuna chiave Gemini configurata. Imposta da UI Impostazioni.", flush=True)
    else:
        print(f"[startup] Pool Gemini pronto: {_initial_pool.size} chiave/i", flush=True)
except Exception as _e:
    print(f"[startup] errore init pool: {_e!r}", flush=True)

def _is_on_topic(question: str) -> bool:
    """Classifica la domanda dell'operatore. Strategia fail-open: in caso di dubbio o errore, accetta."""
    if not question or not question.strip():
        return False
    if _ensure_pool() is None:
        return True  # senza chiave non possiamo classificare → permetti
    try:
        from google.genai import types
        resp = _chat_generate(
            prompt=f"{CHAT_DOMAIN_SYSTEM_PROMPT}\n\nDomanda: {question}",
            config=types.GenerateContentConfig(max_output_tokens=5, temperature=0.0),
        )
        verdict = (resp.text or "").strip().upper()
        is_off = verdict.startswith("NO")
        try:
            print(f"[on-topic] q='{question[:60]}...' verdict={verdict!r} off={is_off}")
        except Exception:
            pass
        # Solo se il classificatore dice esplicitamente NO -> blocca; altrimenti accetta
        return not is_off
    except Exception as e:
        try:
            print(f"[on-topic] errore classificatore (fail-open): {e}")
        except Exception:
            pass
        return True


def _extract_proposed_changes(text: str):
    pattern = r'```proposed_changes\s*([\s\S]*?)```'
    match = re.search(pattern, text)
    if match:
        try:
            changes = json.loads(match.group(1).strip())
            clean_text = re.sub(pattern, '', text).strip()
            return clean_text, changes
        except Exception:
            pass
    return text, []

# -----------------
# Background Tasks
# -----------------
_MESI_IT = {
    "gennaio": "01", "febbraio": "02", "marzo": "03", "aprile": "04",
    "maggio": "05", "giugno": "06", "luglio": "07", "agosto": "08",
    "settembre": "09", "ottobre": "10", "novembre": "11", "dicembre": "12",
    # forme abbreviate
    "gen": "01", "feb": "02", "mar": "03", "apr": "04", "mag": "05",
    "giu": "06", "lug": "07", "ago": "08", "set": "09", "sett": "09",
    "ott": "10", "nov": "11", "dic": "12",
}

def _yy_to_yyyy(yy: str) -> str:
    """20XX se < 70, 19XX altrimenti."""
    n = int(yy)
    return f"20{yy}" if n < 70 else f"19{yy}"

def _parse_event_date_yyyymmdd(data_str) -> Optional[str]:
    """Estrae YYYYMMDD da una stringa data libera (italiana o numerica). None se non parseabile."""
    if data_str is None:
        return None
    s = str(data_str).strip().lower()
    if not s or s in ("n/d", "nd", "n.d.", "null", "none", "n/a"):
        return None
    # Normalizza spazi e separatori
    s = re.sub(r"\s+", " ", s)

    # forma italiana: "10 aprile 2025", "10/apr/2025", "10-apr-25"
    for nome, num in _MESI_IT.items():
        if re.search(rf"\b{nome}\b", s) or f" {nome} " in f" {s} ":
            # cerca giorno (1-2 cifre, eventualmente prima del mese) e anno (2 o 4 cifre)
            mg = re.search(r"\b(\d{1,2})\b", s)
            my4 = re.search(r"\b(\d{4})\b", s)
            my2 = re.search(r"\b(\d{2})\b\s*$", s)  # anno a 2 cifre alla fine
            if mg and my4:
                return f"{my4.group(1)}{num}{mg.group(1).zfill(2)}"
            if mg and my2 and my2.group(1) != mg.group(1):
                return f"{_yy_to_yyyy(my2.group(1))}{num}{mg.group(1).zfill(2)}"
    # DD/MM/YYYY o DD-MM-YYYY o DD.MM.YYYY
    m = re.search(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b", s)
    if m:
        return f"{m.group(3)}{m.group(2).zfill(2)}{m.group(1).zfill(2)}"
    # DD/MM/YY (anno a 2 cifre)
    m = re.search(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2})\b", s)
    if m:
        return f"{_yy_to_yyyy(m.group(3))}{m.group(2).zfill(2)}{m.group(1).zfill(2)}"
    # YYYY-MM-DD ISO
    m = re.search(r"\b(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})\b", s)
    if m:
        return f"{m.group(1)}{m.group(2).zfill(2)}{m.group(3).zfill(2)}"
    return None


def _scan_json_for_date(obj) -> Optional[str]:
    """Ricerca ricorsiva di un campo data parseabile nel JSON consenso.
    Cerca chiavi che contengono 'data' (case-insensitive), 'Data_Evento', 'data_incidente', ecc."""
    DATE_KEYS = ("data", "date", "data_evento", "data_incidente", "data_accadimento", "giorno")
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower().strip()
            if any(dk in kl for dk in DATE_KEYS) and isinstance(v, (str, int, float)):
                parsed = _parse_event_date_yyyymmdd(str(v))
                if parsed:
                    return parsed
        for v in obj.values():
            r = _scan_json_for_date(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _scan_json_for_date(v)
            if r:
                return r
    return None


def _derive_event_id(riassuntivo_json: dict, incident_id: str) -> Optional[str]:
    """Costruisce un event_id basato sulla data dell'evento estratta. Fallback: None (l'archivio userà incident_id)."""
    if not isinstance(riassuntivo_json, dict):
        print(f"[derive-id] {incident_id}: riassuntivo_json non è dict")
        return None
    ev = riassuntivo_json.get("Evento") or {}
    data_raw = ev.get("Data") if isinstance(ev, dict) else None

    yyyymmdd = _parse_event_date_yyyymmdd(data_raw)
    # Fallback: scansiona ricorsivamente JSON per qualunque campo data
    if not yyyymmdd:
        yyyymmdd = _scan_json_for_date(riassuntivo_json)
        if yyyymmdd:
            print(f"[derive-id] {incident_id}: data trovata via scan ricorsivo → {yyyymmdd}")

    if not yyyymmdd:
        print(f"[derive-id] {incident_id}: nessuna data parseabile (Evento.Data={data_raw!r}) → uso fallback incident_id")
        return None
    print(f"[derive-id] {incident_id}: Data={data_raw!r} → YYYYMMDD={yyyymmdd}")
    # Conta quanti incidenti già esistono con stesso event_id-prefix per assegnare il NNN
    existing = []
    for p in INCIDENTS_DIR.iterdir():
        if not p.is_dir():
            continue
        try:
            st = sm.get_state(p.name)
            ev_id = st.get("event_id")
            if isinstance(ev_id, str) and ev_id.startswith(f"INC-{yyyymmdd}-"):
                existing.append(ev_id)
        except Exception:
            continue
    nnn = len(existing) + 1
    return f"INC-{yyyymmdd}-{nnn:03d}"


def background_pipeline(incident_id: str, files: List[str], input_dir: Path, output_dir: str, n_runs: int):
    print(f"[pipeline] START incident={incident_id} files={files} n_runs={n_runs}", flush=True)
    api_keys = _get_api_keys()
    if not api_keys:
        print(f"[pipeline] ABORT {incident_id}: nessuna chiave Gemini configurata.", flush=True)
        sm.update_status(incident_id, "ERROR", error_message="Nessuna chiave Gemini configurata. Inseriscila in Impostazioni.")
        return
    model_used = _get_model()
    print(f"[pipeline] pool: {len(api_keys)} chiave/i · modello: {model_used} · n_runs: {n_runs}", flush=True)
    try:
        result = run_pipeline(
            files_input=files,
            api_key=api_keys[0],
            api_keys=api_keys,
            base_dir=str(input_dir),
            ontology_path=ONTOLOGIA_PATH,
            schema_path=SCHEMA_PATH,
            n_runs=n_runs,
            output_dir=output_dir,
            model=model_used,
        )
        # I report Excel delle N run sono salvati dalla pipeline in {output_dir}/valida_run/
        sm.save_pipeline_result(incident_id, result, output_dir)

        # Deriva event_id dalla data evento estratta dal riassuntivo
        event_id = _derive_event_id(result.riassuntivo_json or {}, incident_id)
        if event_id:
            sm.update_status(incident_id, "PENDING", event_id=event_id, model_used=model_used, n_runs=n_runs)
        else:
            sm.update_status(incident_id, "PENDING", model_used=model_used, n_runs=n_runs)
        print(f"[pipeline] OK {incident_id} status=PENDING event_id={event_id or 'N/A'}", flush=True)

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[pipeline] ERROR {incident_id}: {e!r}\n{tb}", flush=True)
        sm.update_status(incident_id, "ERROR", error_message=str(e))

# -----------------
# Incidents
# -----------------
@app.get("/api/incidents")
def list_incidents():
    return sm.list_incidents()

@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str):
    try:
        state = sm.get_state(incident_id)
        result_data = sm.load_pipeline_result_data(incident_id)
        draft_data = sm.load_draft_json(incident_id)
        witness_drafts = sm.load_witness_drafts(incident_id)
        chat_history = sm.load_chat_history(incident_id)
        return {
            "state": state, "result": result_data, "draft": draft_data,
            "witness_drafts": witness_drafts, "chat_history": chat_history,
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Incident not found")

@app.post("/api/incidents/upload")
def upload_files(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    n_runs: int = Form(3)
):
    print(f"[upload] received {len(files)} files, n_runs={n_runs}", flush=True)
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    saved_paths = []

    for uf in files:
        dest = tmp / uf.filename
        with open(dest, "wb") as f:
            f.write(uf.file.read())
        saved_paths.append(dest)

    incident_id = sm.create_incident(saved_paths, tmp)
    input_dir = sm.get_input_dir(incident_id)
    output_dir = str(sm.get_incident_dir(incident_id))

    files_to_process = sorted([
        f.name for f in input_dir.iterdir()
        if f.suffix.lower() in {".mp3", ".wav", ".m4a", ".ogg", ".txt", ".pdf"}
    ])

    sm.update_status(incident_id, "PROCESSING")
    background_tasks.add_task(background_pipeline, incident_id, files_to_process, input_dir, output_dir, n_runs)
    print(f"[upload] incident {incident_id} -> PROCESSING ({len(files_to_process)} file)", flush=True)

    return {"incident_id": incident_id, "status": "PROCESSING"}

@app.post("/api/incidents/{incident_id}/save_draft")
def save_draft(incident_id: str, req: SaveDraftRequest):
    sm.save_draft_json(incident_id, req.draft_json)
    if req.witness_drafts is not None:
        sm.save_witness_drafts(incident_id, req.witness_drafts)
    return {"status": "ok"}

@app.post("/api/incidents/{incident_id}/resolve_discordance")
def resolve_discordance(incident_id: str, req: ResolveDiscordanceRequest):
    state = sm.get_state(incident_id)
    sm.update_status(incident_id, state["status"], discordance_resolutions=req.resolutions)
    return {"status": "ok"}

def _apply_resolutions(approved_json: dict, discordanze: list, resolutions: dict) -> dict:
    """Applica le scelte dell'utente sulle discordanze al JSON approvato.

    Sostituisce ogni `[DISC: A / B]` con il valore esplicitamente scelto dall'utente
    nel tab Discordanze. Garantisce che WikiUpdater riceva dati puliti, senza marker DISC.

    resolutions: {idx: valore_scelto} (idx = posizione stringa nella lista discordanze)
    discordanze[idx]: {"campo": "Evento.Tipo_Evento", "valori": {...}, ...}
    """
    import copy
    if not resolutions or not discordanze:
        return approved_json
    out = copy.deepcopy(approved_json)
    applied = 0
    for idx_str, scelta in (resolutions or {}).items():
        try:
            idx = int(idx_str)
            if idx < 0 or idx >= len(discordanze):
                continue
            d = discordanze[idx]
            campo = d.get("campo", "")
            if not campo or scelta is None:
                continue
            parts = campo.split(".")
            cursor = out
            for p in parts[:-1]:
                if isinstance(cursor, dict) and p in cursor:
                    cursor = cursor[p]
                else:
                    cursor = None
                    break
            if isinstance(cursor, dict) and parts[-1] in cursor:
                cursor[parts[-1]] = scelta
                applied += 1
        except (ValueError, IndexError, KeyError, TypeError):
            continue
    if applied:
        print(f"[approve] applicate {applied} risoluzioni discordanze al report finale", flush=True)
    return out

_DISC_RE = re.compile(r"^\s*\[DISC:\s*(.*?)\s*/\s*.*?\]\s*$", re.S)

def _strip_disc_markers(obj):
    """Rimuove ricorsivamente i marker [DISC: A / B] residui dal JSON,
    sostituendo con il primo valore.

    Il riassuntivo LLM marca le divergenze con [DISC: ...] su TUTTI i campi
    inconsistenti tra testimoni, ma il judge LLM filtra come 'rilevanti' solo
    un sottoinsieme — le restanti restano nel JSON ma NON sono nel tab UI.
    Senza questo strip, finiscono in Obsidian come frontmatter YAML invalido."""
    if isinstance(obj, dict):
        return {k: _strip_disc_markers(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_disc_markers(v) for v in obj]
    if isinstance(obj, str):
        m = _DISC_RE.match(obj)
        if m:
            return m.group(1).strip()
    return obj

def background_wiki_update(incident_id: str, event_id_for_wiki: str, result_data: dict,
                            approved_json: dict, approval_meta: dict, source_dir):
    """Esegue WikiUpdater in background. Aggiorna wiki_update_status nello state."""
    import time as _time
    t0 = _time.time()
    model_used = _get_model()
    try:
        sm.update_status(incident_id, "APPROVED",
                         wiki_update_status="running",
                         wiki_update_started_at=datetime.now().isoformat(),
                         model_used=model_used)
        print(f"[wiki-bg] {incident_id}: avvio aggiornamento wiki · modello={model_used}", flush=True)
        updater = WikiUpdater(WIKI_DIR, _get_api_key(), model=model_used)
        report = updater.run_approval_update(
            event_id=event_id_for_wiki,
            result_data=result_data,
            approved_json=approved_json,
            approval_metadata=approval_meta,
            source_dir=source_dir,
        )
        elapsed = _time.time() - t0
        # Conta operazioni riuscite/fallite/skip
        ops_summary = ""
        try:
            ops_ok = sum(1 for o in report.operations if o.status == "OK")
            ops_err = sum(1 for o in report.operations if o.status == "ERROR")
            ops_skip = sum(1 for o in report.operations if o.status == "SKIP")
            ops_summary = f" · {ops_ok} OK, {ops_err} errori, {ops_skip} skip"
        except Exception:
            pass
        sm.update_status(incident_id, "APPROVED",
                         wiki_update_status="ok",
                         wiki_update_completed_at=datetime.now().isoformat(),
                         wiki_update_report=report.summary(),
                         wiki_update_elapsed_sec=round(elapsed, 1),
                         model_used=model_used)
        print(f"[wiki-bg] {incident_id}: completato in {elapsed:.1f}s{ops_summary}", flush=True)
    except Exception as e:
        elapsed = _time.time() - t0
        print(f"[wiki-bg] {incident_id}: errore dopo {elapsed:.1f}s: {e!r}", flush=True)
        sm.update_status(incident_id, "APPROVED",
                         wiki_update_status="error",
                         wiki_update_error=str(e),
                         wiki_update_elapsed_sec=round(elapsed, 1))


@app.post("/api/incidents/{incident_id}/approve")
def approve_incident(incident_id: str, req: ApproveRequest, background_tasks: BackgroundTasks):
    state = sm.get_state(incident_id)
    if state["status"] != "PENDING":
        raise HTTPException(status_code=400, detail="Solo incidenti PENDING possono essere approvati")

    result_data = sm.load_pipeline_result_data(incident_id)
    if not result_data:
        raise HTTPException(status_code=400, detail="Dati pipeline non trovati")

    approval_meta = {
        "operator": req.operator,
        "approval_at": datetime.now().isoformat(),
        "notes": req.notes,
        "testimonianze_count": len(result_data.get("trascrizioni", {}))
    }

    event_id_for_wiki = state.get("event_id") or incident_id
    source_dir = sm.get_incident_dir(incident_id)

    # Applica le risoluzioni del tab Discordanze al JSON approvato.
    # Senza questo step, [DISC: A / B] finisce in Obsidian.
    resolutions = state.get("discordance_resolutions") or {}
    discordanze = result_data.get("discordanze", []) if result_data else []
    final_json = _apply_resolutions(req.approved_json, discordanze, resolutions)
    # Strip dei marker DISC fantasma residui (campi non rilevanti per il judge ma marcati)
    final_json = _strip_disc_markers(final_json)

    # Salva subito APPROVED con flag wiki update in pending → risposta immediata
    sm.update_status(
        incident_id, "APPROVED",
        operator=req.operator,
        approval_at=approval_meta["approval_at"],
        chat_approved_json=final_json,
        wiki_update_status="pending",
    )

    # L'aggiornamento wiki (slow, multi-LLM) parte in background
    background_tasks.add_task(
        background_wiki_update,
        incident_id, event_id_for_wiki, result_data,
        final_json, approval_meta, source_dir
    )

    return {"status": "ok", "wiki_update": "started_in_background"}

@app.post("/api/incidents/{incident_id}/reject")
def reject_incident(incident_id: str, req: RejectRequest):
    state = sm.get_state(incident_id)
    if state["status"] != "PENDING":
        raise HTTPException(status_code=400, detail="Solo incidenti PENDING possono essere rifiutati")

    sm.update_status(
        incident_id, "REJECTED",
        operator=req.operator,
        rejection_reason=req.reason,
        rejection_at=datetime.now().isoformat()
    )
    return {"status": "ok"}

@app.post("/api/incidents/{incident_id}/regenerate_wiki")
def regenerate_wiki_endpoint(incident_id: str, background_tasks: BackgroundTasks):
    state = sm.get_state(incident_id)
    if state["status"] != "APPROVED":
        raise HTTPException(status_code=400, detail="Solo incidenti APPROVATI possono rigenerare la wiki")

    result_data = sm.load_pipeline_result_data(incident_id)
    draft_data = sm.load_draft_json(incident_id)

    approval_meta = {
        "operator": state.get("operator", "Sistema"),
        "approval_at": state.get("approval_at", datetime.now().isoformat()),
        "notes": "",
        "testimonianze_count": len(result_data.get("trascrizioni", {})) if result_data else 0
    }

    event_id_for_wiki = state.get("event_id") or incident_id
    source_dir = sm.get_incident_dir(incident_id)
    approved_json = draft_data or (result_data.get("riassuntivo_json") if result_data else {})

    # Applica le risoluzioni dell'utente sulle discordanze prima di rigenerare
    resolutions = state.get("discordance_resolutions") or {}
    discordanze = result_data.get("discordanze", []) if result_data else []
    final_json = _apply_resolutions(approved_json, discordanze, resolutions)
    # Strip dei marker DISC fantasma residui
    final_json = _strip_disc_markers(final_json)

    # Lancia in background per non bloccare il frontend
    background_tasks.add_task(
        background_wiki_update,
        incident_id, event_id_for_wiki, result_data,
        final_json, approval_meta, source_dir
    )
    return {"status": "ok", "wiki_update": "started_in_background"}

@app.get("/api/incidents/{incident_id}/runs_files")
def list_runs_files(incident_id: str):
    """Elenca i file della cartella valida_run/ (run delle N esecuzioni).
    Usato dal frontend per mostrare la lista inline senza aprire Esplora Risorse."""
    try:
        sm.get_state(incident_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Incident not found")
    runs_dir = sm.get_valida_run_dir(incident_id)
    if not runs_dir.exists():
        return {"files": [], "folder": str(runs_dir.resolve()), "exists": False}
    items = []
    for p in sorted(runs_dir.iterdir()):
        if not p.is_file():
            continue
        try:
            st = p.stat()
            items.append({
                "name": p.name,
                "size": st.st_size,
                "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
                "download_url": f"/api/incidents/{incident_id}/runs_files/{p.name}",
            })
        except Exception:
            continue
    return {"files": items, "folder": str(runs_dir.resolve()), "exists": True}


@app.get("/api/incidents/{incident_id}/runs_files/{filename}")
def download_run_file(incident_id: str, filename: str):
    """Scarica un singolo file dalla cartella valida_run/."""
    try:
        sm.get_state(incident_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Incident not found")
    # Anti path-traversal: solo nome file basename, no separatori
    safe_name = Path(filename).name
    if safe_name != filename or "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nome file non valido")
    runs_dir = sm.get_valida_run_dir(incident_id)
    file_path = runs_dir / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File non trovato: {safe_name}")
    # Verifica che il path risolto sia ancora dentro runs_dir (difesa in profondita')
    try:
        file_path.resolve().relative_to(runs_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Path non valido")
    return FileResponse(path=str(file_path), filename=safe_name)


@app.get("/api/incidents/{incident_id}/processing_log")
def download_processing_log(incident_id: str):
    """Scarica il log dettagliato del processo di estrazione/compilazione."""
    try:
        sm.get_state(incident_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Incident not found")
    log_path = sm.get_processing_log_path(incident_id)
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log non disponibile per questo evento")
    return FileResponse(
        path=str(log_path),
        media_type="text/plain",
        filename=f"{incident_id}_processing_log.txt"
    )


@app.get("/api/incidents/{incident_id}/deletion_preview")
def deletion_preview(incident_id: str):
    """Scansione read-only: ritorna tutti i riferimenti dell'evento nella wiki.
    L'operatore userà queste info per pulire manualmente Obsidian (wizard guidato)."""
    try:
        state = sm.get_state(incident_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Incident not found")
    event_id = state.get("event_id") or incident_id
    preview = scan_event_links(WIKI_DIR, event_id)
    preview["incident_status"] = state.get("status")
    preview["incident_dir"] = str(sm.get_incident_dir(incident_id))
    return preview


@app.delete("/api/incidents/{incident_id}")
def delete_incident(incident_id: str):
    inc_dir = sm.get_incident_dir(incident_id)
    if inc_dir.exists():
        shutil.rmtree(inc_dir, ignore_errors=True)
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Not found")

# -----------------
# Chat
# -----------------
# IMPORTANTE: /api/chat/global DEVE essere registrato PRIMA di /api/chat/{incident_id},
# altrimenti la route con path parameter cattura "global" come incident_id.
def _build_incident_catalog() -> str:
    """Catalogo compatto degli incidenti APPROVED per disambiguazione implicita.
    Una riga per incidente con id, data, tipo, gravità, attributi top e sintesi narrativa.
    Permette al chatbot di mappare riferimenti vaghi ("l'incidente sull'AGV", "quello di
    maggio") a uno o più INC-ID candidati senza dover chiedere esplicitamente all'utente."""
    try:
        approved = sm.list_incidents(status_filter="APPROVED")
    except Exception:
        return "(catalogo non disponibile)"
    if not approved:
        return "(nessun incidente approvato in archivio)"

    wiki = Path(WIKI_DIR)
    rows: List[str] = []
    for inc in approved:
        event_id = inc.get("event_id") or inc.get("incident_id")
        page = wiki / "eventi" / f"{event_id}.md"
        fm: Dict[str, Any] = {}
        ev: Dict[str, Any] = {}
        if page.exists():
            try:
                parts = page.read_text(encoding="utf-8", errors="ignore").split("---", 2)
                if len(parts) >= 3:
                    fm = yaml.safe_load(parts[1]) or {}
                    ont = fm.get("dati_ontologia") or {}
                    if isinstance(ont.get("Evento"), dict):
                        ev = ont["Evento"]
            except Exception:
                pass
        sintesi = (ev.get("Descrizione_Narrativa") or "").strip().replace("\n", " ")
        if len(sintesi) > 240:
            sintesi = sintesi[:240] + "…"
        rows.append(
            f"- **{event_id}** · {fm.get('data_incidente', 'N/D')} · "
            f"{ev.get('Tipo_Evento', 'N/D')} · gravità {fm.get('gravita', 'N/D')} · "
            f"linea {fm.get('linea', 'N/D')} · reparto {fm.get('reparto', 'N/D')} · "
            f"macchinario {fm.get('macchinario', 'N/D')}\n"
            f"  Sintesi: {sintesi or 'N/D'}"
        )
    return "\n".join(rows) if rows else "(nessun evento leggibile)"


@app.post("/api/chat/global")
def chat_global(req: ChatMessageRequest):
    if not _is_on_topic(req.message):
        raise HTTPException(status_code=422, detail={"reason": "off_topic"})

    wiki = Path(WIKI_DIR)
    wiki_parts: List[str] = []
    MAX_FILE_BYTES = 80 * 1024  # ignora file enormi
    if wiki.exists():
        for sub in ("eventi", "entita", "analisi", "fonti"):
            d = wiki / sub
            if not d.exists():
                continue
            for p in sorted(d.rglob("*.md")):
                try:
                    if p.stat().st_size > MAX_FILE_BYTES:
                        continue
                    body = p.read_text(encoding="utf-8", errors="ignore")
                    rel = p.relative_to(wiki).as_posix()
                    wiki_parts.append(f"### {rel}\n{body}")
                except Exception:
                    pass
    print(f"[chat/global] wiki pages caricate: {len(wiki_parts)}")

    catalog = _build_incident_catalog()

    # Pacchetto completo solo per INC-ID esplicitamente menzionati nella domanda.
    # Override esplicito: se l'operatore scrive l'ID, allega il JSON completo del report.
    incident_blocks: List[str] = []
    inc_ids = re.findall(r"INC-\d{8}-\d+", req.message)
    for iid in set(inc_ids):
        try:
            state = sm.get_state(iid)
            if state.get("status") == "APPROVED":
                result_data = sm.load_pipeline_result_data(iid) or {}
                draft = sm.load_draft_json(iid) or {}
                approved_json = state.get("chat_approved_json") or draft or result_data.get("riassuntivo_json") or {}
                pack = {
                    "incident_id": iid,
                    "stato": state.get("status"),
                    "operatore": state.get("operator"),
                    "approvato_il": state.get("approval_at"),
                    "report_approvato": approved_json,
                    "metriche_qualita": result_data.get("metriche_qualita"),
                    "trascrizioni": result_data.get("trascrizioni"),
                    "discordanze": result_data.get("discordanze"),
                    "nota_critica": result_data.get("nota_critica"),
                    "processing_summary": result_data.get("processing_summary"),
                }
                incident_blocks.append(
                    f"### PACCHETTO REPORT {iid}\n```json\n{json.dumps(pack, ensure_ascii=False, indent=2)[:20000]}\n```"
                )
        except FileNotFoundError:
            incident_blocks.append(f"### {iid}\n(non trovato nell'archivio)")

    wiki_context = "\n\n---\n\n".join(wiki_parts)[:300000]
    incidents_context = "\n\n".join(incident_blocks)

    system_prompt = (
        "Sei un perito di sicurezza industriale per Acqua Riva S.p.A.. "
        "Rispondi SOLO a domande sull'ambito (sicurezza, incidenti, macchinari, procedure, "
        "fornitori, normative, persone coinvolte, wiki aziendale).\n\n"
        "REGOLE DI INFERENZA INCIDENTE:\n"
        "1. L'operatore può riferirsi a un incidente IMPLICITAMENTE (es. \"l'AGV\", \"quello "
        "di maggio\", \"il near miss in logistica\", \"il guasto della linea 3\"). Usa il "
        "CATALOGO INCIDENTI per mappare il riferimento agli ID candidati.\n"
        "2. Se UN SOLO incidente del catalogo è chiaramente compatibile con la domanda, "
        "rispondi citando esplicitamente l'ID e usando i dati completi dal blocco WIKI.\n"
        "3. Se PIÙ incidenti sono compatibili o NESSUNO è chiaramente identificabile, NON "
        "tirare a indovinare: rispondi con una SOLA domanda di chiarimento all'operatore, "
        "elencando i candidati con l'ID e una sintesi di una riga ciascuno, e chiedendo "
        "quale vuole approfondire. Non rispondere alla domanda originale finché non hai "
        "disambiguato.\n"
        "4. Se la domanda è generale (statistiche, pattern, riepiloghi, confronti) e copre "
        "tutto l'archivio, rispondi aggregando senza chiedere chiarimenti.\n"
        "5. Cita sempre gli ID degli incidenti rilevanti nella risposta finale.\n\n"
        "FONTI DATI:\n"
        "- CATALOGO INCIDENTI: indice rapido per la disambiguazione.\n"
        "- WIKI: pagine markdown complete con frontmatter YAML (campo `dati_ontologia` "
        "contiene TUTTI i campi delle 22 entità ontologiche per ogni evento).\n"
        "- EVENTI MENZIONATI: pacchetto JSON completo dei soli incidenti citati esplicitamente "
        "per ID nella domanda. Quando presente, è la fonte autoritativa per quegli ID."
    )

    prompt = (
        f"{system_prompt}\n\n"
        f"=== CATALOGO INCIDENTI ===\n{catalog}\n\n"
        f"=== WIKI ===\n{wiki_context}\n\n"
        f"=== EVENTI MENZIONATI ===\n{incidents_context}\n\n"
        f"=== DOMANDA OPERATORE ===\n{req.message}"
    )
    print(f"[chat/global] prompt size: {len(prompt)} chars · catalogo: {catalog.count(chr(10) + '-') + 1} incidenti")
    try:
        response = _chat_generate(prompt)
        reply = response.text or "(risposta vuota dal modello)"
    except Exception as e:
        print(f"[chat/global] errore Gemini: {e!r}")
        reply = f"Errore nel contattare l'assistente AI: {str(e)}"

    return {"reply": reply}


@app.post("/api/chat/{incident_id}")
def chat_with_assistant(incident_id: str, req: ChatMessageRequest):
    if not _is_on_topic(req.message):
        raise HTTPException(status_code=422, detail={"reason": "off_topic"})

    history = sm.load_chat_history(incident_id)
    result_data = sm.load_pipeline_result_data(incident_id)
    draft_data = sm.load_draft_json(incident_id)
    context = json.dumps(
        draft_data or (result_data.get("riassuntivo_json") if result_data else {}),
        ensure_ascii=False
    )

    system_prompt = f"""Sei un assistente AI esperto in sicurezza industriale per Acqua Riva. \
Rispondi alle domande dell'operatore in modo conciso e diretto.

Dati incidente attuale (JSON):
{context}

REGOLE DI COMPORTAMENTO (IMPORTANTI):
1. Rispondi DIRETTAMENTE alla domanda dell'operatore, senza divagare.
2. NON proporre modifiche ai dati estratti a meno che l'operatore te lo chieda ESPLICITAMENTE \
con frasi come: "suggerisci correzioni", "cosa miglioreresti", "proponi modifiche", "correggi X", \
"cambia X in Y", "manca qualcosa?", "rivedi i dati".
3. Se l'operatore fa una domanda generica ("cosa pensi?", "spiega X", "riassumi", "raccontami"), \
NON proporre modifiche: limitati a rispondere alla domanda.
4. SOLO quando ti viene chiesto esplicitamente di proporre modifiche, includi ALLA FINE della \
risposta un blocco nel formato:
```proposed_changes
[{{"campo": "nome_campo", "valore_attuale": "...", "valore_proposto": "...", "motivazione": "..."}}]
```
5. Se anche dopo richiesta esplicita non hai modifiche valide da proporre, rispondi normalmente \
senza il blocco proposed_changes."""

    prompt = f"{system_prompt}\n\nOperatore: {req.message}"
    try:
        response = _chat_generate(prompt)
        raw_reply = response.text
    except Exception as e:
        raw_reply = f"Errore AI: {str(e)}"

    clean_reply, proposed_changes = _extract_proposed_changes(raw_reply)
    history.append({"role": "user", "content": req.message})
    history.append({"role": "assistant", "content": clean_reply})
    sm.save_chat_history(incident_id, history)

    return {"reply": clean_reply, "history": history, "proposed_changes": proposed_changes}

# -----------------
# Transcribe audio per chat (Perito + Globale)
# -----------------
@app.post("/api/transcribe")
async def transcribe_audio(audio: UploadFile = File(...)):
    """Trascrive un audio breve (max ~60s) via Gemini, ritorna testo plain.
    Usato da Perito d'evento e Chat globale per input vocale."""
    if not audio.content_type or not audio.content_type.startswith("audio/"):
        raise HTTPException(status_code=400, detail="File audio richiesto (content-type audio/*)")

    tmp_dir = INCIDENTS_DIR / "_tmp_chat"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(audio.filename or "rec.webm").suffix or ".webm"
    tmp_path = tmp_dir / f"{uuid.uuid4().hex}{suffix}"
    tmp_path.write_bytes(await audio.read())
    print(f"[transcribe] received mime={audio.content_type} name={audio.filename} size={tmp_path.stat().st_size}")

    pool = _ensure_pool()
    if pool is None:
        raise HTTPException(status_code=500, detail="Nessuna chiave Gemini configurata. Inseriscila in Impostazioni.")
    # Acquisiamo UN client e lo teniamo per upload + generate (il file uploadato è legato alla chiave)
    pool_idx, client = pool.acquire()
    quota_hit = False
    try:
        # Determina il MIME effettivo da passare a Gemini.
        # Gemini Files API accetta: audio/wav, audio/mp3, audio/aiff, audio/aac, audio/ogg, audio/flac.
        # NON accetta direttamente audio/webm;codecs=opus.
        # Per webm/opus dal browser, convertiamo a wav con pydub (richiede ffmpeg).
        upload_path = tmp_path
        ct = (audio.content_type or "").lower()
        is_webm = "webm" in ct or tmp_path.suffix.lower() == ".webm"
        if is_webm:
            try:
                from pydub import AudioSegment
                wav_path = tmp_path.with_suffix(".wav")
                seg = AudioSegment.from_file(str(tmp_path))
                seg.export(str(wav_path), format="wav")
                upload_path = wav_path
                print(f"[transcribe] webm convertito in wav: {wav_path.stat().st_size} bytes")
            except Exception as conv_err:
                print(f"[transcribe] conversione webm->wav fallita ({conv_err!r}), provo upload diretto")

        try:
            fc = client.files.upload(file=str(upload_path))
        except Exception as up_err:
            quota_hit = _is_quota_error(up_err)
            print(f"[transcribe] upload Gemini fallito: {up_err!r}")
            raise HTTPException(status_code=500, detail=f"Upload Gemini fallito: {up_err}")

        # Attesa che il file sia ACTIVE
        while fc.state.name == "PROCESSING":
            time.sleep(0.5)
            fc = client.files.get(name=fc.name)
        if fc.state.name != "ACTIVE":
            raise HTTPException(status_code=500, detail=f"Upload audio fallito: stato {fc.state.name}")
        resp = client.models.generate_content(
            model=_get_model_chat(),
            contents=["Trascrivi fedelmente l'audio in italiano, parola per parola. Restituisci SOLO il testo trascritto, senza commenti o introduzioni.", fc],
        )
        text = (resp.text or "").strip()
        # Retry con prompt piu' esplicito se la prima trascrizione e' vuota
        if not text:
            print(f"[transcribe] prima trascrizione vuota, retry con prompt esplicito")
            resp2 = client.models.generate_content(
                model=_get_model_chat(),
                contents=[
                    "L'audio contiene parlato in lingua italiana. Trascrivi tutto cio' che senti, "
                    "anche parole singole o frasi brevi. Restituisci SOLO il testo trascritto. "
                    "Se l'audio e' silenzioso o non contiene parlato umano, rispondi esattamente con: VUOTO",
                    fc,
                ],
            )
            text2 = (resp2.text or "").strip()
            if text2 and text2.upper() != "VUOTO":
                text = text2
        print(f"[transcribe] result_len={len(text)} (pool key #{pool_idx})")
        return {"text": text}
    except HTTPException:
        raise
    except Exception as e:
        quota_hit = quota_hit or _is_quota_error(e)
        import traceback
        tb = traceback.format_exc()
        print(f"[transcribe] errore: {e!r}\n{tb}")
        raise HTTPException(status_code=500, detail=f"Trascrizione fallita: {e}")
    finally:
        # Rilascia la chiave del pool (cooldown se 429 è stato rilevato)
        try:
            pool.release(pool_idx, mark_quota_hit=quota_hit)
        except Exception:
            pass
        try:
            tmp_path.unlink(missing_ok=True)
            wav_candidate = tmp_path.with_suffix(".wav")
            if wav_candidate != tmp_path and wav_candidate.exists():
                wav_candidate.unlink(missing_ok=True)
        except Exception:
            pass

# -----------------
# Settings & Stats
# -----------------
@app.get("/api/settings")
def get_settings_endpoint():
    return _load_settings()

@app.post("/api/settings")
def save_settings_endpoint(req: SettingsRequest):
    current = _load_settings()
    data = {k: v for k, v in req.dict().items() if v is not None}
    # Normalizza: api_key legacy -> api_keys lista
    if "api_key" in data and "api_keys" not in data:
        ak = (data.pop("api_key") or "").strip()
        data["api_keys"] = [ak] if ak else []
    if "api_keys" in data and isinstance(data["api_keys"], list):
        data["api_keys"] = [str(k).strip() for k in data["api_keys"] if str(k).strip()]
        if len(data["api_keys"]) > MAX_API_KEYS:
            raise HTTPException(
                status_code=422,
                detail=f"Massimo {MAX_API_KEYS} chiavi consentite (ne sono state inviate {len(data['api_keys'])}).",
            )
    for k in ("ontology_path", "schema_path"):
        data.pop(k, None)
    if "model" in data and data["model"] not in SUPPORTED_GEMINI_MODELS:
        raise HTTPException(
            status_code=422,
            detail=f"Modello non supportato. Ammessi: {list(SUPPORTED_GEMINI_MODELS)}",
        )
    current.update(data)
    current.pop("api_key", None)  # garantisce eliminazione dell'alias legacy dal file
    try:
        SETTINGS_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
        n_keys = len(current.get("api_keys", []))
        print(f"[settings] salvate in {SETTINGS_PATH} · keys={list(data.keys())} · n_api_keys={n_keys}")
    except Exception as e:
        print(f"[settings] errore scrittura {SETTINGS_PATH}: {e!r}")
        raise HTTPException(status_code=500, detail=f"Impossibile scrivere {SETTINGS_PATH}: {e}")
    # Ricarica/ricrea il pool con le nuove chiavi
    try:
        _ensure_pool()
    except Exception as e:
        print(f"[settings] ensure_pool dopo save: {e!r}")
    return {"status": "ok", "path": str(SETTINGS_PATH), "n_keys": len(current.get("api_keys", []))}

@app.get("/api/health")
def health():
    """Health check leggero per il polling del frontend."""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

@app.get("/api/pool/status")
def pool_status():
    """Stato del pool Gemini: chiavi totali, attive, in cooldown."""
    pool = _ensure_pool()
    if pool is None:
        return {"total": 0, "available": 0, "in_cooldown": 0, "keys": [],
                "configured": False, "model": _get_model()}
    s = pool.status()
    s["configured"] = True
    s["model"] = _get_model()
    return s

@app.get("/api/stats")
def get_stats():
    incidents = sm.list_incidents()
    approved = sum(1 for i in incidents if i["status"] == "APPROVED")
    wiki = Path(WIKI_DIR)
    wiki_pages = 0
    for subdir in ["eventi", "entita", "analisi", "fonti"]:
        sub = wiki / subdir
        if sub.exists():
            wiki_pages += sum(1 for _ in sub.rglob("*.md"))
    return {"total_incidents": len(incidents), "approved": approved, "wiki_pages": wiki_pages}

@app.post("/api/settings/reset_wiki")
def reset_wiki():
    """Azzeramento integrale del vault: elimina TUTTO ciò che non è strutturale,
    ricrea le sottocartelle vuote, ripristina index.md e log.md. Preserva solo
    WIKI_SCHEMA.md (documentazione ontologica per il LLM)."""
    wiki = Path(WIKI_DIR)
    if not wiki.exists():
        wiki.mkdir(parents=True, exist_ok=True)

    # Preserva: schema ontologico + configurazione Obsidian (graph.json con colorGroups,
    # workspace, appearance, ecc.). Senza preservarli, l'utente perderebbe palette grafo e layout.
    KEEP_FILES = {"WIKI_SCHEMA.md"}
    KEEP_DIRS  = {".obsidian"}
    SUBDIRS = (
        "eventi",
        "entita/incidenti", "entita/macchinari", "entita/reparti",
        "entita/agenti_causali", "entita/linee", "entita/persone",
        "entita/prodotti", "entita/conseguenze", "entita/fornitori",
        "analisi", "archivio",
    )

    # 1. Cancella tutto fuorché i file/cartelle da preservare
    deleted = []
    for item in wiki.iterdir():
        if item.name in KEEP_FILES or item.name in KEEP_DIRS:
            continue
        try:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
            deleted.append(item.name)
        except Exception as e:
            print(f"[reset_wiki] impossibile rimuovere {item}: {e!r}")

    # 2. Ricrea struttura entità + cartelle dati
    for subdir in SUBDIRS:
        (wiki / subdir).mkdir(parents=True, exist_ok=True)

    # 3. Index pulito
    today = date.today().isoformat()
    template = f"""---
tipo: index
ultimo_aggiornamento: {today}
---
# Knowledge Base — Acqua Riva S.p.A.

## Statistiche
- **Incidenti approvati**: 0
- **Ultimo aggiornamento**: {today}

## Incidenti
| ID Evento | Data | Tipo | Stabilimento | Testimonianze |
|---|---|---|---|---|

## Entità
- [[entita/macchinari/]] Macchinari
- [[entita/reparti/]] Reparti
- [[entita/linee/]] Linee
- [[entita/persone/]] Persone
- [[entita/prodotti/]] Prodotti
- [[entita/conseguenze/]] Conseguenze
- [[entita/agenti_causali/]] Agenti Causali
- [[entita/fornitori/]] Fornitori
"""
    (wiki / "index.md").write_text(template, encoding="utf-8")
    (wiki / "log.md").write_text("# Log operazioni\n", encoding="utf-8")

    # Garantisce colorGroups Obsidian (idempotente, preserva customizzazioni utente)
    try:
        from wiki_updater import _ensure_obsidian_graph_colors
        _ensure_obsidian_graph_colors(wiki)
    except Exception as e:
        print(f"[reset_wiki] avviso: non sono riuscito ad aggiornare graph.json: {e!r}")

    print(f"[reset_wiki] rimossi {len(deleted)} elementi: {deleted}")
    return {"status": "ok", "removed": deleted}

