"""Estado persistente de la interfaz.

Guarda en ``data/estado_ui.json`` (fuera de los Excel de trabajo) el resultado
del último procesamiento, las rutas usadas y los indicadores que muestra el
panel principal. No forma parte del esquema oficial de 11 columnas.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from config import configuracion as cfg
from core.archivos import ErrorArchivo, escribir_json_atomico

LOGGER = logging.getLogger("automatizador")

VERSION_ESTADO = 1


@dataclass
class EstadoUI:
    version: int = VERSION_ESTADO
    actualizado: str = ""
    ultimo_archivo_entrada: str = ""
    ultimo_archivo_procesado: str = ""
    ultimo_manifiesto: str = ""
    ultima_hoja: str = ""
    ultimo_periodo: str = ""
    ultimo_id_ejecucion: str = ""
    ultima_operacion: str = ""
    total_registros: int = 0
    registros_validos: int = 0
    registros_observados: int = 0
    registros_pendientes: int = 0
    registros_nuevos: int = 0
    registros_actualizados: int = 0
    registros_duplicados: int = 0
    registros_error: int = 0
    total_horas: float = 0.0
    ultimo_reporte: str = ""
    mensajes: list[str] = field(default_factory=list)

    def como_dict(self) -> dict[str, Any]:
        return asdict(self)


_CAMPOS_NUMERICOS = {
    "total_registros",
    "registros_validos",
    "registros_observados",
    "registros_pendientes",
    "registros_nuevos",
    "registros_actualizados",
    "registros_duplicados",
    "registros_error",
}


def _ruta(valor: Path | str | None = None) -> Path:
    return Path(valor or cfg.ESTADO_UI_JSON)


def cargar_estado(ruta: Path | str | None = None) -> EstadoUI:
    """Carga el estado; si no existe o está dañado, devuelve uno vacío."""

    archivo = _ruta(ruta)
    if not archivo.is_file():
        return EstadoUI()

    try:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("No se pudo leer el estado de la interfaz: %s", exc)
        return EstadoUI()

    if not isinstance(datos, dict):
        return EstadoUI()

    permitidos = set(EstadoUI.__dataclass_fields__)
    limpio: dict[str, Any] = {}
    for clave, valor in datos.items():
        if clave not in permitidos:
            continue
        if clave in _CAMPOS_NUMERICOS:
            try:
                limpio[clave] = int(valor or 0)
            except (TypeError, ValueError):
                limpio[clave] = 0
        elif clave == "total_horas":
            try:
                limpio[clave] = float(valor or 0)
            except (TypeError, ValueError):
                limpio[clave] = 0.0
        elif clave == "mensajes":
            limpio[clave] = list(valor) if isinstance(valor, list) else []
        else:
            limpio[clave] = "" if valor is None else str(valor)

    return EstadoUI(**limpio)


def guardar_estado(estado: EstadoUI | Mapping[str, Any], ruta: Path | str | None = None) -> Path:
    """Guarda el estado de forma atómica."""

    datos = estado.como_dict() if isinstance(estado, EstadoUI) else dict(estado)
    datos["version"] = VERSION_ESTADO
    datos["actualizado"] = datetime.now().astimezone().isoformat(timespec="seconds")

    try:
        return escribir_json_atomico(datos, _ruta(ruta))
    except ErrorArchivo as exc:
        LOGGER.error("No se pudo guardar el estado de la interfaz: %s", exc)
        raise


def actualizar_desde_estadisticas(
    estadisticas: Any,
    *,
    operacion: str = "PROCESAMIENTO",
    ruta: Path | str | None = None,
    mensajes: list[str] | None = None,
) -> EstadoUI:
    """Actualiza el estado con el resultado de un procesamiento."""

    estado = cargar_estado(ruta)
    estado.ultima_operacion = operacion
    estado.ultimo_archivo_entrada = str(
        getattr(estadisticas, "archivo_entrada", "") or ""
    )
    estado.ultimo_archivo_procesado = str(
        getattr(estadisticas, "archivo_salida", "") or ""
    )
    estado.ultimo_manifiesto = str(
        getattr(estadisticas, "archivo_manifiesto", "") or ""
    )
    estado.ultima_hoja = str(getattr(estadisticas, "hoja", "") or "")
    estado.ultimo_periodo = str(getattr(estadisticas, "periodo", "") or "")
    estado.ultimo_id_ejecucion = str(
        getattr(estadisticas, "id_ejecucion", "") or ""
    )
    estado.total_registros = int(getattr(estadisticas, "registros_totales", 0) or 0)
    estado.registros_validos = int(
        getattr(estadisticas, "registros_validos", 0) or 0
    )
    estado.registros_observados = int(
        getattr(estadisticas, "registros_observados", 0) or 0
    )
    estado.registros_pendientes = int(
        getattr(estadisticas, "registros_pendientes", 0) or 0
    )
    estado.registros_duplicados = int(
        getattr(estadisticas, "registros_duplicados", 0) or 0
    )
    estado.registros_error = int(
        getattr(estadisticas, "registros_error", 0) or 0
    )
    estado.total_horas = float(getattr(estadisticas, "total_horas", 0.0) or 0.0)
    if mensajes:
        estado.mensajes = list(mensajes[-20:])
    guardar_estado(estado, ruta)
    return estado


def registrar_consolidacion(
    resultado: Mapping[str, Any],
    *,
    ruta: Path | str | None = None,
) -> EstadoUI:
    """Actualiza el estado después de una reimportación o consolidación."""

    estado = cargar_estado(ruta)
    estado.ultima_operacion = "CONSOLIDACION"
    estado.registros_nuevos = int(resultado.get("nuevos", 0) or 0)
    estado.registros_actualizados = int(resultado.get("actualizados", 0) or 0)
    estado.registros_duplicados = int(resultado.get("duplicados", 0) or 0)
    if resultado.get("archivo"):
        estado.ultimo_archivo_entrada = str(resultado["archivo"])
    if resultado.get("total_maestro") is not None:
        estado.total_registros = int(resultado["total_maestro"] or 0)
    guardar_estado(estado, ruta)
    return estado


def registrar_reporte(ruta_reporte: Path | str, *, ruta: Path | str | None = None) -> EstadoUI:
    estado = cargar_estado(ruta)
    estado.ultimo_reporte = str(ruta_reporte)
    estado.ultima_operacion = "REPORTE"
    guardar_estado(estado, ruta)
    return estado
