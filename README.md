# BIS Intelligent Assistant — RAG-based Q&A

> **Grounded. Accurate. Official.**  
> Ask any question about Indian Standards, QCOs, ISI Mark certification, and hallmarking — and get answers backed strictly by official BIS documents.

[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-green?logo=fastapi)](https://fastapi.tiangolo.com)
[![NVIDIA NIM](https://img.shields.io/badge/LLM-NVIDIA%20NIM-76b900?logo=nvidia)](https://build.nvidia.com)
[![ChromaDB](https://img.shields.io/badge/Vector%20DB-ChromaDB-purple)](https://www.trychroma.com)

---

## What It Does

- 🔍 **Hybrid Search** — BM25 keyword + dense vector retrieval (ChromaDB) with Reciprocal Rank Fusion
- 📋 **Structured QCO Lookup** — 2,000+ QCO product records with IS standard numbers, mandatory status, and scheme type
- 🛡️ **Hallucination Guardrail** — IS numbers not found in retrieved context are stripped from answers automatically
- ⚡ **Streaming Responses** — Answers appear word-by-word via Server-Sent Events
- 🏷️ **Source Citations** — Every answer includes clickable source links
- ⚠️ **Confidence Scoring** — Low-confidence answers are flagged automatically

---

## Architecture

```
User Query
    │
    ▼
┌─────────────┐    ┌─────────────────────┐
│  Classifier  │───▶│  Query Resolver     │ (follow-up detection)
└─────────────┘    └─────────────────────┘
    │
    ├── structured_lookup ──▶ SQLite QCO table (2,208 rows)
    ├── procedure_rag     ──▶ ChromaDB vector search + BM25
    └── faq_rag           ──▶ ChromaDB vector search + BM25
                                   │
                          Hybrid RRF fusion
                                   │
                          NVIDIA NIM (Nemotron 3 Super 120B)
                                   │
                          Hallucination guardrail
                                   │
                          Streaming SSE response ──▶ Frontend
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| LLM | `nvidia/nemotron-3-super-120b-a12b` via NVIDIA NIM |
| Embeddings | `nvidia/nemotron-3-embed-1b` (2048-dim asymmetric) |
| Vector DB | ChromaDB (persistent) |
| Keyword Search | BM25Okapi (rank-bm25) |
| Backend API | FastAPI + Uvicorn |
| Frontend | Vanilla HTML/CSS/JS (no framework) |
| Structured Data | SQLite (2,208+ QCO records) |
| Scraping | BeautifulSoup + Playwright + PyMuPDF |

---

## Project Structure

```
BIS-RAG/
├── backend/
│   ├── main.py          # FastAPI app — /chat, /chat/stream, /health
│   ├── retrieval.py     # Hybrid BM25 + ChromaDB search
│   ├── generation.py    # LLM prompt building + guardrails
│   ├── classifier.py    # Query routing (structured / RAG / FAQ)
│   ├── followup.py      # Multi-turn query resolution
│   ├── nim.py           # NVIDIA NIM client (chat + embed + stream)
│   ├── config.py        # All tuneable constants
│   └── ingest.py        # Index raw_documents.json → ChromaDB
│
├── scraper/
│   ├── bis_scraper.py   # Phase 1 — HTML + PDF scraping from BIS website
│   └── bis_supplement.py # Phase 2 — seed QCO data + extra HTML pages
│
├── frontend/
│   ├── index.html       # Landing page
│   ├── chat.html        # Chat interface
│   ├── chat.js          # Streaming SSE client
│   ├── app.js           # Landing page interactions
│   ├── styles.css       # Full design system
│   └── icons.js         # SVG icon library
│
├── data/                # Auto-generated — run scrapers to populate
│   └── .gitkeep
│
├── requirements.txt
├── .env.example         # Copy to .env and fill in your NVIDIA key
├── start.bat            # One-click startup (Windows)
└── rebuild_qco.py       # Utility — clean & rebuild QCO database
```

---

## Quick Start

### 1. Clone & Set Up

```bash
git clone https://github.com/Ronak-vegad/sih-2026.git
cd sih-2026

python -m venv venv
venv\Scripts\activate          # Windows
# or: source venv/bin/activate  # Linux/Mac

pip install -r requirements.txt
playwright install chromium
```

### 2. Set Your API Key

```bash
copy .env.example .env
# Edit .env and add your NVIDIA NIM key:
# NVIDIA_API_KEY=nvapi-your-key-here
```
Get a free key at **https://build.nvidia.com**

### 3. Run the Scrapers (one-time setup)

```bash
# Phase 1 — Scrape BIS website + PDFs
python -m scraper.bis_scraper

# Phase 2 — Seed QCO table + additional pages
python -m scraper.bis_supplement
```

### 4. Build the Vector Index

```bash
python -m backend.ingest
```

### 5. Start the App

**Windows — double-click:**
```
start.bat
```

**Or manually:**
```bash
# Terminal 1 — Backend
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2 — Frontend
python -m http.server 3000 --directory frontend
```

Open 👉 **http://localhost:3000/chat.html**

---

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/chat` | POST | Standard JSON response |
| `/chat/stream` | POST | Server-Sent Events (streaming) |
| `/health` | GET | System health + stats |
| `/docs` | GET | Swagger UI |

### Example Request

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Is helmet mandatory in India?", "history": []}'
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `NVIDIA_API_KEY` | ✅ Yes | NVIDIA NIM key (starts with `nvapi-`) |

---

## Sample Questions

- *"Which IS standard applies to LED bulbs?"*
- *"Is helmet certification mandatory in India?"*
- *"How do I apply for the ISI Mark?"*
- *"What is hallmarking and how do I verify it?"*
- *"What products come under CRS?"*
- *"Is cement covered under BIS QCO?"*

---

## Made for SIH 2026

This project is built as part of **Smart India Hackathon 2026** to make BIS regulations accessible to everyone — manufacturers, consumers, importers, and retailers.

---

## License

MIT
