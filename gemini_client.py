"""Gemini 2.0 Flash integration. Every prompt is grounded in scraped UN
source material only — the model is told to refuse rather than guess."""

import json
import logging
import os
import re

log = logging.getLogger("unbrief.gemini")

try:
    import google.generativeai as genai
except ImportError:  # surfaced as a friendly error at call time
    genai = None

# gemini-2.0-flash no longer has free-tier quota (limit 0 as of mid-2026);
# 2.5-flash does. Override with GEMINI_MODEL in .env if Google moves it again.
MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MAX_DIGEST_CHARS = 24000  # ~6000 tokens
MIN_SECTION_CHARS = 400

ANALYST_SYSTEM = (
    "You are a senior UN policy analyst. Write factually and concisely. "
    "Every claim must be traceable to the source material provided. "
    "Do not add information not present in the sources. "
    "If the source material is insufficient for a section, state that explicitly."
)

_configured = False


def _ensure_configured():
    global _configured
    if genai is None:
        raise RuntimeError("google-generativeai is not installed (pip install -r requirements.txt).")
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
    if not _configured:
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        _configured = True


def _model(system_instruction):
    return genai.GenerativeModel(MODEL_NAME, system_instruction=system_instruction)


# ---------------------------------------------------------------------------
# Source digest
# ---------------------------------------------------------------------------

def build_digest(scraped_data, max_chars=MAX_DIGEST_CHARS):
    """Serialize non-empty sources, truncating each proportionally so the
    total stays under the token cap."""
    sections = []
    for source, payload in (scraped_data or {}).items():
        if not payload:
            continue
        body = json.dumps(payload, ensure_ascii=False, default=str)
        sections.append([source, body])
    if not sections:
        return ""
    total = sum(len(body) for _, body in sections)
    if total > max_chars:
        for section in sections:
            share = max(MIN_SECTION_CHARS, int(len(section[1]) / total * max_chars))
            section[1] = section[1][:share]
    return "\n\n".join(f"### SOURCE: {source}\n{body}" for source, body in sections)


def _collect_docs(scraped_data):
    """Flatten the document-list sources into one indexable list."""
    docs = []
    for source in ("un_digital_library", "security_council"):
        for item in scraped_data.get(source) or []:
            title = item.get("title") or item.get("number") or ""
            url = item.get("url", "")
            if title:
                docs.append({"title": title, "url": url})
    return docs


def list_source_labels(scraped_data, source_labels):
    return [source_labels.get(s, s) for s, v in (scraped_data or {}).items() if v]


# ---------------------------------------------------------------------------
# 1. Briefing (non-streaming) + relevance scores
# ---------------------------------------------------------------------------

def generate_briefing(country, committee, agenda, scraped_data):
    """Returns {"markdown": str, "relevance": {url: 1-3}}."""
    _ensure_configured()
    digest = build_digest(scraped_data)
    if not digest:
        return {
            "markdown": ("## Country Overview\nNo UN source material could be retrieved "
                         "for this query, so no briefing can be written. Try a broader "
                         "agenda or check the country spelling."),
            "relevance": {},
        }
    docs = _collect_docs(scraped_data)
    doc_lines = "\n".join(f"{i}. {d['title']}" for i, d in enumerate(docs)) or "(none)"
    prompt = f"""SOURCE MATERIAL scraped from UN websites:

{digest}

---

Write a delegate briefing for {country} in the {committee} committee on the agenda
"{agenda}". Use markdown with exactly these sections:

## Country Overview
(3-4 sentences, specific to this committee and agenda)
## Historical Position
(what this country has stood for on this agenda)
## Key Allies and Adversaries
(based on the voting/document record in the sources)
## Current Likely Stance
(reasoned strictly from the data above)
## Weaknesses in Their Position
(angles for lobbying or pressure)

Use ONLY the source material above. Where it is insufficient for a section,
say so explicitly in that section.

After the briefing, score each document below for relevance to this agenda
(1 = marginal, 2 = related, 3 = directly relevant) and output the scores as a
fenced json block, nothing after it:

{doc_lines}

```json
{{"relevance": [{{"index": 0, "score": 2}}]}}
```"""
    text = _model(ANALYST_SYSTEM).generate_content(prompt).text or ""
    relevance = {}
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S)
    if match:
        try:
            for entry in json.loads(match.group(1)).get("relevance", []):
                idx, score = entry.get("index"), entry.get("score")
                if isinstance(idx, int) and 0 <= idx < len(docs) and score in (1, 2, 3):
                    relevance[docs[idx]["url"]] = score
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            log.warning("could not parse relevance scores: %s", exc)
        text = text[:match.start()]
    return {"markdown": text.strip(), "relevance": relevance}


# ---------------------------------------------------------------------------
# 2. Deep research (non-streaming)
# ---------------------------------------------------------------------------

def generate_deep_research(country, agenda, scraped_data):
    """Returns markdown for the Deep Research tab."""
    _ensure_configured()
    digest = build_digest(scraped_data)
    if not digest:
        return ("## Engagement Timeline\nNo UN source material could be retrieved for "
                "this query, so no research synthesis can be written.")
    prompt = f"""SOURCE MATERIAL scraped from UN websites:

{digest}

---

Write a deep-research synthesis on {country}'s record regarding "{agenda}".
Use markdown with exactly these sections:

## Engagement Timeline
(chronological bullet list; start each bullet with the date or year taken from
the documents, then an em dash, then the event)
## Notable Resolutions or Votes
## Record Contradictions
(stated position vs. what the voting/document record shows; prefix every
bullet in this section with "⚠ ")
## Key Document References
(bullet list of markdown links to the source documents)

Use ONLY the source material above. Where it is insufficient for a section,
say so explicitly in that section. Do not invent dates."""
    return (_model(ANALYST_SYSTEM).generate_content(prompt).text or "").strip()


# ---------------------------------------------------------------------------
# 3. Streaming Q&A
# ---------------------------------------------------------------------------

QA_SYSTEM_TEMPLATE = (
    "Answer only from the UN source material provided below. If the answer is "
    "not in the sources, say: 'This information was not found in available UN "
    "sources.' Do not guess. End every answer with a final line of the form "
    "'Sources consulted: <comma-separated list>'.\n\n"
    "SOURCE MATERIAL for {country} / {committee} / agenda \"{agenda}\":\n\n{digest}"
)


def stream_qa(country, committee, agenda, scraped_data, question, history,
              source_names):
    """Yields answer chunks. `history` is [{"role": "user"|"model", "content": str}]."""
    try:
        _ensure_configured()
    except RuntimeError as exc:
        yield f"[error] {exc}"
        return
    digest = build_digest(scraped_data) or "(no source material was retrieved)"
    model = _model(QA_SYSTEM_TEMPLATE.format(
        country=country, committee=committee, agenda=agenda, digest=digest))
    chat = model.start_chat(
        history=[{"role": h["role"], "parts": [h["content"]]} for h in history])
    acc = []
    try:
        for chunk in chat.send_message(question, stream=True):
            if chunk.text:
                acc.append(chunk.text)
                yield chunk.text
    except Exception as exc:
        log.error("Gemini Q&A stream failed: %s", exc)
        yield f"\n[error] Gemini request failed: {exc}"
        return
    if "sources consulted" not in "".join(acc).lower():
        yield "\n\nSources consulted: " + (", ".join(source_names) or "none")


# ---------------------------------------------------------------------------
# 4. Streaming position paper
# ---------------------------------------------------------------------------

DISCLAIMER = "AI-generated draft. Verify all claims before submitting."


def stream_position_paper(country, committee, agenda, scraped_data):
    try:
        _ensure_configured()
    except RuntimeError as exc:
        yield f"[error] {exc}"
        return
    digest = build_digest(scraped_data)
    if not digest:
        yield ("No UN source material could be retrieved for this query, so a "
               "position paper cannot be drafted.\n\n" + DISCLAIMER)
        return
    prompt = f"""SOURCE MATERIAL scraped from UN websites:

{digest}

---

Write the opening position paragraph (~200 words) of a MUN position paper for
{country} in the {committee} committee on the agenda "{agenda}".

Requirements:
- First person plural diplomatic voice ("The delegation of {country}...").
- Cite at least 2 specific UN documents BY NAME from the source material.
- Use only facts present in the sources; if the sources cannot support two
  document citations, say so plainly instead of inventing them.
- End with this exact line: "{DISCLAIMER}" """
    acc = []
    try:
        for chunk in _model(ANALYST_SYSTEM).generate_content(prompt, stream=True):
            if chunk.text:
                acc.append(chunk.text)
                yield chunk.text
    except Exception as exc:
        log.error("Gemini position paper stream failed: %s", exc)
        yield f"\n[error] Gemini request failed: {exc}"
        return
    if DISCLAIMER.lower() not in "".join(acc).lower():
        yield "\n\n" + DISCLAIMER
