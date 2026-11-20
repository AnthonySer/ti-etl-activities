"""Orquesta la escritura del Anexo 5 usando datos preparados por otra capa.

Este módulo solo empaqueta los cambios solicitados en una copia de la plantilla.
No deriva cifras ni decide reglas sobre períodos, proyectos, áreas u horas.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import shutil
import tempfile

from docx import Document

from core.actualizador_graficos_docx import (
    ResultadoGraficosDocx,
    SerieGraficoDocx,
    actualizar_graficos_docx,
    graficos_presentes,
)
from core.actualizador_tablas_docx import (
    actualizar_tablas_docx,
    localizar_tablas_docx,
)
from core.actualizador_textos_docx import (
    aplicar_formato_tabla_detalle_docx,
    filtrar_marcadores_presentes,
    quitar_subrayado_titulos_graficos_docx,
    reemplazar_marcadores_docx,
)


class ErrorGeneracionDocx(ValueError):
    """La plantilla o el contenido entregado no cumple el contrato del generador."""


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

    La operación adapta el contenido a las tablas y gráficos que realmente
    incluye la plantilla elegida (por ejemplo, cinco tablas y cuatro gráficos en
    la versión anterior, o seis tablas y tres gráficos en la nueva). Trabaja
    sobre una copia temporal y deja la plantilla intacta. Los textos, filas,
    totales y series son responsabilidad de la capa de proyección.
    """

    origen = Path(plantilla).expanduser().resolve()
    destino = Path(salida).expanduser().resolve()
    if not origen.is_file():
        raise ErrorGeneracionDocx(f"No existe la plantilla DOCX: {origen}.")
    if origen == destino:
        raise ErrorGeneracionDocx("La salida debe ser distinta de la plantilla original.")
    if destino.exists():
        raise FileExistsError(f"La salida ya existe y no se sobrescribirá: {destino}.")

    destino.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporal_nombre = tempfile.mkstemp(
        prefix=".anexo5-contenido-", suffix=".docx", dir=destino.parent
    )
    os.close(descriptor)
    temporal = Path(temporal_nombre)
    salida_creada = False
    try:
        # Se trabaja siempre sobre una copia. Además de proteger la plantilla,
        # permite generar aunque Word tenga abierto el archivo original (Windows
        # bloquea la lectura directa, pero no la copia).
        firma_origen = (origen.stat().st_mtime_ns, origen.stat().st_size)
        shutil.copy2(origen, temporal)
        documento = Document(temporal)

        tablas_plantilla = set(localizar_tablas_docx(documento))
        faltan_tablas = tablas_plantilla - set(filas_por_tabla)
        if faltan_tablas:
            raise ErrorGeneracionDocx(
                "Faltan datos para las tablas de la plantilla: "
                + ", ".join(sorted(faltan_tablas))
                + "."
            )
        filas_efectivas = {
            nombre: filas
            for nombre, filas in filas_por_tabla.items()
            if nombre in tablas_plantilla
        }
        totales_efectivos = {
            nombre: total
            for nombre, total in totales_por_tabla.items()
            if nombre in tablas_plantilla
        }
        totales_sin_filas = set(totales_efectivos) - set(filas_efectivas)
        if totales_sin_filas:
            raise ErrorGeneracionDocx(
                "Hay totales sin filas de datos para: "
                + ", ".join(sorted(totales_sin_filas))
                + "."
            )

        graficos_plantilla = graficos_presentes(temporal)
        faltan_series = set(graficos_plantilla) - set(series_por_grafico)
        if faltan_series:
            raise ErrorGeneracionDocx(
                "Faltan series para los gráficos de la plantilla: "
                + ", ".join(str(numero) for numero in sorted(faltan_series))
                + "."
            )
        series_efectivas = {
            numero: series_por_grafico[numero] for numero in graficos_plantilla
        }
        reemplazos_efectivos = filtrar_marcadores_presentes(
            documento, reemplazos_texto
        )

        reemplazar_marcadores_docx(documento, reemplazos_efectivos, exigir_todos=True)
        quitar_subrayado_titulos_graficos_docx(documento)
        actualizar_tablas_docx(documento, filas_efectivas, totales_efectivos)
        aplicar_formato_tabla_detalle_docx(documento, tamano_pt=6, negrita=False)
        documento.save(temporal)
        resultado = actualizar_graficos_docx(temporal, destino, series_efectivas)
        salida_creada = True
        if (origen.stat().st_mtime_ns, origen.stat().st_size) != firma_origen:
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

