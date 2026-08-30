"""
Geographic zone mapping for Saudi Arabian insurance offices.

Approach
--------
Office names in the source data are a mix of Arabic and English. Each name is a
sales point or branch; the city is embedded in the name (typically as a keyword).
We map keywords -> city -> region using standard Saudi geographic groupings.
Unmatched offices fall back to 'Unspecified'.

This is a synthetic mapping intended for DSS demonstration. It is defensible
because it uses the actual office names as the mapping basis, rather than random
assignment.
"""

import re
import unicodedata

# --------------------------------------------------------------------------
# City keyword -> (canonical city English, canonical city Arabic, region)
# The keys are searched as substrings against the original office name (after
# whitespace normalization). Order matters — more specific keywords first.
# --------------------------------------------------------------------------
CITY_KEYWORDS = [
    # (keyword, city_en, region)  — Arabic keyword first
    ("الرياض",   "Riyadh",   "Central"),
    ("Riyadh",   "Riyadh",   "Central"),
    ("الخرج",    "Al Kharj", "Central"),
    ("بريدة",    "Buraidah", "Central"),
    ("Buraidah", "Buraidah", "Central"),
    ("عنيزة",    "Unayzah",  "Central"),
    ("القصيم",   "Al Qassim","Central"),
    ("حائل",     "Hail",     "Central"),

    ("جدة",      "Jeddah",   "Western"),
    ("جده",      "Jeddah",   "Western"),   # spelling variant without ة
    ("Jeddah",   "Jeddah",   "Western"),
    ("القنفذة",  "Al Qunfudhah", "Western"),
    ("مهد",      "Mahd",     "Western"),
    ("بدر",      "Badr",     "Western"),
    ("مكه",      "Mecca",    "Western"),
    ("مكة",      "Mecca",    "Western"),
    ("Mecca",    "Mecca",    "Western"),
    ("Makkah",   "Mecca",    "Western"),
    ("المدينة",  "Medina",   "Western"),
    ("المدينه",  "Medina",   "Western"),
    ("Medina",   "Medina",   "Western"),
    ("الطائف",   "Taif",     "Western"),
    ("Taif",     "Taif",     "Western"),
    ("ينبع",     "Yanbu",    "Western"),

    ("دمام",     "Dammam",   "Eastern"),
    ("Dammam",   "Dammam",   "Eastern"),
    ("الخبر",    "Khobar",   "Eastern"),
    ("Khobar",   "Khobar",   "Eastern"),
    ("الظهران",  "Dhahran",  "Eastern"),
    ("الأحساء",  "Al Ahsa",  "Eastern"),
    ("الاحساء",  "Al Ahsa",  "Eastern"),
    ("الجبيل",   "Jubail",   "Eastern"),
    ("القطيف",   "Qatif",    "Eastern"),
    ("حفر",      "Hafar",    "Eastern"),

    ("تبوك",     "Tabuk",    "Northern"),
    ("Tabuk",    "Tabuk",    "Northern"),
    ("عرعر",     "Arar",     "Northern"),
    ("سكاكا",    "Sakaka",   "Northern"),
    ("الجوف",    "Al Jouf",  "Northern"),

    ("أبها",     "Abha",     "Southern"),
    ("ابها",     "Abha",     "Southern"),
    ("Abha",     "Abha",     "Southern"),
    ("خميس",     "Khamis Mushait", "Southern"),
    ("جيزان",    "Jazan",    "Southern"),
    ("جازان",    "Jazan",    "Southern"),
    ("Jazan",    "Jazan",    "Southern"),
    ("صبيا",     "Sabya",    "Southern"),
    ("نجران",    "Najran",   "Southern"),
    ("Najran",   "Najran",   "Southern"),
    ("الباحة",   "Al Bahah", "Southern"),
    ("عسير",     "Asir",     "Southern"),
    ("بيشة",     "Bisha",    "Southern"),
    ("صامطة",    "Samitah",  "Southern"),
    ("الرس",     "Ar Rass",  "Central"),
    ("قريات",    "Qurayyat", "Northern"),
    # Head office and administrative / non-geographic units, mapped to Central (HQ convention)
    ("المكتب الرئيسي", "Head Office (Riyadh)", "Central"),
    ("الرئيسي",  "Head Office (Riyadh)", "Central"),
    ("إعادة تواصل",   "Call Center",  "Central"),
    ("التعزيزات",     "Support Unit", "Central"),
    ("الميازين",      "Al Mayazin",   "Central"),
    # 'تأمينك' is a well-known Saudi motor product/brand — treat as central operations for zoning
    ("تأمينك",   "Tameenk (Digital)", "Central"),
]


def _normalize(text: str) -> str:
    """Whitespace-normalize while preserving Arabic characters."""
    if text is None:
        return ""
    text = str(text)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def map_office_to_zone(office_name: str) -> dict:
    """
    Return a dict with keys: office_original, city_en, region, zone.
    Zone == region here (5-zone scheme). Returns 'Unspecified' if no keyword matched.
    """
    normalized = _normalize(office_name)
    if not normalized:
        return {
            "OFFICE_NAME": office_name,
            "CITY": "Unspecified",
            "REGION": "Unspecified",
            "ZONE": "Unspecified",
        }

    # Explicit 'unspecified' markers seen in the source
    if normalized in {"غير محدد", "Unknown", "Not specified", "Not Specified"}:
        return {
            "OFFICE_NAME": office_name,
            "CITY": "Unspecified",
            "REGION": "Unspecified",
            "ZONE": "Unspecified",
        }

    for keyword, city, region in CITY_KEYWORDS:
        if keyword in normalized:
            return {
                "OFFICE_NAME": office_name,
                "CITY": city,
                "REGION": region,
                "ZONE": region,
            }

    return {
        "OFFICE_NAME": office_name,
        "CITY": "Other",
        "REGION": "Other",
        "ZONE": "Other",
    }
