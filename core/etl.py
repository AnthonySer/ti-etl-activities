"""Motor de extracción y normalización (ETL) de tickets.

Lee un Excel de origen sin modificarlo, detecta la hoja y la fila de encabezado,
traduce los alias al esquema oficial de 11 columnas y normaliza fechas, horas y
textos. Los problemas detectados se registran como observaciones por fila; nunca
se descarta información en silencio.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl.utils.datetime import from_excel

from config import configuracion as cfg
from core.archivos import (
    ErrorArchivo,
    ErrorEstructura,
    clave_columna,
    leer_excel,
    listar_hojas,
)
from core.esquema import (
    ALIAS_OFICIALES,
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
    canonizar_encabezado,
    fila_a_dict,
    huella_fila,
    mapear_oficial,
    serializar_valor,
)
from core.normalizador import limpiar_area, limpiar_usuario

LOGGER = logging.getLogger("automatizador")

_TOKENS_NULOS = {
    "",
    "NAN",
    "NONE",
    "NULL",
    "N/A",
    "N / A",
    "NA",
    "NAT",
    "<NA>",
    "SIN DATO",
    "SIN DATOS",
}

_HOJAS_AUXILIARES = {
    "RESUMEN",
    "PROYECTOS",
    "ACTIVIDAD",
    "AREA",
    "DETALLE",
    "CONSULTAR",
    "OBSERVACIONES",
    "CONTROL",
    "TICKETS",
    "DATOS",
}

_OBSERVACION = "observacion"
_ERROR = "error"


# ---------------------------------------------------------------------------
# Estructuras de resultado
# ---------------------------------------------------------------------------
@dataclass
class Observacion:
    fila: int
    campo: str
    motivo: str
    valor: str
    accion: str
    severidad: str = _OBSERVACION

    def como_dict(self) -> dict[str, Any]:
        return {
            "fila": self.fila,
            "campo": self.campo,
            "motivo": self.motivo,
            "valor": self.valor,
            "accion": self.accion,
            "severidad": self.severidad,
        }


@dataclass
class ResultadoExtraccion:
    df: pd.DataFrame
    hoja: str
    indice_encabezado: int
    mapeo: dict[str, str]
    columnas_origen: list[str]
    extras: pd.DataFrame | None = None
    observaciones: list[Observacion] = field(default_factory=list)
    total_filas_origen: int = 0
    filas_vacias_omitidas: int = 0

    @property
    def periodo(self) -> str:
        if self.df.empty:
            return ""
        fechas = [
            valor
            for valor in self.df[C_FECHA].tolist()
            if isinstance(valor, (date, datetime))
        ]
        if not fechas:
            return ""
        minimo = min(fechas).strftime("%Y-%m-%d")
        maximo = max(fechas).strftime("%Y-%m-%d")
        return minimo if minimo == maximo else f"{minimo}/{maximo}"


# ---------------------------------------------------------------------------
# Normalización de valores
# ---------------------------------------------------------------------------
def _texto(valor: Any) -> str:
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    texto = unicodedata.normalize("NFC", str(valor))
    texto = (
        texto.replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\ufeff", "")
    )
    return " ".join(texto.split()).strip()


def texto_limpio(valor: Any) -> str | None:
    texto = _texto(valor)
    if not texto or texto.upper() in _TOKENS_NULOS:
        return None
    return texto


def _fecha_desde_serial(valor: float) -> date | None:
    try:
        resultado = from_excel(valor)
    except (ValueError, OverflowError, TypeError):
        return None
    if isinstance(resultado, datetime):
        return resultado.date()
    if isinstance(resultado, date):
        return resultado
    return None


def parsear_fecha(
    valor: Any,
    formato_preferido: str | None = None,
) -> tuple[date | str | None, str | None]:
    """Convierte un valor a fecha.

    Devuelve ``(valor, problema)``. Si la fecha es válida, ``valor`` es un
    ``datetime.date``. Si no puede interpretarse, se devuelve el texto original
    y un mensaje para no perder el dato.
    """

    preferido = (formato_preferido or cfg.FORMATO_FECHA_PREFERIDO).upper()

    if valor is None:
        return None, None
    try:
        if pd.isna(valor):
            return None, None
    except (TypeError, ValueError):
        pass

    if isinstance(valor, datetime):
        return valor.date(), None
    if isinstance(valor, date):
        return valor, None
    if isinstance(valor, pd.Timestamp):
        return valor.date(), None

    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        fecha = _fecha_desde_serial(float(valor))
        if fecha and 1990 <= fecha.year <= 2100:
            return fecha, "Número de serie de Excel convertido a fecha."
        return str(valor), "Fecha numérica fuera de rango."

    texto = _texto(valor)
    if not texto or texto.upper() in _TOKENS_NULOS:
        return None, None

    # Recorta una hora explícita ("2026-09-01 00:00:00" o ISO con T).
    limpio = re.sub(r"[T ]\d{1,2}:\d{2}(:\d{2})?.*$", "", texto).strip()

    # Formato con año al inicio: YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD
    coincidencia = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", limpio)
    if coincidencia:
        anio, mes, dia = (int(parte) for parte in coincidencia.groups())
        try:
            return date(anio, mes, dia), None
        except ValueError:
            return texto, f"Fecha inválida: {texto}."

    # Formato con día/mes al inicio: DD/MM/YYYY, MM/DD/YYYY, DD-MM-YY...
    coincidencia = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", limpio)
    if coincidencia:
        primero, segundo, anio_texto = coincidencia.groups()
        primero_i, segundo_i = int(primero), int(segundo)
        anio = int(anio_texto)
        if anio < 100:
            anio += 2000

        problema: str | None = None
        if primero_i > 12 and segundo_i <= 12:
            dia, mes = primero_i, segundo_i
        elif segundo_i > 12 and primero_i <= 12:
            dia, mes = segundo_i, primero_i
            problema = (
                f"Fecha '{texto}' interpretada como MM/DD/YYYY porque el segundo "
                "componente es mayor que 12."
            )
        elif primero_i <= 12 and segundo_i <= 12:
            if preferido.startswith("MM"):
                mes, dia = primero_i, segundo_i
                problema = (
                    f"Fecha ambigua '{texto}' interpretada como MM/DD/YYYY "
                    "según la configuración explícita."
                )
            else:
                dia, mes = primero_i, segundo_i
                problema = (
                    f"Fecha ambigua '{texto}' interpretada como DD/MM/YYYY "
                    "según la configuración explícita."
                )
        else:
            return texto, f"Fecha inválida: {texto}."

        try:
            return date(anio, mes, dia), problema
        except ValueError:
            return texto, f"Fecha inválida: {texto}."

    # Último recurso: interpretación de pandas con la configuración explícita.
    try:
        interpretada = pd.to_datetime(
            limpio,
            dayfirst=not preferido.startswith("MM"),
            errors="coerce",
        )
    except (ValueError, TypeError):
        interpretada = pd.NaT

    if isinstance(interpretada, pd.Timestamp) and not pd.isna(interpretada):
        return interpretada.date(), (
            f"Fecha '{texto}' interpretada con formato no estándar."
        )

    return texto, f"Fecha inválida: {texto}."


def parsear_horas(valor: Any) -> tuple[float | str | None, str | None]:
    """Convierte horas a número sin reemplazar un valor inválido por cero."""

    if valor is None:
        return None, None
    try:
        if pd.isna(valor):
            return None, None
    except (TypeError, ValueError):
        pass

    if isinstance(valor, bool):
        return _texto(valor), "Horas no numéricas."

    if isinstance(valor, (int, float)):
        numero = float(valor)
        if numero < 0:
            return numero, "Horas negativas."
        return numero, None

    texto = _texto(valor)
    if not texto or texto.upper() in _TOKENS_NULOS:
        return None, None

    limpio = texto.replace(",", ".")
    limpio = re.sub(r"(?i)\b(horas?|hrs?|h)\b", "", limpio).strip()
    limpio = limpio.replace(" ", "")

    try:
        numero = float(limpio)
    except ValueError:
        return texto, f"Horas no numéricas: '{texto}'."

    if numero < 0:
        return numero, f"Horas negativas: {numero}."
    return numero, None


def _normalizar_campo(columna: str, valor: Any) -> Any:
    if columna == C_FECHA:
        return parsear_fecha(valor)[0]
    if columna == C_HORAS:
        return parsear_horas(valor)[0]
    if columna == C_SOLICITANTE:
        return limpiar_usuario(valor)
    if columna == C_AREA:
        return limpiar_area(valor)
    return texto_limpio(valor)


# ---------------------------------------------------------------------------
# Detección de hoja y encabezado
# ---------------------------------------------------------------------------
def _puntaje_hoja(nombre: str, ruta: Path) -> int:
    """Puntúa una hoja para preferir la hoja principal de datos."""

    stem = ruta.stem.lower()
    nombre_lower = nombre.lower()
    puntaje = 0

    if nombre_lower == stem:
        puntaje += 10_000
    elif stem in nombre_lower or nombre_lower in stem:
        puntaje += 5_000
    elif nombre_lower.startswith("consulta"):
        puntaje += 1_000

    if clave_columna(nombre) in _HOJAS_AUXILIARES:
        puntaje -= 500
    return puntaje


def _analizar_encabezado_hoja(
    ruta: Path,
    hoja: str,
) -> tuple[int | None, dict[str, list[str]]]:
    """Busca la fila de encabezado y devuelve el mapeo de columnas oficiales."""

    vista = leer_excel(
        ruta,
        sheet_name=hoja,
        header=None,
        nrows=cfg.FILA_ENCABEZADO_MAX,
    )

    mejor_indice: int | None = None
    mejor_grupos: dict[str, list[str]] = {}

    for indice in range(len(vista)):
        fila = vista.iloc[indice].tolist()
        grupos: dict[str, list[str]] = {}
        for posicion, valor in enumerate(fila, start=1):
            if valor is None:
                continue
            try:
                if pd.isna(valor):
                    continue
            except (TypeError, ValueError):
                pass
            canonica = canonizar_encabezado(valor)
            if canonica is not None:
                etiqueta = str(valor).strip() or f"columna_{posicion}"
                grupos.setdefault(canonica, []).append(etiqueta)

        if len(grupos) > len(mejor_grupos):
            mejor_indice = indice
            mejor_grupos = grupos

        if len(grupos) == len(COLUMNAS_OFICIALES):
            return indice, grupos

    return mejor_indice, mejor_grupos


def seleccionar_hoja_y_encabezado(
    ruta: Path,
    hoja: str | None = None,
) -> tuple[str, int, dict[str, list[str]]]:
    """Selecciona la hoja principal y la fila de encabezado."""

    hojas = listar_hojas(ruta)
    if not hojas:
        raise ErrorEstructura(f"El archivo '{ruta.name}' no contiene hojas.")

    if hoja:
        for nombre in hojas:
            if nombre == hoja:
                hoja_elegida = nombre
                break
        else:
            for nombre in hojas:
                if nombre.lower() == hoja.lower():
                    hoja_elegida = nombre
                    break
            else:
                raise ErrorEstructura(
                    f"La hoja '{hoja}' no existe en '{ruta.name}'. "
                    f"Hojas disponibles: {', '.join(hojas)}"
                )
        indice, grupos = _analizar_encabezado_hoja(ruta, hoja_elegida)
        if indice is None or len(grupos) < len(COLUMNAS_OFICIALES):
            faltantes = [c for c in COLUMNAS_OFICIALES if c not in grupos]
            raise ErrorEstructura(
                f"La hoja '{hoja_elegida}' no contiene el esquema oficial. "
                f"Faltan: {', '.join(faltantes) or 'ninguna'}."
            )
        return hoja_elegida, indice, grupos

    candidatas: list[tuple[int, str, int, dict[str, list[str]]]] = []
    diagnosticos: list[str] = []
    for nombre in hojas:
        indice, grupos = _analizar_encabezado_hoja(ruta, nombre)
        if indice is None:
            continue
        faltantes = [c for c in COLUMNAS_OFICIALES if c not in grupos]
        diagnosticos.append(
            f"{nombre}: {len(grupos)}/11 columnas"
            + (f" (faltan {', '.join(faltantes)})" if faltantes else "")
        )
        if len(grupos) == len(COLUMNAS_OFICIALES):
            candidatas.append((_puntaje_hoja(nombre, ruta), nombre, indice, grupos))

    if not candidatas:
        raise ErrorEstructura(
            f"El archivo '{ruta.name}' no contiene una hoja con las 11 columnas "
            "oficiales. Detalle por hoja: " + "; ".join(diagnosticos)
        )

    candidatas.sort(key=lambda item: (item[0], -item[2]), reverse=True)
    _, nombre, indice, grupos = candidatas[0]
    return nombre, indice, grupos


# ---------------------------------------------------------------------------
# Extracción
# ---------------------------------------------------------------------------
def _validar_entrada(ruta: Path) -> None:
    if not ruta.exists():
        raise ErrorArchivo(f"No existe el archivo de entrada: {ruta}")
    sufijo = ruta.suffix.lower()
    if sufijo == ".xls":
        raise ErrorArchivo(
            "El formato .xls no está soportado. Guarde el archivo como .xlsx."
        )
    if sufijo != ".xlsx":
        raise ErrorArchivo(
            f"Formato no soportado para entrada: '{ruta.suffix}'. Use archivos .xlsx."
        )


def previsualizar(
    ruta: Path | str,
    hoja: str | None = None,
) -> dict[str, Any]:
    """Devuelve una vista previa sin procesar ni escribir nada."""

    ruta = Path(ruta)
    _validar_entrada(ruta)
    hoja_elegida, indice, grupos = seleccionar_hoja_y_encabezado(ruta, hoja)
    vista = leer_excel(
        ruta,
        sheet_name=hoja_elegida,
        header=indice,
        nrows=30,
    )
    faltantes = [c for c in COLUMNAS_OFICIALES if c not in grupos]
    return {
        "hoja": hoja_elegida,
        "indice_encabezado": indice,
        "columnas_origen": [str(c) for c in vista.columns],
        "columnas_reconocidas": grupos,
        "faltantes": faltantes,
        "vista": vista,
        "hojas": listar_hojas(ruta),
    }


def extraer(
    ruta: Path | str,
    hoja: str | None = None,
    formato_fecha: str | None = None,
) -> ResultadoExtraccion:
    """Extrae y normaliza el archivo completo sin modificar el original."""

    ruta = Path(ruta)
    _validar_entrada(ruta)

    hoja_elegida, indice, _grupos = seleccionar_hoja_y_encabezado(ruta, hoja)
    crudo = leer_excel(ruta, sheet_name=hoja_elegida, header=indice)

    columnas_origen = [str(c) for c in crudo.columns]
    detalle, extras, mapeo = mapear_oficial(crudo)

    # Normalizar cada campo y conservar la fila Excel de origen.
    registros: list[dict[str, Any]] = []
    observaciones: list[Observacion] = []
    posiciones_validas: list[int] = []
    omitidas = 0
    preferido = formato_fecha or cfg.FORMATO_FECHA_PREFERIDO

    for posicion, fila in detalle.iterrows():
        numero_fila = indice + 2 + int(posicion)
        valores: dict[str, Any] = {}
        problemas: list[dict[str, Any]] = []

        for columna in COLUMNAS_OFICIALES:
            original = fila[columna]
            if columna == C_FECHA:
                valor, problema = parsear_fecha(original, preferido)
            elif columna == C_HORAS:
                valor, problema = parsear_horas(original)
            elif columna == C_SOLICITANTE:
                valor, problema = limpiar_usuario(original), None
            elif columna == C_AREA:
                valor, problema = limpiar_area(original), None
            else:
                valor, problema = texto_limpio(original), None
            valores[columna] = valor

            if problema:
                severidad = _ERROR if "inválida" in problema.lower() else _OBSERVACION
                observacion = Observacion(
                    fila=numero_fila,
                    campo=columna,
                    motivo=problema,
                    valor=serializar_valor(original) or "",
                    accion=_accion_para(columna, problema),
                    severidad=severidad,
                )
                observaciones.append(observacion)
                problemas.append(observacion.como_dict())

        # Una fila totalmente vacía no es un registro.
        if all(valor is None for valor in valores.values()):
            omitidas += 1
            continue

        if valores[C_DETALLE] is None:
            observacion = Observacion(
                fila=numero_fila,
                campo=C_DETALLE,
                motivo="El detalle del ticket está vacío.",
                valor="",
                accion="Complete el detalle del ticket.",
            )
            observaciones.append(observacion)
            problemas.append(observacion.como_dict())

        if valores[C_SOLICITANTE] is None:
            observacion = Observacion(
                fila=numero_fila,
                campo=C_SOLICITANTE,
                motivo="No se identificó un solicitante válido.",
                valor="",
                accion="Indique el solicitante o corrija el valor de origen.",
                severidad=_ERROR,
            )
            observaciones.append(observacion)
            problemas.append(observacion.como_dict())

        if valores[C_FECHA] is None:
            observacion = Observacion(
                fila=numero_fila,
                campo=C_FECHA,
                motivo="La fecha está vacía.",
                valor="",
                accion="Complete la fecha del ticket.",
            )
            observaciones.append(observacion)
            problemas.append(observacion.como_dict())

        if valores[C_HORAS] is None:
            observacion = Observacion(
                fila=numero_fila,
                campo=C_HORAS,
                motivo="Las horas están vacías.",
                valor="",
                accion="Registre las horas del ticket.",
            )
            observaciones.append(observacion)
            problemas.append(observacion.como_dict())

        registro = dict(valores)
        registro["_fila_origen"] = numero_fila
        registro["_observaciones"] = problemas
        registro["_huella"] = huella_fila(valores)
        registros.append(registro)
        posiciones_validas.append(int(posicion))

    columnas_internas = ["_fila_origen", "_observaciones", "_huella"]
    df = pd.DataFrame(registros, columns=list(COLUMNAS_OFICIALES) + columnas_internas)

    extras_filtrado: pd.DataFrame | None = None
    if extras is not None and len(extras):
        extras_filtrado = extras.iloc[posiciones_validas].reset_index(drop=True)
        extras_filtrado.insert(0, "Fila", [r["_fila_origen"] for r in registros])

    resultado = ResultadoExtraccion(
        df=df,
        hoja=hoja_elegida,
        indice_encabezado=indice,
        mapeo=mapeo,
        columnas_origen=columnas_origen,
        extras=extras_filtrado,
        observaciones=observaciones,
        total_filas_origen=len(detalle),
        filas_vacias_omitidas=omitidas,
    )

    LOGGER.info(
        "Extracción: %s | hoja=%s | encabezado=%s | filas=%s | observaciones=%s",
        ruta.name,
        hoja_elegida,
        indice + 1,
        len(df),
        len(observaciones),
    )
    return resultado


def _accion_para(columna: str, problema: str) -> str:
    if columna == C_FECHA:
        return "Corrija la fecha o confirme el formato configurado."
    if columna == C_HORAS:
        return "Corrija las horas; no se reemplazan por cero."
    if columna == C_SOLICITANTE:
        return "Indique el solicitante correcto."
    if columna == C_AREA:
        return "Asigne un área al solicitante."
    return f"Revise el valor de {columna}."


def fila_publica(fila: dict[str, Any]) -> dict[str, Any]:
    """Devuelve solo los 11 campos oficiales serializados."""

    return fila_a_dict(fila)
