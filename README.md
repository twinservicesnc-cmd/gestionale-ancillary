# Gestionale Noleggi, Ancillary e Danni

Applicazione Streamlit autonoma per importare, normalizzare e analizzare dati
ancillary, report mensili Analisi Contratti RA, Commissioni e segnalazioni di addebito danni.

## Avvio

```bash
pip install -r requirements.txt
streamlit run app_ancillary.py
```

L'archivio iniziale contiene 298 contratti RA di agosto 2026, 364 documenti
Commissioni del report agosto 2026 e 23 segnalazioni danni importate dai file
Excel forniti. I nuovi inserimenti ancillary partono
dal 01/10/2026. L'importatore riconosce i quattro formati e legge anche
i fogli separati per operatore o periodo. Le voci Commissioni si collegano agli
RA tramite numero e data contratto; più fatture possono appartenere allo stesso
RA. Le colonne Assicurazioni e Cdw/Tlw non distinguono prodotti come Gold o
Platinum.
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
