"""Actualización de las tablas existentes, clonando filas con su formato."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import unicodedata

from docx.document import Document as DocumentType
from docx.table import Table


ENCABEZADOS_TABLA = {
    "resumen_tipo_atencion": ("TIPO DE ATENCION", "TIEMPO(H)", "%"),
    "proyectos": ("PROYECTOS", "TIEMPO(H)", "%"),
    "tickets_tipo": ("TIPO DE ACTIVIDAD", "TICKETS", "%"),
    "areas": ("AREAS", "TICKETS", "%"),
    "detalle": (
        "TIPO DE ATENCION", "DETALLE", "FECHA", "USUARIO", "AREA_USUARIO", "HORAS"
    ),
}


class ErrorTablaDocx(ValueError):
    """La estructura de las tablas de la plantilla no coincide con el contrato."""


@dataclass(frozen=True)
class ResultadoTablasDocx:
    filas_datos: tuple[tuple[str, int], ...]
    filas_totales: tuple[tuple[str, bool], ...]


def _clave(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto.upper())
    sin_tildes = "".join(c for c in normalizado if not unicodedata.combining(c))
    return "".join(c for c in sin_tildes if c.isalnum() or c in "%()")


def _encabezado(tabla: Table) -> tuple[str, ...]:
    if not tabla.rows:
        return ()
    return tuple(_clave(celda.text.strip()) for celda in tabla.rows[0].cells)


def localizar_tablas_docx(documento: DocumentType) -> dict[str, Table]:
    """Localiza las cinco tablas por todos sus encabezados, no solo el primero."""

    requeridos = {nombre: tuple(_clave(x) for x in encabezado) for nombre, encabezado in ENCABEZADOS_TABLA.items()}
    encontradas: dict[str, Table] = {}
    for tabla in documento.tables:
        cabecera = _encabezado(tabla)
        for nombre, esperado in requeridos.items():
            if cabecera == esperado:
                if nombre in encontradas:
                    raise ErrorTablaDocx(f"La plantilla contiene más de una tabla '{nombre}'.")
                encontradas[nombre] = tabla
    faltantes = [nombre for nombre in requeridos if nombre not in encontradas]
    if faltantes:
        raise ErrorTablaDocx("No se encontraron las tablas: " + ", ".join(faltantes))
    return encontradas


def _es_total(fila) -> bool:
    valor = fila.cells[0].text.strip().upper()
    return valor.startswith("TOTAL")


def _escribir_celda(celda, valor: object) -> None:
    texto = "" if valor is None else str(valor)
    parrafos = celda.paragraphs
    if not parrafos:
        celda.add_paragraph(texto)
        return
    parrafo = parrafos[0]
    runs = list(parrafo.runs)
    if runs:
        runs[0].text = texto
        for run in runs[1:]:
            run._element.getparent().remove(run._element)
    else:
        parrafo.add_run(texto)
    # Mantiene los párrafos/cell properties existentes; limpia su contenido.
    for parrafo_extra in parrafos[1:]:
        for run in list(parrafo_extra.runs):
            run.text = ""


def _escribir_fila(fila, valores: Sequence[object], nombre: str) -> None:
    if len(valores) != len(fila.cells):
        raise ErrorTablaDocx(
            f"La fila de '{nombre}' tiene {len(valores)} valores; se esperaban {len(fila.cells)}."
        )
    for celda, valor in zip(fila.cells, valores):
        _escribir_celda(celda, valor)


def actualizar_tabla_docx(
    tabla: Table,
    nombre: str,
    filas: Sequence[Sequence[object]],
    fila_total: Sequence[object] | None = None,
) -> tuple[int, bool]:
    """Sustituye filas de datos y total, manteniendo cabecera y tabla existente.

    Una fila fuente de datos y, si existe, la fila de total original actúan como
    moldes XML. ``fila_total=None`` elimina cualquier total de ejemplo.
    """

    if nombre not in ENCABEZADOS_TABLA:
        raise ErrorTablaDocx(f"Tabla DOCX desconocida: {nombre}.")
    if not tabla.rows:
        raise ErrorTablaDocx(f"La tabla '{nombre}' no tiene encabezado.")
    cabecera = tuple(_clave(celda.text.strip()) for celda in tabla.rows[0].cells)
    esperado = tuple(_clave(x) for x in ENCABEZADOS_TABLA[nombre])
    if cabecera != esperado:
        raise ErrorTablaDocx(f"Los encabezados de la tabla '{nombre}' no coinciden con la plantilla.")

    filas_actuales = list(tabla.rows)
    filas_total_actuales = [fila for fila in filas_actuales[1:] if _es_total(fila)]
    filas_datos_actuales = [fila for fila in filas_actuales[1:] if not _es_total(fila)]
    if len(filas_total_actuales) > 1:
        raise ErrorTablaDocx(f"La tabla '{nombre}' contiene varios totales de ejemplo.")
    if filas_datos_actuales:
        molde_datos = deepcopy(filas_datos_actuales[0]._tr)
    elif filas_total_actuales:
        molde_datos = deepcopy(filas_total_actuales[0]._tr)
    else:
        raise ErrorTablaDocx(f"La tabla '{nombre}' no tiene una fila que pueda servir de molde.")
    molde_total = deepcopy(filas_total_actuales[0]._tr) if filas_total_actuales else deepcopy(molde_datos)

    for fila in filas_actuales[1:]:
        tabla._tbl.remove(fila._tr)
    for valores in filas:
        fila_xml = deepcopy(molde_datos)
        tabla._tbl.append(fila_xml)
        _escribir_fila(tabla.rows[-1], valores, nombre)
    tiene_total = fila_total is not None
    if tiene_total:
        fila_xml = deepcopy(molde_total)
        tabla._tbl.append(fila_xml)
        _escribir_fila(tabla.rows[-1], fila_total, nombre)
    return len(filas), tiene_total


def actualizar_tablas_docx(
    documento: DocumentType,
    filas_por_tabla: Mapping[str, Sequence[Sequence[object]]],
    totales_por_tabla: Mapping[str, Sequence[object] | None] | None = None,
) -> ResultadoTablasDocx:
    """Actualiza cualquier subconjunto de tablas con filas ya calculadas.

    No calcula estadísticas ni elige reglas sobre proyectos, áreas u horas.
    """

    desconocidas = set(filas_por_tabla) - set(ENCABEZADOS_TABLA)
    if desconocidas:
        raise ErrorTablaDocx("Tablas desconocidas: " + ", ".join(sorted(desconocidas)))
    tablas = localizar_tablas_docx(documento)
    totales = totales_por_tabla or {}
    totales_sin_filas = set(totales) - set(filas_por_tabla)
    if totales_sin_filas:
        raise ErrorTablaDocx(
            "Se indicaron totales sin filas de datos para: " + ", ".join(sorted(totales_sin_filas))
        )
    filas_datos: list[tuple[str, int]] = []
    filas_totales: list[tuple[str, bool]] = []
    for nombre, filas in filas_por_tabla.items():
        cantidad, tiene_total = actualizar_tabla_docx(
            tablas[nombre], nombre, filas, totales.get(nombre)
        )
        filas_datos.append((nombre, cantidad))
        filas_totales.append((nombre, tiene_total))
    return ResultadoTablasDocx(tuple(filas_datos), tuple(filas_totales))
