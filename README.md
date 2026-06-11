# UNBrief

MUN country research portfolios, built automatically from real UN sources.

<img width="2063" height="1568" alt="image" src="https://github.com/user-attachments/assets/38d581b5-6eb0-447b-b8fb-5a851bb8ea48" />
<img width="975" height="1137" alt="image" src="https://github.com/user-attachments/assets/19a7831c-151f-43c0-889d-72a1594777b6" />


## What it does

Enter a **country**, a **committee**, and an **agenda**. UNBrief picks the UN
sources relevant to that committee, scrapes them, and renders a seven-tab
portfolio: overview briefing, recent UN News, documents & records, deep
research synthesis, grounded Q&A, notes, and a position paper draft.

**Honesty about the AI:** the briefing, deep research, Q&A and position paper
are written by Google Gemini (2.5 Flash by default — 2.0 Flash lost its free
tier; set `GEMINI_MODEL` in `.env` to change it) — but it is restricted to the scraped
UN source material passed to it and instructed to say when the sources are
insufficient rather than guess. It is still an LLM. Verify anything you plan
to say in committee.

Everything (scrapes and Gemini output) is cached in SQLite for 24 hours;
"Force Refresh" on the portfolio page clears the cache for that query.

## Setup

```
git clone <this repo>
cd unbrief
pip install -r requirements.txt
cp .env.example .env        # then put your Gemini key in .env
python app.py
```

Open http://127.0.0.1:5000.

Get a free Gemini API key at [aistudio.google.com](https://aistudio.google.com)
— free, no credit card.
-change the api key in .env

## Committee → sources

UN News and the UN Digital Library run for **every** committee. The rest:

| Committee | Extra sources |
|-----------|---------------|
| UNSC      | Security Council resolutions |
| UNHRC     | OHCHR country page |
| DISEC     | UNODA topic pages |
| ECOSOC    | ECOSOC site, UNdata (WDI stats) |
| ECOFIN    | UN DESA, UNCTAD |
| SOCHUM    | OHCHR, UNICEF |
| SPECPOL   | DPPA |
| LEGAL     | UN Treaty Collection |
| UNEP      | UNEP region pages + search |
| WHO       | WHO country page |
| UNESCO    | UNESCO country page |
| UNICEF    | UNICEF country page |
| WFP       | WFP country page |
| UNDP      | UNDP country page |
| (no match) | UNdata |

Committee input is normalised — "Security Council", "First Committee",
"human rights" etc. all map to the right key. Unknown committees fall back to
the DEFAULT source set.

## Known limitations

- Scrapers may break if UN sites restructure their HTML.
- UN News blocks server-side search, so news comes from the public RSS feed
  ranked by country/agenda keywords — recent items only.
- The UN Digital Library sits behind an AWS WAF JavaScript challenge; when it
  refuses us, the Documents tab falls back to UN press release listings.
- Gemini free tier is limited to 15 requests/minute — heavy Q&A use will hit it.
- Some committee scrapers are best-effort (the UN Treaty Collection in
  particular hides ratification status behind an interactive ASP.NET app).

## License

MIT
