"""
Geographic zone mapping for Saudi Arabian insurance offices.

Approach
--------
Office names in the source data are a mix of Arabic and English. Each name is a
sales point or branch; the city is embedded in the name (typically as a keyword).
We map keywords -> city -> region using standard Saudi geographic groupings, and
give every city its latitude / longitude for the map.

Offices whose name carries no city (online aggregators, e-commerce, telesales,
outlets, head-office units) take the company's default office city, [zones]
default_office_city in config.toml (the head office, Riyadh). LOCATION_BASIS says
which offices were placed that way, so every office has a zone and the report
can still show which ones were defaulted.

City keywords and coordinates follow the team's office-location specification
(Office_Latitude / Office_Longitude); cities that were already mapped here and are
not in it keep their mapping with their city-centre coordinates.
"""

import re
import unicodedata

# --------------------------------------------------------------------------
# City keyword -> (canonical city English, region)
# The keys are searched as substrings against the original office name (after
# whitespace normalization); a key with spaces around it matches a whole word only. Order matters — more specific keywords first.
# --------------------------------------------------------------------------
CITY_KEYWORDS = [
    # (keyword, city_en, region)  — Arabic keyword first
    ("الرياض",   "Riyadh",   "Central"),
    ("Riyadh",   "Riyadh",   "Central"),
    ("الخرج",    "Al Kharj", "Central"),
    ("بريدة",    "Buraidah", "Central"),
    ("Buraidah", "Buraidah", "Central"),
    ("عنيزة",    "Unayzah",  "Central"),
    ("عنيزه",    "Unayzah",  "Central"),
    ("القصيم",   "Al Qassim","Central"),
    ("حائل",     "Hail",     "Central"),
    ("القويعية", "Al Quwayiyah", "Central"),
    ("القويعيه", "Al Quwayiyah", "Central"),
    ("مجمعة",    "Al Majmaah",   "Central"),
    ("مجمعه",    "Al Majmaah",   "Central"),
    ("الدواسر",  "Wadi ad-Dawasir", "Central"),

    ("جدة",      "Jeddah",   "Western"),
    ("جده",      "Jeddah",   "Western"),   # spelling variant without ة
    ("Jeddah",   "Jeddah",   "Western"),
    ("وزيرية",   "Jeddah",   "Western"),   # Al-Wazeeriyah district, Jeddah
    ("وزيريه",   "Jeddah",   "Western"),
    ("القنفذة",  "Al Qunfudhah", "Western"),
    ("القنفذه",  "Al Qunfudhah", "Western"),
    ("مهد",      "Mahd",     "Western"),
    ("بدر",      "Badr",     "Western"),
    ("مكه",      "Mecca",    "Western"),
    ("مكة",      "Mecca",    "Western"),
    ("Mecca",    "Mecca",    "Western"),
    ("Makkah",   "Mecca",    "Western"),
    ("المدينة",  "Medina",   "Western"),
    ("المدينه",  "Medina",   "Western"),
    ("الحناكية", "Medina",   "Western"),   # Al Hanakiyah, Medina province
    ("الحناكيه", "Medina",   "Western"),
    ("Medina",   "Medina",   "Western"),
    ("الطائف",   "Taif",     "Western"),
    ("الحوية",   "Taif",     "Western"),   # Al Hawiyah, Taif
    ("الحويه",   "Taif",     "Western"),
    ("Taif",     "Taif",     "Western"),
    (" قيا ",    "Taif",     "Western"),   # Qiya centre, Taif governorate; whole word only
    ("ينبع",     "Yanbu",    "Western"),

    ("دمام",     "Dammam",   "Eastern"),
    ("Dammam",   "Dammam",   "Eastern"),
    ("الخبر",    "Khobar",   "Eastern"),
    ("Khobar",   "Khobar",   "Eastern"),
    ("الظهران",  "Dhahran",  "Eastern"),
    ("الأحساء",  "Al Ahsa",  "Eastern"),
    ("الاحساء",  "Al Ahsa",  "Eastern"),
    ("الأحسأء",  "Al Ahsa",  "Eastern"),   # misspelling seen in the source
    ("الجبيل",   "Jubail",   "Eastern"),
    ("القطيف",   "Qatif",    "Eastern"),
    ("حفر",      "Hafar Al Batin", "Eastern"),

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
    ("الباحه",   "Al Bahah", "Southern"),
    ("عسير",     "Abha",     "Southern"),
    ("بيشة",     "Bisha",    "Southern"),
    ("بيشه",     "Bisha",    "Southern"),
    ("صامطة",    "Samitah",  "Southern"),
    ("النماص",   "An Namas", "Southern"),
    ("رفيدة",    "Ahad Rafidah", "Southern"),   # Ahad Rufaidah governorate, Asir
    ("رفيده",    "Ahad Rafidah", "Southern"),
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

# City -> (latitude, longitude). From the team's office-location specification, except the
# cities marked "city centre", which were mapped here before and are not in it.
CITY_COORDS = {
    "Riyadh": (24.7136, 46.6753),
    "Jeddah": (21.4858, 39.1925),
    "Mecca": (21.3891, 39.8579),
    "Medina": (24.5247, 39.5692),
    "Dammam": (26.4207, 50.0888),
    "Qatif": (26.5205, 49.9737),
    "Al Ahsa": (25.3624, 49.5871),
    "Taif": (21.2703, 40.4158),
    "Buraidah": (26.3260, 43.9750),
    "Unayzah": (26.0842, 43.9850),
    "Hail": (27.5114, 41.6900),
    "Tabuk": (28.3838, 36.5550),
    "Najran": (17.4933, 44.1277),
    "Jazan": (16.8892, 42.5611),
    "Abha": (18.2164, 42.5053),
    "Khamis Mushait": (18.3000, 42.7333),
    "Al Bahah": (20.0129, 41.4677),
    "Sakaka": (29.9697, 40.2064),
    "Arar": (30.9756, 41.0378),
    "Yanbu": (24.0895, 38.0618),
    "Al Kharj": (24.1556, 47.3350),
    "Hafar Al Batin": (28.4342, 45.9601),
    "Sabya": (17.1493, 42.6248),
    "Samitah": (16.5939, 42.9436),
    "Bisha": (19.9821, 42.6002),
    "Qurayyat": (31.3318, 37.3417),
    "Al Quwayiyah": (24.0670, 45.2670),
    "Al Majmaah": (25.9040, 45.3430),
    "Badr": (23.7742, 38.7885),
    "Ar Rass": (25.8692, 43.4977),
    "An Namas": (19.1197, 42.1522),
    "Al Qunfudhah": (19.1264, 41.0783),
    "Wadi ad-Dawasir": (20.4708, 44.7896),
    "Ahad Rafidah": (18.2044, 42.8131),      # city centre
    # city centre
    "Al Qassim": (26.3260, 43.9750),          # Buraidah, the province capital
    "Mahd": (23.4986, 40.8614),               # Mahd adh Dhahab
    "Khobar": (26.2172, 50.1971),
    "Dhahran": (26.2361, 50.0393),
    "Jubail": (27.0046, 49.6460),
    "Al Jouf": (29.9697, 40.2064),            # Sakaka, the province capital
}
# Head-office units sit in Riyadh
for _c in ("Head Office (Riyadh)", "Call Center", "Support Unit", "Al Mayazin", "Tameenk (Digital)"):
    CITY_COORDS[_c] = CITY_COORDS["Riyadh"]

CITY_REGION = {city: region for _, city, region in CITY_KEYWORDS}


def _normalize(text: str) -> str:
    """Whitespace-normalize while preserving Arabic characters."""
    if text is None:
        return ""
    text = str(text)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _default_city() -> str:
    """[zones] default_office_city in config.toml: where an office with no city in its name sits."""
    from settings import S
    city = S.config.get("zones", {}).get("default_office_city", "Riyadh")
    if city not in CITY_REGION or city not in CITY_COORDS:
        raise SystemExit(f"[zones] default_office_city = '{city}' is not a city in pipeline/zone_mapping.py")
    return city


def _row(office_name, city, basis, city_label=None) -> dict:
    lat, lon = CITY_COORDS[city]
    region = CITY_REGION[city]
    return {
        "OFFICE_NAME": office_name,
        "CITY": city_label or city,
        "REGION": region,
        "ZONE": region,
        "LATITUDE": lat,
        "LONGITUDE": lon,
        "LOCATION_BASIS": basis,
    }


def map_office_to_zone(office_name: str) -> dict:
    """
    Return a dict with keys OFFICE_NAME, CITY, REGION, ZONE, LATITUDE, LONGITUDE, LOCATION_BASIS.
    Zone == region here (5-zone scheme). Every office gets a zone: a name with no city in it
    takes the default office city (LOCATION_BASIS = "Default office city").
    """
    normalized = _normalize(office_name)
    padded = f" {normalized} "   # lets a keyword written " x " match a whole word only
    for keyword, city, _ in CITY_KEYWORDS:
        if normalized and keyword in padded:
            return _row(office_name, city, "City in office name")

    city = _default_city()
    return _row(office_name, city, "Default office city", f"Head office / online ({city})")


def with_office_location(dim_zone):
    """Add LATITUDE, LONGITUDE and LOCATION_BASIS to a dim_zone frame (one row per OFFICE_NAME)."""
    import pandas as pd
    geo = pd.DataFrame([map_office_to_zone(o) for o in dim_zone["OFFICE_NAME"]],
                       columns=["OFFICE_NAME", "LATITUDE", "LONGITUDE", "LOCATION_BASIS"])
    return dim_zone.merge(geo.drop_duplicates("OFFICE_NAME"), on="OFFICE_NAME", how="left")
