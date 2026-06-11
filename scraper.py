"""Committee -> source mapping and all UN source scrapers.

Every scraper owns its try/except, returns {} or [] on failure, and never
returns raw HTML. Results are cached per-source in SQLite (24h TTL; empty
results retried after 1h) by run_scrapers().
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from cache import get_cached, make_key, set_cached
from utils import KNOWN_COMMITTEES, country_codes, slugify, truncate, unep_region

log = logging.getLogger("unbrief.scraper")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 12
EMPTY_RETRY_SECONDS = 3600  # retry failed/empty sources after an hour

COMMITTEE_SOURCES = {
    "UNSC": ["un_news", "un_digital_library", "security_council"],
    "UNHRC": ["un_news", "un_digital_library", "ohchr"],
    "DISEC": ["un_news", "un_digital_library", "unoda"],
    "ECOSOC": ["un_news", "un_digital_library", "ecosoc", "undata"],
    "ECOFIN": ["un_news", "un_digital_library", "undesa", "unctad"],
    "SOCHUM": ["un_news", "un_digital_library", "ohchr", "unicef"],
    "SPECPOL": ["un_news", "un_digital_library", "dppa"],
    "LEGAL": ["un_news", "un_digital_library", "un_treaty_collection"],
    "UNEP": ["un_news", "un_digital_library", "unep"],
    "WHO": ["un_news", "un_digital_library", "who"],
    "UNESCO": ["un_news", "un_digital_library", "unesco"],
    "UNICEF": ["un_news", "un_digital_library", "unicef"],
    "WFP": ["un_news", "un_digital_library", "wfp"],
    "UNDP": ["un_news", "un_digital_library", "undp"],
    "DEFAULT": ["un_news", "un_digital_library", "undata"],
}

assert set(COMMITTEE_SOURCES) == set(KNOWN_COMMITTEES) | {"DEFAULT"}, (
    "COMMITTEE_SOURCES keys out of sync with utils.KNOWN_COMMITTEES"
)

SOURCE_LABELS = {
    "un_news": "UN News",
    "un_digital_library": "UN Digital Library",
    "security_council": "Security Council Resolutions",
    "ohchr": "OHCHR",
    "unoda": "UNODA",
    "ecosoc": "ECOSOC",
    "undata": "UNdata",
    "undesa": "UN DESA",
    "unctad": "UNCTAD",
    "dppa": "DPPA",
    "un_treaty_collection": "UN Treaty Collection",
    "unep": "UNEP",
    "who": "WHO",
    "unesco": "UNESCO",
    "unicef": "UNICEF",
    "wfp": "WFP",
    "undp": "UNDP",
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def fetch(url, params=None):
    resp = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp


def soup_of(resp):
    return BeautifulSoup(resp.content, "lxml")


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def absolutize(href, base):
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    return base.rstrip("/") + "/" + href.lstrip("/")


def paragraphs_of(soup, min_len=60):
    """Visible paragraph/list text, deduplicated, never raw HTML.
    Skips navigation, header and footer blocks (menu cruft, not content)."""
    seen, out = set(), []
    for el in soup.find_all(["p", "li"]):
        if el.find_parent(["nav", "header", "footer"]) is not None:
            continue
        text = clean(el.get_text(" "))
        if len(text) < min_len or text in seen or "toggle submenu" in text.lower():
            continue
        seen.add(text)
        out.append(text)
    return out


def agenda_keywords(agenda):
    stop = {"the", "and", "for", "with", "from", "into", "their", "this", "that"}
    return [w for w in re.findall(r"[a-z]{4,}", (agenda or "").lower()) if w not in stop]


def classify_doc_type(text):
    lower = (text or "").lower()
    if "resolution" in lower:
        return "Resolution"
    if "report" in lower:
        return "Report"
    if "statement" in lower or "letter" in lower or "speech" in lower:
        return "Statement"
    if "draft" in lower:
        return "Draft"
    return "Document"


def harvest_links(url, country, agenda, base, limit=6):
    """Generic 'links on a page that mention the country or agenda' scraper."""
    soup = soup_of(fetch(url))
    needles = [country.lower()] + agenda_keywords(agenda)
    items, seen = [], set()
    for a in soup.find_all("a", href=True):
        title = clean(a.get_text())
        if len(title) < 12:
            continue
        if not any(n in title.lower() for n in needles):
            continue
        link = absolutize(a["href"], base)
        if link in seen:
            continue
        seen.add(link)
        items.append({"title": truncate(title, 160), "url": link})
        if len(items) >= limit:
            break
    return items


# ---------------------------------------------------------------------------
# Universal scrapers
# ---------------------------------------------------------------------------

# unep region (utils) -> UN News region feed slug
_NEWS_REGION = {
    "africa": "africa",
    "asia-and-pacific": "asia-pacific",
    "europe": "europe",
    "latin-america-and-caribbean": "americas",
    "north-america": "americas",
    "west-asia": "middle-east",
}


def scrape_un_news(country, agenda):
    """6 most relevant recent UN News articles for country + agenda.

    UN News rejects server-side search requests outright (HTTP 406), so we
    pool the public global + regional RSS feeds and rank items by
    country/agenda keyword hits."""
    try:
        feed_urls = ["https://news.un.org/feed/subscribe/en/news/all/rss.xml"]
        region = _NEWS_REGION.get(unep_region(country) or "")
        if region:
            feed_urls.append("https://news.un.org/feed/subscribe/en/news/"
                             f"region/{region}/feed/rss.xml")
        items = []
        for url in feed_urls:
            try:
                items += BeautifulSoup(fetch(url).content, "lxml-xml").find_all("item")
            except Exception as exc:
                log.warning("un_news feed %s failed: %s", url, exc)
        country_l = country.lower()
        keywords = agenda_keywords(agenda)
        scored, seen = [], set()
        for item in items:
            title = clean(item.title.get_text()) if item.title else ""
            link = clean(item.link.get_text()) if item.link else ""
            desc_node = item.find("description")
            desc = ""
            if desc_node:
                desc = clean(BeautifulSoup(desc_node.get_text(), "lxml").get_text(" "))
            if not title or not link or link in seen:
                continue
            seen.add(link)
            haystack = f"{title} {desc}".lower()
            score = (2 if country_l in haystack else 0) \
                + sum(k in haystack for k in keywords)
            if not score:
                continue
            date = ""
            pub = item.find("pubDate")
            if pub:
                m = re.search(r"\d{1,2} \w{3} \d{4}", pub.get_text())
                date = m.group() if m else clean(pub.get_text())
            summary = " ".join(re.split(r"(?<=[.!?])\s+", desc)[:2])
            scored.append((score, {"title": truncate(title, 160), "date": date,
                                   "summary": truncate(summary, 320), "url": link}))
        scored.sort(key=lambda pair: -pair[0])  # stable: feed recency kept within ties
        return [article for _, article in scored[:6]]
    except Exception as exc:
        log.error("scraper un_news failed: %s", exc)
        return []


def scrape_un_digital_library(country, committee, agenda):
    """8 most recent UN Digital Library records for country + agenda.

    The library sits behind an AWS WAF JavaScript challenge that plain HTTP
    clients cannot pass, so when the search yields nothing we fall back to
    the UN press release listings, which are served openly."""
    try:
        docs = _undl_search(country, agenda)
    except Exception as exc:
        log.error("scraper un_digital_library failed: %s", exc)
        docs = []
    if docs:
        return docs
    log.warning("un_digital_library empty (WAF challenge likely); "
                "falling back to UN press releases")
    try:
        return _press_release_fallback(country, agenda)
    except Exception as exc:
        log.error("un press release fallback failed: %s", exc)
        return []


def _undl_search(country, agenda):
    resp = fetch(
        "https://digitallibrary.un.org/search",
        params={"p": f"{country} {agenda}", "of": "hb", "rg": 8, "sf": "year", "so": "d"},
    )
    soup = soup_of(resp)
    docs, seen = [], set()
    for link in soup.select("a[href*='/record/']"):
        title = clean(link.get_text())
        if len(title) < 12:
            continue
        url = absolutize(link["href"], "https://digitallibrary.un.org").split("?")[0]
        if url in seen:
            continue
        seen.add(url)
        container = link.find_parent(["div", "td", "li"]) or link
        context = clean(container.get_text(" "))
        m = re.search(r"\b\d{1,2} [A-Z][a-z]+\.? \d{4}\b|\b(?:19|20)\d{2}\b", context)
        docs.append({
            "title": truncate(title, 180),
            "date": m.group() if m else "",
            "type": classify_doc_type(context or title),
            "url": url,
        })
        if len(docs) >= 8:
            break
    return docs


def _press_release_fallback(country, agenda, limit=8):
    needles = [country.lower()] + agenda_keywords(agenda)
    docs, seen = [], set()
    for page in (0, 1, 2):
        soup = soup_of(fetch("https://press.un.org/en/content/press-release",
                             params={"page": page}))
        for a in soup.find_all("a", href=True):
            title = clean(a.get_text())
            if len(title) < 25 or ".doc.htm" not in a["href"]:
                continue
            if not any(n in title.lower() for n in needles):
                continue
            url = absolutize(a["href"], "https://press.un.org")
            if url in seen:
                continue
            seen.add(url)
            container = a.find_parent(["article", "div", "li"]) or a
            time_tag = container.find("time")
            doc_type = classify_doc_type(title)
            docs.append({
                "title": truncate(title, 180),
                "date": clean(time_tag.get_text()) if time_tag else "",
                "type": "Statement" if doc_type == "Document" else doc_type,
                "url": url,
            })
            if len(docs) >= limit:
                return docs
    return docs


# ---------------------------------------------------------------------------
# Committee-specific scrapers
# ---------------------------------------------------------------------------

def scrape_security_council(country, agenda):
    """Recent SC resolutions whose table rows mention the country.

    The per-year tables live at a stable URL pattern on main.un.org;
    we scan the current year plus the two before it."""
    try:
        from datetime import date
        results, needle = [], country.lower()
        for year in range(date.today().year, date.today().year - 3, -1):
            url = ("https://main.un.org/securitycouncil/en/content/"
                   f"resolutions-adopted-security-council-{year}")
            try:
                page = soup_of(fetch(url))
            except Exception as exc:
                log.warning("security_council year page %s failed: %s", year, exc)
                continue
            for row in page.select("tr"):
                cells = [clean(td.get_text(" ")) for td in row.find_all("td")]
                if len(cells) < 3 or needle not in " ".join(cells).lower():
                    continue
                link = row.find("a", href=True)
                results.append({
                    "number": cells[0],
                    "date": cells[1],
                    "title": truncate(cells[2], 180),
                    "url": absolutize(link["href"], "https://main.un.org") if link else url,
                })
                if len(results) >= 6:
                    return results
        return results
    except Exception as exc:
        log.error("scraper security_council failed: %s", exc)
        return []


def scrape_ohchr(country, agenda):
    """UPR and treaty-status text from the OHCHR country page."""
    try:
        soup = soup_of(fetch(f"https://www.ohchr.org/en/countries/{slugify(country)}"))
        paras = paragraphs_of(soup, min_len=50)
        upr = [t for t in paras if "universal periodic review" in t.lower() or "upr" in t.lower()]
        treaty = [t for t in paras
                  if any(k in t.lower() for k in ("treaty", "treaties", "ratif", "convention"))]
        result = {}
        if upr:
            result["upr_summary"] = truncate(" ".join(upr[:3]), 1000)
        if treaty:
            result["treaty_status"] = truncate(" ".join(treaty[:3]), 1000)
        if not result and paras:
            result["overview"] = truncate(" ".join(paras[:3]), 1000)
        return result
    except Exception as exc:
        log.error("scraper ohchr failed: %s", exc)
        return {}


UNODA_BASE = "https://disarmament.unoda.org"

# Agenda keyword triggers -> topic page on the current UNODA site
# (un.org/disarmament/... now redirects here; the old paths are dead).
UNODA_TOPIC_PAGES = [
    (("nuclear", "npt", "non-proliferation", "nonproliferation", "fissile",
      "test ban"), "/en/our-work/weapons-mass-destruction/nuclear-weapons"),
    (("chemical", "wmd"), "/en/our-work/weapons-mass-destruction/chemical-weapons"),
    (("biological", "bioweapon"), "/en/our-work/weapons-mass-destruction/biological-weapons"),
    (("conventional", "small arms", "light weapons", "arms trade", "ammunition",
      "landmine", "cluster"), "/en/our-work/conventional-arms"),
    (("cyber", "information", "telecommunication"),
     "/en/our-work/emerging-challenges/developments-field-information-and-telecommunications-context"),
    (("autonomous",), "/en/our-work/emerging-challenges/lethal-autonomous-weapon-systems"),
    (("artificial intelligence",), "/en/our-work/emerging-challenges/artificial-intelligence-military-domain"),
    (("space",), "/en/our-work/emerging-challenges/outer-space"),
]


def scrape_unoda(country, agenda):
    """UNODA topic pages relevant to the agenda, plus country mentions."""
    try:
        agenda_l = (agenda or "").lower()
        urls = [UNODA_BASE + "/"]
        for triggers, path in UNODA_TOPIC_PAGES:
            if any(t in agenda_l for t in triggers):
                urls.append(UNODA_BASE + path)
        keywords = agenda_keywords(agenda)
        topic_chunks, mentions = [], []
        for url in urls:
            try:
                paras = paragraphs_of(soup_of(fetch(url)))
            except Exception as exc:
                log.warning("scraper unoda sub-page %s failed: %s", url, exc)
                continue
            relevant = [t for t in paras if any(k in t.lower() for k in keywords)]
            topic_chunks.extend(relevant[:4] or paras[:2])
            mentions.extend(t for t in paras if country.lower() in t.lower())
        result = {}
        if topic_chunks:
            result["topic_summary"] = truncate(" ".join(dict.fromkeys(topic_chunks)), 1600)
        if mentions:
            result["country_mentions"] = [truncate(m, 300) for m in dict.fromkeys(mentions)][:6]
        return result
    except Exception as exc:
        log.error("scraper unoda failed: %s", exc)
        return {}


def scrape_ecosoc(country, agenda):
    try:
        return harvest_links("https://www.un.org/ecosoc/en/", country, agenda,
                             "https://www.un.org")
    except Exception as exc:
        log.error("scraper ecosoc failed: %s", exc)
        return []


def scrape_undata(country, agenda):
    """World Development Indicators from the UNdata SDMX API (by ISO3).

    Only the series actually present in the DF_UNDATA_WDI dataflow are
    requested (plain GDP/population/literacy are not in it)."""
    series = {
        "NY_GDP_MKTP_PP_CD": "GDP, PPP (current intl $)",
        "NY_GDP_PCAP_PP_CD": "GDP per capita, PPP",
        "SI_POV_DDAY": "Poverty headcount ($2.15/day)",
        "SI_POV_GINI": "Gini index",
        "MS_MIL_XPND_GD_ZS": "Military expenditure (% of GDP)",
    }
    try:
        iso3 = country_codes(country)[1]
        if not iso3:
            log.error("scraper undata: no ISO3 code for %r", country)
            return {}
        url = (f"http://data.un.org/ws/rest/data/DF_UNDATA_WDI/"
               f"A.{'+'.join(series)}.{iso3}/")
        soup = BeautifulSoup(fetch(url, params={"lastNObservations": 1}).content,
                             "lxml-xml")
        stats = {}
        for node in soup.find_all("Series"):
            dims = {v.get("id"): v.get("value")
                    for v in node.find_all("Value") if v.get("id")}
            code = dims.get("SERIES") or dims.get("INDICATOR")
            label = series.get(code)
            if not label:
                continue
            obs = node.find("Obs")
            value_node = obs.find("ObsValue") if obs else None
            if value_node is None or not value_node.get("value"):
                continue
            time_node = obs.find("ObsDimension")
            stats[label] = _format_stat(code, float(value_node.get("value")),
                                        time_node.get("value") if time_node else "")
        return stats
    except Exception as exc:
        log.error("scraper undata failed: %s", exc)
        return {}


def _format_stat(code, value, year):
    suffix = f" ({year})" if year else ""
    if code == "NY_GDP_MKTP_PP_CD":
        if value >= 1e12:
            return f"${value / 1e12:.2f} trillion{suffix}"
        if value >= 1e9:
            return f"${value / 1e9:.1f} billion{suffix}"
        return f"${value / 1e6:.0f} million{suffix}"
    if code == "NY_GDP_PCAP_PP_CD":
        return f"${value:,.0f}{suffix}"
    if code == "SI_POV_GINI":
        return f"{value:.1f}{suffix}"
    return f"{value:.1f}%{suffix}"


def scrape_undesa(country, agenda):
    try:
        return harvest_links("https://www.un.org/development/desa/en/", country, agenda,
                             "https://www.un.org")
    except Exception as exc:
        log.error("scraper undesa failed: %s", exc)
        return []


def scrape_unctad(country, agenda):
    try:
        try:
            items = harvest_links(f"https://unctad.org/topic/{slugify(agenda)}",
                                  country, agenda, "https://unctad.org")
        except Exception:
            items = []
        if not items:
            items = harvest_links("https://unctad.org/search?query=" +
                                  requests.utils.quote(f"{country} {agenda}"),
                                  country, agenda, "https://unctad.org")
        return items
    except Exception as exc:
        log.error("scraper unctad failed: %s", exc)
        return []


def scrape_dppa(country, agenda):
    try:
        return harvest_links("https://dppa.un.org/en/", country, agenda,
                             "https://dppa.un.org")
    except Exception as exc:
        log.error("scraper dppa failed: %s", exc)
        return []


def scrape_un_treaty_collection(country, agenda):
    """Best-effort: the UNTC participation pages sit behind ASP.NET postbacks,
    so country-level status usually needs manual lookup. We surface the
    chapter list so the user knows where to look."""
    try:
        soup = soup_of(fetch("https://treaties.un.org/pages/ParticipationStatus.aspx"))
        chapters, seen = [], set()
        for el in soup.find_all(["a", "td", "span"]):
            text = clean(el.get_text())
            if len(text) < 10 or text in seen or not re.match(r"^CHAPTER\b", text, re.I):
                continue
            seen.add(text)
            chapters.append(truncate(text, 140))
            if len(chapters) >= 12:
                break
        if not chapters:
            return {}
        return {
            "note": (f"Country-level ratification status for {country} requires the "
                     "interactive UN Treaty Collection site (treaties.un.org)."),
            "treaty_chapters": chapters,
        }
    except Exception as exc:
        log.error("scraper un_treaty_collection failed: %s", exc)
        return {}


def scrape_unep(country, agenda):
    try:
        items = []
        region = unep_region(country)
        if region:
            try:
                items += harvest_links(f"https://www.unep.org/regions/{region}",
                                       country, agenda, "https://www.unep.org")
            except Exception as exc:
                log.warning("scraper unep region page failed: %s", exc)
        # UNEP's site search is JS-only; the topics index is the static fallback.
        try:
            items += harvest_links("https://www.unep.org/explore-topics",
                                   country, agenda, "https://www.unep.org")
        except Exception as exc:
            log.warning("scraper unep topics page failed: %s", exc)
        seen, unique = set(), []
        for item in items:
            if item["url"] not in seen:
                seen.add(item["url"])
                unique.append(item)
        return unique[:8]
    except Exception as exc:
        log.error("scraper unep failed: %s", exc)
        return []


def _stat_lines(paras, limit=6):
    """Pull 'Label: value'-shaped short lines containing numbers."""
    stats = {}
    for line in paras:
        if len(line) > 90 or not re.search(r"\d", line):
            continue
        m = re.match(r"^(.{10,60}?[a-z)])\s*[:\-–]\s*([\d][\d,.\s]*"
                     r"(?:%|years?|million|billion|per [\w\s]+)?)\s*$", line)
        if m:
            stats[clean(m.group(1))] = clean(m.group(2))
        if len(stats) >= limit:
            break
    return stats


def scrape_who(country, agenda):
    try:
        iso3 = country_codes(country)[1]
        if not iso3:
            return {}
        soup = soup_of(fetch(f"https://www.who.int/countries/{iso3.lower()}"))
        paras = paragraphs_of(soup, min_len=10)
        result = {}
        indicators = _stat_lines(paras)
        if indicators:
            result["indicators"] = indicators
        prose = [p for p in paras if len(p) > 80]
        if prose:
            result["profile"] = truncate(" ".join(prose[:4]), 1200)
        return result
    except Exception as exc:
        log.error("scraper who failed: %s", exc)
        return {}


def scrape_unesco(country, agenda):
    try:
        iso2 = country_codes(country)[0]
        slug = iso2.lower() if iso2 else slugify(country)
        soup = soup_of(fetch(f"https://www.unesco.org/en/countries/{slug}"))
        paras = paragraphs_of(soup)
        if not paras:
            return {}
        return {"overview": truncate(" ".join(paras[:5]), 1400)}
    except Exception as exc:
        log.error("scraper unesco failed: %s", exc)
        return {}


def scrape_unicef(country, agenda):
    try:
        slug = slugify(country)
        soup = None
        # NB: unicef.org 403s without the trailing slash.
        for url in (f"https://www.unicef.org/{slug}/",
                    f"https://www.unicef.org/where-we-work/{slug}"):
            try:
                soup = soup_of(fetch(url))
                break
            except Exception:
                continue
        if soup is None:
            log.error("scraper unicef: no country page for %r", country)
            return {}
        paras = paragraphs_of(soup, min_len=10)
        result = {}
        stats = _stat_lines(paras)
        if stats:
            result["key_stats"] = stats
        prose = [p for p in paras if len(p) > 80]
        if prose:
            result["overview"] = truncate(" ".join(prose[:4]), 1200)
        return result
    except Exception as exc:
        log.error("scraper unicef failed: %s", exc)
        return {}


def scrape_wfp(country, agenda):
    try:
        soup = soup_of(fetch(f"https://www.wfp.org/countries/{slugify(country)}"))
        paras = paragraphs_of(soup, min_len=10)
        result = {}
        stats = _stat_lines(paras)
        if stats:
            result["key_stats"] = stats
        prose = [p for p in paras if len(p) > 80]
        if prose:
            result["overview"] = truncate(" ".join(prose[:4]), 1200)
        return result
    except Exception as exc:
        log.error("scraper wfp failed: %s", exc)
        return {}


def scrape_undp(country, agenda):
    try:
        listing = soup_of(fetch("https://www.undp.org/about-us/where-we-work"))
        country_url = None
        for a in listing.find_all("a", href=True):
            if clean(a.get_text()).lower() == country.lower():
                country_url = absolutize(a["href"], "https://www.undp.org")
                break
        if not country_url:
            country_url = f"https://www.undp.org/{slugify(country)}"
        paras = paragraphs_of(soup_of(fetch(country_url)))
        if not paras:
            return {}
        result = {"overview": truncate(" ".join(paras[:4]), 1200)}
        hdi = [p for p in paras if "human development" in p.lower() or "hdi" in p.lower()]
        if hdi:
            result["hdi"] = truncate(hdi[0], 300)
        return result
    except Exception as exc:
        log.error("scraper undp failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

SCRAPERS = {
    "un_news": scrape_un_news,
    "security_council": scrape_security_council,
    "ohchr": scrape_ohchr,
    "unoda": scrape_unoda,
    "ecosoc": scrape_ecosoc,
    "undata": scrape_undata,
    "undesa": scrape_undesa,
    "unctad": scrape_unctad,
    "dppa": scrape_dppa,
    "un_treaty_collection": scrape_un_treaty_collection,
    "unep": scrape_unep,
    "who": scrape_who,
    "unesco": scrape_unesco,
    "unicef": scrape_unicef,
    "wfp": scrape_wfp,
    "undp": scrape_undp,
}


def sources_for(committee):
    return COMMITTEE_SOURCES.get(committee, COMMITTEE_SOURCES["DEFAULT"])


def _run_source(source, country, committee, agenda):
    if source == "un_digital_library":
        return scrape_un_digital_library(country, committee, agenda)
    return SCRAPERS[source](country, agenda)


def run_scrapers(country, committee, agenda, force=False):
    """Run (or load from cache) every source for the committee.

    Returns (results, empty_sources, last_updated):
      results       dict source -> data ({} / [] when nothing came back)
      empty_sources list of sources that produced nothing
      last_updated  unix timestamp of the freshest entry, or None
    """
    results, timestamps, to_fetch = {}, [], []
    for source in sources_for(committee):
        key = make_key(source, country, committee, agenda)
        cached = None if force else get_cached(key)
        if cached is not None:
            value, created = cached
            if not value and time.time() - created > EMPTY_RETRY_SECONDS:
                to_fetch.append(source)  # cached failure is stale; retry
            else:
                results[source] = value
                timestamps.append(created)
        else:
            to_fetch.append(source)

    if to_fetch:
        log.info("scraping %s for %s / %s / %s", to_fetch, country, committee, agenda)
        with ThreadPoolExecutor(max_workers=min(6, len(to_fetch))) as pool:
            futures = {
                pool.submit(_run_source, s, country, committee, agenda): s
                for s in to_fetch
            }
            for fut in as_completed(futures):
                source = futures[fut]
                try:
                    value = fut.result()
                except Exception as exc:  # scrapers catch internally; belt & braces
                    log.error("scraper %s raised: %s", source, exc)
                    value = {}
                results[source] = value
                timestamps.append(set_cached(
                    make_key(source, country, committee, agenda), value))

    empty_sources = [s for s, v in results.items() if not v]
    return results, empty_sources, (max(timestamps) if timestamps else None)
