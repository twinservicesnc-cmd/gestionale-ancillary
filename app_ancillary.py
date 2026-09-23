import io
import json
import re
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st


APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "ancillary.db"
SEED_PATH = APP_DIR / "dati_iniziali.json"
CONTRACT_SEED_PATH = APP_DIR / "contratti_iniziali.json"
DAMAGE_SEED_PATH = APP_DIR / "danni_iniziali.json"
ANCILLARY_START_DATE = date(2026, 10, 1)

st.set_page_config(page_title="Gestionale Ancillary", page_icon="🚗", layout="wide")


def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rentals (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                ra TEXT NOT NULL,
                start_date TEXT,
                rental_days INTEGER NOT NULL DEFAULT 0,
                source_raw TEXT,
                source_base TEXT,
                source_details TEXT,
                source_year TEXT,
                source_km TEXT,
                operator TEXT,
                vehicle_type TEXT,
                ancillary TEXT,
                ancillary_cost REAL,
                rental_type TEXT,
                notes TEXT
            )
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM rentals").fetchone()[0]
        if count == 0 and SEED_PATH.exists():
            rows = json.loads(SEED_PATH.read_text(encoding="utf-8"))
            conn.executemany(
                """
                INSERT INTO rentals VALUES (
                    :id,:created_at,:ra,:start_date,:rental_days,:source_raw,
                    :source_base,:source_details,:source_year,:source_km,
                    :operator,:vehicle_type,:ancillary,:ancillary_cost,
                    :rental_type,:notes
                )
                """,
                rows,
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contracts (
                id TEXT PRIMARY KEY,
                import_file TEXT,
                import_period TEXT,
                contract_date TEXT,
                prefix TEXT,
                number INTEGER,
                ra TEXT NOT NULL,
                start_date TEXT,
                end_date TEXT,
                requested_group TEXT,
                assigned_group TEXT,
                source_raw TEXT,
                source_base TEXT,
                source_details TEXT,
                source_year TEXT,
                source_km TEXT,
                client TEXT,
                duration_days INTEGER,
                kpi1_rpd REAL,
                contract_value REAL,
                kpi_target REAL,
                ancillary_value REAL,
                ancillary_rpd REAL,
                operator TEXT,
                UNIQUE(ra, contract_date)
            )
            """
        )
        contracts_schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'contracts'"
        ).fetchone()[0]
        if "RA TEXT NOT NULL UNIQUE" in upper(contracts_schema):
            conn.execute("ALTER TABLE contracts RENAME TO contracts_legacy_ra_unique")
            conn.execute(
                """
                CREATE TABLE contracts (
                    id TEXT PRIMARY KEY, import_file TEXT, import_period TEXT,
                    contract_date TEXT, prefix TEXT, number INTEGER,
                    ra TEXT NOT NULL, start_date TEXT, end_date TEXT,
                    requested_group TEXT, assigned_group TEXT, source_raw TEXT,
                    source_base TEXT, source_details TEXT, source_year TEXT,
                    source_km TEXT, client TEXT, duration_days INTEGER,
                    kpi1_rpd REAL, contract_value REAL, kpi_target REAL,
                    ancillary_value REAL, ancillary_rpd REAL, operator TEXT,
                    UNIQUE(ra, contract_date)
                )
                """
            )
            conn.execute("INSERT INTO contracts SELECT * FROM contracts_legacy_ra_unique")
            conn.execute("DROP TABLE contracts_legacy_ra_unique")
        contracts_count = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
        if contracts_count == 0 and CONTRACT_SEED_PATH.exists():
            contracts = json.loads(CONTRACT_SEED_PATH.read_text(encoding="utf-8"))
            conn.executemany(
                """
                INSERT INTO contracts VALUES (
                    :id,:import_file,:import_period,:contract_date,:prefix,:number,
                    :ra,:start_date,:end_date,:requested_group,:assigned_group,
                    :source_raw,:source_base,:source_details,:source_year,:source_km,
                    :client,:duration_days,:kpi1_rpd,:contract_value,:kpi_target,
                    :ancillary_value,:ancillary_rpd,:operator
                )
                """,
                contracts,
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS operator_config (
                name TEXT PRIMARY KEY,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS damage_charges (
                id TEXT PRIMARY KEY,
                source_key TEXT NOT NULL UNIQUE,
                submitted_at TEXT,
                control_date TEXT,
                ra TEXT NOT NULL,
                vehicle_category TEXT,
                charge_mode TEXT,
                operator TEXT,
                description TEXT,
                photo_url TEXT,
                amount REAL,
                payment_status TEXT,
                notes TEXT
            )
            """
        )
        damage_count = conn.execute("SELECT COUNT(*) FROM damage_charges").fetchone()[0]
        if damage_count == 0 and DAMAGE_SEED_PATH.exists():
            damages = json.loads(DAMAGE_SEED_PATH.read_text(encoding="utf-8"))
            conn.executemany(
                """
                INSERT OR IGNORE INTO damage_charges VALUES (
                    :id,:source_key,:submitted_at,:control_date,:ra,
                    :vehicle_category,:charge_mode,:operator,:description,
                    :photo_url,:amount,:payment_status,:notes
                )
                """,
                damages,
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        cleanup_done = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'ancillary_history_cleared_2026_10_01'"
        ).fetchone()
        if cleanup_done is None:
            conn.execute("DELETE FROM rentals")
            conn.execute(
                "INSERT INTO app_meta (key, value) VALUES (?, ?)",
                ("ancillary_history_cleared_2026_10_01", datetime.now().isoformat(timespec="seconds")),
            )
        operator_count = conn.execute("SELECT COUNT(*) FROM operator_config").fetchone()[0]
        if operator_count == 0:
            existing_operators = conn.execute(
                """
                SELECT DISTINCT TRIM(operator) AS name FROM rentals
                WHERE TRIM(COALESCE(operator, '')) <> ''
                UNION
                SELECT DISTINCT TRIM(operator) AS name FROM contracts
                WHERE TRIM(COALESCE(operator, '')) <> ''
                UNION
                SELECT DISTINCT TRIM(operator) AS name FROM damage_charges
                WHERE TRIM(COALESCE(operator, '')) <> ''
                """
            ).fetchall()
            conn.executemany(
                "INSERT OR IGNORE INTO operator_config (name, active) VALUES (?, 1)",
                [(upper(row[0]),) for row in existing_operators if upper(row[0])],
            )


def clean(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return re.sub(r"\s+", " ", str(value or "").strip())


def upper(value):
    return clean(value).upper()


def configured_operators(active_only=True):
    query = "SELECT name, active FROM operator_config"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY name"
    with db() as conn:
        return [dict(row) for row in conn.execute(query).fetchall()]


def normalize_ra(value):
    text = upper(value).replace(".", "").replace(" ", "").replace("_", "-")
    match = re.search(r"(?:TOR)?-?(\d+)", text)
    return f"TOR-{match.group(1)}" if match else text


def parse_source(value):
    text = upper(value)
    for wrong, right in {
        "TRAVELJGSAW": "TRAVELJIGSAW",
        "WALK IN": "WALK-IN",
        "ILLIMITATI": "UNLIMITED",
    }.items():
        text = text.replace(wrong, right)
    year_match = re.search(r"\b(20\d{2})\b", text)
    km_match = re.search(r"\b(\d{2,5})\s*KM\b", text)
    known = [
        "OFFICIAL BOOKING WEB", "TRAVELJIGSAW", "TRAVELDRIVE",
        "VIPCARS", "DOYOUSPAIN", "WALK-IN", "RECEPTION",
        "POSTEGO", "POSTE", "WEB",
    ]
    base = next((item for item in known if text.startswith(item)), "")
    if not base:
        marker = re.search(r"\s+(?:AUTO|VAN|CAR)\b", text)
        base = text[: marker.start()].strip() if marker else text
    details = text[len(base):].strip(" -")
    return {
        "source_raw": text,
        "source_base": base,
        "source_details": details,
        "source_year": year_match.group(1) if year_match else "",
        "source_km": km_match.group(1) if km_match else "",
    }


def classify_contract_channel(source_raw):
    text = upper(source_raw)
    if "REPLACEMENT" in text or "WARRANTY" in text:
        return "REPLACEMENT"
    if "WALK-IN" in text or "WALK IN" in text or text.startswith("RECEPTION"):
        return "WALK-IN"
    if "OFFICIAL BOOKING WEB" in text or re.search(r"\b(?:WEB|CNP)\b", text):
        return "WEB/CNP"
    broker_markers = (
        "TRAVELJIGSAW", "TRAVELDRIVE", "DOYOUSPAIN", "VIPCARS",
        "BOOKING GROUP", "TINOLEGGIO", "XML", "POA",
    )
    if any(marker in text for marker in broker_markers):
        return "BROKER"
    return "CORPORATE"


def normalize_rental_type(value):
    text = upper(value).replace("WEB-CNP-ETC", "WEB - CNP ETC").replace("CORPARATE", "CORPORATE")
    parts = []
    for item in text.split(","):
        item = clean(item)
        if item and item not in parts:
            parts.append(item)
    return ", ".join(parts)


def save_record(record):
    source = parse_source(record["source_raw"])
    payload = {
        "id": record.get("id") or str(uuid.uuid4()),
        "created_at": record.get("created_at") or datetime.now().isoformat(timespec="seconds"),
        "ra": normalize_ra(record["ra"]),
        "start_date": str(record.get("start_date") or ""),
        "rental_days": int(record.get("rental_days") or 0),
        **source,
        "operator": upper(record.get("operator")),
        "vehicle_type": upper(record.get("vehicle_type")),
        "ancillary": upper(record.get("ancillary")).replace("PRESNETE", "PRESENTE"),
        "ancillary_cost": record.get("ancillary_cost"),
        "rental_type": normalize_rental_type(record.get("rental_type")),
        "notes": clean(record.get("notes")),
    }
    with db() as conn:
        conn.execute(
            """
            INSERT INTO rentals VALUES (
                :id,:created_at,:ra,:start_date,:rental_days,:source_raw,
                :source_base,:source_details,:source_year,:source_km,
                :operator,:vehicle_type,:ancillary,:ancillary_cost,
                :rental_type,:notes
            )
            ON CONFLICT(id) DO UPDATE SET
                created_at=:created_at, ra=:ra, start_date=:start_date,
                rental_days=:rental_days, source_raw=:source_raw,
                source_base=:source_base, source_details=:source_details,
                source_year=:source_year, source_km=:source_km,
                operator=:operator, vehicle_type=:vehicle_type,
                ancillary=:ancillary, ancillary_cost=:ancillary_cost,
                rental_type=:rental_type, notes=:notes
            """,
            payload,
        )


def load_data():
    with db() as conn:
        frame = pd.read_sql_query("SELECT * FROM rentals ORDER BY start_date DESC, created_at DESC", conn)
    if frame.empty:
        return frame
    frame["start_date"] = pd.to_datetime(frame["start_date"], errors="coerce")
    frame["rental_days"] = pd.to_numeric(frame["rental_days"], errors="coerce").fillna(0).astype(int)
    frame["ancillary_cost"] = pd.to_numeric(frame["ancillary_cost"], errors="coerce")
    frame["rpd"] = frame["ancillary_cost"].div(frame["rental_days"].replace(0, pd.NA))
    frame["has_ancillary"] = frame["ancillary"].fillna("").str.strip().ne("") & frame["ancillary_cost"].fillna(0).gt(0)
    return frame


def classify_damage(description):
    text = upper(description)
    categories = [
        ("PARAURTI", "PARAURTI"), ("PARABREZZA", "PARABREZZA"),
        ("PARAFANGO", "PARAFANGO"), ("PORTA", "PORTA/PORTIERA"),
        ("PORTIER", "PORTA/PORTIERA"), ("FIANC", "FIANCATA"),
        ("SPECCHI", "SPECCHIETTO"), ("CERCH", "CERCHIO/PNEUMATICO"),
        ("PNEUM", "CERCHIO/PNEUMATICO"), ("FARO", "FARO/FANALE"),
        ("FANAL", "FARO/FANALE"), ("TETTO", "TETTO"),
        ("INTERN", "INTERNI"), ("BOLLO", "AMMACCATURA/BOLLO"),
        ("AMMAC", "AMMACCATURA/BOLLO"),
    ]
    return next((label for marker, label in categories if marker in text), "ALTRO/DA CLASSIFICARE")


def save_damage(record):
    record_id = record.get("id") or str(uuid.uuid4())
    submitted_at = record.get("submitted_at") or datetime.now().isoformat(timespec="seconds")
    source_key = clean(record.get("source_key")) or record_id
    payload = {
        "id": record_id,
        "source_key": source_key,
        "submitted_at": submitted_at,
        "control_date": clean(record.get("control_date")),
        "ra": normalize_ra(record.get("ra")),
        "vehicle_category": upper(record.get("vehicle_category")),
        "charge_mode": upper(record.get("charge_mode")),
        "operator": upper(record.get("operator")),
        "description": upper(record.get("description")),
        "photo_url": clean(record.get("photo_url")),
        "amount": record.get("amount"),
        "payment_status": upper(record.get("payment_status")) or "DA VERIFICARE",
        "notes": clean(record.get("notes")),
    }
    with db() as conn:
        conn.execute(
            """
            INSERT INTO damage_charges VALUES (
                :id,:source_key,:submitted_at,:control_date,:ra,
                :vehicle_category,:charge_mode,:operator,:description,
                :photo_url,:amount,:payment_status,:notes
            )
            ON CONFLICT(source_key) DO UPDATE SET
                submitted_at=:submitted_at, control_date=:control_date, ra=:ra,
                vehicle_category=:vehicle_category, charge_mode=:charge_mode,
                operator=:operator, description=:description, photo_url=:photo_url,
                amount=:amount, payment_status=:payment_status, notes=:notes
            """,
            payload,
        )


def load_damages():
    with db() as conn:
        frame = pd.read_sql_query(
            "SELECT * FROM damage_charges ORDER BY control_date DESC, submitted_at DESC", conn
        )
    if frame.empty:
        return frame
    frame["submitted_at"] = pd.to_datetime(frame["submitted_at"], errors="coerce")
    frame["control_date"] = pd.to_datetime(frame["control_date"], errors="coerce")
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    frame["damage_category"] = frame["description"].map(classify_damage)
    frame["has_photo"] = frame["photo_url"].fillna("").str.strip().ne("")
    return frame


def load_contracts():
    with db() as conn:
        frame = pd.read_sql_query("SELECT * FROM contracts ORDER BY contract_date DESC, ra DESC", conn)
    if frame.empty:
        return frame
    for column in ["contract_date", "start_date", "end_date"]:
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
    for column in ["duration_days", "kpi1_rpd", "contract_value", "kpi_target", "ancillary_value", "ancillary_rpd"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["has_ancillary_ra"] = frame["ancillary_value"].fillna(0).gt(0)
    frame["group_changed"] = frame["requested_group"].fillna("").ne(frame["assigned_group"].fillna(""))
    frame["contract_vehicle"] = frame["assigned_group"].fillna("").map(
        lambda value: "VAN" if upper(value).startswith("Z") else "CAR"
    )
    frame["rental_term"] = frame.apply(
        lambda row: "MENSILE"
        if "MENSILE" in upper(row["source_raw"]) or (row["duration_days"] or 0) >= 28
        else "GIORNALIERO",
        axis=1,
    )
    frame["rental_channel"] = frame["source_raw"].map(classify_contract_channel)
    return frame


def _find_header_row(raw):
    for index in range(min(25, len(raw))):
        values = {clean(value) for value in raw.iloc[index].dropna().tolist()}
        if {"Data contratto", "Prefisso", "Numero"}.issubset(values):
            return index
    return None


def detect_excel_type(file_bytes):
    book = pd.ExcelFile(io.BytesIO(file_bytes))
    for sheet in book.sheet_names:
        raw = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet, header=None, nrows=25, dtype=object)
        if _find_header_row(raw) is not None:
            return "CONTRATTI_RA"
    first = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, nrows=5, dtype=object)
    damage_columns = {
        "Informazioni cronologiche", "Numero RA (Rental Agreement)",
        "Categoria Veicolo", "Modalità di addebito", "Operatore responsabile",
    }
    if damage_columns.issubset(first.columns):
        return "ADDEBITO_DANNI"
    if {"RA (Rental Agreement)", "DATA INIZIO NOLEGGIO", "GIORNI NOLEGGIO", "FONTE"}.issubset(first.columns):
        return "ANCILLARY"
    return "SCONOSCIUTO"


def import_contract_workbook(file_bytes, file_name):
    book = pd.ExcelFile(io.BytesIO(file_bytes))
    parsed = {}
    for sheet in book.sheet_names:
        raw = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet, header=None, nrows=25, dtype=object)
        header = _find_header_row(raw)
        if header is None:
            continue
        frame = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet, header=header, dtype=object)
        frame = frame[frame["Numero"].notna()].copy()
        if not frame.empty:
            parsed[sheet] = frame
    if not parsed:
        raise ValueError("Nessun foglio RA riconosciuto.")
    site_name = max(parsed, key=lambda name: len(parsed[name]))
    site = parsed[site_name].copy()

    def ra_from_row(row):
        number = pd.to_numeric(row.get("Numero"), errors="coerce")
        if pd.isna(number):
            return ""
        return f"{upper(row.get('Prefisso'))}-{int(number)}"

    operator_map = {}
    for sheet, frame in parsed.items():
        if sheet == site_name or sheet.upper() == "TOTALE":
            continue
        for _, row in frame.iterrows():
            operator_map[ra_from_row(row)] = clean(sheet)

    contract_dates = pd.to_datetime(site["Data contratto"], errors="coerce")
    valid_dates = contract_dates.dropna()
    period = valid_dates.min().strftime("%Y-%m") if not valid_dates.empty else ""
    imported = 0
    with db() as conn:
        for _, row in site.iterrows():
            ra = ra_from_row(row)
            if not ra:
                continue
            source = parse_source(row.get("Fonte commissione"))
            number = pd.to_numeric(row.get("Numero"), errors="coerce")

            def number_value(column):
                value = pd.to_numeric(row.get(column), errors="coerce")
                return None if pd.isna(value) else float(value)

            def date_value(column):
                value = pd.to_datetime(row.get(column), errors="coerce")
                return "" if pd.isna(value) else value.date().isoformat()

            duration = number_value("Durata (gg)") or 0
            payload = {
                "id": str(uuid.uuid4()), "import_file": file_name, "import_period": period,
                "contract_date": date_value("Data contratto"), "prefix": upper(row.get("Prefisso")),
                "number": int(number), "ra": ra, "start_date": date_value("Data inizio contratto"),
                "end_date": date_value("Data fine contratto"), "requested_group": upper(row.get("Gruppo richiesto")),
                "assigned_group": upper(row.get("Gruppo assegnato")), **source,
                "client": clean(row.get("Cliente")), "duration_days": int(round(duration)),
                "kpi1_rpd": number_value("KPI 1"), "contract_value": number_value("Valore contratto 1"),
                "kpi_target": number_value("KPI 1 Ob."), "ancillary_value": number_value("Valore contratto 2"),
                "ancillary_rpd": number_value("RpD 2"), "operator": operator_map.get(ra, "NON ASSEGNATO"),
            }
            conn.execute(
                """
                INSERT INTO contracts VALUES (
                    :id,:import_file,:import_period,:contract_date,:prefix,:number,
                    :ra,:start_date,:end_date,:requested_group,:assigned_group,
                    :source_raw,:source_base,:source_details,:source_year,:source_km,
                    :client,:duration_days,:kpi1_rpd,:contract_value,:kpi_target,
                    :ancillary_value,:ancillary_rpd,:operator
                )
                ON CONFLICT(ra, contract_date) DO UPDATE SET
                    import_file=:import_file, import_period=:import_period,
                    contract_date=:contract_date, prefix=:prefix, number=:number,
                    start_date=:start_date, end_date=:end_date,
                    requested_group=:requested_group, assigned_group=:assigned_group,
                    source_raw=:source_raw, source_base=:source_base,
                    source_details=:source_details, source_year=:source_year,
                    source_km=:source_km, client=:client, duration_days=:duration_days,
                    kpi1_rpd=:kpi1_rpd, contract_value=:contract_value,
                    kpi_target=:kpi_target, ancillary_value=:ancillary_value,
                    ancillary_rpd=:ancillary_rpd, operator=:operator
                """,
                payload,
            )
            imported += 1
    return imported, site_name, period


def import_damage_workbook(file_bytes, file_name):
    book = pd.ExcelFile(io.BytesIO(file_bytes))
    imported = 0
    skipped = 0
    for sheet in book.sheet_names:
        frame = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet, dtype=object)
        required = {
            "Informazioni cronologiche", "Numero RA (Rental Agreement)",
            "Categoria Veicolo", "Modalità di addebito", "Operatore responsabile",
        }
        if not required.issubset(frame.columns):
            continue
        for _, row in frame.iterrows():
            ra = normalize_ra(row.get("Numero RA (Rental Agreement)"))
            if not ra:
                skipped += 1
                continue
            submitted = pd.to_datetime(row.get("Informazioni cronologiche"), errors="coerce")
            control = pd.to_datetime(row.get("Data del controllo"), errors="coerce")
            submitted_text = "" if pd.isna(submitted) else submitted.isoformat()
            source_key = f"{submitted_text}|{ra}|{sheet}"
            save_damage({
                "source_key": source_key,
                "submitted_at": submitted_text,
                "control_date": "" if pd.isna(control) else control.date().isoformat(),
                "ra": ra,
                "vehicle_category": row.get("Categoria Veicolo"),
                "charge_mode": row.get("Modalità di addebito"),
                "operator": row.get("Operatore responsabile"),
                "description": row.get("Descrizione sintetica del danno"),
                "photo_url": row.get("Caricamento foto danno (opzionale)"),
                "amount": None,
                "payment_status": "DA VERIFICARE",
                "notes": f"Importato da {file_name} - foglio {sheet}",
            })
            imported += 1
    return imported, skipped


def to_excel(frame):
    output = io.BytesIO()
    export = frame.copy()
    export["DATA INIZIO NOLEGGIO"] = export["start_date"].dt.date
    export = export.rename(columns={
        "ra": "RA", "rental_days": "GIORNI NOLEGGIO", "source_raw": "FONTE COMPLETA",
        "source_base": "FONTE PRINCIPALE", "source_details": "DETTAGLIO FONTE",
        "source_year": "ANNO FONTE", "source_km": "KM FONTE", "operator": "OPERATORE",
        "vehicle_type": "TIPO VEICOLO", "ancillary": "ANCILLARY",
        "ancillary_cost": "COSTO ANCILLARY IVA ESCLUSA", "rpd": "RPD",
        "rental_type": "TIPO NOLEGGIO", "notes": "NOTE",
    })
    cols = [
        "RA", "DATA INIZIO NOLEGGIO", "GIORNI NOLEGGIO", "FONTE COMPLETA",
        "FONTE PRINCIPALE", "DETTAGLIO FONTE", "ANNO FONTE", "KM FONTE",
        "OPERATORE", "TIPO VEICOLO", "ANCILLARY", "COSTO ANCILLARY IVA ESCLUSA",
        "RPD", "TIPO NOLEGGIO", "NOTE",
    ]
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        export[cols].to_excel(writer, index=False, sheet_name="Dati ancillary")
    return output.getvalue()


def filters(frame):
    st.sidebar.markdown("### Filtri")
    years = sorted([int(x) for x in frame["start_date"].dt.year.dropna().unique()])
    selected_years = st.sidebar.multiselect("Anno", years, default=years)
    operators = sorted(x for x in frame["operator"].dropna().unique() if x)
    selected_operators = st.sidebar.multiselect("Operatore", operators)
    vehicles = sorted(x for x in frame["vehicle_type"].dropna().unique() if x)
    selected_vehicles = st.sidebar.multiselect("Veicolo", vehicles)
    sources = sorted(x for x in frame["source_base"].dropna().unique() if x)
    selected_sources = st.sidebar.multiselect("Fonte", sources)
    search = st.sidebar.text_input("Cerca RA, fonte o dettaglio")
    result = frame.copy()
    if selected_years:
        result = result[result["start_date"].dt.year.isin(selected_years)]
    if selected_operators:
        result = result[result["operator"].isin(selected_operators)]
    if selected_vehicles:
        result = result[result["vehicle_type"].isin(selected_vehicles)]
    if selected_sources:
        result = result[result["source_base"].isin(selected_sources)]
    if search:
        mask = result[["ra", "source_raw", "source_details", "operator"]].fillna("").apply(
            lambda col: col.str.contains(search, case=False, regex=False)
        ).any(axis=1)
        result = result[mask]
    return result


def metric_row(frame):
    rentals = len(frame)
    ancillary_count = int(frame["has_ancillary"].sum()) if rentals else 0
    revenue = float(frame["ancillary_cost"].fillna(0).sum()) if rentals else 0
    conversion = ancillary_count / rentals if rentals else 0
    average_ticket = revenue / ancillary_count if ancillary_count else 0
    average_rpd = float(frame.loc[frame["has_ancillary"], "rpd"].mean() or 0) if ancillary_count else 0
    cols = st.columns(6)
    cols[0].metric("Noleggi", f"{rentals:,}".replace(",", "."))
    cols[1].metric("Con ancillary", f"{ancillary_count:,}".replace(",", "."))
    cols[2].metric("Conversione", f"{conversion:.1%}")
    cols[3].metric("Ricavi ancillary", f"€ {revenue:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    cols[4].metric("Ticket medio", f"€ {average_ticket:.2f}".replace(".", ","))
    cols[5].metric("RPD medio", f"€ {average_rpd:.2f}".replace(".", ","))


def dashboard(frame):
    st.header("Dashboard ancillary")
    metric_row(frame)
    if frame.empty:
        st.info("Nessun dato corrisponde ai filtri selezionati.")
        return
    monthly = frame.dropna(subset=["start_date"]).copy()
    monthly["mese"] = monthly["start_date"].dt.to_period("M").astype(str)
    monthly = monthly.groupby("mese", as_index=False).agg(
        noleggi=("id", "count"), ricavi=("ancillary_cost", "sum"), conversione=("has_ancillary", "mean")
    )
    c1, c2 = st.columns(2)
    c1.plotly_chart(px.line(monthly, x="mese", y="ricavi", markers=True, title="Ricavi ancillary per mese"), use_container_width=True)
    by_operator = frame.groupby("operator", as_index=False).agg(
        noleggi=("id", "count"), ricavi=("ancillary_cost", "sum"), conversione=("has_ancillary", "mean")
    ).sort_values("ricavi", ascending=False)
    c2.plotly_chart(px.bar(by_operator, x="operator", y="ricavi", color="conversione", title="Ricavi e conversione per operatore"), use_container_width=True)
    c3, c4 = st.columns(2)
    by_source = frame.groupby("source_base", as_index=False).agg(
        noleggi=("id", "count"), ricavi=("ancillary_cost", "sum")
    ).sort_values("ricavi", ascending=False).head(15)
    c3.plotly_chart(px.bar(by_source, x="ricavi", y="source_base", orientation="h", title="Prime 15 fonti per ricavi"), use_container_width=True)
    by_vehicle = frame.groupby("vehicle_type", as_index=False).agg(
        noleggi=("id", "count"), ricavi=("ancillary_cost", "sum"), conversione=("has_ancillary", "mean")
    )
    c4.plotly_chart(px.bar(by_vehicle, x="vehicle_type", y=["noleggi", "ricavi"], barmode="group", title="Confronto CAR e VAN"), use_container_width=True)
    with st.expander("Controllo qualità dei dati"):
        duplicates = frame[frame.duplicated("ra", keep=False)].sort_values("ra")
        q1, q2, q3 = st.columns(3)
        q1.metric("RA duplicati", int(duplicates["ra"].nunique()))
        q2.metric("Fonte mancante", int(frame["source_raw"].fillna("").str.strip().eq("").sum()))
        q3.metric("Veicolo mancante", int(frame["vehicle_type"].fillna("").str.strip().eq("").sum()))
        if not duplicates.empty:
            st.dataframe(
                duplicates[["start_date", "ra", "operator", "source_raw"]],
                use_container_width=True,
                hide_index=True,
            )


def record_form(frame):
    st.header("Inserimento e modifica")
    records_by_id = {row.id: row for row in frame.itertuples()}
    options = [""] + list(records_by_id)
    selected = st.selectbox(
        "Record da modificare (lascia vuoto per inserirne uno nuovo)",
        options,
        format_func=lambda record_id: "" if not record_id else (
            f"{records_by_id[record_id].ra} | "
            f"{records_by_id[record_id].start_date.date() if pd.notna(records_by_id[record_id].start_date) else ''} | "
            f"{records_by_id[record_id].operator}"
        ),
    )
    current = None
    if selected:
        current = frame[frame["id"] == selected].iloc[0].to_dict()
    widget_prefix = (current or {}).get("id", "nuovo")
    rental_options = ["", "CORPORATE", "WALK-IN", "BROKER", "WEB/CNP", "REPLACEMENT", "MENSILE"]
    current_rental = upper((current or {}).get("rental_type", ""))
    rental_aliases = {
        "CORPARATE": "CORPORATE", "WEB - CNP ETC": "WEB/CNP", "WEB-CNP-ETC": "WEB/CNP",
        "ASSISTENZA": "REPLACEMENT", "FULL CREDIT, ASSISTENZA": "REPLACEMENT",
    }
    current_rental = rental_aliases.get(current_rental, current_rental)
    if current_rental not in rental_options:
        current_rental = ""

    a, b = st.columns(2)
    ra = a.text_input("RA (Rental Agreement) *", value=(current or {}).get("ra", ""), key=f"{widget_prefix}_ra")
    start_value = (current or {}).get("start_date")
    start_date = b.date_input(
        "Data inizio noleggio *",
        value=start_value.date() if pd.notna(start_value) else date.today(),
        key=f"{widget_prefix}_date",
    )

    d, e, f = st.columns(3)
    current_operator = upper((current or {}).get("operator", ""))
    operator_options = [""] + [row["name"] for row in configured_operators()]
    if current_operator and current_operator not in operator_options:
        operator_options.append(current_operator)
    operator = d.selectbox(
        "Operatore",
        operator_options,
        index=operator_options.index(current_operator) if current_operator in operator_options else 0,
        key=f"{widget_prefix}_operator",
    )
    vehicle_options = ["", "CAR", "VAN"]
    current_vehicle = upper((current or {}).get("vehicle_type", ""))
    if current_vehicle not in vehicle_options:
        current_vehicle = ""
    vehicle = e.selectbox(
        "Tipo veicolo", vehicle_options, index=vehicle_options.index(current_vehicle), key=f"{widget_prefix}_vehicle"
    )
    rental_type = f.selectbox(
        "Tipo noleggio *", rental_options, index=rental_options.index(current_rental), key=f"{widget_prefix}_rental"
    )

    ancillary_blocked = rental_type in {"CORPORATE", "MENSILE"}
    existing_days = int((current or {}).get("rental_days", 0) or 0)
    days = st.number_input(
        "Giorni noleggio *",
        min_value=0,
        step=1,
        value=0 if ancillary_blocked else max(existing_days, 1),
        disabled=ancillary_blocked,
        key=f"{widget_prefix}_days_{rental_type}",
    )

    car_ancillary = ["", "GOLD", "PLATINUM", "UP GOLD/PLATINUM", "GIÀ PRESENTE"]
    van_ancillary = ["", "NO PROBLEM", "SUPER VAN", "VAN PROTECTION", "CARGO VAN"]
    ancillary_options = car_ancillary if vehicle == "CAR" else van_ancillary if vehicle == "VAN" else [""]
    current_ancillary = upper((current or {}).get("ancillary", ""))
    if "PRESENTE" in current_ancillary and vehicle == "CAR":
        current_ancillary = "GIÀ PRESENTE"
    elif "UP" in current_ancillary and vehicle == "CAR":
        current_ancillary = "UP GOLD/PLATINUM"
    if current_ancillary not in ancillary_options:
        current_ancillary = ""

    g, h = st.columns(2)
    ancillary = g.selectbox(
        "Tipo ancillary",
        ancillary_options,
        index=ancillary_options.index(current_ancillary),
        disabled=ancillary_blocked or not vehicle,
        key=f"{widget_prefix}_ancillary_{vehicle}_{rental_type}",
    )
    existing_cost = (current or {}).get("ancillary_cost")
    existing_daily_cost = (
        float(existing_cost) / existing_days
        if pd.notna(existing_cost) and existing_days > 0
        else 0.0
    )
    daily_cost = h.number_input(
        "Costo giornaliero ancillary (IVA esclusa)",
        min_value=0.0,
        step=0.01,
        value=0.0 if ancillary_blocked else round(existing_daily_cost, 2),
        disabled=ancillary_blocked or not ancillary,
        key=f"{widget_prefix}_cost_{vehicle}_{rental_type}_{ancillary}",
    )
    calculated_total = round(daily_cost * days, 2) if not ancillary_blocked and days else 0.0
    if ancillary_blocked:
        st.info(f"Per i noleggi {rental_type} non vengono inseriti giorni, ancillary o costi ancillary.")
    else:
        st.info(f"Totale ancillary calcolato: € {daily_cost:.2f} × {days} giorni = € {calculated_total:.2f}")
    notes = st.text_area("Note", value=(current or {}).get("notes", ""), key=f"{widget_prefix}_notes")
    submitted = st.button("Salva record", type="primary", use_container_width=True)
    if submitted:
        if not ra or not rental_type:
            st.error("Inserisci almeno RA e tipo noleggio.")
        elif start_date < ANCILLARY_START_DATE:
            st.error("Lo storico ancillary parte dal 01/10/2026. Inserisci una data uguale o successiva.")
        elif not ancillary_blocked and days <= 0:
            st.error("Inserisci i giorni di noleggio.")
        else:
            save_record({
                "id": (current or {}).get("id"), "created_at": (current or {}).get("created_at"),
                "ra": ra, "start_date": start_date.isoformat(), "rental_days": days,
                "source_raw": (current or {}).get("source_raw", ""),
                "operator": operator, "vehicle_type": vehicle,
                "ancillary": ancillary,
                "ancillary_cost": calculated_total if calculated_total > 0 else None,
                "rental_type": rental_type, "notes": notes,
            })
            st.success("Record salvato e normalizzato.")
            st.rerun()
    if current:
        st.divider()
        confirm = st.checkbox(f"Confermo l’eliminazione definitiva di {current['ra']}")
        if st.button("Elimina record", disabled=not confirm, type="secondary"):
            with db() as conn:
                conn.execute("DELETE FROM rentals WHERE id = ?", (current["id"],))
            st.success("Record eliminato.")
            st.rerun()


def archive(frame):
    st.header("Archivio noleggi")
    display = frame[[
        "start_date", "ra", "rental_days", "source_base", "source_details",
        "operator", "vehicle_type", "ancillary", "ancillary_cost", "rpd", "rental_type",
    ]].copy()
    display.columns = [
        "Data", "RA", "Giorni", "Fonte", "Dettaglio fonte", "Operatore",
        "Veicolo", "Ancillary", "Costo", "RPD", "Tipo noleggio",
    ]
    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Data": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Costo": st.column_config.NumberColumn(format="€ %.2f"),
            "RPD": st.column_config.NumberColumn(format="€ %.2f"),
        },
    )
    st.download_button("Esporta archivio filtrato in Excel", to_excel(frame), "archivio_ancillary.xlsx", use_container_width=True)


def contract_filters(frame):
    if frame.empty:
        return frame
    c1, c2, c3, c4 = st.columns(4)
    periods = sorted(x for x in frame["import_period"].dropna().unique() if x)
    selected_periods = c1.multiselect("Periodo RA", periods, default=periods)
    operators = sorted(x for x in frame["operator"].dropna().unique() if x)
    selected_operators = c2.multiselect("Operatore RA", operators)
    sources = sorted(x for x in frame["source_base"].dropna().unique() if x)
    selected_sources = c3.multiselect("Fonte RA", sources)
    groups = sorted(x for x in frame["assigned_group"].dropna().unique() if x)
    selected_groups = c4.multiselect("Gruppo assegnato", groups)
    c5, c6, c7 = st.columns(3)
    channels = sorted(x for x in frame["rental_channel"].dropna().unique() if x)
    selected_channels = c5.multiselect("Tipo noleggio", channels)
    terms = [x for x in ["GIORNALIERO", "MENSILE"] if x in set(frame["rental_term"])]
    selected_terms = c6.multiselect("Durata noleggio", terms)
    vehicles = [x for x in ["CAR", "VAN"] if x in set(frame["contract_vehicle"])]
    selected_vehicles = c7.multiselect("Tipo veicolo", vehicles)
    result = frame.copy()
    if selected_periods:
        result = result[result["import_period"].isin(selected_periods)]
    if selected_operators:
        result = result[result["operator"].isin(selected_operators)]
    if selected_sources:
        result = result[result["source_base"].isin(selected_sources)]
    if selected_groups:
        result = result[result["assigned_group"].isin(selected_groups)]
    if selected_channels:
        result = result[result["rental_channel"].isin(selected_channels)]
    if selected_terms:
        result = result[result["rental_term"].isin(selected_terms)]
    if selected_vehicles:
        result = result[result["contract_vehicle"].isin(selected_vehicles)]
    return result


def contracts_dashboard(frame):
    st.header("Analisi contratti RA")
    frame = contract_filters(frame)
    if frame.empty:
        st.info("Nessun contratto disponibile per i filtri selezionati.")
        return
    contracts = len(frame)
    days = int(frame["duration_days"].fillna(0).sum())
    value = float(frame["contract_value"].fillna(0).sum())
    ancillary = float(frame["ancillary_value"].fillna(0).sum())
    rpd = value / days if days else 0
    ancillary_rpd = ancillary / days if days else 0
    penetration = float(frame["has_ancillary_ra"].mean()) if contracts else 0
    cols = st.columns(7)
    cols[0].metric("Contratti", f"{contracts:,}".replace(",", "."))
    cols[1].metric("Giorni", f"{days:,}".replace(",", "."))
    cols[2].metric("Valore contratti", f"€ {value:,.0f}".replace(",", "."))
    cols[3].metric("RPD contratti", f"€ {rpd:.2f}".replace(".", ","))
    cols[4].metric("Valore ancillary", f"€ {ancillary:,.0f}".replace(",", "."))
    cols[5].metric("RPD ancillary", f"€ {ancillary_rpd:.2f}".replace(".", ","))
    cols[6].metric("Contratti con ancillary", f"{penetration:.1%}")

    daily = frame.copy()
    daily["data"] = daily["contract_date"].dt.date
    by_day = daily.groupby("data", as_index=False).agg(
        contratti=("id", "count"), valore=("contract_value", "sum"), ancillary=("ancillary_value", "sum")
    )
    c1, c2 = st.columns(2)
    c1.plotly_chart(px.line(by_day, x="data", y=["valore", "ancillary"], markers=True, title="Valore contratti e ancillary per giorno"), use_container_width=True)
    by_operator = frame.groupby("operator", as_index=False).agg(
        contratti=("id", "count"), giorni=("duration_days", "sum"),
        valore=("contract_value", "sum"), ancillary=("ancillary_value", "sum")
    )
    by_operator["rpd"] = by_operator["valore"].div(by_operator["giorni"].replace(0, pd.NA))
    by_operator["ancillary_rpd"] = by_operator["ancillary"].div(by_operator["giorni"].replace(0, pd.NA))
    c2.plotly_chart(px.bar(by_operator.sort_values("valore", ascending=False), x="operator", y="valore", color="ancillary_rpd", title="Valore e RPD ancillary per operatore"), use_container_width=True)

    c3, c4 = st.columns(2)
    by_source = frame.groupby("source_base", as_index=False).agg(
        contratti=("id", "count"), valore=("contract_value", "sum"), ancillary=("ancillary_value", "sum")
    ).sort_values("valore", ascending=False).head(15)
    c3.plotly_chart(px.bar(by_source, x="valore", y="source_base", color="ancillary", orientation="h", title="Prime fonti per valore contratto"), use_container_width=True)
    groups = frame.groupby(["requested_group", "assigned_group"], as_index=False).size().sort_values("size", ascending=False).head(20)
    groups["passaggio"] = groups["requested_group"] + " → " + groups["assigned_group"]
    c4.plotly_chart(px.bar(groups, x="size", y="passaggio", orientation="h", title="Gruppo richiesto e assegnato"), use_container_width=True)

    breakdown = frame.groupby(
        ["rental_channel", "rental_term", "contract_vehicle"], as_index=False
    ).agg(
        contratti=("id", "count"), giorni=("duration_days", "sum"),
        valore=("contract_value", "sum"), ancillary=("ancillary_value", "sum"),
    )
    breakdown["rpd"] = breakdown["valore"].div(breakdown["giorni"].replace(0, pd.NA))
    breakdown["ancillary_rpd"] = breakdown["ancillary"].div(breakdown["giorni"].replace(0, pd.NA))
    breakdown["combinazione"] = breakdown["rental_channel"] + " - " + breakdown["rental_term"]
    c5, c6 = st.columns(2)
    c5.plotly_chart(
        px.bar(
            breakdown, x="combinazione", y="contratti", color="contract_vehicle",
            barmode="group", title="Contratti per tipo, durata e veicolo",
        ),
        use_container_width=True,
    )
    vehicle_summary = frame.groupby("contract_vehicle", as_index=False).agg(
        contratti=("id", "count"), valore=("contract_value", "sum"), ancillary=("ancillary_value", "sum")
    )
    c6.plotly_chart(
        px.bar(
            vehicle_summary, x="contract_vehicle", y=["valore", "ancillary"],
            barmode="group", title="Confronto CAR e VAN",
        ),
        use_container_width=True,
    )

    st.subheader("Risultati per tipo di noleggio, durata e veicolo")
    st.dataframe(breakdown, use_container_width=True, hide_index=True)

    st.subheader("Risultati per operatore")
    st.dataframe(by_operator.sort_values("valore", ascending=False), use_container_width=True, hide_index=True)


def ancillary_ra_dashboard(frame):
    st.header("Analisi ancillary RA")
    st.caption("Tutti i valori provengono dai report Analisi contratti e seguono i filtri selezionati, incluso il gruppo assegnato.")
    frame = contract_filters(frame)
    if frame.empty:
        st.info("Nessun dato ancillary disponibile per i filtri selezionati.")
        return

    contracts = len(frame)
    with_ancillary = int(frame["has_ancillary_ra"].sum())
    days = int(frame["duration_days"].fillna(0).sum())
    ancillary_value = float(frame["ancillary_value"].fillna(0).sum())
    ancillary_rpd = ancillary_value / days if days else 0
    penetration = with_ancillary / contracts if contracts else 0
    ticket = ancillary_value / with_ancillary if with_ancillary else 0
    cols = st.columns(6)
    cols[0].metric("Contratti analizzati", f"{contracts:,}".replace(",", "."))
    cols[1].metric("Con ancillary", f"{with_ancillary:,}".replace(",", "."))
    cols[2].metric("Penetrazione", f"{penetration:.1%}")
    cols[3].metric("Valore ancillary", f"€ {ancillary_value:,.0f}".replace(",", "."))
    cols[4].metric("RPD ancillary", f"€ {ancillary_rpd:.2f}".replace(".", ","))
    cols[5].metric("Ticket medio ancillary", f"€ {ticket:.2f}".replace(".", ","))

    daily = frame.copy()
    daily["data"] = daily["contract_date"].dt.date
    by_day = daily.groupby("data", as_index=False).agg(
        valore_ancillary=("ancillary_value", "sum"),
        contratti=("id", "count"),
        contratti_con_ancillary=("has_ancillary_ra", "sum"),
    )
    by_day["penetrazione"] = by_day["contratti_con_ancillary"].div(by_day["contratti"].replace(0, pd.NA))

    c1, c2 = st.columns(2)
    c1.plotly_chart(
        px.line(by_day, x="data", y="valore_ancillary", markers=True, title="Valore ancillary per giorno"),
        use_container_width=True,
    )
    by_operator = frame.groupby("operator", as_index=False).agg(
        contratti=("id", "count"), giorni=("duration_days", "sum"),
        valore_ancillary=("ancillary_value", "sum"), contratti_con_ancillary=("has_ancillary_ra", "sum"),
    )
    by_operator["rpd_ancillary"] = by_operator["valore_ancillary"].div(by_operator["giorni"].replace(0, pd.NA))
    by_operator["penetrazione"] = by_operator["contratti_con_ancillary"].div(by_operator["contratti"].replace(0, pd.NA))
    c2.plotly_chart(
        px.bar(by_operator, x="operator", y="valore_ancillary", color="rpd_ancillary", title="Valore e RPD ancillary per operatore"),
        use_container_width=True,
    )

    c3, c4 = st.columns(2)
    by_group = frame.groupby("assigned_group", as_index=False).agg(
        contratti=("id", "count"), giorni=("duration_days", "sum"),
        valore_ancillary=("ancillary_value", "sum"), contratti_con_ancillary=("has_ancillary_ra", "sum"),
    )
    by_group["rpd_ancillary"] = by_group["valore_ancillary"].div(by_group["giorni"].replace(0, pd.NA))
    by_group["penetrazione"] = by_group["contratti_con_ancillary"].div(by_group["contratti"].replace(0, pd.NA))
    c3.plotly_chart(
        px.bar(by_group.sort_values("valore_ancillary", ascending=False), x="assigned_group", y="valore_ancillary", color="penetrazione", title="Ancillary per gruppo assegnato"),
        use_container_width=True,
    )
    by_mix = frame.groupby(["rental_channel", "rental_term", "contract_vehicle"], as_index=False).agg(
        contratti=("id", "count"), valore_ancillary=("ancillary_value", "sum"),
    )
    by_mix["combinazione"] = by_mix["rental_channel"] + " - " + by_mix["rental_term"]
    c4.plotly_chart(
        px.bar(by_mix, x="combinazione", y="valore_ancillary", color="contract_vehicle", barmode="group", title="Ancillary per tipo, durata e veicolo"),
        use_container_width=True,
    )

    st.subheader("Statistiche ancillary per gruppo assegnato")
    st.dataframe(by_group.sort_values("valore_ancillary", ascending=False), use_container_width=True, hide_index=True)
    st.subheader("Statistiche ancillary per operatore")
    st.dataframe(by_operator.sort_values("valore_ancillary", ascending=False), use_container_width=True, hide_index=True)


def contracts_archive(frame):
    st.header("Archivio contratti RA")
    frame = contract_filters(frame)
    display = frame[[
        "contract_date", "ra", "operator", "client", "duration_days",
        "rental_channel", "rental_term", "contract_vehicle",
        "requested_group", "assigned_group", "source_base", "source_details",
        "contract_value", "kpi1_rpd", "ancillary_value", "ancillary_rpd", "import_file",
    ]].copy()
    display.columns = [
        "Data", "RA", "Operatore", "Cliente", "Giorni",
        "Tipo noleggio", "Durata noleggio", "Veicolo", "Gruppo richiesto",
        "Gruppo assegnato", "Fonte", "Dettaglio fonte", "Valore contratto",
        "RPD contratto", "Valore ancillary", "RPD ancillary", "File importato",
    ]
    st.dataframe(display, use_container_width=True, hide_index=True)


def combined_analysis(contracts, ancillary):
    st.header("Analisi incrociata RA e ancillary")
    if contracts.empty or ancillary.empty:
        st.info("Servono sia contratti RA sia dati ancillary per eseguire il confronto.")
        return
    ancillary = ancillary.copy()
    contracts = contracts.copy()
    ancillary["analysis_year"] = ancillary["start_date"].dt.year
    contracts["analysis_year"] = contracts["contract_date"].dt.year.fillna(contracts["start_date"].dt.year)
    ancillary_summary = ancillary.groupby(["ra", "analysis_year"], as_index=False).agg(
        ancillary_modulo=("ancillary_cost", "sum"), righe_modulo=("id", "count")
    )
    joined = contracts.merge(ancillary_summary, on=["ra", "analysis_year"], how="left")
    joined["ancillary_modulo"] = joined["ancillary_modulo"].fillna(0)
    joined["differenza"] = joined["ancillary_value"].fillna(0) - joined["ancillary_modulo"]
    matched = joined["righe_modulo"].notna().sum()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("RA nel periodo", len(joined))
    c2.metric("RA collegati al modulo", int(matched))
    c3.metric("Copertura collegamento", f"{matched / len(joined):.1%}" if len(joined) else "0%")
    c4.metric("Differenza complessiva", f"€ {joined['differenza'].sum():.2f}".replace(".", ","))
    st.plotly_chart(
        px.scatter(joined, x="ancillary_modulo", y="ancillary_value", color="operator", hover_data=["ra", "source_base"], title="Confronto ancillary modulo e report RA"),
        use_container_width=True,
    )
    anomalies = joined[joined["differenza"].abs() > 0.02][[
        "ra", "operator", "source_base", "ancillary_modulo", "ancillary_value", "differenza"
    ]].sort_values("differenza", key=lambda x: x.abs(), ascending=False)
    st.subheader("Differenze da controllare")
    st.dataframe(anomalies, use_container_width=True, hide_index=True)


def damage_filters(frame):
    if frame.empty:
        return frame
    c1, c2, c3, c4 = st.columns(4)
    years = sorted(frame["control_date"].dropna().dt.year.unique().astype(int).tolist())
    selected_years = c1.multiselect("Anno controllo", years, default=years)
    vehicles = sorted(x for x in frame["vehicle_category"].dropna().unique() if x)
    selected_vehicles = c2.multiselect("Categoria veicolo", vehicles)
    modes = sorted(x for x in frame["charge_mode"].dropna().unique() if x)
    selected_modes = c3.multiselect("Modalità addebito", modes)
    operators = sorted(x for x in frame["operator"].dropna().unique() if x)
    selected_operators = c4.multiselect("Operatore", operators)
    c5, c6, c7 = st.columns(3)
    categories = sorted(x for x in frame["damage_category"].dropna().unique() if x)
    selected_categories = c5.multiselect("Tipo danno", categories)
    statuses = sorted(x for x in frame["payment_status"].dropna().unique() if x)
    selected_statuses = c6.multiselect("Stato addebito", statuses)
    search = c7.text_input("Cerca RA o descrizione")
    result = frame.copy()
    if selected_years:
        result = result[result["control_date"].dt.year.isin(selected_years)]
    if selected_vehicles:
        result = result[result["vehicle_category"].isin(selected_vehicles)]
    if selected_modes:
        result = result[result["charge_mode"].isin(selected_modes)]
    if selected_operators:
        result = result[result["operator"].isin(selected_operators)]
    if selected_categories:
        result = result[result["damage_category"].isin(selected_categories)]
    if selected_statuses:
        result = result[result["payment_status"].isin(selected_statuses)]
    if search:
        needle = upper(search)
        mask = result[["ra", "description", "operator"]].fillna("").apply(
            lambda column: column.str.upper().str.contains(needle, regex=False)
        ).any(axis=1)
        result = result[mask]
    return result


def damage_dashboard(frame):
    st.header("Analisi addebito danni")
    frame = damage_filters(frame)
    if frame.empty:
        st.info("Nessuna segnalazione danni disponibile per i filtri selezionati.")
        return
    total = len(frame)
    vans = int(frame["vehicle_category"].eq("VAN").sum())
    cars = int(frame["vehicle_category"].eq("CAR").sum())
    photo_coverage = float(frame["has_photo"].mean()) if total else 0
    charged = float(frame["amount"].fillna(0).sum())
    collected = float(frame.loc[frame["payment_status"].eq("INCASSATO"), "amount"].fillna(0).sum())
    cols = st.columns(6)
    cols[0].metric("Segnalazioni", total)
    cols[1].metric("CAR", cars)
    cols[2].metric("VAN", vans)
    cols[3].metric("Con foto", f"{photo_coverage:.1%}")
    cols[4].metric("Importo addebitato", f"€ {charged:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    cols[5].metric("Importo incassato", f"€ {collected:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

    monthly = frame.dropna(subset=["control_date"]).copy()
    monthly["mese"] = monthly["control_date"].dt.to_period("M").astype(str)
    monthly = monthly.groupby("mese", as_index=False).agg(segnalazioni=("id", "count"), importo=("amount", "sum"))
    c1, c2 = st.columns(2)
    c1.plotly_chart(px.line(monthly, x="mese", y="segnalazioni", markers=True, title="Segnalazioni per mese"), use_container_width=True)
    by_operator = frame.groupby("operator", as_index=False).agg(segnalazioni=("id", "count"), importo=("amount", "sum"))
    c2.plotly_chart(px.bar(by_operator, x="operator", y="segnalazioni", color="importo", title="Danni per operatore"), use_container_width=True)
    c3, c4 = st.columns(2)
    by_mode = frame.groupby("charge_mode", as_index=False).size().rename(columns={"size": "segnalazioni"})
    c3.plotly_chart(px.bar(by_mode, x="charge_mode", y="segnalazioni", title="Danni per modalità di addebito"), use_container_width=True)
    by_category = frame.groupby(["damage_category", "vehicle_category"], as_index=False).size().rename(columns={"size": "segnalazioni"})
    c4.plotly_chart(px.bar(by_category, x="damage_category", y="segnalazioni", color="vehicle_category", barmode="group", title="Tipologia danni CAR e VAN"), use_container_width=True)
    st.subheader("Riepilogo per operatore")
    st.dataframe(by_operator.sort_values("segnalazioni", ascending=False), use_container_width=True, hide_index=True)


def damage_archive(frame):
    st.header("Archivio addebito danni")
    frame = damage_filters(frame)
    if frame.empty:
        st.info("Nessuna segnalazione presente.")
        return
    display = frame[[
        "control_date", "ra", "vehicle_category", "charge_mode", "operator",
        "damage_category", "description", "amount", "payment_status", "photo_url", "notes",
    ]].copy()
    display.columns = [
        "Data controllo", "RA", "Veicolo", "Modalità", "Operatore", "Tipo danno",
        "Descrizione", "Importo", "Stato", "Foto", "Note",
    ]
    st.dataframe(
        display, use_container_width=True, hide_index=True,
        column_config={
            "Data controllo": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Importo": st.column_config.NumberColumn(format="€ %.2f"),
            "Foto": st.column_config.LinkColumn(display_text="Apri foto"),
        },
    )
    st.download_button("Esporta archivio danni in Excel", to_excel(display), "archivio_addebito_danni.xlsx", use_container_width=True)


def damage_form(frame):
    st.header("Inserimento e modifica addebito danni")
    records = {row.id: row for row in frame.itertuples()}
    selected = st.selectbox(
        "Segnalazione da modificare (lascia vuoto per inserirne una nuova)",
        [""] + list(records),
        format_func=lambda record_id: "" if not record_id else (
            f"{records[record_id].ra} | "
            f"{records[record_id].control_date.date() if pd.notna(records[record_id].control_date) else ''} | "
            f"{records[record_id].operator}"
        ),
    )
    current = frame[frame["id"] == selected].iloc[0].to_dict() if selected else {}
    prefix = current.get("id", "nuovo_danno")
    c1, c2, c3 = st.columns(3)
    ra = c1.text_input("RA *", value=current.get("ra", ""), key=f"{prefix}_damage_ra")
    current_date = current.get("control_date")
    control_date = c2.date_input(
        "Data del controllo *",
        value=current_date.date() if pd.notna(current_date) else date.today(),
        key=f"{prefix}_damage_date",
    )
    vehicle_options = ["", "CAR", "VAN"]
    current_vehicle = upper(current.get("vehicle_category", ""))
    vehicle = c3.selectbox(
        "Categoria veicolo *", vehicle_options,
        index=vehicle_options.index(current_vehicle) if current_vehicle in vehicle_options else 0,
        key=f"{prefix}_damage_vehicle",
    )
    c4, c5 = st.columns(2)
    mode_options = ["", "IN PRESENZA", "KEY BOX", "CLIENTE NON ASPETTA (AFTER RENTAL)", "ALTRO"]
    current_mode = upper(current.get("charge_mode", ""))
    if current_mode and current_mode not in mode_options:
        mode_options.append(current_mode)
    charge_mode = c4.selectbox(
        "Modalità di addebito *", mode_options,
        index=mode_options.index(current_mode) if current_mode in mode_options else 0,
        key=f"{prefix}_damage_mode",
    )
    operator_options = [""] + [row["name"] for row in configured_operators()]
    current_operator = upper(current.get("operator", ""))
    if current_operator and current_operator not in operator_options:
        operator_options.append(current_operator)
    operator = c5.selectbox(
        "Operatore responsabile *", operator_options,
        index=operator_options.index(current_operator) if current_operator in operator_options else 0,
        key=f"{prefix}_damage_operator",
    )
    description = st.text_area("Descrizione sintetica del danno *", value=current.get("description", ""), key=f"{prefix}_damage_description")
    photo_url = st.text_input("Collegamento foto danno", value=current.get("photo_url", ""), key=f"{prefix}_damage_photo")
    c6, c7 = st.columns(2)
    amount_value = current.get("amount")
    amount = c6.number_input(
        "Importo addebitato", min_value=0.0, step=10.0,
        value=0.0 if pd.isna(amount_value) else float(amount_value), key=f"{prefix}_damage_amount",
    )
    status_options = ["DA VERIFICARE", "ADDEBITATO", "INCASSATO", "ANNULLATO"]
    current_status = upper(current.get("payment_status", "")) or "DA VERIFICARE"
    status = c7.selectbox(
        "Stato addebito", status_options,
        index=status_options.index(current_status) if current_status in status_options else 0,
        key=f"{prefix}_damage_status",
    )
    notes = st.text_area("Note", value=current.get("notes", ""), key=f"{prefix}_damage_notes")
    if st.button("Salva segnalazione danno", type="primary", use_container_width=True):
        if not all([ra, vehicle, charge_mode, operator, description]):
            st.error("Compila RA, categoria veicolo, modalità, operatore e descrizione.")
        else:
            save_damage({
                "id": current.get("id"), "source_key": current.get("source_key"),
                "submitted_at": current.get("submitted_at"), "control_date": control_date.isoformat(),
                "ra": ra, "vehicle_category": vehicle, "charge_mode": charge_mode,
                "operator": operator, "description": description, "photo_url": photo_url,
                "amount": amount if amount > 0 else None, "payment_status": status, "notes": notes,
            })
            st.success("Segnalazione danno salvata.")
            st.rerun()
    if current:
        confirm = st.checkbox(f"Confermo l’eliminazione definitiva della segnalazione {current['ra']}")
        if st.button("Elimina segnalazione danno", disabled=not confirm, use_container_width=True):
            with db() as conn:
                conn.execute("DELETE FROM damage_charges WHERE id = ?", (current["id"],))
            st.success("Segnalazione eliminata.")
            st.rerun()


def import_backup():
    st.header("Importazione e backup")
    st.subheader("Backup completo")
    if DB_PATH.exists():
        st.download_button("Scarica database", DB_PATH.read_bytes(), "ancillary_backup.db", mime="application/octet-stream", use_container_width=True)
    st.caption("Conserva periodicamente il file di backup in una posizione sicura.")
    st.subheader("Importazione intelligente da Excel")
    st.caption("Il gestionale riconosce automaticamente riepiloghi ancillary, report Analisi Contratti RA e file Addebito Danni.")
    upload = st.file_uploader("Carica un file Excel", type=["xlsx"])
    if upload:
        file_bytes = upload.getvalue()
        detected = detect_excel_type(file_bytes)
        labels = {
            "ANCILLARY": "Riepilogo ancillary", "CONTRATTI_RA": "Analisi Contratti RA",
            "ADDEBITO_DANNI": "Riepilogo addebito danni", "SCONOSCIUTO": "Formato non riconosciuto",
        }
        st.info(f"Formato rilevato: **{labels[detected]}**")
        if st.button("Importa, scorpora e analizza", type="primary", disabled=detected == "SCONOSCIUTO", use_container_width=True):
            if detected == "CONTRATTI_RA":
                added, main_sheet, period = import_contract_workbook(file_bytes, upload.name)
                st.success(f"Importati o aggiornati {added} contratti del periodo {period}. Foglio principale: {main_sheet}.")
                st.rerun()
            if detected == "ADDEBITO_DANNI":
                added, skipped = import_damage_workbook(file_bytes, upload.name)
                st.success(f"Importate o aggiornate {added} segnalazioni danni.")
                if skipped:
                    st.info(f"Escluse {skipped} righe senza numero RA.")
                st.rerun()
            imported = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, dtype=object)
            added = 0
            skipped_before_start = 0
            for _, row in imported.iterrows():
                days = pd.to_numeric(row.get("GIORNI NOLEGGIO"), errors="coerce")
                cost = pd.to_numeric(row.get("COSTO TOTALE ANCILLARY (iva esclusa)"), errors="coerce")
                start = pd.to_datetime(row.get("DATA INIZIO NOLEGGIO"), errors="coerce")
                if pd.notna(start) and start.date() < ANCILLARY_START_DATE:
                    skipped_before_start += 1
                    continue
                vehicle = upper(row.get("TIPO DI VEICOLO"))
                ancillary = upper(row.get("SCELTA ANCILLARY (CAR)")) or upper(row.get("SCELTA ANCILLARY (VAN)"))
                save_record({
                    "ra": row.get("RA (Rental Agreement)"),
                    "start_date": "" if pd.isna(start) else start.date().isoformat(),
                    "rental_days": 0 if pd.isna(days) else int(days),
                    "source_raw": row.get("FONTE"), "operator": row.get("OPERATORE"),
                    "vehicle_type": vehicle, "ancillary": ancillary,
                    "ancillary_cost": None if pd.isna(cost) else float(cost),
                    "rental_type": row.get("TIPO NOLEGGIO"), "notes": "Importato da Excel",
                })
                added += 1
            st.success(f"Importati {added} record ancillary.")
            if skipped_before_start:
                st.info(f"Esclusi {skipped_before_start} record anteriori al 01/10/2026.")
            st.rerun()


def operator_settings():
    st.header("Configurazione operatori")
    st.caption("Gli operatori attivi compaiono nella tendina del modulo di inserimento. Lo storico non viene modificato.")

    with st.form("add_operator", clear_on_submit=True):
        new_operator = st.text_input("Nuovo operatore")
        add_operator = st.form_submit_button("Aggiungi operatore", type="primary", use_container_width=True)
    if add_operator:
        name = upper(new_operator)
        if not name:
            st.error("Inserisci il nome dell’operatore.")
        else:
            with db() as conn:
                conn.execute(
                    "INSERT INTO operator_config (name, active) VALUES (?, 1) "
                    "ON CONFLICT(name) DO UPDATE SET active = 1",
                    (name,),
                )
            st.success(f"Operatore {name} disponibile nel modulo.")
            st.rerun()

    rows = configured_operators(active_only=False)
    if not rows:
        st.info("Nessun operatore configurato.")
        return

    st.subheader("Operatori configurati")
    st.dataframe(
        pd.DataFrame(rows).rename(columns={"name": "Operatore", "active": "Attivo"}),
        use_container_width=True,
        hide_index=True,
        column_config={"Attivo": st.column_config.CheckboxColumn()},
    )
    selected_name = st.selectbox("Operatore da gestire", [row["name"] for row in rows])
    selected_row = next(row for row in rows if row["name"] == selected_name)
    c1, c2 = st.columns(2)
    renamed = c1.text_input("Nuovo nome", value=selected_name, key=f"rename_{selected_name}")
    active = c2.checkbox("Attivo nella tendina", value=bool(selected_row["active"]), key=f"active_{selected_name}")
    if st.button("Salva modifiche operatore", type="primary", use_container_width=True):
        new_name = upper(renamed)
        if not new_name:
            st.error("Il nome non può essere vuoto.")
        else:
            try:
                with db() as conn:
                    conn.execute(
                        "UPDATE operator_config SET name = ?, active = ? WHERE name = ?",
                        (new_name, int(active), selected_name),
                    )
                    if new_name != selected_name:
                        conn.execute("UPDATE rentals SET operator = ? WHERE UPPER(TRIM(operator)) = ?", (new_name, selected_name))
                        conn.execute("UPDATE contracts SET operator = ? WHERE UPPER(TRIM(operator)) = ?", (new_name, selected_name))
                        conn.execute("UPDATE damage_charges SET operator = ? WHERE UPPER(TRIM(operator)) = ?", (new_name, selected_name))
                st.success("Operatore aggiornato.")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("Esiste già un operatore con questo nome.")

    confirm_delete = st.checkbox("Confermo la rimozione dalla configurazione")
    if st.button("Elimina operatore", disabled=not confirm_delete, use_container_width=True):
        with db() as conn:
            conn.execute("DELETE FROM operator_config WHERE name = ?", (selected_name,))
        st.success("Operatore rimosso dalla tendina. I dati storici restano invariati.")
        st.rerun()


def login():
    configured = str(st.secrets.get("ADMIN_PASSWORD", "") or "").strip()
    if not configured:
        return True
    if st.session_state.get("authenticated"):
        return True
    st.title("Gestionale Ancillary")
    password = st.text_input("Password", type="password")
    if st.button("Accedi", type="primary", use_container_width=True):
        if password == configured:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Password errata.")
    return False


init_db()
if not login():
    st.stop()

st.title("Gestionale Noleggi, Ancillary e Danni")
st.caption("Importazione automatica dei report, archivio contratti, statistiche, ancillary e addebito danni")
all_data = load_data()
all_contracts = load_contracts()
all_damages = load_damages()
filtered = filters(all_data) if not all_data.empty else all_data
page = st.sidebar.radio(
    "Sezione",
    [
        "Dashboard ancillary", "Analisi contratti RA", "Analisi ancillary RA", "Analisi incrociata",
        "Analisi addebito danni", "Archivio addebito danni", "Inserimento addebito danni",
        "Archivio ancillary", "Archivio contratti RA", "Inserimento / modifica",
        "Configurazione operatori", "Importazione e backup",
    ],
)

if page == "Dashboard ancillary":
    dashboard(filtered)
elif page == "Analisi contratti RA":
    contracts_dashboard(all_contracts)
elif page == "Analisi ancillary RA":
    ancillary_ra_dashboard(all_contracts)
elif page == "Analisi incrociata":
    combined_analysis(all_contracts, all_data)
elif page == "Analisi addebito danni":
    damage_dashboard(all_damages)
elif page == "Archivio addebito danni":
    damage_archive(all_damages)
elif page == "Inserimento addebito danni":
    damage_form(all_damages)
elif page == "Archivio ancillary":
    archive(filtered)
elif page == "Archivio contratti RA":
    contracts_archive(all_contracts)
elif page == "Inserimento / modifica":
    record_form(all_data)
elif page == "Configurazione operatori":
    operator_settings()
else:
    import_backup()
