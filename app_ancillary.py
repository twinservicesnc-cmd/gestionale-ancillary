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
                ra TEXT NOT NULL UNIQUE,
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
                operator TEXT
            )
            """
        )
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


def clean(value):
    return re.sub(r"\s+", " ", str(value or "").strip())


def upper(value):
    return clean(value).upper()


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
                ON CONFLICT(ra) DO UPDATE SET
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
    options = [""] + [f"{row.ra} | {row.start_date.date() if pd.notna(row.start_date) else ''} | {row.operator}" for row in frame.itertuples()]
    selected = st.selectbox("Record da modificare (lascia vuoto per inserirne uno nuovo)", options)
    current = None
    if selected:
        ra = selected.split(" | ", 1)[0]
        current = frame[frame["ra"] == ra].iloc[0].to_dict()
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
    operator = d.text_input("Operatore", value=(current or {}).get("operator", ""), key=f"{widget_prefix}_operator")
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
    result = frame.copy()
    if selected_periods:
        result = result[result["import_period"].isin(selected_periods)]
    if selected_operators:
        result = result[result["operator"].isin(selected_operators)]
    if selected_sources:
        result = result[result["source_base"].isin(selected_sources)]
    if selected_groups:
        result = result[result["assigned_group"].isin(selected_groups)]
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

    st.subheader("Risultati per operatore")
    st.dataframe(by_operator.sort_values("valore", ascending=False), use_container_width=True, hide_index=True)


def contracts_archive(frame):
    st.header("Archivio contratti RA")
    frame = contract_filters(frame)
    display = frame[[
        "contract_date", "ra", "operator", "client", "duration_days",
        "requested_group", "assigned_group", "source_base", "source_details",
        "contract_value", "kpi1_rpd", "ancillary_value", "ancillary_rpd", "import_file",
    ]].copy()
    display.columns = [
        "Data", "RA", "Operatore", "Cliente", "Giorni", "Gruppo richiesto",
        "Gruppo assegnato", "Fonte", "Dettaglio fonte", "Valore contratto",
        "RPD contratto", "Valore ancillary", "RPD ancillary", "File importato",
    ]
    st.dataframe(display, use_container_width=True, hide_index=True)


def combined_analysis(contracts, ancillary):
    st.header("Analisi incrociata RA e ancillary")
    if contracts.empty or ancillary.empty:
        st.info("Servono sia contratti RA sia dati ancillary per eseguire il confronto.")
        return
    ancillary_summary = ancillary.groupby("ra", as_index=False).agg(
        ancillary_modulo=("ancillary_cost", "sum"), righe_modulo=("id", "count")
    )
    joined = contracts.merge(ancillary_summary, on="ra", how="left")
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


def import_backup():
    st.header("Importazione e backup")
    st.subheader("Backup completo")
    if DB_PATH.exists():
        st.download_button("Scarica database", DB_PATH.read_bytes(), "ancillary_backup.db", mime="application/octet-stream", use_container_width=True)
    st.caption("Conserva periodicamente il file di backup in una posizione sicura.")
    st.subheader("Importazione intelligente da Excel")
    st.caption("Il gestionale riconosce automaticamente i riepiloghi ancillary e i report mensili Analisi Contratti RA, anche con più fogli per operatore.")
    upload = st.file_uploader("Carica un file Excel", type=["xlsx"])
    if upload:
        file_bytes = upload.getvalue()
        detected = detect_excel_type(file_bytes)
        labels = {"ANCILLARY": "Riepilogo ancillary", "CONTRATTI_RA": "Analisi Contratti RA", "SCONOSCIUTO": "Formato non riconosciuto"}
        st.info(f"Formato rilevato: **{labels[detected]}**")
        if st.button("Importa, scorpora e analizza", type="primary", disabled=detected == "SCONOSCIUTO", use_container_width=True):
            if detected == "CONTRATTI_RA":
                added, main_sheet, period = import_contract_workbook(file_bytes, upload.name)
                st.success(f"Importati o aggiornati {added} contratti del periodo {period}. Foglio principale: {main_sheet}.")
                st.rerun()
            imported = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, dtype=object)
            added = 0
            for _, row in imported.iterrows():
                days = pd.to_numeric(row.get("GIORNI NOLEGGIO"), errors="coerce")
                cost = pd.to_numeric(row.get("COSTO TOTALE ANCILLARY (iva esclusa)"), errors="coerce")
                start = pd.to_datetime(row.get("DATA INIZIO NOLEGGIO"), errors="coerce")
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

st.title("Gestionale Noleggi e Ancillary")
st.caption("Importazione automatica dei report, archivio contratti, statistiche e controllo ancillary")
all_data = load_data()
all_contracts = load_contracts()
if all_data.empty:
    st.warning("Archivio vuoto. Importa il file Excel dalla sezione Importazione e backup.")
filtered = filters(all_data) if not all_data.empty else all_data
page = st.sidebar.radio(
    "Sezione",
    [
        "Dashboard ancillary", "Analisi contratti RA", "Analisi incrociata",
        "Archivio ancillary", "Archivio contratti RA", "Inserimento / modifica",
        "Importazione e backup",
    ],
)

if page == "Dashboard ancillary":
    dashboard(filtered)
elif page == "Analisi contratti RA":
    contracts_dashboard(all_contracts)
elif page == "Analisi incrociata":
    combined_analysis(all_contracts, all_data)
elif page == "Archivio ancillary":
    archive(filtered)
elif page == "Archivio contratti RA":
    contracts_archive(all_contracts)
elif page == "Inserimento / modifica":
    record_form(all_data)
else:
    import_backup()
