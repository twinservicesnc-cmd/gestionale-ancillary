# Gestionale Ancillary

Applicazione Streamlit autonoma per inserire, normalizzare e analizzare i dati ancillary.

## Avvio

```bash
pip install -r requirements.txt
streamlit run app_ancillary.py
```

L'archivio iniziale contiene i 246 record importati dal file Excel fornito.
Alla prima esecuzione viene creato `ancillary.db`.

## Password su Streamlit Cloud

Inserire nei Secrets:

```toml
ADMIN_PASSWORD = "una-password-sicura"
```

Se il valore non è configurato, l'app si apre senza schermata di accesso.

## Backup

La sezione **Importazione e backup** consente di scaricare una copia completa
del database e di importare altri file Excel con la stessa struttura.
