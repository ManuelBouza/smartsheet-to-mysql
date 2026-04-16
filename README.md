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
python3 smartsheet_to_pandas.py 1234567890123456
```

Opciones útiles:

- `--mysql-table partner_downline_complaint_tracker`
- `--if-exists replace`
- `--show-columns`

## Qué escribe en MySQL

- Una tabla principal con una fila por row de Smartsheet.
- Una tabla de metadatos `<tabla>__meta` con contexto del sheet y un `metadata_json`.

## Uso desde código

```python
from smartsheet_to_pandas import build_mysql_engine, fetch_sheet, sheet_to_dataframes, write_extract_to_mysql

sheet = fetch_sheet(1234567890123456)
extract = sheet_to_dataframes(sheet)
engine = build_mysql_engine()
write_extract_to_mysql(extract, table_name="mi_sheet", engine=engine)
```

## Fuentes oficiales revisadas

- Smartsheet Get Started: https://developers.smartsheet.com/api/smartsheet/guides/getting-started
- Smartsheet Authentication: https://developers.smartsheet.com/api/smartsheet/guides/basics/authentication
- Smartsheet OpenAPI `GET /sheets/{sheetId}`: https://developers.smartsheet.com/api/smartsheet/openapi/sheets/getsheet
- SDK oficial Python: https://github.com/smartsheet/smartsheet-python-sdk
- Docs del SDK Python: https://smartsheet.github.io/smartsheet-python-sdk/
