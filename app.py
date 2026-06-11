"""UNBrief — MUN country research portfolios built from scraped UN sources."""

import logging
import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, stream_with_context, url_for)

import gemini_client
import models
import utils
from cache import clear_query, get_cached, make_key, set_cached
from scraper import COMMITTEE_SOURCES, SOURCE_LABELS, run_scrapers, sources_for

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "unbrief-dev-secret")
models.init_db()

log = logging.getLogger("unbrief.app")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _query_from(values):
    """Extract and normalise (country, committee_key, agenda) from a mapping."""
    country = utils.canonical_country((values.get("country") or "").strip())
    committee = utils.normalise_committee((values.get("committee") or "").strip())
    agenda = " ".join((values.get("agenda") or "").split())
    return country, committee, agenda


def _session_key(country, committee, agenda):
    return f"{country}:{committee}:{agenda}".lower()


def _index_context(form=None, errors=None):
    return {
        "countries": utils.country_names(),
        "committees": utils.KNOWN_COMMITTEES,
        "committee_sources": COMMITTEE_SOURCES,
        "committee_aliases": utils.COMMITTEE_ALIASES,
        "source_labels": SOURCE_LABELS,
        "form": form or {},
        "errors": errors or {},
    }


def _bad_request(message):
    return jsonify({"error": message}), 400


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return render_template("index.html", **_index_context())


@app.post("/research")
def research():
    raw = {k: (request.form.get(k) or "").strip()
           for k in ("country", "committee", "agenda")}
    errors = {k: "This field is required." for k, v in raw.items() if not v}
    if errors:
        return render_template("index.html",
                               **_index_context(form=raw, errors=errors)), 400
    country, committee, agenda = _query_from(raw)
    run_scrapers(country, committee, agenda)  # threaded inside; warms the cache
    return redirect(url_for("portfolio", country=country,
                            committee=committee, agenda=agenda))


@app.get("/portfolio")
def portfolio():
    country, committee, agenda = _query_from(request.args)
    if not (country and agenda):
        return redirect(url_for("index"))
    results, empty_sources, last_updated = run_scrapers(country, committee, agenda)
    iso2 = utils.country_codes(country)[0]
    skey = _session_key(country, committee, agenda)
    boot = {
        "country": country,
        "committee": committee,
        "agenda": agenda,
        "iso2": iso2.lower() if iso2 else None,
        "lastUpdated": (datetime.fromtimestamp(last_updated)
                        .strftime("%d %b %Y, %H:%M") if last_updated else "—"),
        "sources": [{"id": s, "label": SOURCE_LABELS.get(s, s),
                     "empty": s in empty_sources}
                    for s in sources_for(committee)],
        "data": results,
        "note": models.get_note(skey),
        "qaHistory": models.get_qa_history(skey),
        "zeroData": all(not v for v in results.values()),
    }
    return render_template("portfolio.html", boot=boot)


# ---------------------------------------------------------------------------
# Gemini API (cached JSON)
# ---------------------------------------------------------------------------

def _json_query():
    body = request.get_json(silent=True) or {}
    country, committee, agenda = _query_from(body)
    if not (country and agenda):
        return None, None, None, body
    return country, committee, agenda, body


@app.post("/api/briefing")
def api_briefing():
    country, committee, agenda, _ = _json_query()
    if not country:
        return _bad_request("country, committee and agenda are required")
    key = make_key("gemini_briefing", country, committee, agenda)
    cached = get_cached(key)
    if cached is not None and cached[0]:
        return jsonify(cached[0])
    data, _, _ = run_scrapers(country, committee, agenda)
    try:
        result = gemini_client.generate_briefing(country, committee, agenda, data)
    except Exception as exc:
        log.error("briefing generation failed: %s", exc)
        return jsonify({"error": str(exc)}), 502
    set_cached(key, result)
    return jsonify(result)


@app.post("/api/deep-research")
def api_deep_research():
    country, committee, agenda, _ = _json_query()
    if not country:
        return _bad_request("country, committee and agenda are required")
    key = make_key("gemini_deep", country, committee, agenda)
    cached = get_cached(key)
    if cached is not None and cached[0]:
        return jsonify(cached[0])
    data, _, _ = run_scrapers(country, committee, agenda)
    try:
        markdown = gemini_client.generate_deep_research(country, agenda, data)
    except Exception as exc:
        log.error("deep research generation failed: %s", exc)
        return jsonify({"error": str(exc)}), 502
    result = {"markdown": markdown}
    set_cached(key, result)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Streaming endpoints
# ---------------------------------------------------------------------------

@app.post("/api/qa")
def api_qa():
    country, committee, agenda, body = _json_query()
    question = (body.get("question") or "").strip()
    if not (country and question):
        return _bad_request("country, committee, agenda and question are required")
    skey = _session_key(country, committee, agenda)
    history = models.get_qa_history(skey)
    data, _, _ = run_scrapers(country, committee, agenda)
    source_names = [SOURCE_LABELS.get(s, s) for s, v in data.items() if v]

    def generate():
        models.add_qa_message(skey, "user", question)
        chunks = []
        for chunk in gemini_client.stream_qa(country, committee, agenda, data,
                                             question, history, source_names):
            chunks.append(chunk)
            yield chunk
        models.add_qa_message(skey, "model", "".join(chunks))

    return Response(stream_with_context(generate()),
                    mimetype="text/plain; charset=utf-8")


@app.post("/api/position-paper")
def api_position_paper():
    country, committee, agenda, _ = _json_query()
    if not country:
        return _bad_request("country, committee and agenda are required")
    data, _, _ = run_scrapers(country, committee, agenda)
    stream = gemini_client.stream_position_paper(country, committee, agenda, data)
    return Response(stream_with_context(stream),
                    mimetype="text/plain; charset=utf-8")


# ---------------------------------------------------------------------------
# Notes / history / cache management
# ---------------------------------------------------------------------------

@app.post("/api/save-notes")
def api_save_notes():
    country, committee, agenda, body = _json_query()
    if not country:
        return _bad_request("country, committee and agenda are required")
    saved_at = models.save_note(_session_key(country, committee, agenda),
                                body.get("content") or "")
    return jsonify({"saved_at": datetime.fromtimestamp(saved_at).strftime("%H:%M:%S")})


@app.post("/api/clear-qa")
def api_clear_qa():
    country, committee, agenda, _ = _json_query()
    if not country:
        return _bad_request("country, committee and agenda are required")
    models.clear_qa_history(_session_key(country, committee, agenda))
    return jsonify({"ok": True})


@app.post("/api/regenerate")
def api_regenerate():
    country, committee, agenda, _ = _json_query()
    if not country:
        return _bad_request("country, committee and agenda are required")
    clear_query(country, committee, agenda)
    run_scrapers(country, committee, agenda, force=True)
    return jsonify({"ok": True})


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "False").lower() in ("1", "true", "yes")
    app.run(host="127.0.0.1", port=5000, debug=debug)
