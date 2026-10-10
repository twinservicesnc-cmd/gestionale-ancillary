"""Servizi Delivery / Collection e import dello storico trasferimenti."""
import hashlib
import calendar
import io
import json
import math
import re
import uuid
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from zoneinfo import ZoneInfo

import pandas as pd

SERVICE_TYPES = ['Delivery', 'Collection', 'Trasferimento']
STATUSES = ['Da fare', 'Completato', 'Annullato', 'Da verificare']
FIELDS = ['service_type', 'service_date', 'service_time', 'driver', 'plate',
          'client', 'departure', 'destination', 'cost', 'status', 'notes']
LABELS = dict(zip(FIELDS, ['Tipo servizio', 'Data', 'Ora', 'Autista', 'Targa',
                         'Cliente', 'Partenza', 'Destinazione / indirizzo',
                         'Costo (€)', 'Stato', 'Note']))
EXTRA_FIELDS = ['km', 'fuel_cost', 'extra_cost', 'requested_by', 'vehicle_type', 'cost_per_km', 'reference']
LABELS.update(dict(zip(EXTRA_FIELDS, ['Km', 'Carburante (€)', 'Spese extra (€)', 'Commissionato da', 'Tipo veicolo'])))
LABELS.update(cost_per_km='Costo al km (€)', reference='Riferimento')
SIMPLE_FIELDS = ['vehicle_type', 'plate', 'service_date', 'departure', 'destination',
                 'km', 'reference', 'requested_by', 'driver', 'cost_per_km', 'cost']
LABELS.update(vehicle_type='Tipologia', service_date='Data trasferimento',
              departure='Luogo ritiro', destination='Luogo consegna',
              cost='Totale km × costo al km (€)', total_cost='Totale complessivo (€)')
SUMMARY_FIELDS = SIMPLE_FIELDS + ['extra_cost', 'fuel_cost', 'total_cost']
REPORT_FIELDS = SUMMARY_FIELDS + ['service_type', 'service_time', 'client', 'status', 'notes']
ALL_FIELDS = FIELDS + EXTRA_FIELDS


def text(value):
    return '' if value is None or pd.isna(value) else str(value).strip()


def init_services(db):
    with db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS delivery_collection (
            id TEXT PRIMARY KEY, service_type TEXT NOT NULL, service_date TEXT NOT NULL,
            service_time TEXT NOT NULL DEFAULT '', driver TEXT NOT NULL DEFAULT '',
            plate TEXT NOT NULL, client TEXT NOT NULL DEFAULT '',
            departure TEXT NOT NULL DEFAULT '', destination TEXT NOT NULL DEFAULT '',
            cost REAL, status TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
            source_key TEXT UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL DEFAULT '')''')
        existing = {row[1] for row in conn.execute('PRAGMA table_info(delivery_collection)')}
        for field in EXTRA_FIELDS:
            if field not in existing:
                kind = 'REAL' if field in ['km', 'fuel_cost', 'extra_cost', 'cost_per_km'] else 'TEXT'
                conn.execute(f'ALTER TABLE delivery_collection ADD COLUMN {field} {kind}')


def validate_service(record):
    result = {key: text(record.get(key)) for key in FIELDS if key != 'cost'}
    result['plate'] = re.sub(r'[^A-Z0-9]', '', result['plate'].upper())
    if not result['plate']:
        raise ValueError('Inserisci la targa.')
    if result['service_type'] not in SERVICE_TYPES or result['status'] not in STATUSES:
        raise ValueError('Tipo di servizio o stato non valido.')
    date.fromisoformat(result['service_date'])
    if result['service_time']:
        time.fromisoformat(result['service_time'])
    cost = record.get('cost')
    result['cost'] = None if cost is None or text(cost) == '' else float(cost)
    if result['cost'] is not None and (not math.isfinite(result['cost']) or result['cost'] < 0):
        raise ValueError('Il costo deve essere un numero positivo o zero.')
    for field in EXTRA_FIELDS:
        value = record.get(field)
        if field in ['km', 'fuel_cost', 'extra_cost', 'cost_per_km']:
            result[field] = None if not text(value) else float(value)
            if result[field] is not None and (not math.isfinite(result[field]) or result[field] < 0):
                raise ValueError(f'{LABELS[field]}: inserisci un numero positivo o zero.')
        else:
            result[field] = text(value)
    if result['cost_per_km'] is not None:
        result['cost'] = calculate_cost(result['km'], result['cost_per_km'])
    return result


def calculate_cost(km, cost_per_km):
    if km is None or cost_per_km is None:
        raise ValueError('Inserisci km e costo al km.')
    values = [float(km), float(cost_per_km)]
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError('Km e costo al km devono essere positivi o zero.')
    return float((Decimal(str(km)) * Decimal(str(cost_per_km))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def overall_cost(base, extra, fuel=None):
    if base is None or pd.isna(base):
        return None
    extra = 0 if extra is None or pd.isna(extra) else extra
    fuel = 0 if fuel is None or pd.isna(fuel) else fuel
    return float((Decimal(str(base)) + Decimal(str(extra)) + Decimal(str(fuel))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def service_report(frame):
    report = frame.copy()
    report['cost'] = [calculate_cost(row['km'], row['cost_per_km'])
        if pd.notna(row['km']) and pd.notna(row['cost_per_km']) else row['cost']
        for row in report.to_dict('records')]
    report['total_cost'] = [overall_cost(row['cost'], row['extra_cost'], row['fuel_cost']) for row in report.to_dict('records')]
    result = report[REPORT_FIELDS].rename(columns=LABELS)
    result[LABELS['service_date']] = pd.to_datetime(result[LABELS['service_date']])
    return result


def services_excel(report, table_to_excel):
    from openpyxl import load_workbook
    out = io.BytesIO()
    workbook = load_workbook(io.BytesIO(table_to_excel(report, 'Delivery Collection')))
    sheet = workbook.active
    money_fields = ['cost_per_km', 'cost', 'extra_cost', 'total_cost', 'fuel_cost']
    for field in money_fields:
        index = list(report.columns).index(LABELS[field]) + 1
        for row in range(2, sheet.max_row + 1):
            sheet.cell(row, index).number_format = '#,##0.00 "€"'
    date_index = list(report.columns).index(LABELS['service_date']) + 1
    for row in range(2, sheet.max_row + 1):
        sheet.cell(row, date_index).number_format = 'dd/mm/yyyy'
    workbook.save(out)
    return out.getvalue()


def period_bounds(mode, year, month=1, end_year=None, end_month=None):
    if mode == 'Tutto lo storico':
        return None, None
    if mode == 'Un anno':
        return date(year, 1, 1), date(year, 12, 31)
    start = date(year, month, 1)
    if mode == 'Un mese':
        end_year, end_month = year, month
    end = date(end_year, end_month, calendar.monthrange(end_year, end_month)[1])
    if end < start:
        raise ValueError('Il mese finale deve essere uguale o successivo al mese iniziale.')
    return start, end


def save_service(db, record, username='', identifier=None):
    payload = validate_service(record)
    now = datetime.now(ZoneInfo('Europe/Rome')).isoformat(timespec='seconds')
    with db() as conn:
        if identifier:
            assignments = ','.join(f'{field}=?' for field in ALL_FIELDS)
            updated = conn.execute(f'UPDATE delivery_collection SET {assignments}, updated_at=?, updated_by=? WHERE id=?',
                                  [payload[field] for field in ALL_FIELDS] + [now, username, identifier]).rowcount
            if not updated:
                raise ValueError('Servizio non trovato. Riapri l’elenco.')
        else:
            identifier = str(uuid.uuid4())
            columns = ['id'] + ALL_FIELDS + ['created_at', 'updated_at', 'updated_by']
            conn.execute(f"INSERT INTO delivery_collection ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                         [identifier] + [payload[field] for field in ALL_FIELDS] + [now, now, username])
    return identifier


def _column(value):
    return re.sub(r'\s+', ' ', text(value).upper()).strip()


def parse_services(file_bytes):
    """Read all source tabs; report invalid rows without silently losing them."""
    records, errors = [], []
    book = pd.ExcelFile(io.BytesIO(file_bytes))
    recognized = False
    aliases = {
        'service_type': ['TUPI DI SERVIZIO', 'TIPI DI SERVIZIO', 'TIPO DI SERVIZIO', 'TIPO SERVIZIO'],
        'service_date': ['DATA', 'DATA DEL SERVIZIO', 'DATA TRAFERIMENTO', 'DATA TRASFERIMENTO', 'DATA TRASFERIMENTO'],
        'service_time': ['ORARIO', 'ORARIO DI RITIRO', 'ORA'],
        'driver': ['AUTISTA'], 'plate': ['TARGA', 'TARGA AUTO-VAN'], 'client': ['CLIENTE'],
        'departure': ['LUOGO DI PARTENZA', 'PARTENZA', 'LUOGO RITIRO'],
        'destination': ['INDIRIZZO DI CONSEGNA', 'LUOGO DI DESTINAZIONE', 'DESTINAZIONE / INDIRIZZO', 'LUOGO CONSEGNA'],
        'cost': ['COSTO', 'COSTO (€)', 'TOTALE KM × COSTO AL KM (€)'], 'notes': ['NOTE'], 'status': ['STATO'],
        'km': ['KM'], 'fuel_cost': ['CARBURANTE', 'CARBURANTE (€)'],
        'extra_cost': ['SPESE EXTRA', 'SPESE EXTRA (€)'],
        'requested_by': ['COMMISSIONATA DA:', 'COMMISSIONATA DA', 'COMMISSIONATO DA'],
        'vehicle_type': ['TIPOLOGIA', 'TIPO VEICOLO'],
        'cost_per_km': ['COSTO AL KM', 'COSTO A KM', 'COSTO AL KM (€)'],
        'reference': ['RIFERIMENTO'],
    }
    for sheet in book.sheet_names:
        frame = pd.read_excel(book, sheet_name=sheet, dtype=object)
        names = {_column(column): column for column in frame.columns}
        if not any(name in names for name in aliases['plate']) or not any(name in names for name in aliases['service_date']):
            continue
        recognized = True
        for index, row in frame.iterrows():
            def field(key):
                return next((row[names[name]] for name in aliases[key] if name in names), None)
            if not text(field('plate')):
                if any(text(field(key)) for key in ['driver', 'client', 'destination']):
                    errors.append(f'{sheet}, riga {index + 2}: targa mancante.')
                continue
            if _column(field('plate')) in aliases['plate']:
                continue
            try:
                raw_date = field('service_date')
                if not text(raw_date) or isinstance(raw_date, (int, float)):
                    raise ValueError('Data servizio mancante o non valida.')
                parsed_date = pd.to_datetime(raw_date, dayfirst=True, errors='raise').date().isoformat()
                raw_time = field('service_time')
                if not text(raw_time):
                    hour = ''
                elif isinstance(raw_time, (time, datetime)):
                    hour = raw_time.strftime('%H:%M')
                elif isinstance(raw_time, (int, float)) and 0 <= raw_time < 1:
                    minutes = round(raw_time * 1440) % 1440
                    hour = f'{minutes // 60:02d}:{minutes % 60:02d}'
                else:
                    hour = time.fromisoformat(text(raw_time)).strftime('%H:%M')
                raw_type = text(field('service_type')).upper()
                kind = {'DELIVERY': 'Delivery', 'CONSEGNA': 'Delivery', 'COLLECTION': 'Collection',
                        'RITIRO': 'Collection', 'TRASFERIMENTO': 'Trasferimento', '': 'Trasferimento'}.get(raw_type)
                if not kind:
                    raise ValueError('Tipo servizio non riconosciuto: ' + raw_type)
                def amount(value):
                    if isinstance(value, str):
                        value = value.replace('€', '').replace(' ', '')
                        if value in ['.', '-', '—']:
                            return None
                        if ',' in value:
                            value = value.replace('.', '').replace(',', '.')
                    return None if not text(value) else value
                raw_cost = amount(field('cost'))
                notes = text(field('notes'))
                placeholders = [f'{LABELS[key]}: {text(field(key))}' for key in ['cost', 'km', 'fuel_cost', 'extra_cost']
                                if text(field(key)) in ['.', '-', '—']]
                if placeholders:
                    notes = '\n'.join(filter(None, [notes, 'Valori non indicati nel file: ' + '; '.join(placeholders)]))
                # The old form reused "Punteggio" for notes and sometimes contained dates.
                extra = row.get(names.get('PUNTEGGIO'))
                if text(extra):
                    notes = '\n'.join(filter(None, [notes, 'Campo Punteggio: ' + text(extra)]))
                unlabeled = [text(row[column]) for column in frame.columns
                             if str(column).startswith('Unnamed:') and text(row[column])]
                if unlabeled:
                    notes = '\n'.join(filter(None, [notes, 'Valori senza intestazione: ' + ' | '.join(unlabeled)]))
                payload = validate_service(dict(service_type=kind, service_date=parsed_date,
                    service_time=hour, driver=text(field('driver')), plate=text(field('plate')),
                    client=text(field('client')), departure=text(field('departure')),
                    destination=text(field('destination')), cost=None if not text(raw_cost) else raw_cost,
                    status=text(field('status')) or 'Da verificare', notes=notes,
                    **{key: amount(field(key)) if key in ['km', 'fuel_cost', 'extra_cost', 'cost_per_km'] else
                       (text(field(key)) or (unlabeled[0] if unlabeled else '')) if key == 'reference' else text(field(key)) for key in EXTRA_FIELDS}))
                stamp = text(row.get(names.get('INFORMAZIONI CRONOLOGICHE')))
                fingerprint = {key: value for key, value in payload.items() if key not in ['reference', 'cost_per_km']}
                if payload['cost_per_km'] is not None:
                    fingerprint['cost_per_km'] = payload['cost_per_km']
                key = hashlib.sha256(json.dumps([fingerprint, stamp], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                records.append(dict(payload, source_key=key))
            except (ValueError, TypeError, OverflowError) as exc:
                errors.append(f'{sheet}, riga {index + 2}: {exc}')
    if not recognized:
        raise ValueError('File non riconosciuto: servono Targa e Data / Data del servizio.')
    return records, errors


def import_services(db, records, username=''):
    now = datetime.now(ZoneInfo('Europe/Rome')).isoformat(timespec='seconds')
    added = duplicate = 0
    with db() as conn:
        for record in records:
            payload = validate_service(record)
            columns = ['id'] + ALL_FIELDS + ['source_key', 'created_at', 'updated_at', 'updated_by']
            result = conn.execute(f"INSERT INTO delivery_collection ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) ON CONFLICT(source_key) DO NOTHING",
                [str(uuid.uuid4())] + [payload[field] for field in ALL_FIELDS] + [record['source_key'], now, now, username])
            added += result.rowcount
            duplicate += 1 - result.rowcount
    return added, duplicate


def services_pdf(frame):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=landscape(A4), leftMargin=24, rightMargin=24)
    styles = getSampleStyleSheet()
    styles['Title'].fontName = 'Helvetica-Bold'
    small = styles['BodyText'].clone('Services'); small.fontSize = 8; small.leading = 10; small.fontName = 'Helvetica'
    full_report = frame.copy()
    frame = frame[[LABELS[field] for field in SUMMARY_FIELDS]].copy()
    frame[LABELS['service_date']] = pd.to_datetime(frame[LABELS['service_date']]).dt.strftime('%d/%m/%Y')
    for field in ['cost_per_km', 'cost', 'extra_cost', 'fuel_cost', 'total_cost']:
        frame[LABELS[field]] = frame[LABELS[field]].map(lambda value: '' if pd.isna(value) else f'{value:.2f}')
    rows = [[Paragraph(escape(str(column)), small) for column in frame.columns]]
    rows += [[Paragraph(escape('' if pd.isna(value) else str(value)).replace('\n', '<br/>'), small)
              for value in row] for row in frame.itertuples(index=False, name=None)]
    widths = [43, 48, 60, 65, 65, 32, 55, 58, 48, 52, 67, 60, 60, 70]
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e5edf5')),
                              ('GRID', (0,0), (-1,-1), .3, colors.lightgrey),
                              ('VALIGN', (0,0), (-1,-1), 'TOP')]))
    total = full_report[LABELS['total_cost']].sum()
    missing = full_report[LABELS['total_cost']].isna().sum()
    story = [Paragraph('Delivery / Collection', styles['Title']),
             Paragraph(f'{len(frame)} servizi · Totale complessivo degli importi indicati: € {total:.2f} · {missing} senza totale', small),
             Spacer(1,12), table]
    story += [Spacer(1,12), Paragraph('Dettagli facoltativi', styles['Heading2'])]
    for row in full_report.to_dict('records'):
        details = [f'{LABELS[field]}: {text(row[LABELS[field]])}'
                   for field in ['service_type', 'service_time', 'client', 'status', 'fuel_cost', 'notes']
                   if text(row[LABELS[field]])]
        if details:
            label = f"{text(row[LABELS['plate']])} · {pd.Timestamp(row[LABELS['service_date']]).strftime('%d/%m/%Y')}"
            story += [Paragraph(escape(label + ' — ' + '; '.join(details)).replace('\n', '<br/>'), small), Spacer(1,6)]
    doc.build(story)
    return out.getvalue()


def services_page(st, db, table_to_excel, current_user):
    init_services(db)
    username = current_user().get('username', '')
    st.header('Delivery / Collection')
    flash = st.session_state.pop('services_flash', None)
    if flash:
        st.success(flash)
    with db() as conn:
        frame = pd.read_sql_query('SELECT * FROM delivery_collection ORDER BY service_date DESC, service_time DESC, created_at DESC', conn)
    today = datetime.now(ZoneInfo('Europe/Rome')).date()
    stored_years = pd.to_datetime(frame['service_date'], errors='coerce').dt.year.dropna().astype(int).tolist()
    years = list(range(min(stored_years + [today.year]), max(stored_years + [today.year]) + 1))
    months = ['Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno',
              'Luglio', 'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre']
    period = st.selectbox('Periodo dello storico', ['Tutto lo storico', 'Un mese', 'Più mesi', 'Un anno'])
    start, end = None, None
    if period == 'Un anno':
        year = st.selectbox('Anno', years, index=years.index(today.year))
        start, end = period_bounds(period, year)
    elif period in ['Un mese', 'Più mesi']:
        a, b = st.columns(2)
        month = a.selectbox('Mese' if period == 'Un mese' else 'Dal mese', list(range(1, 13)),
                            index=today.month - 1, format_func=lambda value: months[value - 1])
        year = b.selectbox('Anno' if period == 'Un mese' else 'Anno iniziale', years, index=years.index(today.year))
        end_year, end_month = year, month
        if period == 'Più mesi':
            a, b = st.columns(2)
            end_month = a.selectbox('Al mese', list(range(1, 13)), index=today.month - 1,
                                    format_func=lambda value: months[value - 1])
            end_year = b.selectbox('Anno finale', years, index=years.index(today.year))
        try:
            start, end = period_bounds(period, year, month, end_year, end_month)
        except ValueError as exc:
            st.error(str(exc))
            return
    a, b = st.columns(2)
    kind = a.selectbox('Tipo', ['Tutti'] + SERVICE_TYPES)
    status = b.selectbox('Stato', ['Tutti'] + STATUSES)
    search = st.text_input('Cerca targa, autista o cliente')
    shown = frame.copy()
    if start is not None:
        shown = shown[shown.service_date.between(start.isoformat(), end.isoformat())]
        st.caption(f'Periodo: {start.strftime("%d/%m/%Y")} – {end.strftime("%d/%m/%Y")}. Excel e PDF rispettano il periodo e gli altri filtri.')
    if kind != 'Tutti': shown = shown[shown.service_type.eq(kind)]
    if status != 'Tutti': shown = shown[shown.status.eq(status)]
    if search.strip():
        shown = shown[shown[['plate', 'driver', 'client']].fillna('').apply(
            lambda column: column.str.contains(search.strip(), case=False, regex=False)).any(axis=1)]
    report = service_report(shown)
    display = report[[LABELS[field] for field in SUMMARY_FIELDS]]
    st.dataframe(display, hide_index=True, use_container_width=True)
    st.caption(f"{len(shown)} servizi · Totale complessivo: € {report[LABELS['total_cost']].sum():.2f} · {report[LABELS['total_cost']].isna().sum()} senza totale")
    if not shown.empty:
        a, b = st.columns(2)
        a.download_button('Scarica Excel', services_excel(report, table_to_excel), 'delivery_collection.xlsx',
                          mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        b.download_button('Scarica PDF', services_pdf(report), 'delivery_collection.pdf', mime='application/pdf')
    choices = {r['id']: r for r in shown.to_dict('records')}
    selected = st.selectbox('Nuovo servizio o modifica uno esistente', [''] + list(choices),
        format_func=lambda key: '➕ Nuovo servizio' if not key else
        f"{choices[key]['service_date']} · {choices[key]['service_type']} · {choices[key]['plate']} · {choices[key]['driver']}")
    record = choices.get(selected, {})
    nonce = st.session_state.get('services_nonce', 0)
    with st.form(f'service_{selected}_{nonce}'):
        vehicle_types = ['AUTO', 'VAN']
        stored_type = text(record.get('vehicle_type')).upper()
        vehicle_type = st.radio('Tipologia *', vehicle_types, horizontal=True,
                               index=vehicle_types.index(stored_type) if stored_type in vehicle_types else None)
        a, b = st.columns(2)
        plate = a.text_input('Targa *', value=record.get('plate', ''))
        service_date = b.date_input('Data trasferimento *', value=date.fromisoformat(record['service_date']) if record else datetime.now(ZoneInfo('Europe/Rome')).date())
        a, b = st.columns(2)
        departure = a.text_input('Luogo ritiro *', value=record.get('departure', ''))
        destination = b.text_input('Luogo consegna *', value=record.get('destination', ''))
        km_value = record.get('km')
        km = st.number_input('Km *', min_value=0.0, value=None if km_value is None or pd.isna(km_value) else float(km_value), step=1.0)
        reference = st.text_input('Riferimento', value=text(record.get('reference')),
                                  help='Corrisponde al campo senza titolo del modulo originale.')
        requested_by = st.text_input('Commissionato da *', value=text(record.get('requested_by')))
        a, b = st.columns(2)
        driver = a.text_input('Autista *', value=record.get('driver', ''))
        rate_value = record.get('cost_per_km')
        rate = b.number_input('Costo al km (€) *', min_value=0.0,
            value=None if rate_value is None or pd.isna(rate_value) else float(rate_value), step=0.01, format='%.2f')
        st.caption('Totale automatico = km × costo al km. Calcolato al salvataggio.')
        with st.expander('Dettagli facoltativi'):
            a, b = st.columns(2)
            service_type = a.selectbox('Tipo di servizio', SERVICE_TYPES,
                index=SERVICE_TYPES.index(record.get('service_type', 'Trasferimento')))
            service_time = b.text_input('Ora (HH:MM)', value=text(record.get('service_time')), placeholder='09:30')
            a, b = st.columns(2)
            client = a.text_input('Cliente', value=text(record.get('client')))
            status = b.selectbox('Stato servizio', STATUSES,
                index=STATUSES.index(record.get('status', 'Da fare')))
            a, b = st.columns(2)
            optional_costs = {}
            for field, column in [('fuel_cost', a), ('extra_cost', b)]:
                value = record.get(field)
                optional_costs[field] = column.number_input(LABELS[field], min_value=0.0,
                    value=None if value is None or pd.isna(value) else float(value), step=1.0)
            st.caption('Totale complessivo = km × costo al km + spese extra + carburante.')
            notes = st.text_area('Note', value=text(record.get('notes')))
        submitted = st.form_submit_button('Salva servizio', type='primary')
    if submitted:
        try:
            if not all([vehicle_type, plate.strip(), departure.strip(), destination.strip(), requested_by.strip(), driver.strip()]):
                raise ValueError('Compila i campi obbligatori indicati con *.')
            cost = calculate_cost(km, rate)
            # Preserve legacy data omitted from the simplified form.
            payload = {field: record.get(field) for field in ALL_FIELDS}
            payload.update(service_type=service_type, service_time=service_time, client=client,
                status=status, notes=notes, **optional_costs, service_date=service_date.isoformat(),
                driver=driver, plate=plate, departure=departure, destination=destination,
                km=km, reference=reference, requested_by=requested_by,
                vehicle_type=vehicle_type, cost_per_km=rate, cost=cost)
            save_service(db, payload, username, selected or None)
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
        else:
            st.session_state['services_nonce'] = nonce + 1
            total = overall_cost(cost, optional_costs['extra_cost'], optional_costs['fuel_cost'])
            st.session_state['services_flash'] = f'Servizio salvato. Costo km: € {cost:.2f}. Totale con extra e carburante: € {total:.2f}.'
            st.rerun()
    with st.expander('Importa storico da Excel'):
        st.caption('Legge tutti i fogli. Le righe storiche senza tipo diventano Trasferimento; lo stato resta Da verificare. Reimportare lo stesso file non duplica i servizi.')
        uploaded = st.file_uploader('File trasferimenti o Delivery / Collection', type=['xlsx'], key='services_import')
        if uploaded:
            try:
                rows, errors = parse_services(uploaded.getvalue())
            except Exception as exc:
                st.error(f'Impossibile leggere il file: {exc}')
            else:
                st.write(f'{len(rows)} righe valide da importare.')
                if errors:
                    st.warning('Righe escluse: ' + '\n'.join(errors))
                if rows:
                    st.dataframe(pd.DataFrame(rows)[ALL_FIELDS].rename(columns=LABELS), hide_index=True, use_container_width=True)
                    if st.button('Importa righe valide', key='services_confirm_import'):
                        added, duplicate = import_services(db, rows, username)
                        st.session_state['services_flash'] = f'Importati {added} servizi; {duplicate} già presenti.'
                        st.rerun()
