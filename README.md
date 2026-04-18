# Smartsheet to MySQL

Sincroniza un sheet de Smartsheet a una tabla MySQL usando el SDK oficial de Smartsheet para Python.

## Qué hace

- lee un sheet completo desde Smartsheet
- transforma sus filas a un DataFrame
- mapea columnas de negocio cuando hace falta (por ejemplo `CTM`)
- hace upsert en MySQL
- permite decidir qué columnas técnicas sincronizar por config o por CLI

## Casos de uso

- replicar un sheet en MySQL para análisis o reporting
- alimentar una tabla legacy con nombres de columnas propios
- correr syncs manuales o programados

## Antes de publicar o compartir este repo

- `.env` está ignorado y no debe subirse
- `.env.example` contiene solo placeholders
- `docker-compose.yml` usa credenciales de desarrollo, no productivas
- revisá que tu `smartsheet_sync.config.json` no tenga nada sensible si planeás versionarlo

## Por qué este enfoque

- Smartsheet mantiene un SDK oficial para Python y su guía de arranque recomienda `pip install smartsheet-python-sdk`.
- El método `Sheets.get_sheet(...)` soporta `include`, `level`, `page_size` y `page`, que es justo lo necesario para traer el sheet completo y conservar detalles de celdas complejas.
- Para un script interno o proceso machine-to-machine, la documentación oficial recomienda autenticación con access token; OAuth queda más orientado a apps con consentimiento de usuario.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Editá `.env` con tus valores reales. Ejemplo:

```bash
SMARTSHEET_ACCESS_TOKEN="tu_token"
SMARTSHEET_SHEET_ID="1234567890123456"
# Opcional si tu cuenta está en otra región:
# SMARTSHEET_API_BASE="https://api.smartsheet.eu/2.0"
```

## Configuración MySQL

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
.venv/bin/python sync_smartsheet_to_mysql.py
```

Prueba segura sin escribir en MySQL:

```bash
.venv/bin/python sync_smartsheet_to_mysql.py --dry-run
```

Sincronización real a una tabla específica:

```bash
.venv/bin/python sync_smartsheet_to_mysql.py --mysql-table CTM
```

Opciones útiles:

- `SMARTSHEET_SHEET_ID=1234567890123456`
- `--mysql-table partner_downline_complaint_tracker`
- `--page-size 500`
- `--dry-run`
- `--mark-missing-as-deleted`
- `--log-level DEBUG`
- `--sync-config ./smartsheet_sync.config.json`
- `--technical-columns __created_at,__modified_at`
- `--include-technical-columns __row_id`
- `--exclude-technical-columns last_synced_at`

### Configuración de columnas técnicas (fichero + CLI)

Por defecto, el sync conserva el comportamiento actual seguro:

- Tablas generales: sincroniza todas las columnas técnicas conocidas (`__row_id`, `__row_number`, `__parent_id`, `__sibling_id`, `__created_at`, `__modified_at`, `last_synced_at`, `is_deleted`, `deleted_at`).
- Tabla `CTM`: mantiene el contrato reducido (`__created_at`, `__modified_at`).

Podés crear `smartsheet_sync.config.json` (o pasar otra ruta con `--sync-config`) para ajustar este set sin tocar código:

```json
{
  "technical_columns": {
    "default": {
      "include": ["__parent_id"],
      "exclude": ["deleted_at"]
    },
    "tables": {
      "CTM": {
        "columns": ["__created_at", "__modified_at", "last_synced_at"]
      }
    }
  }
}
```

Reglas de precedencia:

1. Defaults del código (seguros y compatibles)
2. Fichero de config (`technical_columns.default` + `technical_columns.tables.<tabla>`)
3. CLI (siempre overridea config):
   - `--technical-columns` reemplaza el set final
   - `--include-technical-columns` agrega columnas
   - `--exclude-technical-columns` quita columnas

## Mejoras operativas incluidas

- **Logging configurable** con `--log-level` o `LOG_LEVEL`
- **Modo dry-run** para validar extracción y mapeo sin escribir en MySQL
- **Marcado opcional de filas ausentes** con `--mark-missing-as-deleted`, que usa `is_deleted` y `deleted_at`
- **Código modularizado** en el paquete `smartsheet_sync/`
- **Tests unitarios** base en `tests/`

## Qué escribe en MySQL

- Una única tabla principal con una fila por row de Smartsheet.
- Cada columna del sheet se copia como columna de la tabla.
- También se incluyen columnas técnicas de fila como `__row_id`, `__row_number`, `__created_at`, `__modified_at`, `last_synced_at`, `is_deleted` y `deleted_at` (excepto en `CTM`, donde se sincronizan solo `__created_at` y `__modified_at`).
- Las columnas técnicas pueden configurarse por fichero y por CLI (con precedencia de CLI).
- La sincronización es incremental por `__row_id`: inserta filas nuevas y actualiza filas existentes sin recrear la tabla.
- Si una fila desaparece del sheet, no se borra de MySQL salvo que ejecutes `--mark-missing-as-deleted`, que la marca como borrada lógica.
- Si aparece una columna nueva en Smartsheet, el script la añade a la tabla.

## Desarrollo

Instala dependencias y ejecuta tests:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
.venv/bin/pytest
ruff check .
```

## Publicación recomendada

Antes de subir a GitHub, revisá esta checklist:

- [ ] `.env` no está trackeado
- [ ] no hay tokens ni passwords reales en `README.md`, `docker-compose.yml` o `smartsheet_sync.config.json`
- [ ] `.env.example` usa placeholders
- [ ] la tabla/flujo de ejemplo no expone nombres internos sensibles si eso es un problema para tu organización

## Uso desde código

```python
import os

from smartsheet_sync import build_mysql_engine, fetch_sheet, sheet_to_dataframe, write_dataframe_to_mysql

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
