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
    with st.form("record_form"):
        a, b, c = st.columns(3)
        ra = a.text_input("RA (Rental Agreement) *", value=(current or {}).get("ra", ""))
        start_value = (current or {}).get("start_date")
        start_date = b.date_input("Data inizio noleggio *", value=start_value.date() if pd.notna(start_value) else date.today())
        days = c.number_input("Giorni noleggio *", min_value=0, step=1, value=int((current or {}).get("rental_days", 1)))
        source = st.text_input("Fonte completa *", value=(current or {}).get("source_raw", ""))
        d, e, f = st.columns(3)
        operator = d.text_input("Operatore", value=(current or {}).get("operator", ""))
        vehicle = e.selectbox("Tipo veicolo", ["", "CAR", "VAN"], index=["", "CAR", "VAN"].index((current or {}).get("vehicle_type", "") or ""))
        rental_type = f.text_input("Tipo noleggio", value=(current or {}).get("rental_type", ""))
        g, h = st.columns(2)
        ancillary = g.text_input("Ancillary", value=(current or {}).get("ancillary", ""))
        existing_cost = (current or {}).get("ancillary_cost")
        cost = h.number_input("Costo ancillary IVA esclusa", min_value=0.0, step=0.01, value=float(existing_cost) if pd.notna(existing_cost) else 0.0)
        calculated_rpd = cost / days if days else 0
        st.info(f"RPD calcolato automaticamente: € {calculated_rpd:.2f}")
        notes = st.text_area("Note", value=(current or {}).get("notes", ""))
        submitted = st.form_submit_button("Salva record", type="primary", use_container_width=True)
    if submitted:
        if not ra or not source:
            st.error("Inserisci almeno RA e fonte.")
        else:
            save_record({
                "id": (current or {}).get("id"), "created_at": (current or {}).get("created_at"),
                "ra": ra, "start_date": start_date.isoformat(), "rental_days": days,
                "source_raw": source, "operator": operator, "vehicle_type": vehicle,
                "ancillary": ancillary, "ancillary_cost": cost if cost > 0 else None,
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


def import_backup():
    st.header("Importazione e backup")
    st.subheader("Backup completo")
    if DB_PATH.exists():
        st.download_button("Scarica database", DB_PATH.read_bytes(), "ancillary_backup.db", mime="application/octet-stream", use_container_width=True)
    st.caption("Conserva periodicamente il file di backup in una posizione sicura.")
    st.subheader("Importa dati da Excel")
    upload = st.file_uploader("File Excel con le colonne del riepilogo ancillary", type=["xlsx"])
    if upload and st.button("Importa e normalizza", type="primary"):
        imported = pd.read_excel(upload, sheet_name=0, dtype=object)
        required = {"RA (Rental Agreement)", "DATA INIZIO NOLEGGIO", "GIORNI NOLEGGIO", "FONTE"}
        if not required.issubset(imported.columns):
            st.error("Il file non contiene tutte le colonne obbligatorie.")
            return
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
        st.success(f"Importati {added} record.")
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

st.title("Gestionale Ancillary")
st.caption("Archivio autonomo, statistiche e controllo delle vendite ancillary")
all_data = load_data()
if all_data.empty:
    st.warning("Archivio vuoto. Importa il file Excel dalla sezione Importazione e backup.")
filtered = filters(all_data) if not all_data.empty else all_data
page = st.sidebar.radio("Sezione", ["Dashboard", "Archivio", "Inserimento / modifica", "Importazione e backup"])

if page == "Dashboard":
    dashboard(filtered)
elif page == "Archivio":
    archive(filtered)
elif page == "Inserimento / modifica":
    record_form(all_data)
else:
    import_backup()
