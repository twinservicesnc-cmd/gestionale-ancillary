"""Servizi Delivery / Collection e import dello storico trasferimenti."""
import hashlib
import io
import json
import math
import re
import uuid
from datetime import date, datetime, time
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
EXTRA_FIELDS = ['km', 'fuel_cost', 'extra_cost', 'requested_by', 'vehicle_type']
LABELS.update(dict(zip(EXTRA_FIELDS, ['Km', 'Carburante (€)', 'Spese extra (€)', 'Commissionato da', 'Tipo veicolo'])))
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
                kind = 'REAL' if field in ['km', 'fuel_cost', 'extra_cost'] else 'TEXT'
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
        if field in ['km', 'fuel_cost', 'extra_cost']:
            result[field] = None if not text(value) else float(value)
            if result[field] is not None and (not math.isfinite(result[field]) or result[field] < 0):
                raise ValueError(f'{LABELS[field]}: inserisci un numero positivo o zero.')
        else:
            result[field] = text(value)
    return result


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
        'service_date': ['DATA', 'DATA DEL SERVIZIO', 'DATA TRAFERIMENTO', 'DATA TRASFERIMENTO'],
        'service_time': ['ORARIO', 'ORARIO DI RITIRO', 'ORA'],
        'driver': ['AUTISTA'], 'plate': ['TARGA', 'TARGA AUTO-VAN'], 'client': ['CLIENTE'],
        'departure': ['LUOGO DI PARTENZA', 'PARTENZA', 'LUOGO RITIRO'],
        'destination': ['INDIRIZZO DI CONSEGNA', 'LUOGO DI DESTINAZIONE', 'DESTINAZIONE / INDIRIZZO', 'LUOGO CONSEGNA'],
        'cost': ['COSTO', 'COSTO (€)'], 'notes': ['NOTE'], 'status': ['STATO'],
        'km': ['KM'], 'fuel_cost': ['CARBURANTE', 'CARBURANTE (€)'],
        'extra_cost': ['SPESE EXTRA', 'SPESE EXTRA (€)'],
        'requested_by': ['COMMISSIONATA DA:', 'COMMISSIONATA DA', 'COMMISSIONATO DA'],
        'vehicle_type': ['TIPOLOGIA', 'TIPO VEICOLO'],
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
                    **{key: amount(field(key)) if key in ['km', 'fuel_cost', 'extra_cost'] else text(field(key)) for key in EXTRA_FIELDS}))
                stamp = text(row.get(names.get('INFORMAZIONI CRONOLOGICHE')))
                key = hashlib.sha256(json.dumps([payload, stamp], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
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
    frame = frame.copy()
    frame['Data'] = pd.to_datetime(frame['Data']).dt.strftime('%d/%m/%Y')
    rows = [[Paragraph(escape(str(column)), small) for column in frame.columns]]
    rows += [[Paragraph(escape('' if pd.isna(value) else str(value)).replace('\n', '<br/>'), small)
              for value in row] for row in frame.itertuples(index=False, name=None)]
    widths = [68, 62, 42, 65, 65, 70, 65, 116, 50, 75, 115]
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e5edf5')),
                              ('GRID', (0,0), (-1,-1), .3, colors.lightgrey),
                              ('VALIGN', (0,0), (-1,-1), 'TOP')]))
    doc.build([Paragraph('Delivery / Collection', styles['Title']), Spacer(1,12), table])
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
    a, b = st.columns(2)
    kind = a.selectbox('Tipo', ['Tutti'] + SERVICE_TYPES)
    status = b.selectbox('Stato', ['Tutti'] + STATUSES)
    search = st.text_input('Cerca targa, autista o cliente')
    shown = frame.copy()
    if kind != 'Tutti': shown = shown[shown.service_type.eq(kind)]
    if status != 'Tutti': shown = shown[shown.status.eq(status)]
    if search.strip():
        shown = shown[shown[['plate', 'driver', 'client']].fillna('').apply(
            lambda column: column.str.contains(search.strip(), case=False, regex=False)).any(axis=1)]
    display = shown[FIELDS].rename(columns=LABELS)
    st.dataframe(display, hide_index=True, use_container_width=True)
    st.caption(f'{len(shown)} servizi · Costi indicati: € {shown.cost.sum():.2f} · {shown.cost.isna().sum()} senza costo')
    if not shown.empty:
        a, b = st.columns(2)
        a.download_button('Scarica Excel', table_to_excel(shown[ALL_FIELDS].rename(columns=LABELS), 'Delivery Collection'), 'delivery_collection.xlsx',
                          mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        pdf_display = display.copy()
        pdf_display['Note'] = [ '\n'.join(filter(None, [text(row['notes'])] +
            [f'{LABELS[field]}: {text(row[field])}' for field in EXTRA_FIELDS if text(row[field])]))
            for row in shown.to_dict('records')]
        b.download_button('Scarica PDF', services_pdf(pdf_display), 'delivery_collection.pdf', mime='application/pdf')
    choices = {r['id']: r for r in shown.to_dict('records')}
    selected = st.selectbox('Nuovo servizio o modifica uno esistente', [''] + list(choices),
        format_func=lambda key: '➕ Nuovo servizio' if not key else
        f"{choices[key]['service_date']} · {choices[key]['service_type']} · {choices[key]['plate']} · {choices[key]['driver']}")
    record = choices.get(selected, {})
    nonce = st.session_state.get('services_nonce', 0)
    with st.form(f'service_{selected}_{nonce}'):
        a, b, c = st.columns(3)
        service_type = a.selectbox('Servizio', SERVICE_TYPES, index=SERVICE_TYPES.index(record.get('service_type', 'Delivery')))
        service_date = b.date_input('Data *', value=date.fromisoformat(record['service_date']) if record else datetime.now(ZoneInfo('Europe/Rome')).date())
        service_time = c.text_input('Ora (HH:MM)', value=record.get('service_time', ''), placeholder='09:30')
        a, b = st.columns(2)
        driver = a.text_input('Autista', value=record.get('driver', ''))
        plate = b.text_input('Targa *', value=record.get('plate', ''))
        client = st.text_input('Cliente', value=record.get('client', ''))
        a, b = st.columns(2)
        departure = a.text_input('Partenza', value=record.get('departure', ''))
        destination = b.text_input('Destinazione / indirizzo', value=record.get('destination', ''))
        a, b = st.columns(2)
        cost = a.number_input('Costo (€) — facoltativo', min_value=0.0,
            value=None if record.get('cost') is None or pd.isna(record.get('cost')) else float(record['cost']), step=1.0)
        new_status = b.selectbox('Stato servizio', STATUSES, index=STATUSES.index(record.get('status', 'Da fare')))
        notes = st.text_area('Note', value=record.get('notes', ''))
        extras = {}
        with st.expander('Dettagli facoltativi: km, spese e committente'):
            for field in EXTRA_FIELDS:
                if field in ['km', 'fuel_cost', 'extra_cost']:
                    value = record.get(field)
                    extras[field] = st.number_input(LABELS[field], min_value=0.0,
                        value=None if value is None or pd.isna(value) else float(value), step=1.0)
                else:
                    extras[field] = st.text_input(LABELS[field], value=text(record.get(field)))
        submitted = st.form_submit_button('Salva servizio', type='primary')
    if submitted:
        try:
            save_service(db, dict(service_type=service_type, service_date=service_date.isoformat(),
                service_time=service_time, driver=driver, plate=plate, client=client,
                departure=departure, destination=destination, cost=cost, status=new_status, notes=notes, **extras), username, selected or None)
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
        else:
            st.session_state['services_nonce'] = nonce + 1
            st.session_state['services_flash'] = 'Servizio salvato.'
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
