"""
Configurazione centralizzata per il sistema.
Legge da variabili d'ambiente (file .env) se disponibili.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR      = Path(__file__).parent
INCIDENTS_DIR = BASE_DIR / "incidents"
WIKI_DIR      = BASE_DIR / "LLM sicurezza"

# Legge API key da .env o dalla config di analisi_testimonianze
try:
    from analisi_testimonianze import API_KEY as _AT_KEY
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", _AT_KEY)
except Exception:
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

GEMINI_MODEL_ANALYSIS = "gemini-3.5-flash"
GEMINI_MODEL_WIKI     = "gemini-3.5-flash"
GEMINI_MODEL_CHAT     = "gemini-3.5-flash"

SUPPORTED_GEMINI_MODELS = ("gemini-2.5-flash", "gemini-3.5-flash")

# Percorsi file ontologia e schema
ONTOLOGIA_PATH = str(BASE_DIR / "Nuova_Ontologia_Tabellare.xlsx")
SCHEMA_PATH    = str(BASE_DIR / "Nuovo_schema_Report.xlsx")

# Filtro domande chat — perito di sicurezza industriale Acqua Riva
# IMPORTANTE: in caso di dubbio, classifica come ON-TOPIC (preferisci falsi positivi a falsi negativi).
CHAT_DOMAIN_SYSTEM_PROMPT = """Sei un classificatore binario per un assistente aziendale di sicurezza industriale.

Classifica la domanda come ON-TOPIC se riguarda anche solo MARGINALMENTE uno qualsiasi dei seguenti:
- sicurezza sul lavoro, infortuni, near-miss, DPI, ergonomia
- normative (HSE, ISO 45001, D.Lgs. 81/2008, RSPP, INAIL)
- macchinari industriali, linee produttive, manutenzione, guasti, componenti
- procedure operative, checklist, manuali tecnici, specifiche
- azienda Acqua Riva S.p.A., suoi prodotti (acqua, bevande), reparti, persone, fornitori
- eventi/incidenti archiviati, ID INC-*, testimonianze, report
- revisione, modifica, correzione di report, dati estratti, metriche
- generiche richieste di aiuto, saluti, follow-up su risposte precedenti

Classifica come OFF-TOPIC SOLO se la domanda è chiaramente fuori contesto (es. ricette di cucina,
risultati sportivi, codice di programmazione generico, gossip, intrattenimento, conversioni di valuta).

In caso di dubbio: ON-TOPIC.

Rispondi SOLO con la parola: OK (on-topic) oppure NO (off-topic)."""
