"""Generación del Excel procesado con el esquema oficial de 11 columnas."""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, PieChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from config import configuracion as cfg
from core.archivos import ErrorArchivo
from core.esquema import (
    C_ACTIVIDAD,
    C_AREA,
    C_ATENCION,
    C_DETALLE,
    C_FECHA,
    C_HORAS,
    C_PROYECTO,
    C_REQUERIMIENTO,
    C_RESPONSABLE,
    C_SOLICITANTE,
    C_SUBTIPO,
    COLUMNAS_OFICIALES,
    validar_estructura_exacta,
)

LOGGER = logging.getLogger("automatizador")

_AZUL = "1F3864"
_AZUL_CLARO = "D9E2F3"
_GRIS = "F2F2F2"
_VERDE = "E2EFDA"
_AMBAR = "FFF2CC"
_ROJO = "FCE4D6"

_ANCHOS = {
    C_DETALLE: 62,
    C_FECHA: 13,
    C_SOLICITANTE: 26,
    C_AREA: 26,
    C_ATENCION: 17,
    C_REQUERIMIENTO: 24,
    C_RESPONSABLE: 24,
    C_SUBTIPO: 22,
    C_PROYECTO: 30,
    C_HORAS: 11,
    C_ACTIVIDAD: 15,
}


def resolver_salida(
    ruta_entrada: Path,
    ruta_salida: Path | str | None = None,
    sobrescribir: bool = False,
) -> Path:
    """Calcula la ruta de salida sin sobrescribir silenciosamente."""

    ruta_entrada = Path(ruta_entrada)

    if ruta_salida:
        destino = Path(ruta_salida)
        if not destino.is_absolute():
            destino = cfg.BASE_DIR / destino
    else:
        destino = cfg.OUTPUT_DIR / f"{ruta_entrada.stem}_procesado.xlsx"

    if destino.suffix.lower() != ".xlsx":
        raise ErrorArchivo("El archivo procesado debe tener extensión .xlsx.")

    if destino.resolve() == ruta_entrada.resolve():
        raise ErrorArchivo(
            "La ruta de salida no puede ser igual al archivo de entrada. "
            "Los archivos originales nunca se modifican."
        )

    destino.parent.mkdir(parents=True, exist_ok=True)

    if destino.exists() and not sobrescribir:
        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = destino.with_name(f"{destino.stem}_{marca}{destino.suffix}")
        candidato = base
        contador = 2
        while candidato.exists():
            candidato = base.with_name(f"{base.stem}_v{contador}{base.suffix}")
            contador += 1
        LOGGER.warning(
            "El archivo de salida ya existía. Se generará una nueva versión: %s",
            candidato,
        )
        destino = candidato

    return destino


# ---------------------------------------------------------------------------
# Utilidades de escritura
# ---------------------------------------------------------------------------
def _borde_fino() -> Border:
    lado = Side(style="thin", color="BFBFBF")
    return Border(left=lado, right=lado, top=lado, bottom=lado)


def _estilo_encabezado(hoja: Worksheet, columnas: int, fila: int = 1) -> None:
    relleno = PatternFill("solid", fgColor=_AZUL)
    fuente = Font(name="Segoe UI", bold=True, color="FFFFFF", size=10)
    for columna in range(1, columnas + 1):
        celda = hoja.cell(row=fila, column=columna)
        celda.fill = relleno
        celda.font = fuente
        celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        celda.border = _borde_fino()
    hoja.row_dimensions[fila].height = 26


def _valor_legible(columna: str, valor: Any) -> Any:
    if valor is None:
        return None
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    if columna == C_FECHA:
        if isinstance(valor, (datetime, date, pd.Timestamp)):
            return valor
        return str(valor)
    if columna == C_HORAS:
        if isinstance(valor, (int, float)):
            return float(valor)
        return str(valor)
    return str(valor)


def escribir_detalle(
    hoja: Worksheet,
    df: pd.DataFrame,
    fila_inicio: int = 1,
) -> int:
    """Escribe las 11 columnas oficiales con formato legible."""

    for indice, columna in enumerate(COLUMNAS_OFICIALES, start=1):
        hoja.cell(row=fila_inicio, column=indice, value=columna)

    _estilo_encabezado(hoja, len(COLUMNAS_OFICIALES), fila_inicio)

    for desplazamiento, (_, fila) in enumerate(df.iterrows(), start=1):
        numero = fila_inicio + desplazamiento
        for indice, columna in enumerate(COLUMNAS_OFICIALES, start=1):
            celda = hoja.cell(
                row=numero,
                column=indice,
                value=_valor_legible(columna, fila.get(columna)),
            )
            celda.font = Font(name="Segoe UI", size=9)
            celda.border = _borde_fino()
            if columna == C_DETALLE:
                celda.alignment = Alignment(vertical="top", wrap_text=True)
            elif columna in (C_FECHA, C_HORAS):
                celda.alignment = Alignment(horizontal="center", vertical="top")
            else:
                celda.alignment = Alignment(vertical="top", wrap_text=True)

            if columna == C_FECHA and isinstance(
                celda.value, (datetime, date, pd.Timestamp)
            ):
                celda.number_format = "DD/MM/YYYY"
            elif columna == C_HORAS and isinstance(celda.value, (int, float)):
                celda.number_format = "0.00"

    for indice, columna in enumerate(COLUMNAS_OFICIALES, start=1):
        hoja.column_dimensions[get_column_letter(indice)].width = _ANCHOS.get(
            columna, 18
        )

    ultima = fila_inicio + max(len(df), 1)
    hoja.freeze_panes = f"A{fila_inicio + 1}"
    hoja.auto_filter.ref = (
        f"A{fila_inicio}:{get_column_letter(len(COLUMNAS_OFICIALES))}{ultima}"
    )
    hoja.sheet_view.showGridLines = True
    return ultima


def _escribir_tabla(
    hoja: Worksheet,
    fila_inicio: int,
    encabezados: Iterable[str],
    filas: Iterable[Iterable[Any]],
) -> int:
    encabezados = list(encabezados)
    for indice, titulo in enumerate(encabezados, start=1):
        hoja.cell(row=fila_inicio, column=indice, value=titulo)
    _estilo_encabezado(hoja, len(encabezados), fila_inicio)

    numero = fila_inicio
    for desplazamiento, fila in enumerate(filas, start=1):
        numero = fila_inicio + desplazamiento
        for indice, valor in enumerate(fila, start=1):
            celda = hoja.cell(row=numero, column=indice, value=valor)
            celda.font = Font(name="Segoe UI", size=9)
            celda.border = _borde_fino()
            celda.alignment = Alignment(vertical="top", wrap_text=True)
    return numero


def _resumen_basico(df: pd.DataFrame) -> dict[str, Any]:
    horas = pd.to_numeric(df[C_HORAS], errors="coerce")
    return {
        "Total de tickets": int(len(df)),
        "Total de horas": round(float(horas.fillna(0).sum()), 2),
        "Promedio de horas por ticket": round(float(horas.mean()), 2)
        if horas.notna().any()
        else 0,
        "Mediana de horas por ticket": round(float(horas.median()), 2)
        if horas.notna().any()
        else 0,
        "Solicitantes únicos": int(
            df[C_SOLICITANTE].dropna().astype(str).str.strip().str.upper().nunique()
        ),
        "Responsables únicos": int(
            df[C_RESPONSABLE].dropna().astype(str).str.strip().str.upper().nunique()
        ),
        "Áreas únicas": int(
            df[C_AREA].dropna().astype(str).str.strip().str.upper().nunique()
        ),
    }


def _distribucion(df: pd.DataFrame, columna: str) -> list[tuple[str, int]]:
    serie = df[columna].dropna().astype(str).str.strip()
    if serie.empty:
        return []
    conteo = serie.value_counts()
    return [(str(indice), int(valor)) for indice, valor in conteo.items()]


def _escribir_resumen(
    hoja: Worksheet,
    df: pd.DataFrame,
    resumen: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> None:
    datos = dict(resumen or {})
    if not datos:
        datos = _resumen_basico(df)

    hoja.column_dimensions["A"].width = 38
    hoja.column_dimensions["B"].width = 26

    titulo = hoja.cell(row=1, column=1, value="RESUMEN DEL PROCESAMIENTO")
    titulo.font = Font(name="Segoe UI", size=14, bold=True, color=_AZUL)
    hoja.merge_cells("A1:B1")

    fila = 3
    for clave, valor in datos.items():
        hoja.cell(row=fila, column=1, value=clave).font = Font(
            name="Segoe UI", size=10, bold=True
        )
        celda = hoja.cell(row=fila, column=2, value=valor)
        celda.font = Font(name="Segoe UI", size=10)
        celda.alignment = Alignment(horizontal="left")
        fila += 1

    if meta:
        fila += 1
        hoja.cell(row=fila, column=1, value="CONTROL DE EJECUCIÓN").font = Font(
            name="Segoe UI", size=11, bold=True, color=_AZUL
        )
        fila += 1
        for clave, valor in meta.items():
            hoja.cell(row=fila, column=1, value=clave).font = Font(
                name="Segoe UI", size=9, bold=True
            )
            hoja.cell(row=fila, column=2, value=valor).font = Font(
                name="Segoe UI", size=9
            )
            fila += 1

    fila += 1
    for columna, etiqueta in (
        (C_ATENCION, "Distribución por atención"),
        (C_REQUERIMIENTO, "Distribución por requerimiento"),
        (C_RESPONSABLE, "Distribución por responsable"),
        (C_AREA, "Distribución por área"),
        (C_SUBTIPO, "Distribución por subtipo"),
        (C_PROYECTO, "Distribución por proyecto"),
        (C_ACTIVIDAD, "Distribución por actividad"),
    ):
        distribucion = _distribucion(df, columna)
        if not distribucion:
            continue
        hoja.cell(row=fila, column=1, value=etiqueta).font = Font(
            name="Segoe UI", size=11, bold=True, color=_AZUL
        )
        fila += 1
        hoja.cell(row=fila, column=1, value=etiqueta.split(" por ")[-1].capitalize()).font = Font(
            name="Segoe UI", size=9, bold=True
        )
        hoja.cell(row=fila, column=2, value="Tickets").font = Font(
            name="Segoe UI", size=9, bold=True
        )
        fila += 1
        for valor, cantidad in distribucion:
            hoja.cell(row=fila, column=1, value=valor).font = Font(
                name="Segoe UI", size=9
            )
            hoja.cell(row=fila, column=2, value=cantidad).font = Font(
                name="Segoe UI", size=9
            )
            fila += 1
        fila += 1


def _escribir_observaciones(
    hoja: Worksheet,
    observaciones: Iterable[Mapping[str, Any]],
) -> None:
    encabezados = ("Fila", "Severidad", "Campo", "Motivo", "Valor encontrado", "Acción requerida")
    fila = _escribir_tabla(hoja, 1, encabezados, ())
    colores = {_OBSERVACION: _AMBAR, _ERROR: _ROJO}

    fila = 1
    for observacion in observaciones:
        fila += 1
        valores = (
            observacion.get("fila"),
            observacion.get("severidad", _OBSERVACION),
            observacion.get("campo", ""),
            observacion.get("motivo", ""),
            observacion.get("valor", ""),
            observacion.get("accion", ""),
        )
        for indice, valor in enumerate(valores, start=1):
            celda = hoja.cell(row=fila, column=indice, value=valor)
            celda.font = Font(name="Segoe UI", size=9)
            celda.border = _borde_fino()
            celda.alignment = Alignment(vertical="top", wrap_text=True)
        color = colores.get(str(observacion.get("severidad")), _GRIS)
        hoja.cell(row=fila, column=2).fill = PatternFill("solid", fgColor=color)

    anchos = (8, 13, 18, 60, 30, 42)
    for indice, ancho in enumerate(anchos, start=1):
        hoja.column_dimensions[get_column_letter(indice)].width = ancho
    hoja.freeze_panes = "A2"
    hoja.auto_filter.ref = f"A1:{get_column_letter(len(encabezados))}{max(fila, 1)}"


_OBSERVACION = "observacion"
_ERROR = "error"


def _escribir_control(
    hoja: Worksheet,
    meta: Mapping[str, Any],
) -> None:
    hoja.column_dimensions["A"].width = 34
    hoja.column_dimensions["B"].width = 78

    hoja.cell(row=1, column=1, value="CONTROL DE EJECUCIÓN").font = Font(
        name="Segoe UI", size=14, bold=True, color=_AZUL
    )
    hoja.merge_cells("A1:B1")

    fila = 3
    for clave, valor in meta.items():
        hoja.cell(row=fila, column=1, value=clave).font = Font(
            name="Segoe UI", size=10, bold=True
        )
        celda = hoja.cell(row=fila, column=2, value=valor)
        celda.font = Font(name="Segoe UI", size=10)
        celda.alignment = Alignment(vertical="top", wrap_text=True)
        fila += 1

    fila += 1
    hoja.cell(row=fila, column=1, value="Estructura oficial (11 columnas)").font = Font(
        name="Segoe UI", size=10, bold=True, color=_AZUL
    )
    fila += 1
    for posicion, columna in enumerate(COLUMNAS_OFICIALES, start=1):
        hoja.cell(row=fila, column=1, value=posicion).font = Font(
            name="Segoe UI", size=9
        )
        hoja.cell(row=fila, column=2, value=columna).font = Font(
            name="Segoe UI", size=9
        )
        fila += 1


def _escribir_extras(hoja: Worksheet, extras: pd.DataFrame) -> None:
    encabezados = [str(c) for c in extras.columns]
    filas = extras.itertuples(index=False, name=None)
    ultima = _escribir_tabla(hoja, 1, encabezados, filas)
    for indice in range(1, len(encabezados) + 1):
        hoja.column_dimensions[get_column_letter(indice)].width = 24
    hoja.freeze_panes = "A2"
    hoja.auto_filter.ref = f"A1:{get_column_letter(len(encabezados))}{max(ultima, 1)}"


def _verificar_libro(ruta: Path) -> None:
    libro = load_workbook(ruta, read_only=True)
    try:
        if cfg.HOJA_DETALLE not in libro.sheetnames:
            raise ErrorArchivo(
                f"El archivo generado no contiene la hoja '{cfg.HOJA_DETALLE}'."
            )
        hoja = libro[cfg.HOJA_DETALLE]
        encabezados = next(hoja.iter_rows(min_row=1, max_row=1, values_only=True))
        validar_estructura_exacta(encabezados, contexto="archivo procesado")
        if hoja.max_column != len(COLUMNAS_OFICIALES):
            raise ErrorArchivo(
                "El detalle del archivo procesado no tiene exactamente 11 columnas."
            )
    finally:
        libro.close()


def escribir_procesado(
    df: pd.DataFrame,
    destino: Path,
    *,
    observaciones: Iterable[Mapping[str, Any]] = (),
    extras: pd.DataFrame | None = None,
    resumen: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> Path:
    """Escribe el Excel procesado de forma atómica y valida su estructura."""

    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    descriptor, nombre_temporal = tempfile.mkstemp(
        prefix=f".{destino.stem}_",
        suffix=".xlsx",
        dir=str(destino.parent),
    )
    os.close(descriptor)
    temporal = Path(nombre_temporal)

    try:
        libro = Workbook()
        hoja = libro.active
        hoja.title = cfg.HOJA_DETALLE
        escribir_detalle(hoja, df)

        _escribir_observaciones(libro.create_sheet("Observaciones"), observaciones)
        _escribir_resumen(libro.create_sheet("Resumen"), df, resumen, meta)
        _escribir_control(libro.create_sheet("Control"), meta or {})
        if extras is not None and len(extras):
            _escribir_extras(libro.create_sheet("Origen_Adicional"), extras)

        libro.save(temporal)
        libro.close()

        _verificar_libro(temporal)
        os.replace(temporal, destino)

    except PermissionError as exc:
        raise ErrorArchivo(
            f"No se pudo escribir '{destino}'. "
            "Verifique que el archivo no esté abierto en Excel."
        ) from exc
    except OSError as exc:
        if getattr(exc, "winerror", None) in (32, 33):
            raise ErrorArchivo(
                f"No se pudo escribir '{destino}'. "
                "Verifique que el archivo no esté abierto en Excel."
            ) from exc
        raise
    finally:
        try:
            temporal.unlink(missing_ok=True)
        except OSError:
            pass

    return destino


def escribir_reporte_excel(
    df: pd.DataFrame,
    destino: Path,
    resumen: Mapping[str, Any],
    distribuciones: Mapping[str, pd.DataFrame],
    recurrencia: pd.DataFrame | None = None,
    titulo: str = "REPORTE DE TICKETS TI",
) -> Path:
    """Genera un reporte Excel con indicadores, distribuciones y gráficos."""

    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    libro = Workbook()
    hoja = libro.active
    hoja.title = "Resumen"

    hoja["A1"] = titulo
    hoja["A1"].font = Font(name="Segoe UI", size=15, bold=True, color=_AZUL)
    hoja.merge_cells("A1:C1")
    hoja["A2"] = "Generado por el Sistema Integral de Tickets TI - INKACROPS"
    hoja["A2"].font = Font(name="Segoe UI", size=9, italic=True, color="7F7F7F")

    fila = 4
    hoja.cell(row=fila, column=1, value="Indicador").font = Font(
        name="Segoe UI", size=10, bold=True, color="FFFFFF"
    )
    hoja.cell(row=fila, column=1).fill = PatternFill("solid", fgColor=_AZUL)
    hoja.cell(row=fila, column=2, value="Valor").font = Font(
        name="Segoe UI", size=10, bold=True, color="FFFFFF"
    )
    hoja.cell(row=fila, column=2).fill = PatternFill("solid", fgColor=_AZUL)
    fila += 1
    for clave, valor in resumen.items():
        hoja.cell(row=fila, column=1, value=clave).font = Font(name="Segoe UI", size=10)
        hoja.cell(row=fila, column=2, value=valor).font = Font(name="Segoe UI", size=10)
        fila += 1

    hoja.column_dimensions["A"].width = 42
    hoja.column_dimensions["B"].width = 24

    indice_hoja = 2
    for nombre, tabla in distribuciones.items():
        if tabla is None or tabla.empty:
            continue
        titulo_hoja = str(nombre)[:31]
        ws = libro.create_sheet(titulo_hoja)
        filas = [tuple(fila) for fila in tabla.itertuples(index=False, name=None)]
        _escribir_tabla(ws, 1, [str(c) for c in tabla.columns], filas)
        ws.column_dimensions["A"].width = 40
        ws.column_dimensions["B"].width = 14
        if len(tabla.columns) >= 2:
            ws.column_dimensions["C"].width = 14

        if len(tabla.columns) >= 2 and len(tabla) > 0:
            grafico = BarChart()
            grafico.type = "bar"
            grafico.style = 10
            grafico.title = str(nombre)
            datos = Reference(
                ws,
                min_col=2,
                min_row=1,
                max_row=len(tabla) + 1,
            )
            categorias = Reference(
                ws,
                min_col=1,
                min_row=2,
                max_row=len(tabla) + 1,
            )
            grafico.add_data(datos, titles_from_data=True)
            grafico.set_categories(categorias)
            grafico.height = 9
            grafico.width = 19
            ws.add_chart(grafico, f"E{2}")

        indice_hoja += 1

    if recurrencia is not None and not recurrencia.empty:
        ws = libro.create_sheet("Recurrencia")
        filas = [tuple(fila) for fila in recurrencia.itertuples(index=False, name=None)]
        _escribir_tabla(ws, 1, [str(c) for c in recurrencia.columns], filas)
        ws.column_dimensions["A"].width = 70
        ws.column_dimensions["B"].width = 12
        ws.column_dimensions["C"].width = 14

    libro.save(destino)
    libro.close()
    return destino
