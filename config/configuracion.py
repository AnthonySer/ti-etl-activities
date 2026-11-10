"""Rutas y parámetros centralizados del sistema de tickets de TI - INKACROPS."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Rutas base
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
PROYECTO_RAIZ = BASE_DIR.parent

DEFAULT_CONFIG = {
    "input_dir": "input",
    "output_dir": "output",
    "revision_dir": "output/revision",
    "backups_dir": "output/respaldos",
    "data_dir": "data",
    "reportes_dir": "reportes",
    "auditoria_csv": "data/auditoria_ejecuciones.csv",
    "logs_dir": "logs",
    "max_backups": 10,
    "log_level": "INFO",
    "formato_fecha_reporte": "DD/MM/YYYY",
}

CONFIG_LOCAL_PATH = BASE_DIR / "config" / "configuracion_local.json"

INPUT_DIR = BASE_DIR / DEFAULT_CONFIG["input_dir"]
OUTPUT_DIR = BASE_DIR / DEFAULT_CONFIG["output_dir"]
DATA_DIR = BASE_DIR / DEFAULT_CONFIG["data_dir"]
BACKUPS_DIR = BASE_DIR / DEFAULT_CONFIG["backups_dir"]
REVISION_DIR = BASE_DIR / DEFAULT_CONFIG["revision_dir"]
LOGS_DIR = BASE_DIR / DEFAULT_CONFIG["logs_dir"]
REPORTES_DIR = BASE_DIR / DEFAULT_CONFIG["reportes_dir"]

# Base de correspondencias USUARIO -> AREA_USUARIO (gestión de usuarios/áreas).
MAESTRO_XLSX = DATA_DIR / "maestro_areas.xlsx"
MAESTRO_CSV = DATA_DIR / "maestro_areas.csv"

# Excel maestro histórico de tickets (11 columnas oficiales).
MAESTRO_TICKETS_XLSX = DATA_DIR / "maestro_tickets.xlsx"
MAESTRO_TICKETS_CSV = DATA_DIR / "maestro_tickets.csv"

# Trazabilidad independiente del Excel principal.
HISTORIAL_CSV = DATA_DIR / "maestro_historial.csv"
HISTORIAL_TICKETS_CSV = DATA_DIR / "maestro_tickets_historial.csv"
AUDITORIA_CSV = BASE_DIR / DEFAULT_CONFIG["auditoria_csv"]

# Estado de la interfaz (último procesamiento, rutas y métricas).
ESTADO_UI_JSON = DATA_DIR / "estado_ui.json"

# Bloqueo cooperativo para evitar dos escrituras simultáneas.
LOCK_SISTEMA = DATA_DIR / ".sistema.lock"

# Retención de copias y revisiones.
MAX_BACKUPS = int(DEFAULT_CONFIG["max_backups"])

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
# ``HOJA_CONSULTA`` es la hoja canónica nueva; ``HOJA_DETALLE`` se conserva por
# compatibilidad con los archivos históricos y las pruebas existentes.
HOJA_CONSULTA = "Consulta"
HOJA_DETALLE = "Tickets"
HOJA_MAESTRO = "MAESTRO"
HOJA_CONTROL = "CONTROL"

# Modos de generación del Excel procesado.
MODO_NORMAL = "normal"
MODO_DETALLE = "detalle"
MODO_LEGACY = "legacy"
MODOS_GENERACION = (MODO_NORMAL, MODO_DETALLE)

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


def _ruta_relativa_o_absoluta(valor: str | Path | None, predeterminado: str) -> str:
    """Normaliza la ruta para persistirla en JSON, manteniendo rutas relativas dentro del proyecto."""
    if valor is None or str(valor).strip() == "":
        return predeterminado
    ruta = Path(str(valor))
    if not ruta.is_absolute():
        return ruta.as_posix()
    try:
        return ruta.resolve().relative_to(BASE_DIR.resolve()).as_posix()
    except ValueError:
        return str(ruta)


def _ruta_config(valor: str | Path | None, predeterminado: str) -> Path:
    camino = str(valor) if valor is not None else predeterminado
    ruta = Path(camino)
    if not ruta.is_absolute():
        ruta = BASE_DIR / ruta
    return ruta


def _config_por_defecto() -> dict[str, Any]:
    return {
        "input_dir": _ruta_relativa_o_absoluta(DEFAULT_CONFIG["input_dir"], DEFAULT_CONFIG["input_dir"]),
        "output_dir": _ruta_relativa_o_absoluta(DEFAULT_CONFIG["output_dir"], DEFAULT_CONFIG["output_dir"]),
        "revision_dir": _ruta_relativa_o_absoluta(DEFAULT_CONFIG["revision_dir"], DEFAULT_CONFIG["revision_dir"]),
        "backups_dir": _ruta_relativa_o_absoluta(DEFAULT_CONFIG["backups_dir"], DEFAULT_CONFIG["backups_dir"]),
        "data_dir": _ruta_relativa_o_absoluta(DEFAULT_CONFIG["data_dir"], DEFAULT_CONFIG["data_dir"]),
        "reportes_dir": _ruta_relativa_o_absoluta(DEFAULT_CONFIG["reportes_dir"], DEFAULT_CONFIG["reportes_dir"]),
        "auditoria_csv": _ruta_relativa_o_absoluta(DEFAULT_CONFIG["auditoria_csv"], DEFAULT_CONFIG["auditoria_csv"]),
        "logs_dir": _ruta_relativa_o_absoluta(DEFAULT_CONFIG["logs_dir"], DEFAULT_CONFIG["logs_dir"]),
        "max_backups": 10,
        "log_level": "INFO",
        "formato_fecha_reporte": "DD/MM/YYYY",
    }


def cargar_configuracion_local() -> dict[str, Any]:
    """Carga la configuración local del equipo si existe; si no, devuelve valores seguros por defecto."""
    if not CONFIG_LOCAL_PATH.exists():
        return _config_por_defecto()

    try:
        datos = json.loads(CONFIG_LOCAL_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _config_por_defecto()

    if not isinstance(datos, dict):
        return _config_por_defecto()

    base = _config_por_defecto()
    base.update(datos)
    return base


def validar_configuracion(config: dict[str, Any]) -> dict[str, Any]:
    """Valida la configuración local y normaliza rutas relativas desde la raíz del proyecto."""
    if not isinstance(config, dict):
        raise ValueError("La configuración local debe ser un diccionario.")

    base = _config_por_defecto()
    base.update(config)

    max_backups = int(base.get("max_backups", 10))
    if max_backups < 1 or max_backups > 100:
        raise ValueError("max_backups debe estar entre 1 y 100.")

    nivel = str(base.get("log_level", "INFO")).upper()
    if nivel not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("log_level debe ser DEBUG, INFO, WARNING, ERROR o CRITICAL.")

    entradas = {
        "input_dir": _ruta_config(base.get("input_dir", DEFAULT_CONFIG["input_dir"]), DEFAULT_CONFIG["input_dir"]),
        "output_dir": _ruta_config(base.get("output_dir", DEFAULT_CONFIG["output_dir"]), DEFAULT_CONFIG["output_dir"]),
        "revision_dir": _ruta_config(base.get("revision_dir", DEFAULT_CONFIG["revision_dir"]), DEFAULT_CONFIG["revision_dir"]),
        "backups_dir": _ruta_config(base.get("backups_dir", DEFAULT_CONFIG["backups_dir"]), DEFAULT_CONFIG["backups_dir"]),
        "data_dir": _ruta_config(base.get("data_dir", DEFAULT_CONFIG["data_dir"]), DEFAULT_CONFIG["data_dir"]),
        "reportes_dir": _ruta_config(base.get("reportes_dir", DEFAULT_CONFIG["reportes_dir"]), DEFAULT_CONFIG["reportes_dir"]),
        "auditoria_csv": _ruta_config(base.get("auditoria_csv", DEFAULT_CONFIG["auditoria_csv"]), DEFAULT_CONFIG["auditoria_csv"]),
        "logs_dir": _ruta_config(base.get("logs_dir", DEFAULT_CONFIG["logs_dir"]), DEFAULT_CONFIG["logs_dir"]),
    }

    conflictos = {
        "input_dir": ["output_dir", "data_dir", "reportes_dir"],
        "output_dir": ["input_dir", "revision_dir", "backups_dir"],
        "data_dir": ["input_dir", "output_dir", "reportes_dir"],
    }
    for origen, comparaciones in conflictos.items():
        raiz_origen = entradas[origen].resolve()
        for destino in comparaciones:
            if entradas[destino].resolve() == raiz_origen:
                raise ValueError(f"La ruta '{origen}' no puede coincidir con '{destino}'.")

    if entradas["auditoria_csv"].exists() and entradas["auditoria_csv"].is_dir():
        raise ValueError("auditoria_csv debe apuntar a un archivo, no a un directorio.")

    validada = {
        "input_dir": _ruta_relativa_o_absoluta(base.get("input_dir", DEFAULT_CONFIG["input_dir"]), DEFAULT_CONFIG["input_dir"]),
        "output_dir": _ruta_relativa_o_absoluta(base.get("output_dir", DEFAULT_CONFIG["output_dir"]), DEFAULT_CONFIG["output_dir"]),
        "revision_dir": _ruta_relativa_o_absoluta(base.get("revision_dir", DEFAULT_CONFIG["revision_dir"]), DEFAULT_CONFIG["revision_dir"]),
        "backups_dir": _ruta_relativa_o_absoluta(base.get("backups_dir", DEFAULT_CONFIG["backups_dir"]), DEFAULT_CONFIG["backups_dir"]),
        "data_dir": _ruta_relativa_o_absoluta(base.get("data_dir", DEFAULT_CONFIG["data_dir"]), DEFAULT_CONFIG["data_dir"]),
        "reportes_dir": _ruta_relativa_o_absoluta(base.get("reportes_dir", DEFAULT_CONFIG["reportes_dir"]), DEFAULT_CONFIG["reportes_dir"]),
        "auditoria_csv": _ruta_relativa_o_absoluta(base.get("auditoria_csv", DEFAULT_CONFIG["auditoria_csv"]), DEFAULT_CONFIG["auditoria_csv"]),
        "logs_dir": _ruta_relativa_o_absoluta(base.get("logs_dir", DEFAULT_CONFIG["logs_dir"]), DEFAULT_CONFIG["logs_dir"]),
        "max_backups": max_backups,
        "log_level": nivel,
        "formato_fecha_reporte": str(base.get("formato_fecha_reporte", "DD/MM/YYYY")),
    }
    return validada


def guardar_configuracion_local(config: dict[str, Any]) -> dict[str, Any]:
    """Guarda la configuración local en JSON de forma atómica y devuelve la versión validada."""
    validada = validar_configuracion(config)
    CONFIG_LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporal = CONFIG_LOCAL_PATH.with_suffix(".tmp")
    temporal.write_text(json.dumps(validada, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporal.replace(CONFIG_LOCAL_PATH)
    return validada


def aplicar_configuracion(config: dict[str, Any]) -> dict[str, Any]:
    """Actualiza los valores en memoria para que la aplicación use la nueva configuración."""
    validada = validar_configuracion(config)

    globals()["INPUT_DIR"] = _ruta_config(validada["input_dir"], DEFAULT_CONFIG["input_dir"])
    globals()["OUTPUT_DIR"] = _ruta_config(validada["output_dir"], DEFAULT_CONFIG["output_dir"])
    globals()["DATA_DIR"] = _ruta_config(validada["data_dir"], DEFAULT_CONFIG["data_dir"])
    globals()["BACKUPS_DIR"] = _ruta_config(validada["backups_dir"], DEFAULT_CONFIG["backups_dir"])
    globals()["REVISION_DIR"] = _ruta_config(validada["revision_dir"], DEFAULT_CONFIG["revision_dir"])
    globals()["LOGS_DIR"] = _ruta_config(validada["logs_dir"], DEFAULT_CONFIG["logs_dir"])
    globals()["REPORTES_DIR"] = _ruta_config(validada["reportes_dir"], DEFAULT_CONFIG["reportes_dir"])
    globals()["AUDITORIA_CSV"] = _ruta_config(validada["auditoria_csv"], DEFAULT_CONFIG["auditoria_csv"])
    globals()["MAESTRO_XLSX"] = globals()["DATA_DIR"] / "maestro_areas.xlsx"
    globals()["MAESTRO_CSV"] = globals()["DATA_DIR"] / "maestro_areas.csv"
    globals()["MAESTRO_TICKETS_XLSX"] = globals()["DATA_DIR"] / "maestro_tickets.xlsx"
    globals()["MAESTRO_TICKETS_CSV"] = globals()["DATA_DIR"] / "maestro_tickets.csv"
    globals()["HISTORIAL_CSV"] = globals()["DATA_DIR"] / "maestro_historial.csv"
    globals()["HISTORIAL_TICKETS_CSV"] = globals()["DATA_DIR"] / "maestro_tickets_historial.csv"
    globals()["ESTADO_UI_JSON"] = globals()["DATA_DIR"] / "estado_ui.json"
    globals()["LOCK_SISTEMA"] = globals()["DATA_DIR"] / ".sistema.lock"
    globals()["LOG_FILE"] = globals()["LOGS_DIR"] / "sistema.log"
    globals()["RUTAS_BUSQUEDA"] = [
        globals()["INPUT_DIR"],
        BASE_DIR,
        PROYECTO_RAIZ / "2026" / "EXCEL",
        PROYECTO_RAIZ,
    ]
    globals()["MAX_BACKUPS"] = int(validada["max_backups"])
    globals()["LOG_LEVEL"] = validada["log_level"]
    globals()["FORMATO_FECHA_REPORTE"] = validada["formato_fecha_reporte"]
    return validada


# Carga inicial segura desde el archivo local si existe; en caso contrario deja la configuración por defecto.
CONFIG_LOCAL = cargar_configuracion_local()
if CONFIG_LOCAL:
    aplicar_configuracion(CONFIG_LOCAL)


def asegurar_directorios() -> None:
    """Crea las carpetas de trabajo si no existen."""

    for directorio in (
        INPUT_DIR,
        OUTPUT_DIR,
        DATA_DIR,
        BACKUPS_DIR,
        REVISION_DIR,
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
