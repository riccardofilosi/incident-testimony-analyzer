# Incident Testimony Analyzer

Progetto realizzato per l'esame di **Smart Factory** — Laurea Magistrale in Ingegneria
Gestionale, Sapienza Università di Roma.

## Il problema

Dopo un incidente in fabbrica si raccolgono le testimonianze di chi era presente:
registrazioni audio o testi, ognuno con la sua versione. Trasformarle in un report
strutturato è lavoro manuale lungo, e le contraddizioni fra testimoni emergono tardi o
non emergono affatto.

## L'obiettivo

Estrarre in automatico dalle testimonianze un report strutturato dell'incidente,
misurando quanto ci si può fidare di ogni dato estratto, e lasciare la decisione finale
a una persona.

## Come

- **Trascrizione**: ogni audio viene trascritto più volte; si tiene la versione con meno
  passaggi incomprensibili. Si stima anche la qualità dell'audio e il tono del parlante
  (velocità, altezza della voce, energia).
- **Filtro di rilevanza**: una testimonianza che non riguarda un evento critico viene
  scartata prima dell'analisi.
- **Estrazione guidata da un'ontologia**: un modello strutturato dell'incidente (evento,
  luogo, persone, cause, impatti, azioni, fornitori, normative) guida l'LLM. Ogni campo
  è marcato come *diretto* (solo ciò che il testimone dice) o *condensato* (inferenza
  ammessa, ma motivata).
- **Più estrazioni, una scelta**: la stessa testimonianza viene estratta più volte; si
  misura la coerenza fra le estrazioni e si sceglie la migliore.
- **LLM come giudice**: un secondo passaggio confronta il report con la trascrizione e
  segnala allucinazioni e omissioni.
- **Confronto fra testimoni**: le versioni vengono unite in un report riassuntivo; le
  discordanze sono classificate per impatto sulla sicurezza (critico, rilevante,
  informativo).
- **Decisione umana**: l'operatore corregge, risolve le discordanze e approva o rifiuta.
- **Base di conoscenza**: ogni incidente approvato aggiorna un vault Obsidian che collega
  eventi, macchinari, linee, persone e fornitori, per far emergere pattern ricorrenti.
- **Chat**: domande in linguaggio naturale sul singolo incidente o sull'archivio.

Output: report Excel multi-foglio (individuale per testimone e riassuntivo) e una nota
critica con le metriche di qualità.

## Limiti

Il sistema prepara il report, non lo certifica: senza l'approvazione dell'operatore
nessun dato entra nella base di conoscenza. Il giudice è anch'esso un LLM, quindi la
misura di fedeltà è una stima, non una verifica. L'analisi del tono della voce è un
indizio per l'analista, non una valutazione del testimone.

## Stack

Python · FastAPI · Google Gemini API · pandas/openpyxl · librosa ·
React + TypeScript + Vite + Tailwind · Obsidian (Dataview)

## Struttura

```text
AVVIA.bat                      # avvio con doppio click
webapp/
├── main.py                    # FastAPI: upload, pipeline, discordanze, approvazione, chat
├── analisi_testimonianze.py   # pipeline: trascrizione, estrazione, metriche, report
├── wiki_updater.py            # aggiornamento del vault Obsidian dopo l'approvazione
├── client_pool.py             # rotazione delle chiavi API
├── state_manager.py           # stato degli incidenti su disco
├── config.py
├── Nuova_Ontologia_Tabellare.xlsx   # ontologia dell'incidente
├── Nuovo_Schema_Report.xlsx         # layout del report Excel
└── frontend/                  # interfaccia React
```

## Avvio

Requisiti: Windows 10/11, Python 3.13+, Node.js 20+, ffmpeg, una chiave API Google
Gemini. **Nessuna chiave è inclusa nel repository.**

```bat
:: la chiave si inserisce dall'interfaccia (Impostazioni)
:: oppure copiando webapp\.env.example in webapp\.env
AVVIA.bat
```

Al primo avvio lo script crea l'ambiente Python, installa le dipendenze e apre
l'interfaccia su `http://localhost:5173` (backend su `http://localhost:8000`).

Il repository non contiene testimonianze né incidenti di esempio: si caricano
dall'interfaccia.

## Sviluppo

Codice sviluppato con Claude Code.
