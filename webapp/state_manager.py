"""
StateManager — gestisce lo stato degli incidenti su file system.
Ogni incidente vive in incidents/INC-YYYYMMDD-NNN/ con incident_state.json.
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

try:
    from filelock import FileLock
    _HAS_FILELOCK = True
except ImportError:
    _HAS_FILELOCK = False

STATI_VALIDI = ("DRAFT", "PROCESSING", "PENDING", "APPROVED", "REJECTED", "ERROR")


class StateManager:
    def __init__(self, incidents_root: str | Path):
        self.root = Path(incidents_root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Creazione incidente
    # ------------------------------------------------------------------

    def create_incident(self, input_files: list[str | Path], base_dir: str | Path) -> str:
        """
        Crea la cartella incidente, copia i file di input, ritorna incident_id.
        """
        date_str = datetime.now().strftime("%Y%m%d")
        idx = self._next_index(date_str)
        incident_id = f"INC-{date_str}-{idx:03d}"

        inc_dir = self.root / incident_id
        input_dir = inc_dir / "input"
        input_dir.mkdir(parents=True, exist_ok=True)

        copied = []
        for f in input_files:
            f = Path(f)
            if f.exists():
                dest = input_dir / f.name
                shutil.copy2(f, dest)
                copied.append(f.name)

        state = {
            "incident_id": incident_id,
            "status": "DRAFT",
            "event_id": None,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "input_files": copied,
            "base_dir": str(base_dir),
            "operator": None,
            "approval_at": None,
            "rejection_reason": None,
            "rejection_at": None,
            "wiki_update_report": None,
            "modifications": [],
            "discordance_resolutions": {},
            "chat_approved_json": None,
        }
        self._write_state(incident_id, state)
        return incident_id

    # ------------------------------------------------------------------
    # Lettura stato
    # ------------------------------------------------------------------

    def get_state(self, incident_id: str) -> dict:
        state_path = self.root / incident_id / "incident_state.json"
        if not state_path.exists():
            raise FileNotFoundError(f"Stato non trovato: {state_path}")
        return json.loads(state_path.read_text(encoding="utf-8"))

    def list_incidents(self, status_filter: str = None) -> list[dict]:
        result = []
        for p in sorted(self.root.iterdir()):
            if not p.is_dir() or not p.name.startswith("INC-"):
                continue
            try:
                state = self.get_state(p.name)
                if status_filter is None or state["status"] == status_filter:
                    result.append(state)
            except Exception:
                continue
        return sorted(result, key=lambda s: s.get("updated_at", ""), reverse=True)

    # ------------------------------------------------------------------
    # Aggiornamento stato
    # ------------------------------------------------------------------

    def update_status(self, incident_id: str, new_status: str, **kwargs):
        if new_status not in STATI_VALIDI:
            raise ValueError(f"Stato non valido: {new_status}")
        state = self.get_state(incident_id)
        state["status"] = new_status
        state["updated_at"] = datetime.now().isoformat()
        for k, v in kwargs.items():
            state[k] = v
        self._write_state(incident_id, state)

    def save_pipeline_result(self, incident_id: str, result, output_dir: str):
        """Salva il riferimento ai risultati della pipeline nello stato."""
        state = self.get_state(incident_id)
        state["pipeline_output_dir"] = output_dir
        state["report_ind_paths"] = result.report_ind_paths
        state["report_rias_path"] = result.report_rias_path
        state["updated_at"] = datetime.now().isoformat()

        # Salva il PipelineResult come JSON (esclude i workbook)
        result_path = self.root / incident_id / "pipeline_result.json"
        result_data = {
            "risultati": {
                k: {
                    "consensus": v["consensus"],
                    "metriche": v["metriche"],
                    "run_scelto": v.get("run_scelto"),
                }
                for k, v in result.risultati.items()
            },
            "discordanze": result.discordanze,
            "metriche_qualita": result.metriche_qualita,
            "trascrizioni": result.trascrizioni,
            "dati_prosodici": result.dati_prosodici,
            "nota_critica": result.nota_critica,
            "trascrizioni_testo": result.trascrizioni_testo,
            "report_ind_paths": result.report_ind_paths,
            "report_rias_path": result.report_rias_path,
            "riassuntivo_json": result.riassuntivo_json,
            "processing_summary": getattr(result, "processing_summary", {}) or {},
        }
        result_path.write_text(json.dumps(result_data, ensure_ascii=False, indent=2),
                               encoding="utf-8")

        # Persist processing log alongside pipeline_result.json
        log_text = getattr(result, "processing_log", "") or ""
        if log_text:
            log_path = self.root / incident_id / "processing_log.txt"
            log_path.write_text(log_text, encoding="utf-8")

        self._write_state(incident_id, state)

    def get_processing_log_path(self, incident_id: str) -> Path:
        return self.root / incident_id / "processing_log.txt"

    def get_valida_run_dir(self, incident_id: str) -> Path:
        return self.root / incident_id / "valida_run"

    def load_pipeline_result_data(self, incident_id: str) -> dict | None:
        result_path = self.root / incident_id / "pipeline_result.json"
        if not result_path.exists():
            return None
        return json.loads(result_path.read_text(encoding="utf-8"))

    def save_draft_json(self, incident_id: str, approved_json: dict):
        """Salva la bozza corrente del JSON consenso (modificato dall'operatore)."""
        draft_path = self.root / incident_id / "draft_data.json"
        draft_path.write_text(json.dumps(approved_json, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        state = self.get_state(incident_id)
        state["updated_at"] = datetime.now().isoformat()
        self._write_state(incident_id, state)

    def load_draft_json(self, incident_id: str) -> dict | None:
        draft_path = self.root / incident_id / "draft_data.json"
        if not draft_path.exists():
            return None
        return json.loads(draft_path.read_text(encoding="utf-8"))

    def save_witness_drafts(self, incident_id: str, drafts: dict):
        """Salva i draft modificati per ogni testimone. drafts: {filename: consensus_dict}"""
        path = self.root / incident_id / "witness_drafts.json"
        path.write_text(json.dumps(drafts, ensure_ascii=False, indent=2), encoding="utf-8")
        state = self.get_state(incident_id)
        state["updated_at"] = datetime.now().isoformat()
        self._write_state(incident_id, state)

    def load_witness_drafts(self, incident_id: str) -> dict | None:
        path = self.root / incident_id / "witness_drafts.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def record_modification(self, incident_id: str, field: str, old_value: str,
                             new_value: str, source: str, justification: str = ""):
        """Registra una modifica campo nell'audit trail."""
        state = self.get_state(incident_id)
        state["modifications"].append({
            "timestamp": datetime.now().isoformat(),
            "field": field,
            "old_value": old_value,
            "new_value": new_value,
            "source": source,
            "justification": justification,
        })
        state["updated_at"] = datetime.now().isoformat()
        self._write_state(incident_id, state)

    def save_chat_history(self, incident_id: str, messages: list):
        chat_path = self.root / incident_id / "chat_history.json"
        chat_path.write_text(json.dumps(messages, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    def load_chat_history(self, incident_id: str) -> list:
        chat_path = self.root / incident_id / "chat_history.json"
        if not chat_path.exists():
            return []
        return json.loads(chat_path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Utilità
    # ------------------------------------------------------------------

    def get_incident_dir(self, incident_id: str) -> Path:
        return self.root / incident_id

    def get_input_dir(self, incident_id: str) -> Path:
        return self.root / incident_id / "input"

    # ------------------------------------------------------------------
    # Privato
    # ------------------------------------------------------------------

    def _next_index(self, date_str: str) -> int:
        existing = [
            p.name for p in self.root.iterdir()
            if p.is_dir() and p.name.startswith(f"INC-{date_str}-")
        ]
        return len(existing) + 1

    def _write_state(self, incident_id: str, state: dict):
        state_path = self.root / incident_id / "incident_state.json"
        lock_path  = self.root / incident_id / ".state.lock"

        if _HAS_FILELOCK:
            with FileLock(str(lock_path), timeout=10):
                state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
        else:
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
