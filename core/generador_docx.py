"""Orquesta la escritura del Anexo 5 usando datos preparados por otra capa.

Este módulo solo empaqueta los cambios solicitados en una copia de la plantilla.
No deriva cifras ni decide reglas sobre períodos, proyectos, áreas u horas.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from docx import Document

from core.actualizador_graficos_docx import (
    ResultadoGraficosDocx,
    SerieGraficoDocx,
    actualizar_graficos_docx,
)
from core.actualizador_tablas_docx import (
    ENCABEZADOS_TABLA,
    actualizar_tablas_docx,
)
from core.actualizador_textos_docx import reemplazar_marcadores_docx


class ErrorGeneracionDocx(ValueError):
    """La plantilla o el contenido entregado no cumple el contrato del generador."""


def _sha256(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def generar_anexo5_docx(
    plantilla: str | Path,
    salida: str | Path,
    *,
    reemplazos_texto: Mapping[str, str],
    filas_por_tabla: Mapping[str, Sequence[Sequence[object]]],
    totales_por_tabla: Mapping[str, Sequence[object] | None],
    series_por_grafico: Mapping[int, SerieGraficoDocx],
) -> ResultadoGraficosDocx:
    """Genera en una ruta nueva a partir de texto, tablas y gráficas ya resueltos.

    La operación valida que se aporten las cinco tablas y los cuatro gráficos,
    trabaja sobre una copia temporal y deja la plantilla intacta. Los textos,
    filas, totales y series son responsabilidad de la capa de proyección.
    """

    origen = Path(plantilla).expanduser().resolve()
    destino = Path(salida).expanduser().resolve()
    if not origen.is_file():
        raise ErrorGeneracionDocx(f"No existe la plantilla DOCX: {origen}.")
    if origen == destino:
        raise ErrorGeneracionDocx("La salida debe ser distinta de la plantilla original.")
    if destino.exists():
        raise FileExistsError(f"La salida ya existe y no se sobrescribirá: {destino}.")
    if set(filas_por_tabla) != set(ENCABEZADOS_TABLA):
        faltan = set(ENCABEZADOS_TABLA) - set(filas_por_tabla)
        extras = set(filas_por_tabla) - set(ENCABEZADOS_TABLA)
        detalle = []
        if faltan:
            detalle.append("faltan " + ", ".join(sorted(faltan)))
        if extras:
            detalle.append("sobran " + ", ".join(sorted(extras)))
        raise ErrorGeneracionDocx("Se requieren exactamente las cinco tablas: " + "; ".join(detalle) + ".")
    if set(totales_por_tabla) - set(ENCABEZADOS_TABLA):
        raise ErrorGeneracionDocx("Hay totales para tablas desconocidas.")
    if set(series_por_grafico) != {1, 2, 3, 4}:
        raise ErrorGeneracionDocx("Se requieren datos para los cuatro gráficos nativos (1, 2, 3 y 4).")

    hash_origen = _sha256(origen)
    destino.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporal_nombre = tempfile.mkstemp(
        prefix=".anexo5-contenido-", suffix=".docx", dir=destino.parent
    )
    os.close(descriptor)
    temporal = Path(temporal_nombre)
    salida_creada = False
    try:
        shutil.copy2(origen, temporal)
        documento = Document(temporal)
        reemplazar_marcadores_docx(documento, reemplazos_texto, exigir_todos=True)
        actualizar_tablas_docx(documento, filas_por_tabla, totales_por_tabla)
        documento.save(temporal)
        resultado = actualizar_graficos_docx(temporal, destino, series_por_grafico)
        salida_creada = True
        if _sha256(origen) != hash_origen:
            destino.unlink(missing_ok=True)
            salida_creada = False
            raise ErrorGeneracionDocx("La plantilla oficial cambió durante la generación.")
        return resultado
    except Exception:
        if salida_creada:
            destino.unlink(missing_ok=True)
        raise
    finally:
        temporal.unlink(missing_ok=True)

