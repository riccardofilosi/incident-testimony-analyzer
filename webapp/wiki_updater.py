"""
WikiUpdater — aggiorna la wiki markdown dopo l'approvazione di un incidente.
Una chiamata LLM per file wiki. Output: solo il markdown aggiornato.
"""

import json
import yaml
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types


@dataclass
class WikiOp:
    operation: str
    path: str
    status: str
    note: str = ""


@dataclass
class WikiUpdateReport:
    event_id: str
    operations: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def summary(self) -> str:
        ok  = [o for o in self.operations if o.status == "OK"]
        err = [o for o in self.operations if o.status != "OK"]
        return (f"Wiki aggiornata: {len(ok)} operazioni OK, {len(err)} errori. "
                f"Evento: {self.event_id}")


# Palette per il grafo Obsidian: 1 colore per tipo di entità.
# Formato Obsidian: { a: 1, rgb: (R<<16) | (G<<8) | B } (standard RGB decimal).
_GRAPH_PALETTE = [
    ("path:eventi/",              "#b8341c"),  # vermiglio · evento
    ("path:entita/incidenti/",    "#7a1f0a"),  # oxblood · incidente·dati
    ("path:entita/macchinari/",   "#5c6b78"),  # grigio acciaio
    ("path:entita/linee/",        "#2a7a8c"),  # ciano
    ("path:entita/reparti/",      "#8a6a1f"),  # ocra
    ("path:entita/persone/",      "#5a8a5f"),  # verde oliva
    ("path:entita/fornitori/",    "#6b3f7a"),  # viola
    ("path:entita/prodotti/",     "#3d8a8a"),  # turchese
    ("path:entita/conseguenze/",  "#c97a3d"),  # arancione
    ("path:entita/agenti_causali/", "#c9a554"),  # giallo intenso
    ("path:analisi/",             "#998f7a"),  # grigio chiaro
]

def _hex_to_obsidian_rgb(h: str) -> int:
    h = h.lstrip("#")
    r = int(h[0:2], 16); g = int(h[2:4], 16); b = int(h[4:6], 16)
    return (r << 16) | (g << 8) | b


def _ensure_obsidian_graph_colors(wiki_root: Path) -> None:
    """Assicura che .obsidian/graph.json contenga la palette colorGroups.
    Preserva tutti gli altri setting Obsidian dell'utente. Idempotente."""
    obs_dir = wiki_root / ".obsidian"
    graph_file = obs_dir / "graph.json"
    try:
        obs_dir.mkdir(parents=True, exist_ok=True)
        if graph_file.exists():
            data = json.loads(graph_file.read_text(encoding="utf-8"))
        else:
            data = {
                "collapse-filter": True, "search": "", "showTags": False,
                "showAttachments": False, "hideUnresolved": False, "showOrphans": True,
                "collapse-color-groups": False, "collapse-display": True,
                "showArrow": False, "textFadeMultiplier": 0,
                "nodeSizeMultiplier": 1, "lineSizeMultiplier": 1,
                "collapse-forces": True, "centerStrength": 0.518,
                "repelStrength": 10, "linkStrength": 1, "linkDistance": 250,
                "scale": 1, "close": False,
            }
        wanted = [
            {"query": q, "color": {"a": 1, "rgb": _hex_to_obsidian_rgb(c)}}
            for q, c in _GRAPH_PALETTE
        ]
        existing = data.get("colorGroups") or []
        wanted_queries = {q for q, _ in _GRAPH_PALETTE}
        # Conserva gruppi custom (query non standard) ma RISCRIVI SEMPRE i colori standard
        # per assicurare formato RGB corretto (in passato BGR scambiato).
        custom = [g for g in existing
                  if isinstance(g, dict) and g.get("query") not in wanted_queries]
        new_groups = wanted + custom
        if new_groups != existing:
            data["colorGroups"] = new_groups
            data["collapse-color-groups"] = False
            graph_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[graph-colors] impossibile aggiornare {graph_file}: {e!r}")


class WikiUpdater:
    def __init__(self, wiki_root: str | Path, api_key: str,
                 model: str = "gemini-2.5-flash"):
        self.wiki_root = Path(wiki_root)
        self.wiki_root.mkdir(parents=True, exist_ok=True)
        for subdir in ("eventi", "entita/incidenti", "entita/macchinari", "entita/reparti",
                       "entita/agenti_causali", "entita/linee", "entita/persone",
                       "entita/prodotti", "entita/conseguenze", "entita/fornitori",
                       "analisi", "archivio"):
            (self.wiki_root / subdir).mkdir(parents=True, exist_ok=True)
        # Garantisce sempre colorGroups Obsidian aggiornati (idempotente)
        _ensure_obsidian_graph_colors(self.wiki_root)
        # Pool-aware: se il pool globale è inizializzato (vedi client_pool.init_pool),
        # _call_llm lo usa. self.client è il fallback per back-compat e quando il pool non c'è.
        self.client = genai.Client(api_key=api_key) if api_key else None
        self.model  = model
        schema_path = self.wiki_root / "WIKI_SCHEMA.md"
        self.schema = schema_path.read_text(encoding="utf-8") if schema_path.exists() else ""

    # ------------------------------------------------------------------
    # Entry point principale
    # ------------------------------------------------------------------

    def run_approval_update(self, event_id: str, result_data: dict,
                             approved_json: dict, approval_metadata: dict,
                             source_dir: str | Path) -> WikiUpdateReport:
        """
        Orchestrates all wiki updates for one approved incident.
        result_data: dict loaded from pipeline_result.json
        approved_json: final consensus JSON approved by operator
        approval_metadata: {operator, approval_at, notes, discordance_resolutions}
        source_dir: path to incident dir (for archiving report files)
        """
        ops = []
        report = WikiUpdateReport(event_id=event_id)
        import time as _t
        _step_t0 = _t.time()
        def _step_log(name: str):
            nonlocal _step_t0
            dt = _t.time() - _step_t0
            print(f"  [wiki] step '{name}' completato in {dt:.1f}s", flush=True)
            _step_t0 = _t.time()

        try:
            # 1. Archive documents
            print(f"  [wiki] step 1/7: archivio documenti...", flush=True)
            ops.append(self._archive_documents(event_id, source_dir, result_data))
            _step_log("archivio")

            # 2. Create event page (LLM)
            print(f"  [wiki] step 2/7: pagina evento (LLM)...", flush=True)
            ops.append(self._create_event_page(event_id, result_data, approved_json,
                                                approval_metadata))
            _step_log("pagina evento")

            # 3. Update entity pages (LLM, una per entità)
            print(f"  [wiki] step 3/7: pagine entità (LLM, multipli)...", flush=True)
            ops.extend(self._update_entity_pages(event_id, approved_json))
            _step_log("pagine entità")

            # 4. Update pattern analysis (LLM)
            print(f"  [wiki] step 4/7: analisi pattern (LLM)...", flush=True)
            ops.append(self._update_pattern_analysis())
            _step_log("pattern")

            # 5. Update index (pure Python, no LLM)
            print(f"  [wiki] step 5/7: indice...", flush=True)
            ops.append(self._update_index(event_id, approved_json, approval_metadata))
            _step_log("indice")

            # 5b. Update entita/incidenti — categoria entità per gli incidenti
            print(f"  [wiki] step 6/7: entità incidente...", flush=True)
            ops.append(self._upsert_incidente_entity(event_id, approved_json, approval_metadata))
            _step_log("entità incidente")

            # 6. Append log (pure Python, no LLM)
            print(f"  [wiki] step 7/7: log...", flush=True)
            ops.append(self._append_log(event_id, ops))
            _step_log("log")

        except Exception as e:
            print(f"  [wiki] ERRORE: {e!r}", flush=True)
            report.errors.append(str(e))

        report.operations = ops
        return report

    # ------------------------------------------------------------------
    # Step 1: archive documents
    # ------------------------------------------------------------------

    def _archive_documents(self, event_id: str, source_dir: str | Path,
                            result_data: dict) -> WikiOp:
        source_dir = Path(source_dir)
        archive_dir = self.wiki_root / "archivio" / event_id
        archive_dir.mkdir(parents=True, exist_ok=True)

        copied = []
        # Copy Excel reports
        output_dir = Path(result_data.get("report_ind_paths", {}).get(
            next(iter(result_data.get("report_ind_paths", {})), ""), ""))
        if output_dir.parent.exists():
            for f in output_dir.parent.glob("*.xlsx"):
                shutil.copy2(f, archive_dir / f.name); copied.append(f.name)
            for f in output_dir.parent.glob("*.txt"):
                shutil.copy2(f, archive_dir / f.name); copied.append(f.name)
        else:
            # fallback: look in source_dir parent
            for f in source_dir.glob("*.xlsx"):
                shutil.copy2(f, archive_dir / f.name); copied.append(f.name)
            for f in source_dir.glob("*.txt"):
                shutil.copy2(f, archive_dir / f.name); copied.append(f.name)

        # Write metadata
        meta = {"event_id": event_id, "archived_at": datetime.now().isoformat(),
                 "files": copied}
        (archive_dir / "metadata.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        return WikiOp("ARCHIVE", str(archive_dir), "OK",
                      f"{len(copied)} files archived")

    # ------------------------------------------------------------------
    # Step 2: create event page
    # ------------------------------------------------------------------

    def _create_event_page(self, event_id: str, result_data: dict,
                            approved_json: dict, approval_metadata: dict) -> WikiOp:
        """Pagina evento narrativa.

        Strategia: il LLM scrive SOLO le sezioni narrative (Sintesi · Dinamica · Note critiche).
        Frontmatter, intestazione, link a entità e link al report tabellare deterministico
        sono generati in Python — coerenti, sempre completi, mai troncati.
        Le tabelle dei campi vivono in `entita/incidenti/{event_id}-DATI.md`.
        """
        page_path = self.wiki_root / "eventi" / f"{event_id}.md"
        page_path.parent.mkdir(parents=True, exist_ok=True)

        testimonianze = list(result_data.get("trascrizioni", {}).keys())
        discordanze   = result_data.get("discordanze", [])
        nota_critica  = result_data.get("nota_critica", "") or ""

        # ── Estrazione attributi chiave per frontmatter (Python deterministico) ───
        ev = approved_json.get("Evento") if isinstance(approved_json.get("Evento"), dict) else {}
        rep_d = approved_json.get("Reparto") if isinstance(approved_json.get("Reparto"), dict) else {}
        lin_d = approved_json.get("Linea") if isinstance(approved_json.get("Linea"), dict) else {}
        mac_d = approved_json.get("Macchinario") if isinstance(approved_json.get("Macchinario"), dict) else {}
        prod_d = (approved_json.get("Contesto Operativo") or {}) if isinstance(approved_json.get("Contesto Operativo"), dict) else {}

        data_inc = ev.get("Data", "N/D")
        tipo_inc = ev.get("Tipo_Evento", "incidente")
        gravita  = ev.get("Gravità") or ev.get("Gravita") or "N/D"
        linea    = lin_d.get("Nome_Linea") or lin_d.get("Nome") or "N/D"
        reparto  = rep_d.get("Nome_Area") or rep_d.get("Nome") or "N/D"
        macchin  = mac_d.get("Modello") or mac_d.get("Nome") or "N/D"
        prodotto = prod_d.get("Tipo_Prodotto") or prod_d.get("Prodotto") or "N/D"

        operatore = approval_metadata.get("operator", "N/D")
        data_appr = approval_metadata.get("approval_at", datetime.now().isoformat())

        # Tags da whitelist (slugify lowercase, niente spazi)
        def _slug_tag(s):
            return re.sub(r"[^a-z0-9_]+", "_", str(s).lower().strip()).strip("_")
        tag_set = [
            "incidente",
            f"tipo_{_slug_tag(tipo_inc)}",
            f"gravita_{_slug_tag(gravita)}",
        ]
        if linea != "N/D":   tag_set.append(f"linea_{_slug_tag(linea)}")
        if reparto != "N/D": tag_set.append(f"reparto_{_slug_tag(reparto)}")
        tag_set = sorted(set(t for t in tag_set if t and t.endswith("_n_d") is False))

        # ── Prompt narrativo (LLM) — output corto, nessuna tabella ────────────────
        contesto = {
            "Evento": ev,
            "Reparto": rep_d, "Linea": lin_d, "Macchinario": mac_d,
            "Componente": approved_json.get("Componente"),
            "Causa Tecnica": approved_json.get("Causa Tecnica"),
            "Persona": approved_json.get("Persona"),
            "Conseguenza Immediata": approved_json.get("Conseguenza Immediata"),
            "Fattore Umano": approved_json.get("Fattore Umano"),
            "Fattore Ambientale": approved_json.get("Fattore Ambientale"),
        }

        prompt = f"""Sei un perito di sicurezza industriale per Acqua Riva S.p.A.

Compito: scrivi le SOLE sezioni narrative della pagina dell'evento {event_id}.
NON includere tabelle, NON includere frontmatter, NON includere link Obsidian: quelli li
aggiunge il sistema. Scrivi SOLO il corpo testuale di 3 sezioni.

Dati riassuntivi (consenso approvato):
{json.dumps(contesto, ensure_ascii=False, indent=2)[:80000]}

Discordanze tra testimoni (rilevanti per la narrazione):
{json.dumps(discordanze, ensure_ascii=False, indent=2)[:30000]}

Nota critica dalla pipeline:
{nota_critica[:8000]}

Output ATTESO (in italiano, tono tecnico e impersonale):

## Sintesi

[3-5 frasi: cosa è successo, dove, chi è coinvolto, gravità.]

## Dinamica

[1-2 paragrafi: sequenza cronologica dell'evento, condizioni operative pre-evento,
modalità di accadimento, ruolo dei testimoni nella dinamica.
Se ci sono discordanze rilevanti tra testimoni, menzionale qui in modo critico.]

## Causa e Conseguenze

[1-2 paragrafi: causa tecnica principale, fattori contributivi (umano/ambientale),
componente che ha ceduto, danni e impatti immediati.
Cita esplicitamente eventuali lotti/fornitori se rilevanti per la causa.]

Regole:
- SCRIVI N/D solo quando il dato manca davvero.
- NON inventare nomi, lotti, date.
- NON creare tabelle markdown.
- NON includere link [[...]].
- NON includere frontmatter YAML.
- Massimo 600 parole totali.
"""
        narrative = self._call_llm(prompt).strip()

        # ── Estrazione entità linkabili (per sezione "Entità Collegate") ──────────
        entity_links = []
        for entity_type, entity_name in self._extract_entities(approved_json):
            slug = self._slug(entity_name)
            entity_links.append(f"- [[entita/{entity_type}/{slug}|{entity_name}]] ({entity_type})")

        # ── Composizione finale deterministica ────────────────────────────────────
        # Raggruppa entità estratte in YAML arrays con i link di Obsidian
        yaml_entities = {}
        for etype, ename in self._extract_entities(approved_json):
            slug = self._slug(ename)
            key = f"collegamenti_{etype}"
            if key not in yaml_entities:
                yaml_entities[key] = []
            yaml_entities[key].append(f"[[entita/{etype}/{slug}|{ename}]]")
        
        # Pulisci e formatta il JSON completo
        def _clean_dict(d):
            if isinstance(d, dict):
                return {k: _clean_dict(v) for k, v in d.items() if v and v != "N/D"}
            if isinstance(d, list):
                return [_clean_dict(x) for x in d if x and x != "N/D"]
            return d
        
        cleaned_json = _clean_dict(approved_json)
        
        # Crea dizionario frontmatter completo
        frontmatter_dict = {
            "id": event_id,
            "tipo": "evento",
            "data_incidente": data_inc,
            "data_approvazione": data_appr,
            "operatore": operatore,
            "status": "APPROVED",
            "gravita": gravita,
            "linea": linea,
            "reparto": reparto,
            "macchinario": macchin,
            "prodotto": prodotto,
            "testimoni": testimonianze,
            "tags": tag_set or ["incidente"]
        }
        # Merge collegamenti e dati grezzi dell'ontologia
        frontmatter_dict.update(yaml_entities)
        frontmatter_dict["dati_ontologia"] = cleaned_json
        
        frontmatter_yaml = yaml.dump(frontmatter_dict, allow_unicode=True, default_flow_style=False, sort_keys=False)
        
        content = f"""---
{frontmatter_yaml.strip()}
---

# {event_id}

> 📂 **Archivio file Excel**: [[archivio/{event_id}/metadata|metadata archivio]]

{narrative}

## Entità Collegate

{chr(10).join(entity_links) if entity_links else "_Nessuna entità estratta._"}

## Riferimenti

- Archivio: [[archivio/{event_id}/metadata]]
- Numero testimonianze: {len(testimonianze)}
- Numero discordanze: {len(discordanze)}
"""
        page_path.write_text(content, encoding="utf-8")
        return WikiOp("CREA_PAGINA_EVENTO", str(page_path), "OK",
                      f"narrativa + full YAML frontmatter + {len(entity_links)} link entità")

    # ------------------------------------------------------------------
    # Step 3: update entity pages
    # ------------------------------------------------------------------

    def _extract_attrs_for_entity(self, entity_type: str, entity_name: str,
                                    approved_json: dict) -> dict:
        """Ritorna gli attributi specifici dell'entità dal JSON consenso.
        Per entità lista (persone, fornitori), cerca per Nome."""
        def _match_by_name(items, name):
            if isinstance(items, dict):
                items = [items]
            if not isinstance(items, list):
                return {}
            target = str(name).strip().lower()
            for it in items:
                if not isinstance(it, dict): continue
                full = (str(it.get("Nome", "")) + " " + str(it.get("Cognome", ""))).strip().lower()
                if full == target or str(it.get("Nome", "")).strip().lower() == target:
                    return it
            return items[0] if items else {}

        if entity_type == "macchinari":
            return approved_json.get("Macchinario", {}) or {}
        if entity_type == "reparti":
            return approved_json.get("Reparto", {}) or {}
        if entity_type == "linee":
            return approved_json.get("Linea", {}) or {}
        if entity_type == "agenti_causali":
            return approved_json.get("Causa Tecnica", {}) or {}
        if entity_type == "conseguenze":
            return approved_json.get("Conseguenza Immediata", {}) or {}
        if entity_type == "prodotti":
            ctx = approved_json.get("Contesto Operativo", {}) or {}
            return {k: v for k, v in ctx.items() if "prodott" in k.lower() or "ciclo" in k.lower()}
        if entity_type == "persone":
            return _match_by_name(approved_json.get("Persona"), entity_name)
        if entity_type == "fornitori":
            return _match_by_name(approved_json.get("Fornitore"), entity_name)
        return {}

    def _update_entity_pages(self, event_id: str, approved_json: dict) -> list[WikiOp]:
        ops = []
        entities = self._extract_entities(approved_json)

        for entity_type, entity_name in entities:
            try:
                slug = self._slug(entity_name)
                type_dir = self.wiki_root / "entita" / entity_type
                type_dir.mkdir(parents=True, exist_ok=True)
                page_path = type_dir / f"{slug}.md"

                if page_path.exists():
                    ops.append(WikiOp("AGGIORNA_ENTITA", str(page_path), "SKIP",
                                      f"{entity_type}/{entity_name} (Dataview si aggiorna da solo)"))
                    continue

                content = f"""---
id_entita: {slug}
tipo: {entity_type}
---

# {entity_name}

## Storico Eventi
```dataview
TABLE data_incidente as "Data", gravita as "Gravità"
FROM "eventi"
WHERE contains(file.outlinks, this.file.link)
```
"""
                page_path.write_text(content, encoding="utf-8")
                ops.append(WikiOp("AGGIORNA_ENTITA", str(page_path), "OK",
                                  f"{entity_type}/{entity_name} (creata con Dataview)"))
            except Exception as e:
                ops.append(WikiOp("AGGIORNA_ENTITA", entity_name, "ERROR", str(e)))

        return ops

    # ------------------------------------------------------------------
    # Step 4: update pattern analysis
    # ------------------------------------------------------------------

    def _update_pattern_analysis(self) -> WikiOp:
        pattern_path = self.wiki_root / "analisi" / "pattern_ricorrenti.md"
        pattern_path.parent.mkdir(parents=True, exist_ok=True)
        eventi_dir   = self.wiki_root / "eventi"

        eventi_summaries = []
        for p in sorted(eventi_dir.glob("*.md")):
            if p.name.startswith("_"): continue
            content = p.read_text(encoding="utf-8")
            # Includi YAML frontmatter e sezioni narrative per estrarre la causa
            eventi_summaries.append(f"### {p.stem}\\n{content[:3000]}\\n")
        if not eventi_summaries:
            return WikiOp("AGGIORNA_PATTERN", str(pattern_path), "SKIP",
                          "Nessun evento disponibile per l'analisi.")

        prompt = f"""Sei un analista dati della sicurezza industriale.
Analizza i seguenti eventi (frontmatter YAML con i dati + sintesi narrativa) ed estrai pattern ricorrenti:

{chr(10).join(eventi_summaries)}

Crea il documento markdown:
1. "Distribuzione per Tipo di Incidente"
2. "Distribuzione per Reparto e Linea"
3. "Macchinari Coinvolti Frequentemente"
4. "Cause Radice Più Frequenti" (LEGGERE DAI CAMPI Causa Tecnica o Agente Causale NEL YAML)
5. "Trend e Raccomandazioni Sistemiche"

Output: SOLO il corpo markdown.
"""
        analysis = self._call_llm(prompt)
        # Wrap frontmatter
        now = datetime.now().strftime("%Y-%m-%d")
        final_md = f"""---
tipo: analisi
ultimo_aggiornamento: {now}
eventi_analizzati: {len(eventi_summaries)}
tags:
  - analisi
  - pattern
---

# Pattern Ricorrenti

{analysis}
"""
        pattern_path.write_text(final_md, encoding="utf-8")
        return WikiOp("AGGIORNA_PATTERN", str(pattern_path), "OK")

    # ------------------------------------------------------------------
    # Step 5: update index (pure Python)
    # ------------------------------------------------------------------

    def _update_index(self, event_id: str, approved_json: dict,
                      approval_metadata: dict) -> WikiOp:
        index_path = self.wiki_root / "index.md"
        content = """---
tipo: index
---
# Knowledge Base — Acqua Riva S.p.A.

## Incidenti
```dataview
TABLE data_incidente as "Data", gravita as "Gravità", length(testimoni) as "Testimonianze"
FROM "eventi"
SORT data_incidente DESC
```

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
        index_path.write_text(content, encoding="utf-8")
        return WikiOp("AGGIORNA_INDICE", str(index_path), "OK")

    # ------------------------------------------------------------------
    # Step 5b: crea/aggiorna nodo entità "incidente" per il grafo
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt_md_cell(v) -> str:
        """Sanitizza un valore per inserirlo in una cella di tabella markdown."""
        if v is None:
            return "N/D"
        if isinstance(v, (dict, list)):
            try:
                return f"`{json.dumps(v, ensure_ascii=False)}`"
            except Exception:
                return str(v)
        s = str(v).strip()
        if not s or s.lower() in ("n/d", "null", "none", "n/a"):
            return "N/D"
        # escape pipe e newline
        return s.replace("|", "\\|").replace("\n", " ")

    def _render_entity_section(self, title: str, obj) -> str:
        """Renderizza un'entità (dict o list) come sezione markdown con tabella Campo|Valore."""
        if obj is None or (isinstance(obj, (dict, list)) and len(obj) == 0):
            return f"### {title}\n_N/D — nessun dato estratto._\n"
        lines = [f"### {title}", ""]
        if isinstance(obj, dict):
            lines.append("| Campo | Valore |")
            lines.append("|---|---|")
            for k, v in obj.items():
                if isinstance(v, dict):
                    lines.append(f"| **{k}** | _(vedi sotto)_ |")
                else:
                    lines.append(f"| **{k}** | {self._fmt_md_cell(v)} |")
            # sub-dict (es. Persona.Ruolo, Persona.Turno)
            for k, v in obj.items():
                if isinstance(v, dict) and v:
                    lines.append("")
                    lines.append(f"**{k}**")
                    lines.append("")
                    lines.append("| Campo | Valore |")
                    lines.append("|---|---|")
                    for k2, v2 in v.items():
                        lines.append(f"| **{k2}** | {self._fmt_md_cell(v2)} |")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                lines.append(f"**#{i+1}**")
                lines.append("")
                lines.append("| Campo | Valore |")
                lines.append("|---|---|")
                if isinstance(item, dict):
                    for k, v in item.items():
                        if isinstance(v, dict):
                            lines.append(f"| **{k}** | _(vedi sotto)_ |")
                        else:
                            lines.append(f"| **{k}** | {self._fmt_md_cell(v)} |")
                    for k, v in item.items():
                        if isinstance(v, dict) and v:
                            lines.append("")
                            lines.append(f"**{k}**")
                            lines.append("")
                            lines.append("| Campo | Valore |")
                            lines.append("|---|---|")
                            for k2, v2 in v.items():
                                lines.append(f"| **{k2}** | {self._fmt_md_cell(v2)} |")
                else:
                    lines.append(f"| _valore_ | {self._fmt_md_cell(item)} |")
                lines.append("")
        return "\n".join(lines) + "\n"

    def _upsert_incidente_entity(self, event_id: str, approved_json: dict,
                                  approval_metadata: dict = None) -> WikiOp:
        # Dati ora vivono nelle properties YAML dell'evento. Se esiste una pagina
        # DATI legacy (formato tabellare pre-ontology-first), la rimuove.
        old = self.wiki_root / "entita" / "incidenti" / f"{event_id}-DATI.md"
        if old.exists():
            try:
                old.unlink()
                return WikiOp("CREA_ENTITA_INCIDENTE", str(old), "OK",
                              "pagina DATI legacy rimossa, dati nel frontmatter evento")
            except Exception as e:
                return WikiOp("CREA_ENTITA_INCIDENTE", str(old), "ERROR", repr(e))
        return WikiOp("CREA_ENTITA_INCIDENTE", f"{event_id}-DATI", "SKIP",
                      "nessun DATI legacy, dati nel frontmatter evento")

    # ------------------------------------------------------------------
    # Step 6: append log (pure Python, no LLM)
    # ------------------------------------------------------------------

    def _append_log(self, event_id: str, ops: list[WikiOp]) -> WikiOp:
        log_path = self.wiki_root / "log.md"
        now      = datetime.now().strftime("%Y-%m-%d %H:%M")
        ok_count = sum(1 for o in ops if o.status == "OK")
        line     = f"\n## [{now}] APPROVAZIONE | {event_id} — {ok_count} operazioni OK"

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)

        return WikiOp("APPEND_LOG", str(log_path), "OK")

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str) -> str:
        cfg = types.GenerateContentConfig(max_output_tokens=8192, temperature=0.2)
        # Pool-aware: usa il pool globale se disponibile, altrimenti il client legacy.
        try:
            from client_pool import get_pool
            pool = get_pool()
        except Exception:
            pool = None
        if pool is not None:
            if pool.size == 1:
                resp = pool.primary_client.models.generate_content(
                    model=self.model, contents=prompt, config=cfg,
                )
            else:
                with pool.lease() as c:
                    resp = c.models.generate_content(
                        model=self.model, contents=prompt, config=cfg,
                    )
        elif self.client is not None:
            resp = self.client.models.generate_content(
                model=self.model, contents=prompt, config=cfg,
            )
        else:
            raise RuntimeError("WikiUpdater: nessun client Gemini disponibile (né pool né fallback).")
        text = resp.text.strip()
        # Strip outer code fences if present
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        return text.strip()

    def _extract_entities(self, approved_json: dict) -> list[tuple[str, str]]:
        """Extracts (entity_type, entity_name) pairs from consensus JSON."""
        entities = []
        _skip = {"n/d", "null", "none", "n/a", ""}

        def _ok(v):
            return v and str(v).lower().strip() not in _skip

        def _normalize_disc(name):
            m = re.match(r'\[DISC:\s*(.+?)\s*/\s*.+\]', str(name))
            return m.group(1).strip() if m else str(name).strip()

        # Macchinario
        mach = approved_json.get("Macchinario", {})
        if isinstance(mach, dict):
            nome = mach.get("Nome") or mach.get("Modello")
            if _ok(nome):
                entities.append(("macchinari", _normalize_disc(nome)))

        # Reparto
        rep = approved_json.get("Reparto", {})
        if isinstance(rep, dict):
            nome = rep.get("Nome")
            if _ok(nome):
                entities.append(("reparti", _normalize_disc(nome)))

        # Causa Tecnica
        causa = approved_json.get("Causa Tecnica", {})
        if isinstance(causa, dict):
            tipo = causa.get("Tipo_Guasto") or causa.get("Tipo_Causa")
            if _ok(tipo):
                entities.append(("agenti_causali", _normalize_disc(tipo)))

        # Linea
        linea = approved_json.get("Linea", {})
        if isinstance(linea, dict):
            nome = linea.get("Nome_Linea") or linea.get("Nome")
            if _ok(nome):
                entities.append(("linee", _normalize_disc(nome)))

        # Persone (array)
        persone = approved_json.get("Persona", [])
        if isinstance(persone, dict):
            persone = [persone]
        if isinstance(persone, list):
            for p in persone:
                if isinstance(p, dict):
                    nome = (p.get("Nome", "") + " " + p.get("Cognome", "")).strip()
                    if _ok(nome):
                        entities.append(("persone", _normalize_disc(nome)))

        # Conseguenza Immediata
        cons = approved_json.get("Conseguenza Immediata", {})
        if isinstance(cons, dict):
            tipo = cons.get("Tipo_Conseguenza") or cons.get("Tipo")
            if _ok(tipo):
                entities.append(("conseguenze", _normalize_disc(tipo)))

        # Prodotto (da Contesto Operativo o Macchinario)
        ctx = approved_json.get("Contesto Operativo", {})
        if isinstance(ctx, dict):
            prod = ctx.get("Prodotto") or ctx.get("Tipo_Prodotto")
            if _ok(prod):
                entities.append(("prodotti", _normalize_disc(prod)))

        # Fornitore (singolo o array) — ontologia: Nome, Lotto_Produzione,
        # Certificazione_Materiale, Paese_Origine. Relazione ha_fornito → Componente
        forn = approved_json.get("Fornitore", {})
        if isinstance(forn, dict):
            forn = [forn]
        if isinstance(forn, list):
            for f in forn:
                if isinstance(f, dict):
                    nome = f.get("Nome")
                    if _ok(nome):
                        entities.append(("fornitori", _normalize_disc(nome)))

        return entities

    @staticmethod
    def _slug(name: str) -> str:
        """Converts entity name to filesystem-safe slug."""
        s = re.sub(r'[^A-Za-z0-9\s_-]', '', name)
        s = re.sub(r'\s+', '_', s.strip())
        return s.upper()[:50]


# ----------------------------------------------------------------------
# Read-only scan: dove appare un evento nella wiki?
# ----------------------------------------------------------------------

def scan_event_links(wiki_root: str | Path, event_id: str) -> dict:
    """Trova tutti i riferimenti all'evento nella wiki, SENZA modificare nulla.
    Output usato dal wizard frontend per guidare l'operatore nella pulizia manuale."""
    root = Path(wiki_root)
    out = {
        "event_id": event_id,
        "wiki_root": str(root),
        "event_page": None,
        "event_page_exists": False,
        "archive_dir": None,
        "archive_files": [],
        "entity_pages": [],
        "index_has_row": False,
        "log_path": str(root / "log.md"),
    }

    event_page = root / "eventi" / f"{event_id}.md"
    out["event_page"] = str(event_page)
    out["event_page_exists"] = event_page.exists()

    archive_dir = root / "archivio" / event_id
    out["archive_dir"] = str(archive_dir)
    if archive_dir.exists():
        out["archive_files"] = sorted([str(p.relative_to(root)) for p in archive_dir.rglob("*") if p.is_file()])

    # Index check
    index_path = root / "index.md"
    if index_path.exists():
        try:
            out["index_has_row"] = event_id in index_path.read_text(encoding="utf-8")
        except Exception:
            pass

    # Scansione entità: cerca [[event_id]], [[../../eventi/event_id]] o riga di tabella
    entita_dir = root / "entita"
    if entita_dir.exists():
        link_patterns = [
            re.escape(f"[[{event_id}]]"),
            re.escape(f"[[../../eventi/{event_id}]]"),
            re.escape(f"[[eventi/{event_id}]]"),
            re.escape(event_id),
        ]
        link_re = re.compile("|".join(link_patterns))
        # Frontmatter conteggio_eventi
        count_re = re.compile(r"^conteggio_eventi\s*:\s*(\d+)", re.MULTILINE)

        for p in sorted(entita_dir.rglob("*.md")):
            try:
                body = p.read_text(encoding="utf-8")
            except Exception:
                continue
            if not link_re.search(body):
                continue
            entity_type = p.parent.name  # macchinari, reparti, ...
            count_match = count_re.search(body)
            count = int(count_match.group(1)) if count_match else None
            out["entity_pages"].append({
                "type": entity_type,
                "name": p.stem,
                "path": str(p.relative_to(root)),
                "abs_path": str(p),
                "conteggio_eventi": count,
                "is_orphan_after_delete": count == 1,
            })

    return out
