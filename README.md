# Smartsheet to MySQL

La vía recomendada para un script Python de extracción desde Smartsheet es usar el SDK oficial de Python sobre el endpoint oficial `GET /sheets/{sheetId}`. Este proyecto descarga el sheet y vuelca sus filas a una tabla MySQL.

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
SMARTSHEET_SHEET_ID="1234567890123456"
# Opcional si tu cuenta está en otra región:
# SMARTSHEET_API_BASE="https://api.smartsheet.eu/2.0"
```

## Variables MySQL

Puedes usar una URL completa:

```bash
MYSQL_URL="mysql+pymysql://usuario:password@host:3306/base_de_datos"
```

O variables separadas:

```bash
MYSQL_HOST="localhost"
MYSQL_PORT="3306"
MYSQL_DATABASE="smartsheet"
MYSQL_USER="app_user"
MYSQL_PASSWORD="tu_password"
MYSQL_TABLE="partner_downline_complaint_tracker"
```

`MYSQL_TABLE` es opcional; si no se define, el script usa una versión saneada del nombre del sheet.

## Uso

```bash
python3 smartsheet_to_pandas.py
```

Opciones útiles:

- `SMARTSHEET_SHEET_ID=1234567890123456`
- `--mysql-table partner_downline_complaint_tracker`
- `--page-size 500`

## Qué escribe en MySQL

- Una única tabla principal con una fila por row de Smartsheet.
- Cada columna del sheet se copia como columna de la tabla.
- También se incluyen columnas técnicas de fila como `__row_id`, `__row_number`, `__created_at`, `__modified_at` y `last_synced_at`.
- La sincronización es incremental por `__row_id`: inserta filas nuevas y actualiza filas existentes sin recrear la tabla.
- Si una fila desaparece del sheet, no se borra de MySQL.
- Si aparece una columna nueva en Smartsheet, el script la añade a la tabla.

## Uso desde código

```python
import os

from smartsheet_to_pandas import build_mysql_engine, fetch_sheet, sheet_to_dataframe, write_dataframe_to_mysql

sheet = fetch_sheet(int(os.environ["SMARTSHEET_SHEET_ID"]))
dataframe = sheet_to_dataframe(sheet)
engine = build_mysql_engine()
write_dataframe_to_mysql(dataframe, table_name="mi_sheet", engine=engine)
```

## Fuentes oficiales revisadas

- Smartsheet Get Started: https://developers.smartsheet.com/api/smartsheet/guides/getting-started
- Smartsheet Authentication: https://developers.smartsheet.com/api/smartsheet/guides/basics/authentication
- Smartsheet OpenAPI `GET /sheets/{sheetId}`: https://developers.smartsheet.com/api/smartsheet/openapi/sheets/getsheet
- SDK oficial Python: https://github.com/smartsheet/smartsheet-python-sdk
- Docs del SDK Python: https://smartsheet.github.io/smartsheet-python-sdk/
