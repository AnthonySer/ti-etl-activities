"""Consolidación de un Excel procesado en el maestro histórico de tickets."""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any

from config import configuracion as cfg
from core import auditoria
from core.archivos import ErrorArchivo
from core.esquema import COLUMNAS_OFICIALES, huella_fila
from core.maestro_tickets import MaestroTickets
from core.reimportacion import (
    buscar_manifiesto_corregido,
    cargar_filas_manifiesto,
    leer_corregido,
)

LOGGER = logging.getLogger("automatizador")


def _ids_desde_manifiesto(
    df,
    filas_manifiesto,
) -> list[str | None]:
    """Asigna el identificador técnico de cada fila del archivo procesado."""

    por_huella: dict[str, deque[str]] = defaultdict(deque)
    for original in filas_manifiesto:
        por_huella[original.huella].append(original.id_tecnico)

    ids: list[str | None] = []
    for _, fila in df.iterrows():
        huella = huella_fila(fila)
        cola = por_huella.get(huella)
        if cola:
            ids.append(cola.popleft())
        else:
            ids.append(None)
    return ids


def consolidar_procesado(
    ruta_procesado: Path | str,
    ruta_manifiesto: Path | str | None = None,
    ruta_maestro: Path | str | None = None,
    *,
    usuario_ejecutor: str = "",
    id_ejecucion: str | None = None,
    permitir_duplicados: bool = False,
    crear_backup: bool = True,
) -> dict[str, Any]:
    """Incorpora un archivo procesado al maestro histórico sin eliminar nada."""

    ruta_procesado = Path(ruta_procesado)
    if not ruta_procesado.exists():
        raise ErrorArchivo(f"No existe el archivo procesado: {ruta_procesado}")

    if ruta_manifiesto is None:
        ruta_manifiesto = buscar_manifiesto_corregido(ruta_procesado)
    else:
        ruta_manifiesto = Path(ruta_manifiesto)
        if not ruta_manifiesto.is_file():
            raise ErrorArchivo(
                f"No existe el manifiesto indicado: {ruta_manifiesto}"
            )

    filas_manifiesto, manifiesto = cargar_filas_manifiesto(ruta_manifiesto)
    df, hoja = leer_corregido(ruta_procesado)

    ids = _ids_desde_manifiesto(df, filas_manifiesto)
    sin_id = sum(1 for valor in ids if valor is None)
    if sin_id:
        LOGGER.warning(
            "El manifiesto no cubre %s fila(s) de '%s'. Se incorporarán como "
            "registros nuevos y se aplicará la protección contra duplicados.",
            sin_id,
            ruta_procesado.name,
        )

    maestro = MaestroTickets.cargar(ruta_maestro)
    ejecucion = id_ejecucion or auditoria.nueva_ejecucion()

    resultado = maestro.aplicar(
        df.loc[:, list(COLUMNAS_OFICIALES)],
        ids=ids,
        id_ejecucion=ejecucion,
        tipo_operacion="CONSOLIDACION",
        archivo_fuente=ruta_procesado.name,
        permitir_duplicados=permitir_duplicados,
    )

    guardado = maestro.guardar(ruta_xlsx=ruta_maestro, crear_backup=crear_backup)

    if resultado.cambios_historial:
        auditoria.registrar_cambios_maestro(
            resultado.cambios_historial,
            id_ejecucion=ejecucion,
            tipo="CONSOLIDACION",
            archivo_fuente=ruta_procesado.name,
        )

    auditoria.registrar_evento(
        "CONSOLIDACION",
        archivo_original=str(ruta_procesado.resolve()),
        archivo_salida=str(guardado.ruta_xlsx or ""),
        periodo=str(manifiesto.get("periodo") or ""),
        nuevos=resultado.nuevos,
        modificados=resultado.actualizados,
        usuario_ejecutor=usuario_ejecutor,
        mensajes=(
            f"Hoja: {hoja} | Sin cambios: {resultado.sin_cambios} | "
            f"Duplicados omitidos: {resultado.duplicados} | "
            f"Filas sin id: {sin_id} | Total maestro: {len(maestro)}"
        ),
        id_ejecucion=ejecucion,
    )

    LOGGER.info(
        "Consolidación %s: nuevos=%s actualizados=%s sin_cambios=%s "
        "duplicados=%s total=%s",
        ruta_procesado.name,
        resultado.nuevos,
        resultado.actualizados,
        resultado.sin_cambios,
        resultado.duplicados,
        len(maestro),
    )

    return {
        "id_ejecucion": ejecucion,
        "archivo": ruta_procesado.name,
        "hoja": hoja,
        "manifiesto": str(ruta_manifiesto),
        "periodo": str(manifiesto.get("periodo") or ""),
        "nuevos": resultado.nuevos,
        "actualizados": resultado.actualizados,
        "sin_cambios": resultado.sin_cambios,
        "duplicados": resultado.duplicados,
        "omitidos": resultado.omitidos,
        "sin_id": sin_id,
        "total_maestro": len(maestro),
        "rutas": guardado,
        "fecha": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
