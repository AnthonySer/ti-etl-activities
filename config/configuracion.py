"""Rutas y parámetros centralizados del sistema de tickets de TI - INKACROPS."""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Rutas base
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
PROYECTO_RAIZ = BASE_DIR.parent

INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
DATA_DIR = BASE_DIR / "data"
BACKUPS_DIR = DATA_DIR / "backups"
LOGS_DIR = BASE_DIR / "logs"
REPORTES_DIR = BASE_DIR / "reportes"

# Base de correspondencias USUARIO -> AREA_USUARIO (gestión de usuarios/áreas).
MAESTRO_XLSX = DATA_DIR / "maestro_areas.xlsx"
MAESTRO_CSV = DATA_DIR / "maestro_areas.csv"

# Excel maestro histórico de tickets (11 columnas oficiales).
MAESTRO_TICKETS_XLSX = DATA_DIR / "maestro_tickets.xlsx"
MAESTRO_TICKETS_CSV = DATA_DIR / "maestro_tickets.csv"

# Trazabilidad independiente del Excel principal.
HISTORIAL_CSV = DATA_DIR / "maestro_historial.csv"
HISTORIAL_TICKETS_CSV = DATA_DIR / "maestro_tickets_historial.csv"
AUDITORIA_CSV = DATA_DIR / "auditoria_ejecuciones.csv"

# Estado de la interfaz (último procesamiento, rutas y métricas).
ESTADO_UI_JSON = DATA_DIR / "estado_ui.json"

# Bloqueo cooperativo para evitar dos escrituras simultáneas.
LOCK_SISTEMA = DATA_DIR / ".sistema.lock"

# ---------------------------------------------------------------------------
# Archivos históricos de referencia y columnas del maestro de áreas
# ---------------------------------------------------------------------------
ARCHIVO_SEPTIEMBRE = "consulta_septiembre_2026.xlsx"
ARCHIVO_AGOSTO = "consulta_agosto_2026.xlsx"
ARCHIVO_JULIO = "consulta_julio_2026.xlsx"

COLUMNA_USUARIO = "USUARIO"
COLUMNA_AREA = "AREA_USUARIO"

ALIAS_USUARIO = ("USUARIO", "SOLICITANTE")
ALIAS_AREA = ("AREA_USUARIO", "AREA")

SUFIJO_MANIFIESTO = ".manifest.json"

# ---------------------------------------------------------------------------
# Parámetros de negocio y validación
# ---------------------------------------------------------------------------
# Formato explícito para fechas ambiguas (no se interpreta en silencio).
FORMATO_FECHA_PREFERIDO = "DD/MM/YYYY"
FORMATO_FECHA_ALTERNATIVO = "YYYY-MM-DD"
FORMATO_FECHA_EXCEL = "DD/MM/YYYY"

# Hoja principal del Excel procesado y del maestro histórico.
HOJA_DETALLE = "Tickets"
HOJA_MAESTRO = "MAESTRO"
HOJA_CONTROL = "CONTROL"

# Umbrales de coincidencia al reimportar un Excel corregido.
UMBRAL_COINCIDENCIA = 0.78
MARGEN_COINCIDENCIA = 0.06

# Un detalle normalizado que se repite al menos N veces se marca como recurrente.
RECURRENCIA_MINIMA = 3

# Días hábiles usados para capacidad nominal en los reportes.
DIAS_HABILES_MES = 22
HORAS_JORNADA = 8

# Cada archivo procesado genera un manifiesto JSON junto a la salida.
RUTAS_BUSQUEDA = [
    INPUT_DIR,
    BASE_DIR,
    PROYECTO_RAIZ / "2026" / "EXCEL",
    PROYECTO_RAIZ,
]

# Número máximo de filas que se revisan para localizar el encabezado.
FILA_ENCABEZADO_MAX = 30

# Además del .xlsx se genera un espejo .csv de la base de correspondencias.
ESCRIBIR_CSV_ESPEJO = True

LOG_FILE = LOGS_DIR / "sistema.log"
LOG_MAX_BYTES = 2_000_000
LOG_BACKUP_COUNT = 5


def asegurar_directorios() -> None:
    """Crea las carpetas de trabajo si no existen."""

    for directorio in (
        INPUT_DIR,
        OUTPUT_DIR,
        DATA_DIR,
        BACKUPS_DIR,
        LOGS_DIR,
        REPORTES_DIR,
    ):
        directorio.mkdir(parents=True, exist_ok=True)


def ruta_visible(ruta: Path) -> str:
    """Devuelve una ruta corta y legible para los resúmenes en consola."""

    ruta = Path(ruta)
    try:
        return ruta.resolve().relative_to(BASE_DIR.resolve()).as_posix()
    except ValueError:
        return str(ruta)


def resolver_ruta(valor: Path | str | None, por_defecto: Path | str | None = None) -> Path:
    """Resuelve una ruta relativa tomando como base la carpeta del proyecto."""

    ruta = Path(valor) if valor else Path(por_defecto)
    if not ruta.is_absolute():
        ruta = BASE_DIR / ruta
    return ruta


def resolver_entrada(valor: Path | str) -> Path:
    """Resuelve un archivo de entrada probando la ruta tal cual y las raíces del proyecto."""

    ruta = Path(valor)
    if ruta.is_absolute():
        return ruta

    candidatos = [Path.cwd() / ruta, BASE_DIR / ruta, PROYECTO_RAIZ / ruta]
    for candidato in candidatos:
        if candidato.exists():
            return candidato
    return BASE_DIR / ruta
