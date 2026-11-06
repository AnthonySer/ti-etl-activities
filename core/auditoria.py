"""Auditoría y trazabilidad de ejecuciones, independiente del Excel principal.

Registra cada procesamiento, reimportación, consolidación, asignación manual,
corrección y restauración en ``data/auditoria_ejecuciones.csv``. Nunca agrega
columnas al detalle oficial de 11 columnas.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from config import configuracion as cfg
from core.archivos import ErrorArchivo, escribir_dataframe_atomico

LOGGER = logging.getLogger("automatizador")

COLUMNAS_AUDITORIA = (
    "ID_EJECUCION",
    "FECHA_HORA",
    "TIPO",
    "ARCHIVO_ORIGINAL",
    "ARCHIVO_SALIDA",
    "PERIODO",
    "LEIDOS",
    "VALIDOS",
    "OBSERVADOS",
    "PENDIENTES",
    "DUPLICADOS",
    "ERRORES",
    "NUEVOS",
    "MODIFICADOS",
    "USUARIO_EJECUTOR",
    "ESTADO",
    "MENSAJES",
)


def nueva_ejecucion() -> str:
    """Genera un identificador único y legible para una ejecución."""

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{marca}_{uuid.uuid4().hex[:6]}"


def _vacio(valor: Any) -> Any:
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    return valor


def cargar_auditoria(ruta: Path | str | None = None) -> pd.DataFrame:
    """Carga el historial de ejecuciones; si no existe, devuelve vacío."""

    ruta = Path(ruta or cfg.AUDITORIA_CSV)
    if not ruta.is_file():
        return pd.DataFrame(columns=list(COLUMNAS_AUDITORIA))

    try:
        datos = pd.read_csv(ruta, encoding="utf-8-sig")
    except OSError as exc:
        raise ErrorArchivo(f"No se pudo leer la auditoría '{ruta}': {exc}") from exc

    for columna in COLUMNAS_AUDITORIA:
        if columna not in datos.columns:
            datos[columna] = ""
    return datos.loc[:, list(COLUMNAS_AUDITORIA)]


def registrar(datos: Mapping[str, Any], ruta: Path | str | None = None) -> Path:
    """Agrega o actualiza una ejecución de forma atómica."""

    ruta = Path(ruta or cfg.AUDITORIA_CSV)
    historial = cargar_auditoria(ruta)

    fila = {columna: _vacio(datos.get(columna, "")) for columna in COLUMNAS_AUDITORIA}
    if not fila["ID_EJECUCION"]:
        fila["ID_EJECUCION"] = nueva_ejecucion()
    if not fila["FECHA_HORA"]:
        fila["FECHA_HORA"] = datetime.now().astimezone().isoformat(timespec="seconds")
    if not fila["ESTADO"]:
        fila["ESTADO"] = "FINALIZADO"

    nuevo = pd.DataFrame([fila], columns=list(COLUMNAS_AUDITORIA))
    combinado = pd.concat([historial, nuevo], ignore_index=True)
    combinado = combinado.drop_duplicates(
        subset=["ID_EJECUCION"], keep="last"
    ).reset_index(drop=True)

    escribir_dataframe_atomico(combinado, ruta)
    return ruta


def registrar_procesamiento(
    estadisticas: Any,
    *,
    archivo_original: str,
    usuario_ejecutor: str = "",
    periodo: str = "",
    mensajes: str = "",
    estado: str = "FINALIZADO",
) -> Path:
    """Registra el resultado de un procesamiento ETL."""

    return registrar(
        {
            "ID_EJECUCION": getattr(estadisticas, "id_ejecucion", nueva_ejecucion()),
            "TIPO": "PROCESAMIENTO",
            "ARCHIVO_ORIGINAL": archivo_original,
            "ARCHIVO_SALIDA": getattr(estadisticas, "archivo_salida", ""),
            "PERIODO": periodo,
            "LEIDOS": getattr(estadisticas, "registros_totales", 0),
            "VALIDOS": getattr(estadisticas, "registros_validos", 0),
            "OBSERVADOS": getattr(estadisticas, "registros_observados", 0),
            "PENDIENTES": getattr(estadisticas, "registros_pendientes", 0),
            "DUPLICADOS": getattr(estadisticas, "registros_duplicados", 0),
            "ERRORES": getattr(estadisticas, "registros_error", 0),
            "USUARIO_EJECUTOR": usuario_ejecutor,
            "ESTADO": estado,
            "MENSAJES": mensajes,
        }
    )


def registrar_evento(
    tipo: str,
    *,
    archivo_original: str = "",
    archivo_salida: str = "",
    periodo: str = "",
    nuevos: int = 0,
    modificados: int = 0,
    usuario_ejecutor: str = "",
    estado: str = "FINALIZADO",
    mensajes: str = "",
    id_ejecucion: str | None = None,
) -> Path:
    """Registra un evento genérico (asignación, corrección, restauración...)."""

    return registrar(
        {
            "ID_EJECUCION": id_ejecucion or nueva_ejecucion(),
            "TIPO": tipo,
            "ARCHIVO_ORIGINAL": archivo_original,
            "ARCHIVO_SALIDA": archivo_salida,
            "PERIODO": periodo,
            "NUEVOS": nuevos,
            "MODIFICADOS": modificados,
            "USUARIO_EJECUTOR": usuario_ejecutor,
            "ESTADO": estado,
            "MENSAJES": mensajes,
        }
    )


def resumen_auditoria(limite: int = 20) -> list[dict[str, Any]]:
    """Devuelve las últimas ejecuciones para mostrarlas en la interfaz."""

    historial = cargar_auditoria()
    if historial.empty:
        return []
    return historial.tail(limite).iloc[::-1].to_dict(orient="records")


def contar_por_tipo(historial: pd.DataFrame | None = None) -> dict[str, int]:
    historial = cargar_auditoria() if historial is None else historial
    if historial.empty:
        return {}
    return {
        str(clave): int(valor)
        for clave, valor in historial["TIPO"].value_counts().items()
    }


def ultima_ejecucion(historial: pd.DataFrame | None = None) -> dict[str, Any] | None:
    historial = cargar_auditoria() if historial is None else historial
    if historial.empty:
        return None
    return historial.iloc[-1].to_dict()


def limpiar_auditoria(ruta: Path | str | None = None) -> Path:
    """Reinicia la auditoría conservando una copia de seguridad."""

    ruta = Path(ruta or cfg.AUDITORIA_CSV)
    if ruta.is_file():
        from core.archivos import respaldar_archivo

        respaldar_archivo(ruta)
    vacio = pd.DataFrame(columns=list(COLUMNAS_AUDITORIA))
    escribir_dataframe_atomico(vacio, ruta)
    return ruta


def registrar_cambios_maestro(
    cambios: Iterable[Mapping[str, Any]],
    *,
    id_ejecucion: str,
    tipo: str,
    archivo_fuente: str = "",
    ruta: Path | str | None = None,
) -> Path | None:
    """Registra cambios campo a campo en el historial del maestro histórico."""

    cambios = list(cambios)
    if not cambios:
        return None

    ruta = Path(ruta or cfg.HISTORIAL_TICKETS_CSV)
    if ruta.is_file():
        try:
            historial = pd.read_csv(ruta, encoding="utf-8-sig")
        except OSError as exc:
            raise ErrorArchivo(
                f"No se pudo leer el historial de tickets '{ruta}': {exc}"
            ) from exc
    else:
        historial = pd.DataFrame(
            columns=(
                "FECHA",
                "ID_EJECUCION",
                "TIPO",
                "ID_TECNICO",
                "CAMPO",
                "VALOR_ANTERIOR",
                "VALOR_NUEVO",
                "ARCHIVO_FUENTE",
            )
        )

    fecha = datetime.now().astimezone().isoformat(timespec="seconds")
    filas = []
    for cambio in cambios:
        filas.append(
            {
                "FECHA": fecha,
                "ID_EJECUCION": id_ejecucion,
                "TIPO": tipo,
                "ID_TECNICO": cambio.get("ID_TECNICO", ""),
                "CAMPO": cambio.get("CAMPO", ""),
                "VALOR_ANTERIOR": cambio.get("VALOR_ANTERIOR", ""),
                "VALOR_NUEVO": cambio.get("VALOR_NUEVO", ""),
                "ARCHIVO_FUENTE": archivo_fuente,
            }
        )

    combinado = pd.concat(
        [historial, pd.DataFrame(filas)], ignore_index=True
    )
    combinado = combinado.drop_duplicates(
        subset=["ID_EJECUCION", "ID_TECNICO", "CAMPO", "VALOR_NUEVO"],
        keep="last",
    ).reset_index(drop=True)
    escribir_dataframe_atomico(combinado, ruta)
    return ruta
