"""Committee normalisation, country code lookups, slugs, text helpers."""

import re
import unicodedata

# ---------------------------------------------------------------------------
# Committees
# ---------------------------------------------------------------------------

# Must stay in sync with scraper.COMMITTEE_SOURCES (scraper asserts this on import).
KNOWN_COMMITTEES = [
    "UNSC", "UNHRC", "DISEC", "ECOSOC", "ECOFIN", "SOCHUM", "SPECPOL",
    "LEGAL", "UNEP", "WHO", "UNESCO", "UNICEF", "WFP", "UNDP",
]

# Alias (lowercase) -> committee key. Matched exactly first, then as substring.
COMMITTEE_ALIASES = {
    "security council": "UNSC",
    "un security council": "UNSC",
    "human rights council": "UNHRC",
    "human rights": "UNHRC",
    "disarmament and international security": "DISEC",
    "disarmament": "DISEC",
    "first committee": "DISEC",
    "economic and financial": "ECOFIN",
    "second committee": "ECOFIN",
    "social, humanitarian and cultural": "SOCHUM",
    "social humanitarian and cultural": "SOCHUM",
    "third committee": "SOCHUM",
    "special political and decolonization": "SPECPOL",
    "special political": "SPECPOL",
    "fourth committee": "SPECPOL",
    "sixth committee": "LEGAL",
    "legal committee": "LEGAL",
    "economic and social council": "ECOSOC",
    "environment programme": "UNEP",
    "environment": "UNEP",
    "world health organization": "WHO",
    "world health organisation": "WHO",
    "world health": "WHO",
    "world food programme": "WFP",
    "development programme": "UNDP",
    "children's fund": "UNICEF",
}


def normalise_committee(raw):
    """Map free-form committee input to a COMMITTEE_SOURCES key, or DEFAULT."""
    if not raw:
        return "DEFAULT"
    cleaned = " ".join(raw.strip().split())
    upper = cleaned.upper()
    if upper in KNOWN_COMMITTEES:
        return upper
    lower = cleaned.lower().replace("the ", "").strip()
    if lower in COMMITTEE_ALIASES:
        return COMMITTEE_ALIASES[lower]
    for alias, key in COMMITTEE_ALIASES.items():
        if alias in lower:
            return key
    return "DEFAULT"


# ---------------------------------------------------------------------------
# Countries — all 193 UN member states: name -> (ISO2, ISO3)
# ---------------------------------------------------------------------------

COUNTRIES = {
    "Afghanistan": ("AF", "AFG"), "Albania": ("AL", "ALB"), "Algeria": ("DZ", "DZA"),
    "Andorra": ("AD", "AND"), "Angola": ("AO", "AGO"), "Antigua and Barbuda": ("AG", "ATG"),
    "Argentina": ("AR", "ARG"), "Armenia": ("AM", "ARM"), "Australia": ("AU", "AUS"),
    "Austria": ("AT", "AUT"), "Azerbaijan": ("AZ", "AZE"), "Bahamas": ("BS", "BHS"),
    "Bahrain": ("BH", "BHR"), "Bangladesh": ("BD", "BGD"), "Barbados": ("BB", "BRB"),
    "Belarus": ("BY", "BLR"), "Belgium": ("BE", "BEL"), "Belize": ("BZ", "BLZ"),
    "Benin": ("BJ", "BEN"), "Bhutan": ("BT", "BTN"), "Bolivia": ("BO", "BOL"),
    "Bosnia and Herzegovina": ("BA", "BIH"), "Botswana": ("BW", "BWA"), "Brazil": ("BR", "BRA"),
    "Brunei Darussalam": ("BN", "BRN"), "Bulgaria": ("BG", "BGR"), "Burkina Faso": ("BF", "BFA"),
    "Burundi": ("BI", "BDI"), "Cabo Verde": ("CV", "CPV"), "Cambodia": ("KH", "KHM"),
    "Cameroon": ("CM", "CMR"), "Canada": ("CA", "CAN"), "Central African Republic": ("CF", "CAF"),
    "Chad": ("TD", "TCD"), "Chile": ("CL", "CHL"), "China": ("CN", "CHN"),
    "Colombia": ("CO", "COL"), "Comoros": ("KM", "COM"), "Congo": ("CG", "COG"),
    "Costa Rica": ("CR", "CRI"), "Côte d'Ivoire": ("CI", "CIV"), "Croatia": ("HR", "HRV"),
    "Cuba": ("CU", "CUB"), "Cyprus": ("CY", "CYP"), "Czechia": ("CZ", "CZE"),
    "Democratic People's Republic of Korea": ("KP", "PRK"),
    "Democratic Republic of the Congo": ("CD", "COD"), "Denmark": ("DK", "DNK"),
    "Djibouti": ("DJ", "DJI"), "Dominica": ("DM", "DMA"), "Dominican Republic": ("DO", "DOM"),
    "Ecuador": ("EC", "ECU"), "Egypt": ("EG", "EGY"), "El Salvador": ("SV", "SLV"),
    "Equatorial Guinea": ("GQ", "GNQ"), "Eritrea": ("ER", "ERI"), "Estonia": ("EE", "EST"),
    "Eswatini": ("SZ", "SWZ"), "Ethiopia": ("ET", "ETH"), "Fiji": ("FJ", "FJI"),
    "Finland": ("FI", "FIN"), "France": ("FR", "FRA"), "Gabon": ("GA", "GAB"),
    "Gambia": ("GM", "GMB"), "Georgia": ("GE", "GEO"), "Germany": ("DE", "DEU"),
    "Ghana": ("GH", "GHA"), "Greece": ("GR", "GRC"), "Grenada": ("GD", "GRD"),
    "Guatemala": ("GT", "GTM"), "Guinea": ("GN", "GIN"), "Guinea-Bissau": ("GW", "GNB"),
    "Guyana": ("GY", "GUY"), "Haiti": ("HT", "HTI"), "Honduras": ("HN", "HND"),
    "Hungary": ("HU", "HUN"), "Iceland": ("IS", "ISL"), "India": ("IN", "IND"),
    "Indonesia": ("ID", "IDN"), "Iran": ("IR", "IRN"), "Iraq": ("IQ", "IRQ"),
    "Ireland": ("IE", "IRL"), "Israel": ("IL", "ISR"), "Italy": ("IT", "ITA"),
    "Jamaica": ("JM", "JAM"), "Japan": ("JP", "JPN"), "Jordan": ("JO", "JOR"),
    "Kazakhstan": ("KZ", "KAZ"), "Kenya": ("KE", "KEN"), "Kiribati": ("KI", "KIR"),
    "Kuwait": ("KW", "KWT"), "Kyrgyzstan": ("KG", "KGZ"),
    "Lao People's Democratic Republic": ("LA", "LAO"), "Latvia": ("LV", "LVA"),
    "Lebanon": ("LB", "LBN"), "Lesotho": ("LS", "LSO"), "Liberia": ("LR", "LBR"),
    "Libya": ("LY", "LBY"), "Liechtenstein": ("LI", "LIE"), "Lithuania": ("LT", "LTU"),
    "Luxembourg": ("LU", "LUX"), "Madagascar": ("MG", "MDG"), "Malawi": ("MW", "MWI"),
    "Malaysia": ("MY", "MYS"), "Maldives": ("MV", "MDV"), "Mali": ("ML", "MLI"),
    "Malta": ("MT", "MLT"), "Marshall Islands": ("MH", "MHL"), "Mauritania": ("MR", "MRT"),
    "Mauritius": ("MU", "MUS"), "Mexico": ("MX", "MEX"), "Micronesia": ("FM", "FSM"),
    "Monaco": ("MC", "MCO"), "Mongolia": ("MN", "MNG"), "Montenegro": ("ME", "MNE"),
    "Morocco": ("MA", "MAR"), "Mozambique": ("MZ", "MOZ"), "Myanmar": ("MM", "MMR"),
    "Namibia": ("NA", "NAM"), "Nauru": ("NR", "NRU"), "Nepal": ("NP", "NPL"),
    "Netherlands": ("NL", "NLD"), "New Zealand": ("NZ", "NZL"), "Nicaragua": ("NI", "NIC"),
    "Niger": ("NE", "NER"), "Nigeria": ("NG", "NGA"), "North Macedonia": ("MK", "MKD"),
    "Norway": ("NO", "NOR"), "Oman": ("OM", "OMN"), "Pakistan": ("PK", "PAK"),
    "Palau": ("PW", "PLW"), "Panama": ("PA", "PAN"), "Papua New Guinea": ("PG", "PNG"),
    "Paraguay": ("PY", "PRY"), "Peru": ("PE", "PER"), "Philippines": ("PH", "PHL"),
    "Poland": ("PL", "POL"), "Portugal": ("PT", "PRT"), "Qatar": ("QA", "QAT"),
    "Republic of Korea": ("KR", "KOR"), "Republic of Moldova": ("MD", "MDA"),
    "Romania": ("RO", "ROU"), "Russian Federation": ("RU", "RUS"), "Rwanda": ("RW", "RWA"),
    "Saint Kitts and Nevis": ("KN", "KNA"), "Saint Lucia": ("LC", "LCA"),
    "Saint Vincent and the Grenadines": ("VC", "VCT"), "Samoa": ("WS", "WSM"),
    "San Marino": ("SM", "SMR"), "Sao Tome and Principe": ("ST", "STP"),
    "Saudi Arabia": ("SA", "SAU"), "Senegal": ("SN", "SEN"), "Serbia": ("RS", "SRB"),
    "Seychelles": ("SC", "SYC"), "Sierra Leone": ("SL", "SLE"), "Singapore": ("SG", "SGP"),
    "Slovakia": ("SK", "SVK"), "Slovenia": ("SI", "SVN"), "Solomon Islands": ("SB", "SLB"),
    "Somalia": ("SO", "SOM"), "South Africa": ("ZA", "ZAF"), "South Sudan": ("SS", "SSD"),
    "Spain": ("ES", "ESP"), "Sri Lanka": ("LK", "LKA"), "Sudan": ("SD", "SDN"),
    "Suriname": ("SR", "SUR"), "Sweden": ("SE", "SWE"), "Switzerland": ("CH", "CHE"),
    "Syrian Arab Republic": ("SY", "SYR"), "Tajikistan": ("TJ", "TJK"),
    "Thailand": ("TH", "THA"), "Timor-Leste": ("TL", "TLS"), "Togo": ("TG", "TGO"),
    "Tonga": ("TO", "TON"), "Trinidad and Tobago": ("TT", "TTO"), "Tunisia": ("TN", "TUN"),
    "Türkiye": ("TR", "TUR"), "Turkmenistan": ("TM", "TKM"), "Tuvalu": ("TV", "TUV"),
    "Uganda": ("UG", "UGA"), "Ukraine": ("UA", "UKR"), "United Arab Emirates": ("AE", "ARE"),
    "United Kingdom": ("GB", "GBR"), "United Republic of Tanzania": ("TZ", "TZA"),
    "United States": ("US", "USA"), "Uruguay": ("UY", "URY"), "Uzbekistan": ("UZ", "UZB"),
    "Vanuatu": ("VU", "VUT"), "Venezuela": ("VE", "VEN"), "Viet Nam": ("VN", "VNM"),
    "Yemen": ("YE", "YEM"), "Zambia": ("ZM", "ZMB"), "Zimbabwe": ("ZW", "ZWE"),
}

COUNTRY_ALIASES = {
    "usa": "United States", "united states of america": "United States",
    "america": "United States", "us": "United States",
    "uk": "United Kingdom", "great britain": "United Kingdom", "britain": "United Kingdom",
    "russia": "Russian Federation",
    "south korea": "Republic of Korea", "korea": "Republic of Korea",
    "north korea": "Democratic People's Republic of Korea",
    "dprk": "Democratic People's Republic of Korea",
    "iran (islamic republic of)": "Iran",
    "turkey": "Türkiye", "turkiye": "Türkiye",
    "vietnam": "Viet Nam",
    "tanzania": "United Republic of Tanzania",
    "moldova": "Republic of Moldova",
    "syria": "Syrian Arab Republic",
    "laos": "Lao People's Democratic Republic",
    "czech republic": "Czechia",
    "ivory coast": "Côte d'Ivoire", "cote d'ivoire": "Côte d'Ivoire",
    "cote divoire": "Côte d'Ivoire",
    "drc": "Democratic Republic of the Congo", "dr congo": "Democratic Republic of the Congo",
    "republic of the congo": "Congo", "congo-brazzaville": "Congo",
    "uae": "United Arab Emirates", "burma": "Myanmar", "cape verde": "Cabo Verde",
    "swaziland": "Eswatini", "macedonia": "North Macedonia", "east timor": "Timor-Leste",
    "brunei": "Brunei Darussalam",
    "micronesia (federated states of)": "Micronesia",
    "bolivia (plurinational state of)": "Bolivia",
    "venezuela (bolivarian republic of)": "Venezuela",
    "holland": "Netherlands",
}

_COUNTRIES_LOWER = {name.lower(): name for name in COUNTRIES}


def canonical_country(raw):
    """Return the canonical UN member-state name, or the cleaned input if unknown."""
    if not raw:
        return ""
    cleaned = " ".join(raw.strip().split())
    lower = cleaned.lower()
    if lower in _COUNTRIES_LOWER:
        return _COUNTRIES_LOWER[lower]
    if lower in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[lower]
    return cleaned


def country_codes(raw):
    """Return (ISO2, ISO3) for a country name/alias, or (None, None)."""
    return COUNTRIES.get(canonical_country(raw), (None, None))


def country_names():
    return sorted(COUNTRIES)


# ---------------------------------------------------------------------------
# UNEP regions (region slug used at unep.org/regions/{slug})
# ---------------------------------------------------------------------------

_UNEP_REGIONS = {
    "africa": {
        "DZ", "AO", "BJ", "BW", "BF", "BI", "CV", "CM", "CF", "TD", "KM", "CG",
        "CD", "CI", "DJ", "EG", "GQ", "ER", "SZ", "ET", "GA", "GM", "GH", "GN",
        "GW", "KE", "LS", "LR", "LY", "MG", "MW", "ML", "MR", "MU", "MA", "MZ",
        "NA", "NE", "NG", "RW", "ST", "SN", "SC", "SL", "SO", "ZA", "SS", "SD",
        "TZ", "TG", "TN", "UG", "ZM", "ZW",
    },
    "asia-and-pacific": {
        "AF", "AU", "BD", "BT", "BN", "KH", "CN", "FJ", "IN", "ID", "IR", "JP",
        "KZ", "KI", "KP", "KR", "KG", "LA", "MY", "MV", "MH", "FM", "MN", "MM",
        "NR", "NP", "NZ", "PK", "PW", "PG", "PH", "WS", "SG", "SB", "LK", "TJ",
        "TH", "TL", "TO", "TM", "TV", "UZ", "VU", "VN",
    },
    "europe": {
        "AL", "AD", "AM", "AT", "AZ", "BY", "BE", "BA", "BG", "HR", "CY", "CZ",
        "DK", "EE", "FI", "FR", "GE", "DE", "GR", "HU", "IS", "IE", "IT", "LV",
        "LI", "LT", "LU", "MT", "MC", "ME", "NL", "MK", "NO", "PL", "PT", "MD",
        "RO", "RU", "SM", "RS", "SK", "SI", "ES", "SE", "CH", "TR", "UA", "GB",
    },
    "latin-america-and-caribbean": {
        "AG", "AR", "BS", "BB", "BZ", "BO", "BR", "CL", "CO", "CR", "CU", "DM",
        "DO", "EC", "SV", "GD", "GT", "GY", "HT", "HN", "JM", "MX", "NI", "PA",
        "PY", "PE", "KN", "LC", "VC", "SR", "TT", "UY", "VE",
    },
    "north-america": {"CA", "US"},
    "west-asia": {"BH", "IQ", "IL", "JO", "KW", "LB", "OM", "QA", "SA", "SY", "AE", "YE"},
}


def unep_region(country):
    iso2 = country_codes(country)[0]
    if not iso2:
        return None
    for region, members in _UNEP_REGIONS.items():
        if iso2 in members:
            return region
    return None


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def slugify(text):
    """Lowercase ASCII slug: 'Côte d'Ivoire' -> 'cote-divoire'."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return text.strip("-")


def truncate(text, max_chars):
    """Truncate at a word boundary with an ellipsis."""
    if not text or len(text) <= max_chars:
        return text or ""
    cut = text[:max_chars].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.") + "…"
