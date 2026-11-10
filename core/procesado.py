"""Generación del Excel procesado con el esquema oficial de 11 columnas."""

from __future__ import annotations

import logging
import os
import tempfile
import unicodedata
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


def _normalizar_texto(valor: Any) -> str:
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    texto = unicodedata.normalize("NFKD", str(valor).upper())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.split())


def _categoria_actividad(valor: Any) -> str:
    texto = _normalizar_texto(valor)
    if texto in {"INCIDENCIA", "INCIDENTE", "INCIDENTES"}:
        return "Incidencia"
    if texto in {"PROYECTO", "PROYECTOS"}:
        return "Proyecto"
    if texto in {"SOLICITUD", "SOLICITUDES"}:
        return "Solicitud"
    return texto.title() if texto else ""


def _tabla_resumen(df: pd.DataFrame) -> pd.DataFrame:
    horas = pd.to_numeric(df[C_HORAS], errors="coerce").fillna(0.0)
    total_horas = float(horas.sum())
    categorias = ["Incidencia", "Proyecto", "Solicitud"]
    filas = []
    for categoria in categorias:
        mask = df[C_ACTIVIDAD].map(_categoria_actividad) == categoria
        if not mask.any():
            continue
        total = float(horas[mask].sum())
        porcentaje = 0.0 if total_horas == 0 else total / total_horas
        filas.append(
            {
                "TIPO DE ATENCION": categoria,
                "TIEMPO(H)": round(total, 2),
                "%": round(porcentaje, 6),
            }
        )
    filas.append(
        {
            "TIPO DE ATENCION": "TOTAL GENERAL",
            "TIEMPO(H)": round(total_horas, 2),
            "%": 1.0 if total_horas else 0.0,
        }
    )
    return pd.DataFrame(filas, columns=["TIPO DE ATENCION", "TIEMPO(H)", "%"])


def _tabla_proyectos(df: pd.DataFrame) -> pd.DataFrame:
    validos = df[[C_PROYECTO, C_HORAS]].copy()
    validos = validos[validos[C_PROYECTO].notna()]
    validos[C_PROYECTO] = validos[C_PROYECTO].astype(str).str.strip()
    clave_proyecto = validos[C_PROYECTO].str.casefold()
    validos = validos[
        ~clave_proyecto.isin({"", "nan", "none", "null", "n/a"})
    ]
    validos[C_HORAS] = pd.to_numeric(validos[C_HORAS], errors="coerce").fillna(0.0)
    total = float(validos[C_HORAS].sum())
    if validos.empty:
        filas = []
    else:
        agrupado = validos.groupby(C_PROYECTO, sort=False)[C_HORAS].sum().reset_index()
        agrupado.columns = ["PROYECTOS", "TIEMPO(H)"]
        agrupado["%"] = agrupado["TIEMPO(H)"].apply(
            lambda valor: 0.0 if total == 0 else valor / total
        )
        agrupado["TIEMPO(H)"] = agrupado["TIEMPO(H)"].round(2)
        agrupado["%"] = agrupado["%"].round(6)
        filas = agrupado.to_dict(orient="records")
    filas.append(
        {
            "PROYECTOS": "TOTAL GENERAL",
            "TIEMPO(H)": round(total, 2),
            "%": 1.0 if total else 0.0,
        }
    )
    return pd.DataFrame(filas, columns=["PROYECTOS", "TIEMPO(H)", "%"])


def _tabla_actividad(df: pd.DataFrame) -> pd.DataFrame:
    """Resume la hoja ACTIVIDAD usando solo la columna oficial ``Actividad``.

    Se incluyen únicamente las categorías Incidencia y Solicitud. Los registros
    clasificados como Proyecto quedan fuera de la tabla, de los totales y de los
    porcentajes.
    """

    categorias = df[C_ACTIVIDAD].map(_categoria_actividad)
    incluidas = categorias.isin({"Incidencia", "Solicitud"})
    filtrado = pd.DataFrame(
        {
            "TIPO DE ACTIVIDAD": categorias[incluidas],
        }
    )
    if filtrado.empty:
        filas = []
        total = 0
    else:
        agrupado = filtrado.groupby("TIPO DE ACTIVIDAD", sort=False).size().reset_index(
            name="TICKETS"
        )
        total = int(agrupado["TICKETS"].sum())
        agrupado["%"] = agrupado["TICKETS"].apply(
            lambda cantidad: 0.0 if total == 0 else cantidad / total
        )
        agrupado["TICKETS"] = agrupado["TICKETS"].astype(int)
        agrupado["%"] = agrupado["%"].round(6)
        agrupado = agrupado.sort_values(
            ["TICKETS", "TIPO DE ACTIVIDAD"], ascending=[False, True]
        )
        filas = agrupado.to_dict(orient="records")
    filas.append(
        {
            "TIPO DE ACTIVIDAD": "TOTAL GENERAL",
            "TICKETS": total,
            "%": 1.0 if total else 0.0,
        }
    )
    return pd.DataFrame(filas, columns=["TIPO DE ACTIVIDAD", "TICKETS", "%"])


def _tabla_area(df: pd.DataFrame) -> pd.DataFrame:
    areas = df[[C_AREA]].copy()
    valores_area = areas[C_AREA].fillna("").astype(str).str.strip()
    sin_area = valores_area.str.casefold().isin({"", "nan", "none", "null", "n/a"})
    areas[C_AREA] = valores_area.mask(sin_area, "Sin área")
    if areas.empty:
        filas = []
        total = 0
    else:
        agrupado = areas.groupby(C_AREA, sort=False).size().reset_index(name="TICKETS")
        total = int(agrupado["TICKETS"].sum())
        agrupado["%"] = agrupado["TICKETS"].apply(
            lambda cantidad: 0.0 if total == 0 else cantidad / total
        )
        agrupado = agrupado.rename(columns={C_AREA: "ÁREAS"})
        agrupado["TICKETS"] = agrupado["TICKETS"].astype(int)
        agrupado["%"] = agrupado["%"].round(6)
        agrupado = agrupado.sort_values(["TICKETS", "ÁREAS"], ascending=[False, True])
        filas = agrupado.to_dict(orient="records")
    filas.append({"ÁREAS": "TOTAL GENERAL", "TICKETS": total, "%": 1.0 if total else 0.0})
    return pd.DataFrame(filas, columns=["ÁREAS", "TICKETS", "%"])


def _tabla_detalle(df: pd.DataFrame) -> pd.DataFrame:
    detalle = df[[C_ACTIVIDAD, C_DETALLE, C_FECHA, C_SOLICITANTE, C_AREA, C_HORAS]].copy()
    detalle = detalle.rename(
        columns={
            C_ACTIVIDAD: "TIPO DE ATENCION",
            C_DETALLE: "DETALLE",
            C_FECHA: "FECHA",
            C_SOLICITANTE: "USUARIO",
            C_AREA: "AREA_USUARIO",
            C_HORAS: "HORAS",
        }
    )
    detalle = detalle.loc[
        :,
        ["TIPO DE ATENCION", "DETALLE", "FECHA", "USUARIO", "AREA_USUARIO", "HORAS"],
    ].copy()
    for columna in detalle.columns:
        detalle[columna] = detalle[columna].map(_valor_reporte)
    return detalle


def _valor_reporte(valor: Any) -> Any:
    if valor is None:
        return None
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(valor, pd.Timestamp):
        return valor.to_pydatetime()
    return valor


def _agregar_grafico_pastel(hoja: Worksheet, tabla: pd.DataFrame) -> None:
    if tabla is None:
        return
    cantidad_categorias = max(len(tabla) - 1, 0)
    if cantidad_categorias == 0:
        return
    grafico = PieChart()
    grafico.style = 10
    grafico.title = None
    grafico.height = 7
    grafico.width = 11

    datos = Reference(hoja, min_col=2, min_row=1, max_row=cantidad_categorias + 1)
    categorias = Reference(hoja, min_col=1, min_row=2, max_row=cantidad_categorias + 1)
    grafico.add_data(datos, titles_from_data=True)
    grafico.set_categories(categorias)
    hoja.add_chart(grafico, "E2")


def escribir_reporte_excel(
    df: pd.DataFrame,
    destino: Path,
    resumen: Mapping[str, Any],
    distribuciones: Mapping[str, pd.DataFrame],
    recurrencia: pd.DataFrame | None = None,
    titulo: str = "REPORTE DE TICKETS TI",
) -> Path:
    """Genera un libro Excel con exactamente cinco hojas según la especificación."""

    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    libro = Workbook()
    hojas = [
        ("RESUMEN", _tabla_resumen(df)),
        ("PROYECTOS", _tabla_proyectos(df)),
        ("ACTIVIDAD", _tabla_actividad(df)),
        ("AREA", _tabla_area(df)),
        ("DETALLE", _tabla_detalle(df)),
    ]

    for indice, (nombre_hoja, tabla) in enumerate(hojas):
        ws = libro.active if indice == 0 else libro.create_sheet()
        ws.title = nombre_hoja
        filas = [tuple(fila) for fila in tabla.itertuples(index=False, name=None)]
        _escribir_tabla(ws, 1, [str(c) for c in tabla.columns], filas)

        if nombre_hoja == "DETALLE":
            ws.column_dimensions["A"].width = 24
            ws.column_dimensions["B"].width = 38
            ws.column_dimensions["C"].width = 14
            ws.column_dimensions["D"].width = 26
            ws.column_dimensions["E"].width = 22
            ws.column_dimensions["F"].width = 12
            for fila in range(2, len(tabla) + 2):
                celda_fecha = ws.cell(row=fila, column=3)
                valor = celda_fecha.value
                if isinstance(valor, pd.Timestamp):
                    celda_fecha.value = valor.to_pydatetime()
                    valor = celda_fecha.value
                elif isinstance(valor, str) and valor.strip():
                    convertido = pd.to_datetime(valor, dayfirst=True, errors="coerce")
                    if not pd.isna(convertido):
                        celda_fecha.value = convertido.to_pydatetime()
                        valor = celda_fecha.value
                if isinstance(valor, (date, datetime)):
                    celda_fecha.number_format = "dd/mm/yyyy"
                ws.cell(row=fila, column=6).number_format = "0.00"
            continue

        ws.column_dimensions["A"].width = 28
        ws.column_dimensions["B"].width = 14
        ws.column_dimensions["C"].width = 12
        es_actividad_area = nombre_hoja in {"ACTIVIDAD", "AREA"}
        ultima_fila_categoria = len(tabla)
        if len(tabla) > 0:
            for fila in range(2, len(tabla) + 2):
                ws.cell(row=fila, column=2).number_format = (
                    "0" if es_actividad_area else "0.00"
                )
                ws.cell(row=fila, column=3).number_format = "0.00%"
            total_fila = len(tabla) + 1
            for celda in ws[total_fila]:
                celda.font = Font(name="Segoe UI", size=9, bold=True)
                celda.fill = PatternFill("solid", fgColor=_AZUL_CLARO)
                celda.border = _borde_fino()
        if ultima_fila_categoria > 1:
            _agregar_grafico_pastel(ws, tabla)

    libro.save(destino)
    libro.close()
    return destino
