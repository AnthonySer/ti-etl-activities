"""Trazabilidad de la retroalimentación: manifiestos e historial de cambios.

El manifiesto se escribe junto a cada archivo procesado y registra qué áreas
fueron asignadas automáticamente por el maestro. Así, al revisar el archivo,
el sistema puede distinguir una asignación automática de una corrección manual.

El historial es un CSV independiente (``data/maestro_historial.csv``) que
registra únicamente las correcciones aplicadas al maestro, con la fecha del
archivo de origen. No se agregan columnas al Excel de trabajo.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from config import configuracion as cfg
from core.archivos import ErrorArchivo, escribir_dataframe_atomico
from core.normalizador import normalizar_usuario

LOGGER = logging.getLogger("automatizador")

COLUMNAS_HISTORIAL = (
    "FECHA_REGISTRO",
    "FECHA_ARCHIVO",
    "FECHA_ARCHIVO_NS",
    "FUENTE",
    "USUARIO",
    "AREA_ANTERIOR",
    "AREA_NUEVA",
    "ORIGEN",
)


@dataclass
class Manifiesto:
    """Datos mínimos del manifiesto que necesita la retroalimentación."""

    ruta: Path | None = None
    asignaciones: dict[str, str] = field(default_factory=dict)
    preexistentes: dict[str, str] = field(default_factory=dict)
    pendientes: set[str] = field(default_factory=set)
    fecha_archivo_ns: int | None = None


# ---------------------------------------------------------------------------
# Manifiestos
# ---------------------------------------------------------------------------
def ruta_manifiesto(ruta_salida: Path | str) -> Path:
    """Devuelve la ruta del manifiesto asociado a una salida procesada."""

    ruta = Path(ruta_salida)
    return ruta.with_name(f"{ruta.stem}{cfg.SUFIJO_MANIFIESTO}")


def _candidatos_manifiesto(ruta_revisado: Path) -> list[Path]:
    return [
        ruta_manifiesto(ruta_revisado),
        ruta_revisado.with_name(
            f"{ruta_revisado.name}{cfg.SUFIJO_MANIFIESTO}"
        ),
    ]


def buscar_manifiesto(ruta_revisado: Path | str) -> Path | None:
    """Busca el manifiesto de un archivo revisado en ubicaciones conocidas."""

    ruta_revisado = Path(ruta_revisado)

    for candidato in _candidatos_manifiesto(ruta_revisado):
        if candidato.is_file():
            return candidato

    # El archivo revisado pudo renombrarse (por ejemplo, *_revisado.xlsx).
    # Se busca un único manifiesto cuyo nombre sea compatible.
    if not cfg.OUTPUT_DIR.exists():
        return None

    compatibles: list[Path] = []
    for candidato in cfg.OUTPUT_DIR.glob(f"*{cfg.SUFIJO_MANIFIESTO}"):
        stem_manifiesto = candidato.name[: -len(cfg.SUFIJO_MANIFIESTO)]
        if (
            ruta_revisado.stem.startswith(stem_manifiesto)
            or stem_manifiesto.startswith(ruta_revisado.stem)
        ):
            compatibles.append(candidato)

    if len(compatibles) == 1:
        return compatibles[0]
    if len(compatibles) > 1:
        LOGGER.warning(
            "Hay varios manifiestos compatibles con %s: %s. "
            "Indique uno con --manifiesto.",
            ruta_revisado.name,
            ", ".join(c.name for c in compatibles),
        )
    return None


def _normalizar_mapa(valor: Any, campo_area: str = "area") -> dict[str, str]:
    """Convierte un manifiesto en un mapa ``clave_normalizada -> área``."""

    mapa: dict[str, str] = {}

    if isinstance(valor, dict):
        elementos: Iterable[Any] = valor.items()
    elif isinstance(valor, list):
        elementos = valor
    else:
        return mapa

    for elemento in elementos:
        if isinstance(elemento, tuple):
            clave_bruta, area = elemento
            usuario = clave_bruta
        elif isinstance(elemento, dict):
            clave_bruta = elemento.get("clave") or elemento.get("usuario")
            area = elemento.get(campo_area) or elemento.get("AREA_USUARIO")
            usuario = elemento.get("usuario") or clave_bruta
        else:
            continue

        if isinstance(area, dict):
            area = area.get("area") or area.get("AREA_USUARIO")
        if area is None:
            continue

        clave = normalizar_usuario(clave_bruta or usuario)
        if clave is None:
            continue
        mapa[clave] = str(area).strip()

    return mapa


def cargar_manifiesto(ruta: Path | str | None) -> Manifiesto | None:
    """Carga y valida un manifiesto JSON. Devuelve None si no existe."""

    if ruta is None:
        return None

    ruta = Path(ruta)
    if not ruta.is_file():
        return None

    try:
        with ruta.open("r", encoding="utf-8") as archivo:
            datos = json.load(archivo)
    except (OSError, json.JSONDecodeError) as exc:
        raise ErrorArchivo(
            f"No se pudo leer el manifiesto '{ruta}': {exc}"
        ) from exc

    if not isinstance(datos, dict):
        raise ErrorArchivo(
            f"El manifiesto '{ruta}' no tiene el formato esperado (objeto JSON)."
        )

    version = datos.get("version")
    if version not in (None, 1):
        LOGGER.warning(
            "Manifiesto %s con versión desconocida: %s", ruta.name, version
        )

    asignaciones = _normalizar_mapa(
        datos.get("asignaciones_automaticas", datos.get("asignaciones"))
    )
    preexistentes = _normalizar_mapa(
        datos.get("areas_preexistentes", datos.get("preexistentes"))
    )

    pendientes: set[str] = set()
    for elemento in datos.get("usuarios_pendientes", datos.get("pendientes", [])) or []:
        if isinstance(elemento, dict):
            elemento = elemento.get("clave") or elemento.get("usuario")
        clave = normalizar_usuario(elemento)
        if clave:
            pendientes.add(clave)

    fecha_ns: int | None = None
    valor_ns = datos.get("fecha_archivo_ns")
    try:
        if valor_ns is not None and not pd.isna(valor_ns):
            fecha_ns = int(valor_ns)
    except (TypeError, ValueError):
        fecha_ns = None

    return Manifiesto(
        ruta=ruta,
        asignaciones=asignaciones,
        preexistentes=preexistentes,
        pendientes=pendientes,
        fecha_archivo_ns=fecha_ns,
    )


# ---------------------------------------------------------------------------
# Historial de correcciones
# ---------------------------------------------------------------------------
def _a_entero(valor: Any) -> int | None:
    try:
        if valor is None or pd.isna(valor):
            return None
        return int(float(valor))
    except (TypeError, ValueError):
        return None


def cargar_historial(ruta: Path | str | None = None) -> pd.DataFrame:
    """Carga el historial; si no existe, devuelve un dataframe vacío."""

    ruta = Path(ruta or cfg.HISTORIAL_CSV)
    if not ruta.is_file():
        return pd.DataFrame(columns=list(COLUMNAS_HISTORIAL))

    try:
        historial = pd.read_csv(ruta, encoding="utf-8-sig")
    except OSError as exc:
        raise ErrorArchivo(
            f"No se pudo leer el historial '{ruta}': {exc}"
        ) from exc

    for columna in COLUMNAS_HISTORIAL:
        if columna not in historial.columns:
            historial[columna] = ""
    return historial.loc[:, list(COLUMNAS_HISTORIAL)]


def ultimas_manuales(historial: pd.DataFrame | None) -> dict[str, int]:
    """Devuelve la fecha de archivo más reciente por usuario en el historial."""

    ultimas: dict[str, int] = {}
    if historial is None or len(historial) == 0:
        return ultimas

    for _, fila in historial.iterrows():
        clave = normalizar_usuario(fila.get("USUARIO"))
        if clave is None:
            continue
        fecha_ns = _a_entero(fila.get("FECHA_ARCHIVO_NS"))
        if fecha_ns is None:
            continue
        ultimas[clave] = max(ultimas.get(clave, 0), fecha_ns)

    return ultimas


def _fecha_iso_desde_ns(fecha_ns: int | None) -> str:
    if not fecha_ns:
        return ""
    try:
        return datetime.fromtimestamp(fecha_ns / 1_000_000_000).isoformat(
            timespec="seconds"
        )
    except (OSError, OverflowError, ValueError):
        return ""


def registrar_historial(
    cambios: Iterable[dict[str, Any]],
    ruta: Path | str | None = None,
    fuente: str = "",
    fecha_archivo_ns: int | None = None,
) -> Path | None:
    """Agrega correcciones al historial de forma atómica e idempotente."""

    cambios = [dict(cambio) for cambio in cambios]
    if not cambios:
        return None

    ruta = Path(ruta or cfg.HISTORIAL_CSV)
    historial = cargar_historial(ruta)

    fecha_registro = datetime.now().isoformat(timespec="seconds")
    fecha_archivo = _fecha_iso_desde_ns(fecha_archivo_ns)

    filas: list[dict[str, Any]] = []
    for cambio in cambios:
        filas.append(
            {
                "FECHA_REGISTRO": fecha_registro,
                "FECHA_ARCHIVO": fecha_archivo,
                "FECHA_ARCHIVO_NS": int(fecha_archivo_ns or 0),
                "FUENTE": fuente,
                "USUARIO": str(cambio.get("USUARIO", "")),
                "AREA_ANTERIOR": str(cambio.get("AREA_ANTERIOR", "")),
                "AREA_NUEVA": str(cambio.get("AREA_NUEVA", "")),
                "ORIGEN": str(cambio.get("ORIGEN", "manual")),
            }
        )

    nuevo = pd.DataFrame(filas, columns=list(COLUMNAS_HISTORIAL))
    combinado = pd.concat([historial, nuevo], ignore_index=True)

    # Reejecutar el mismo archivo no debe duplicar sus correcciones.
    combinado = combinado.drop_duplicates(
        subset=[
            "FECHA_ARCHIVO_NS",
            "FUENTE",
            "USUARIO",
            "AREA_NUEVA",
            "ORIGEN",
        ],
        keep="last",
    ).reset_index(drop=True)

    escribir_dataframe_atomico(combinado, ruta)
    LOGGER.info("Historial actualizado: %s (%s filas)", ruta, len(combinado))
    return ruta
