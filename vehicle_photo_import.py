"""Import reviewed vehicle data transcribed from photographed paper sheets."""
import copy
import hashlib
import json
import re
from datetime import date

import pandas as pd

STATES = ['Da verificare', 'Presente', 'Non presente', 'Non previsto']
ACCESSORIES = ['Triangolo', 'Giubbotto riflettente', 'Libretto', 'Assicurazione',
               'Cavo elettrico domestico', 'Cavo elettrico per colonnina',
               'Modulo CAI', 'Cappelliera']
STATE_LABELS = dict(zip(ACCESSORIES, ['Triangolo', 'Giubbino', 'Libretto', 'Assicurazione',
                                   'Cavo domestico', 'Cavo colonnina', 'CAI', 'Cappelliera']))


def clean(value):
    return '' if value is None or pd.isna(value) else str(value).strip()


def plate(value):
    return re.sub(r'[^A-Z0-9]', '', clean(value).upper())


def append_note(previous, incoming):
    lines = [line.strip() for line in clean(previous).split('\n') if line.strip()]
    for line in clean(incoming).split('\n'):
        if line.strip() and line.strip() not in lines:
            lines.append(line.strip())
    return '\n'.join(lines)


def read_batch(raw):
    document = json.loads(raw)
    if not isinstance(document, dict) or document.get('format') != 'vehicle_photo_review_v1':
        raise ValueError('File non riconosciuto: carica il JSON di importazione dalle foto.')
    records = document.get('vehicles')
    if not isinstance(records, list) or not records or len(records) > 500:
        raise ValueError('Il file deve contenere da 1 a 500 vetture.')
    seen = set()
    for record in records:
        value = plate(record.get('plate'))
        if not re.fullmatch(r'[A-Z]{2}[0-9]{3}[A-Z]{2}', value) or value in seen:
            raise ValueError('Targa non valida o duplicata: ' + value)
        seen.add(value)
        record['plate'] = value
        for item, state in record.get('accessories', {}).items():
            if item not in ACCESSORIES or state not in STATES:
                raise ValueError('Accessorio o stato non valido: ' + item)
    return document


def review_frame(document):
    rows = []
    for record in document['vehicles']:
        row = {'Importa': True, 'Targa': record['plate'],
               'Danni': record.get('damage_status', 'Da verificare'),
               'Descrizione danni': record.get('damage_notes', ''),
               'Lavori da eseguire': record.get('repair_tasks', ''),
               'Note / da verificare': record.get('review_notes', '')}
        row.update({STATE_LABELS[item]: record.get('accessories', {}).get(item, 'Da verificare') for item in ACCESSORIES})
        rows.append(row)
    return pd.DataFrame(rows)


def reviewed_records(frame):
    result, seen = [], set()
    for row in frame.to_dict('records'):
        if not row.get('Importa'):
            continue
        value = plate(row.get('Targa'))
        if not re.fullmatch(r'[A-Z]{2}[0-9]{3}[A-Z]{2}', value) or value in seen:
            raise ValueError('Targa non valida o duplicata: ' + value)
        seen.add(value)
        status = clean(row.get('Danni'))
        if status not in ['Da verificare', 'Con danni', 'Senza danni']:
            raise ValueError('Stato danni non valido: ' + value)
        if status == 'Con danni' and not clean(row.get('Descrizione danni')):
            raise ValueError('Descrivi i danni per ' + value)
        accessories = {item: clean(row.get(STATE_LABELS[item])) for item in ACCESSORIES}
        if any(state not in STATES for state in accessories.values()):
            raise ValueError('Stato accessori non valido: ' + value)
        result.append(dict(plate=value, damage_status=status,
            damage_notes=clean(row.get('Descrizione danni')),
            repair_tasks=clean(row.get('Lavori da eseguire')),
            review_notes=clean(row.get('Note / da verificare')), accessories=accessories))
    return result


def merged_payload(record, previous, labels, operator, checked_at, event_id=''):
    payload = copy.deepcopy(previous) if previous else {
        'plate': record['plate'], 'brand': '', 'model': '', 'group': '', 'ra': '',
        'park_number': '', 'km': None, 'fuel': None, 'event_id': event_id, 'event_title': '',
        'damage_status': 'Da verificare', 'damage_notes': '', 'repair_tasks': '',
        'maintenance': 'Da verificare', 'maintenance_notes': '',
        'cleaning_inside': 'Da verificare', 'cleaning_outside': 'Da verificare',
        'preparation_status': 'Da completare', 'tires_ok': False,
        'tires': [{'Marca': '', 'Modello': '', 'Tipo': '', 'Misura': '', 'Stato / note': 'Da verificare'}],
        'notes': '', 'checklist': []}
    payload.update(plate=record['plate'], operator=operator, checked_at=checked_at,
                   preparation_operator=operator)
    if event_id:
        payload['event_id'] = event_id
    items = {item['Voce']: dict(item) for item in payload.get('checklist', [])}
    for label in labels:
        items.setdefault(label, {'Voce': label, 'Stato': 'Da verificare', 'Presente': False,
                                 'Non previsto': False, '_nuova_voce': True})
    for item, state in record['accessories'].items():
        if state == 'Da verificare':
            continue
        items[item] = {'Voce': item, 'Stato': state, 'Presente': state == 'Presente',
                       'Non previsto': state == 'Non previsto'}
    payload['checklist'] = list(items.values())
    if record['damage_status'] != 'Da verificare':
        payload['damage_status'] = record['damage_status']
    payload['damage_notes'] = append_note(payload.get('damage_notes'), record['damage_notes'])
    payload['repair_tasks'] = append_note(payload.get('repair_tasks'), record['repair_tasks'])
    if record['damage_status'] == 'Con danni' or record['repair_tasks']:
        payload['preparation_status'] = 'Da completare'
    if record['repair_tasks']:
        payload['maintenance'] = 'Sì — da effettuare'
    payload['notes'] = append_note(payload.get('notes'), 'Importazione da schede fotografate del 12/10/2026.')
    payload['notes'] = append_note(payload['notes'], record['review_notes'])
    return payload


def import_reviewed(records, db, save_vehicle_check, labels, operator, checked_at, event_id=''):
    if not clean(operator):
        raise ValueError('Indica l’operatore che verifica e importa i dati.')
    date.fromisoformat(checked_at)
    planned = []
    for record in records:
        with db() as conn:
            row = conn.execute("SELECT id,payload FROM vehicle_checks WHERE replace(replace(upper(plate),' ',''),'-','')=? ORDER BY checked_at DESC,updated_at DESC LIMIT 1", (record['plate'],)).fetchone()
        previous = json.loads(row[1]) if row else {}
        check_date = previous.get('checked_at') or checked_at
        payload = merged_payload(record, previous, labels, operator, check_date, event_id)
        # Compare data including review date/operator. Reimporting unchanged entries is a no-op.
        if previous != payload:
            planned.append((payload, row[0] if row else None))
    completed = 0
    for payload, identifier in planned:
        try:
            save_vehicle_check(payload, [], check_id=identifier)
        except Exception as exc:
            raise ValueError(f"Importate {completed} vetture prima dell’errore sulla targa {payload['plate']}: {exc}. Riprova: le righe già salvate non vengono duplicate.") from exc
        completed += 1
    return completed, len(records) - completed


def photo_import_panel(st, db, save_vehicle_check, labels, current_operator, load_events):
    with st.expander('📷 Importa dati ricavati dalle foto: accessori e danni'):
        st.caption('Carica il file JSON preparato dalle foto, correggi l’anteprima e importa. Le immagini da sole non vengono lette automaticamente da questa funzione.')
        uploaded = st.file_uploader('File dati vetture dalle foto (.json)', type=['json'], key='vehicle_photo_batch')
        if uploaded is None:
            return
        try:
            document = read_batch(uploaded.getvalue())
        except Exception as exc:
            st.error(str(exc)); return
        if document.get('unassigned'):
            st.warning('\n'.join(document['unassigned']))
        token = hashlib.sha256(uploaded.getvalue()).hexdigest()[:12]
        original = review_frame(document)
        edited = st.data_editor(original, hide_index=True, use_container_width=True,
            key='photo_review_' + token, num_rows='fixed', column_config={
                'Importa': st.column_config.CheckboxColumn('Importa'),
                'Danni': st.column_config.SelectboxColumn('Danni', options=['Da verificare', 'Con danni', 'Senza danni']),
                **{STATE_LABELS[item]: st.column_config.SelectboxColumn(STATE_LABELS[item], options=STATES) for item in ACCESSORIES}})
        events = load_events()
        event_map = {str(row.id): row.title for row in events.itertuples()} if not events.empty else {}
        event_id = st.selectbox('Associa a evento (facoltativo)', [''] + list(event_map),
                               format_func=lambda value: event_map.get(value, 'Nessun nuovo evento'), key='photo_event')
        operator = st.text_input('Operatore che verifica *', value=current_operator(), key='photo_operator')
        checked_at = st.date_input('Data delle schede fotografate', value=date(2026, 10, 12), key='photo_date')
        verified = st.checkbox('Ho verificato targhe, danni e accessori nell’anteprima.', key='photo_confirm_' + token)
        st.caption('Aggiorna la scheda più recente delle targhe esistenti. Conserva foto e dati non presenti nel file; nessuna email viene inviata.')
        if st.button('Importa vetture selezionate', disabled=not verified, key='photo_save_' + token):
            try:
                records = reviewed_records(edited)
                if not records:
                    raise ValueError('Seleziona almeno una vettura.')
                added, unchanged = import_reviewed(records, db, save_vehicle_check, labels, operator, checked_at.isoformat(), event_id)
            except Exception as exc:
                st.error(str(exc))
            else:
                st.session_state['vehicle_saved_message'] = f'{added} schede salvate; {unchanged} già aggiornate.'
                st.rerun()
