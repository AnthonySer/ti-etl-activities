"""Lector de Excel para el Anexo 5; no ejecuta ETL ni escribe la fuente."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from numbers import Number
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
    C_SOLICITANTE,
    COLUMNAS_OFICIALES,
    canonizar_encabezado,
    mapear_oficial,
)
from core.archivos import ErrorEstructura
from core.modelo_informe_docx import (
    DatosInforme,
    ErrorLecturaInforme,
    HallazgoInforme,
    PeriodoDetectado,
    RegistroInforme,
)
from core.procesado import _categoria_actividad


_CATEGORIAS_RECONOCIDAS = {"Incidencia", "Solicitud", "Proyecto"}
_LIMITE_BUSQUEDA_ENCABEZADO = 30


def _vacio(valor: Any) -> bool:
    if valor is None:
        return True
    try:
        if pd.isna(valor):
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(valor, str) and not valor.strip()


def _hash_archivo(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _fila_es_encabezado(valores: tuple[Any, ...] | list[Any]) -> bool:
    halladas = {
        canonizar_encabezado(valor)
        for valor in valores
        if not _vacio(valor)
    }
    return set(COLUMNAS_OFICIALES).issubset(halladas)


def _buscar_hoja_y_encabezado(ruta: Path, hoja: str | None) -> tuple[str, int]:
    with pd.ExcelFile(ruta) as libro:
        hojas_libro = tuple(libro.sheet_names)
    if hoja is not None:
        coincidencias = [nombre for nombre in hojas_libro if nombre.casefold() == hoja.casefold()]
        if not coincidencias:
            raise ErrorLecturaInforme(
                f"No se encontró la hoja '{hoja}'. Hojas: {', '.join(hojas_libro)}."
            )
        hojas_candidatas = coincidencias
    else:
        hojas_candidatas = [
            nombre
            for nombre in hojas_libro
            if nombre.casefold() in {cfg.HOJA_CONSULTA.casefold(), cfg.HOJA_DETALLE.casefold()}
        ]
    if not hojas_candidatas:
        raise ErrorLecturaInforme(
            "El libro debe incluir una hoja llamada 'Consulta' o 'Tickets'. "
            f"Hojas encontradas: {', '.join(hojas_libro)}."
        )
    if len(hojas_candidatas) > 1:
        raise ErrorLecturaInforme(
            "El libro contiene las hojas 'Consulta' y 'Tickets'; indique cuál es "
            "la fuente corregida que debe leerse."
        )

    nombre = hojas_candidatas[0]
    muestra = pd.read_excel(
        ruta,
        sheet_name=nombre,
        header=None,
        nrows=_LIMITE_BUSQUEDA_ENCABEZADO,
        dtype=object,
    )
    candidatas = [
        indice
        for indice, fila in muestra.iterrows()
        if _fila_es_encabezado(tuple(fila.tolist()))
    ]
    if len(candidatas) != 1:
        if not candidatas:
            raise ErrorLecturaInforme(
                f"No se encontró un encabezado con las 11 columnas oficiales en "
                f"las primeras {_LIMITE_BUSQUEDA_ENCABEZADO} filas de '{nombre}'."
            )
        raise ErrorLecturaInforme(
            f"Se encontraron varios encabezados posibles en la hoja '{nombre}': "
            + ", ".join(str(i + 1) for i in candidatas)
            + "."
        )
    return nombre, int(candidatas[0])


def _fecha(valor: Any) -> tuple[date | None, str]:
    if _vacio(valor):
        return None, "vacio"
    if isinstance(valor, pd.Timestamp):
        return valor.date(), "valido"
    if isinstance(valor, datetime):
        return valor.date(), "valido"
    if isinstance(valor, date):
        return valor, "valido"
    if isinstance(valor, Number) and not isinstance(valor, bool):
        # Los seriales Excel con formato fecha llegan como Timestamp. Un número
        # sin ese formato no se interpreta como fecha por aproximación.
        return None, "invalido"
    try:
        parsed = pd.to_datetime(valor, errors="raise", dayfirst=True)
        return parsed.date(), "valido"
    except (TypeError, ValueError, OverflowError):
        return None, "invalido"


def _horas(valor: Any) -> tuple[Decimal | None, str]:
    if _vacio(valor):
        return None, "vacio"
    try:
        numero = Decimal(str(valor).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None, "invalido"
    if not numero.is_finite() or numero < 0:
        return None, "invalido"
    return numero, "valido"


def _categoria(valor: Any) -> str | None:
    if _vacio(valor):
        return None
    resultado = _categoria_actividad(valor)
    return resultado if resultado in _CATEGORIAS_RECONOCIDAS else None


def _periodos(registros: tuple[RegistroInforme, ...]) -> tuple[PeriodoDetectado, ...]:
    filas: dict[tuple[int, int], list[int]] = {}
    for registro in registros:
        if registro.fecha is not None:
            clave = (registro.fecha.year, registro.fecha.month)
            filas.setdefault(clave, []).append(registro.fila_origen)
    return tuple(
        PeriodoDetectado(anio, mes, tuple(filas[(anio, mes)]))
        for anio, mes in sorted(filas)
    )


def leer_excel_informe(ruta: str | Path, hoja: str | None = None) -> DatosInforme:
    """Lee una sola fuente Excel y conserva cada valor oficial tal como aparece.

    La fuente se abre solo para lectura. No se consulta el manifiesto, el ETL,
    maestros, SQL ni reportes previos para reponer correcciones.
    """

    ruta = Path(ruta).expanduser().resolve()
    if not ruta.is_file():
        raise ErrorLecturaInforme(f"No existe el archivo seleccionado: {ruta}")
    if ruta.suffix.casefold() != ".xlsx":
        raise ErrorLecturaInforme("La fuente del generador debe ser un archivo .xlsx.")

    hash_antes = _hash_archivo(ruta)
    nombre_hoja, indice_encabezado = _buscar_hoja_y_encabezado(ruta, hoja)
    origen = pd.read_excel(ruta, sheet_name=nombre_hoja, header=indice_encabezado, dtype=object)
    try:
        detalle, _, _ = mapear_oficial(origen)
    except (ErrorEstructura, ValueError, KeyError) as exc:
        raise ErrorLecturaInforme(f"Las columnas de '{nombre_hoja}' no cumplen el esquema oficial: {exc}") from exc

    registros: list[RegistroInforme] = []
    hallazgos: list[HallazgoInforme] = []
    for indice, fila in detalle.iterrows():
        valores = tuple((col, fila[col]) for col in COLUMNAS_OFICIALES)
        if all(_vacio(valor) for _, valor in valores):
            continue
        fila_excel = indice_encabezado + int(indice) + 2
        fecha, estado_fecha = _fecha(fila[C_FECHA])
        horas, estado_horas = _horas(fila[C_HORAS])
        categoria = _categoria(fila[C_ACTIVIDAD])
        registro = RegistroInforme(
            fila_origen=fila_excel,
            valores_originales=valores,
            detalle=fila[C_DETALLE],
            fecha_original=fila[C_FECHA],
            fecha=fecha,
            solicitante_original=fila[C_SOLICITANTE],
            area_original=fila[C_AREA],
            atencion_original=fila[C_ATENCION],
            proyecto_original=fila[C_PROYECTO],
            horas_original=fila[C_HORAS],
            horas=horas,
            actividad_original=fila[C_ACTIVIDAD],
            categoria=categoria,
            estado_fecha=estado_fecha,
            estado_horas=estado_horas,
        )
        registros.append(registro)
        if estado_fecha == "invalido":
            hallazgos.append(HallazgoInforme("FECHA_INVALIDA", "bloqueante", "La fecha no se puede interpretar.", (fila_excel,), str(fila[C_FECHA])))
        if estado_horas == "invalido":
            hallazgos.append(HallazgoInforme("HORAS_INVALIDAS", "bloqueante", "Las horas no son numéricas, finitas y no negativas.", (fila_excel,), str(fila[C_HORAS])))
        if categoria is None:
            hallazgos.append(HallazgoInforme("ACTIVIDAD_DESCONOCIDA", "pendiente_regla", "La actividad requiere clasificación o una regla aprobada.", (fila_excel,), str(fila[C_ACTIVIDAD])))

    if hash_antes != _hash_archivo(ruta):
        raise ErrorLecturaInforme("El archivo cambió mientras se leía; vuelva a seleccionarlo.")

    inicios_sin_solicitante = tuple(
        registro.fila_origen for registro in registros if _vacio(registro.solicitante_original)
    )
    if inicios_sin_solicitante:
        hallazgos.append(HallazgoInforme(
            "SOLICITANTE_VACIO", "advertencia", "Hay filas sin solicitante identificado.", inicios_sin_solicitante
        ))
    inicios_horas_vacias = tuple(
        registro.fila_origen for registro in registros if registro.estado_horas == "vacio"
    )
    if inicios_horas_vacias:
        hallazgos.append(HallazgoInforme(
            "HORAS_VACIAS", "advertencia", "Hay filas sin valor de horas; no se imputó cero.", inicios_horas_vacias
        ))
    return DatosInforme(
        ruta_fuente=ruta,
        hash_fuente_sha256=hash_antes,
        hoja=nombre_hoja,
        fila_encabezado=indice_encabezado + 1,
        columnas_oficiales=tuple(COLUMNAS_OFICIALES),
        registros=tuple(registros),
        periodos_detectados=_periodos(tuple(registros)),
        hallazgos=tuple(hallazgos),
    )
