# Smartsheet to pandas

La vía recomendada para un script Python de extracción desde Smartsheet es usar el SDK oficial de Python sobre el endpoint oficial `GET /sheets/{sheetId}`.

Motivos:

- Smartsheet mantiene un SDK oficial para Python y su guía de arranque recomienda `pip install smartsheet-python-sdk`.
- El método `Sheets.get_sheet(...)` soporta `include`, `level`, `page_size` y `page`, que es justo lo necesario para traer el sheet completo y conservar detalles de celdas complejas.
- Para un script interno o proceso machine-to-machine, la documentación oficial recomienda autenticación con access token; OAuth queda más orientado a apps con consentimiento de usuario.

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Crea un archivo `.env` en la raíz del proyecto:

```bash
SMARTSHEET_ACCESS_TOKEN="tu_token"
# Opcional si tu cuenta está en otra región:
# SMARTSHEET_API_BASE="https://api.smartsheet.eu/2.0"
```

## Uso

```bash
python3 smartsheet_to_pandas.py 1234567890123456 --output-dir ./output
```

Eso genera:

- `output/rows.csv`: dataset ancho, una fila por row de Smartsheet
- `output/cells.csv`: dataset normalizado, una fila por celda
- `output/sheet_metadata.json`: metadatos del sheet y sus columnas

## Uso desde código

```python
from smartsheet_to_pandas import fetch_sheet, sheet_to_dataframes

sheet = fetch_sheet(1234567890123456)
extract = sheet_to_dataframes(sheet)

rows_df = extract.rows_df
cells_df = extract.cells_df
metadata = extract.metadata
```

## Fuentes oficiales revisadas

- Smartsheet Get Started: https://developers.smartsheet.com/api/smartsheet/guides/getting-started
- Smartsheet Authentication: https://developers.smartsheet.com/api/smartsheet/guides/basics/authentication
- Smartsheet OpenAPI `GET /sheets/{sheetId}`: https://developers.smartsheet.com/api/smartsheet/openapi/sheets/getsheet
- SDK oficial Python: https://github.com/smartsheet/smartsheet-python-sdk
- Docs del SDK Python: https://smartsheet.github.io/smartsheet-python-sdk/
