"""Indicadores, análisis de recurrencia y reportes de TI."""

from __future__ import annotations

import logging
import unicodedata
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from config import configuracion as cfg
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
)
from core.procesado import escribir_reporte_excel

LOGGER = logging.getLogger("automatizador")


def clave_grupo(valor: Any) -> str:
    """Clave transparente para agrupar (mayúsculas, sin tildes, espacios)."""

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


def clave_detalle(valor: Any) -> str:
    """Normaliza el detalle para detectar solicitudes idénticas.

    No agrupa por palabras genéricas: solo considera el texto completo
    normalizado. Dos problemas distintos con palabras comunes no se fusionan.
    """

    clave = clave_grupo(valor)
    # Elimina signos de puntuación repetidos, pero conserva el contenido.
    for signo in ".,;:!?¿¡\"'()[]{}":
        clave = clave.replace(signo, " ")
    return " ".join(clave.split())


def _horas(df: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(df[C_HORAS], errors="coerce").fillna(0.0)


def _fechas(df: pd.DataFrame) -> list[date]:
    return [
        valor
        for valor in df[C_FECHA].tolist()
        if isinstance(valor, (date, datetime))
    ]


def _display_por_clave(df: pd.DataFrame, columna: str) -> dict[str, str]:
    displays: dict[str, str] = {}
    for valor in df[columna].tolist():
        clave = clave_grupo(valor)
        if not clave:
            continue
        if clave not in displays:
            displays[clave] = " ".join(str(valor).split())
    return displays


def tabla_agrupada(df: pd.DataFrame, columna: str) -> pd.DataFrame:
    """Agrupa por una columna y devuelve tickets, horas y porcentajes."""

    if df.empty:
        return pd.DataFrame(
            columns=["Grupo", "Tickets", "Horas", "% Tickets", "% Horas"]
        )

    trabajo = pd.DataFrame(
        {
            "clave": df[columna].map(clave_grupo),
            "horas": _horas(df),
        }
    )
    trabajo = trabajo[trabajo["clave"] != ""]
    if trabajo.empty:
        return pd.DataFrame(
            columns=["Grupo", "Tickets", "Horas", "% Tickets", "% Horas"]
        )

    agrupado = trabajo.groupby("clave", sort=False).agg(
        Tickets=("clave", "size"),
        Horas=("horas", "sum"),
    )
    displays = _display_por_clave(df, columna)
    agrupado.insert(0, "Grupo", [displays.get(clave, clave) for clave in agrupado.index])

    total_tickets = int(agrupado["Tickets"].sum()) or 1
    total_horas = float(agrupado["Horas"].sum()) or 1.0
    agrupado["% Tickets"] = (agrupado["Tickets"] / total_tickets * 100).round(1)
    agrupado["Horas"] = agrupado["Horas"].round(2)
    agrupado["% Horas"] = (agrupado["Horas"] / total_horas * 100).round(1)
    agrupado = agrupado.sort_values(
        ["Tickets", "Horas"], ascending=[False, False]
    ).reset_index(drop=True)
    return agrupado


def evolucion_semanal(df: pd.DataFrame) -> pd.DataFrame:
    """Tickets y horas por semana ISO."""

    if df.empty:
        return pd.DataFrame(columns=["Semana", "Desde", "Hasta", "Tickets", "Horas"])

    trabajo = pd.DataFrame({"fecha": df[C_FECHA], "horas": _horas(df)})
    trabajo = trabajo[
        trabajo["fecha"].map(lambda valor: isinstance(valor, (date, datetime)))
    ].copy()
    if trabajo.empty:
        return pd.DataFrame(columns=["Semana", "Desde", "Hasta", "Tickets", "Horas"])

    trabajo["fecha"] = pd.to_datetime(trabajo["fecha"])
    iso = trabajo["fecha"].dt.isocalendar()
    trabajo["Semana"] = iso["year"].astype(str) + "-S" + iso["week"].astype(str).str.zfill(2)

    agrupado = trabajo.groupby("Semana", sort=True).agg(
        Desde=("fecha", "min"),
        Hasta=("fecha", "max"),
        Tickets=("fecha", "size"),
        Horas=("horas", "sum"),
    ).reset_index()
    agrupado["Desde"] = agrupado["Desde"].dt.strftime("%Y-%m-%d")
    agrupado["Hasta"] = agrupado["Hasta"].dt.strftime("%Y-%m-%d")
    agrupado["Horas"] = agrupado["Horas"].round(2)
    return agrupado


def recurrencia_detalle(
    df: pd.DataFrame,
    minimo: int | None = None,
) -> pd.DataFrame:
    """Solicitudes con detalle idéntico (normalizado) que se repiten."""

    minimo = cfg.RECURRENCIA_MINIMA if minimo is None else minimo
    columnas = ["Detalle recurrente", "Tickets", "Horas", "Responsables", "Áreas"]
    if df.empty:
        return pd.DataFrame(columns=columnas)

    trabajo = pd.DataFrame(
        {
            "clave": df[C_DETALLE].map(clave_detalle),
            "detalle": df[C_DETALLE].astype(str).str.strip(),
            "horas": _horas(df),
            "responsable": df[C_RESPONSABLE].map(clave_grupo),
            "area": df[C_AREA].map(clave_grupo),
        }
    )
    trabajo = trabajo[trabajo["clave"] != ""]
    if trabajo.empty:
        return pd.DataFrame(columns=columnas)

    agrupado = trabajo.groupby("clave", sort=False).agg(
        Tickets=("clave", "size"),
        Horas=("horas", "sum"),
        Responsables=("responsable", lambda s: ", ".join(sorted(set(s) - {""}))),
        Áreas=("area", lambda s: ", ".join(sorted(set(s) - {""}))),
        Ejemplo=("detalle", "first"),
    )
    agrupado = agrupado[agrupado["Tickets"] >= minimo]
    if agrupado.empty:
        return pd.DataFrame(columns=columnas)

    agrupado = agrupado.sort_values(
        ["Tickets", "Horas"], ascending=[False, False]
    ).reset_index(drop=True)
    agrupado["Horas"] = agrupado["Horas"].round(2)
    agrupado = agrupado.rename(columns={"Ejemplo": "Detalle recurrente"})
    return agrupado.loc[:, columnas]


def recurrencia_categoria(df: pd.DataFrame, minimo: int = 3) -> pd.DataFrame:
    """Combinaciones Requerimiento/Subtipo/Actividad con mayor volumen."""

    columnas = ["Requerimiento", "Subtipo", "Actividad", "Tickets", "Horas"]
    if df.empty:
        return pd.DataFrame(columns=columnas)

    trabajo = pd.DataFrame(
        {
            "requerimiento": df[C_REQUERIMIENTO].map(clave_grupo),
            "subtipo": df[C_SUBTIPO].map(clave_grupo),
            "actividad": df[C_ACTIVIDAD].map(clave_grupo),
            "horas": _horas(df),
        }
    )
    trabajo = trabajo[
        (trabajo["requerimiento"] != "")
        | (trabajo["subtipo"] != "")
        | (trabajo["actividad"] != "")
    ]
    if trabajo.empty:
        return pd.DataFrame(columns=columnas)

    agrupado = trabajo.groupby(
        ["requerimiento", "subtipo", "actividad"], sort=False
    ).agg(Tickets=("requerimiento", "size"), Horas=("horas", "sum")).reset_index()
    agrupado = agrupado[agrupado["Tickets"] >= minimo]
    if agrupado.empty:
        return pd.DataFrame(columns=columnas)

    agrupado["Horas"] = agrupado["Horas"].round(2)
    agrupado = agrupado.sort_values(
        ["Tickets", "Horas"], ascending=[False, False]
    ).reset_index(drop=True)
    agrupado = agrupado.rename(
        columns={
            "requerimiento": "Requerimiento",
            "subtipo": "Subtipo",
            "actividad": "Actividad",
        }
    )
    return agrupado.loc[:, columnas]


def calcular_indicadores(
    df: pd.DataFrame,
    dias_habiles: int = cfg.DIAS_HABILES_MES,
    horas_jornada: float = cfg.HORAS_JORNADA,
) -> dict[str, Any]:
    """Indicadores generales de carga y esfuerzo."""

    horas = _horas(df)
    total_horas = float(horas.sum())
    fechas = _fechas(df)
    total = len(df)

    responsables = df[C_RESPONSABLE].map(clave_grupo)
    responsables = responsables[responsables != ""].nunique()
    solicitantes = df[C_SOLICITANTE].map(clave_grupo)
    solicitantes = solicitantes[solicitantes != ""].nunique()
    areas = df[C_AREA].map(clave_grupo)
    areas = areas[areas != ""].nunique()

    capacidad = dias_habiles * horas_jornada * max(responsables, 1)

    return {
        "Total de tickets": total,
        "Horas registradas": round(total_horas, 2),
        "Promedio de horas por ticket": round(total_horas / total, 2) if total else 0,
        "Mediana de horas por ticket": round(float(horas.median()), 2)
        if total
        else 0,
        "Días hábiles considerados": dias_habiles,
        "Promedio de tickets por día": round(total / dias_habiles, 1)
        if dias_habiles
        else 0,
        "Promedio de horas por día": round(total_horas / dias_habiles, 1)
        if dias_habiles
        else 0,
        "Responsables": responsables,
        "Solicitantes únicos": solicitantes,
        "Áreas únicas": areas,
        "Capacidad nominal estimada (h)": round(capacidad, 1),
        "Imputación de horas (%)": round(total_horas / capacidad * 100, 1)
        if capacidad
        else 0,
        "Periodo desde": min(fechas).strftime("%Y-%m-%d") if fechas else "",
        "Periodo hasta": max(fechas).strftime("%Y-%m-%d") if fechas else "",
        "Nota": (
            "Las horas representan esfuerzo declarado, no tiempo de resolución. "
            "No se calculan SLA ni tiempos de respuesta porque el origen no "
            "contiene esos campos."
        ),
    }


def distribuciones(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        "Por atencion": tabla_agrupada(df, C_ATENCION),
        "Por requerimiento": tabla_agrupada(df, C_REQUERIMIENTO),
        "Por responsable": tabla_agrupada(df, C_RESPONSABLE),
        "Por area": tabla_agrupada(df, C_AREA),
        "Por subtipo": tabla_agrupada(df, C_SUBTIPO),
        "Por proyecto": tabla_agrupada(df, C_PROYECTO),
        "Por actividad": tabla_agrupada(df, C_ACTIVIDAD),
        "Por solicitante": tabla_agrupada(df, C_SOLICITANTE),
    }


def esfuerzo_por_responsable(df: pd.DataFrame) -> pd.DataFrame:
    tabla = tabla_agrupada(df, C_RESPONSABLE)
    if tabla.empty:
        return tabla
    tabla = tabla.rename(columns={"% Horas": "% Esfuerzo"})
    return tabla.loc[:, ["Grupo", "Tickets", "Horas", "% Esfuerzo"]]


def _markdown_tabla(tabla: pd.DataFrame) -> str:
    if tabla is None or tabla.empty:
        return "_Sin datos._\n"
    columnas = [str(c) for c in tabla.columns]
    lineas = [
        "| " + " | ".join(columnas) + " |",
        "| " + " | ".join("---" for _ in columnas) + " |",
    ]
    for _, fila in tabla.iterrows():
        valores = []
        for columna in tabla.columns:
            valor = fila[columna]
            if isinstance(valor, float):
                valores.append(f"{valor:.2f}".rstrip("0").rstrip("."))
            else:
                valores.append(str(valor))
        lineas.append("| " + " | ".join(valores) + " |")
    return "\n".join(lineas) + "\n"


def generar_markdown(
    df: pd.DataFrame,
    indicadores: dict[str, Any],
    tablas: dict[str, pd.DataFrame],
    recurrencia: pd.DataFrame,
    titulo: str,
) -> str:
    lineas = [
        f"# {titulo}",
        "",
        f"Generado: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        "## Indicadores generales",
        "",
    ]
    for clave, valor in indicadores.items():
        if clave == "Nota":
            continue
        lineas.append(f"- **{clave}:** {valor}")

    lineas.extend(["", f"> {indicadores.get('Nota', '')}", ""])

    for nombre, tabla in tablas.items():
        lineas.extend([f"## {nombre.replace('_', ' ').title()}", ""])
        lineas.append(_markdown_tabla(tabla))

    lineas.extend(["## Recurrencia (detalle idéntico normalizado)", ""])
    lineas.append(_markdown_tabla(recurrencia))

    lineas.extend(
        [
            "## Limitaciones",
            "",
            "- Las horas son esfuerzo declarado; no equivalen a tiempo de resolución.",
            "- No se calculan SLA ni tiempos de respuesta: el origen no contiene "
            "fechas de atención y cierre.",
            "- La recurrencia agrupa únicamente detalles idénticos tras normalizar "
            "mayúsculas, tildes y espacios; no fusiona problemas distintos.",
            "",
        ]
    )
    return "\n".join(lineas)


def generar_reporte(
    df: pd.DataFrame,
    ruta_salida: Path | str | None = None,
    titulo: str = "Reporte de tickets TI - INKACROPS",
    dias_habiles: int = cfg.DIAS_HABILES_MES,
    recurrencia_minima: int = cfg.RECURRENCIA_MINIMA,
) -> dict[str, Any]:
    """Genera reporte Excel y Markdown reproducibles a partir de los datos."""

    if df is None or df.empty:
        raise ValueError("No hay registros para generar el reporte.")

    faltantes = [columna for columna in COLUMNAS_OFICIALES if columna not in df.columns]
    if faltantes:
        raise ValueError(
            "Faltan columnas oficiales para el reporte: " + ", ".join(faltantes)
        )

    if ruta_salida is None:
        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        ruta_salida = cfg.REPORTES_DIR / f"reporte_tickets_{marca}.xlsx"
    ruta_salida = Path(ruta_salida)
    if not ruta_salida.is_absolute():
        ruta_salida = cfg.BASE_DIR / ruta_salida
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)

    indicadores = calcular_indicadores(df, dias_habiles=dias_habiles)
    tablas = distribuciones(df)
    recurrencia = recurrencia_detalle(df, minimo=recurrencia_minima)
    categorias = recurrencia_categoria(df, minimo=max(recurrencia_minima, 2))

    escribir_reporte_excel(
        df,
        ruta_salida,
        indicadores,
        tablas,
        recurrencia=recurrencia,
        titulo=titulo.upper(),
    )

    ruta_md = ruta_salida.with_suffix(".md")
    contenido = generar_markdown(df, indicadores, tablas, recurrencia, titulo)
    ruta_md.write_text(contenido + "\n", encoding="utf-8")

    LOGGER.info("Reporte generado: %s", ruta_salida)
    return {
        "xlsx": ruta_salida,
        "markdown": ruta_md,
        "indicadores": indicadores,
        "tablas": tablas,
        "recurrencia": recurrencia,
        "recurrencia_categoria": categorias,
    }
