"""Orquestación del procesamiento: ETL, asignación de áreas y Excel procesado."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from config import configuracion as cfg
from core import auditoria
from core.archivos import ErrorArchivo, escribir_json_atomico
from core.esquema import (
    C_AREA,
    C_FECHA,
    C_HORAS,
    C_SOLICITANTE,
    COLUMNAS_OFICIALES,
    fila_a_dict,
    huella_fila,
    serializar_valor,
)
from core.etl import extraer
from core.maestro import Maestro
from core.normalizador import limpiar_area, normalizar_usuario
from core.procesado import escribir_procesado, resolver_salida
from core.retroalimentacion import ruta_manifiesto

LOGGER = logging.getLogger("automatizador")

ESTADO_VALIDO = "VALIDO"
ESTADO_OBSERVADO = "OBSERVADO"
ESTADO_PENDIENTE = "PENDIENTE_DE_ASIGNACION"
ESTADO_DUPLICADO = "DUPLICADO"
ESTADO_ERROR = "ERROR"

_PRECEDENCIA = {
    ESTADO_VALIDO: 0,
    ESTADO_DUPLICADO: 1,
    ESTADO_OBSERVADO: 2,
    ESTADO_PENDIENTE: 3,
    ESTADO_ERROR: 4,
}


@dataclass
class EstadisticasProcesamiento:
    archivo_entrada: str
    hoja: str
    archivo_salida: str
    registros_totales: int
    usuarios_unicos: int
    usuarios_en_maestro: int
    areas_asignadas: int
    usuarios_pendientes: int
    registros_pendientes: int
    areas_existentes_preservadas: int
    registros_sin_usuario: int
    archivo_manifiesto: str | None = None
    id_ejecucion: str = ""
    registros_validos: int = 0
    registros_observados: int = 0
    registros_duplicados: int = 0
    registros_error: int = 0
    total_horas: float = 0.0
    periodo: str = ""
    filas_vacias_omitidas: int = 0
    observaciones: int = 0
    ruta_reporte: str | None = None

    def como_dict(self) -> dict[str, Any]:
        return asdict(self)


def _hash_archivo(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _agregar_observacion(
    fila: dict[str, Any],
    observaciones: list[dict[str, Any]],
    campo: str,
    motivo: str,
    valor: Any = "",
    accion: str = "Revise el registro.",
    severidad: str = "observacion",
) -> None:
    item = {
        "fila": fila.get("_fila_origen"),
        "campo": campo,
        "motivo": motivo,
        "valor": serializar_valor(valor) or "",
        "accion": accion,
        "severidad": severidad,
    }
    observaciones.append(item)
    fila["_observaciones"].append(item)


def _estado_para(
    fila: dict[str, Any],
    *,
    usuario_valido: bool,
    tiene_area: bool,
    usuario_conocido: bool,
    duplicado: bool,
) -> str:
    observaciones = fila.get("_observaciones") or []
    tiene_error = any(
        str(item.get("severidad")) == "error" for item in observaciones
    )
    tiene_observacion = bool(observaciones)

    if not usuario_valido or tiene_error or fila.get(C_SOLICITANTE) is None:
        return ESTADO_ERROR
    if not usuario_conocido and not tiene_area:
        return ESTADO_PENDIENTE
    if tiene_observacion or (not usuario_conocido and tiene_area):
        return ESTADO_OBSERVADO
    if duplicado:
        return ESTADO_DUPLICADO
    return ESTADO_VALIDO


def _prioridad(estado: str) -> int:
    return _PRECEDENCIA.get(estado, 0)


def procesar_archivo(
    ruta_entrada: Path | str,
    maestro: Maestro | None,
    ruta_salida: Path | str | None = None,
    hoja: str | None = None,
    sobrescribir: bool = False,
    formato_fecha: str | None = None,
    id_ejecucion: str | None = None,
    usuario_ejecutor: str = "",
    registrar_auditoria: bool = True,
) -> EstadisticasProcesamiento:
    """Procesa un Excel, asigna áreas y genera el archivo de 11 columnas."""

    ruta_entrada = Path(ruta_entrada)
    maestro = maestro or Maestro()
    ejecucion = id_ejecucion or auditoria.nueva_ejecucion()

    extraccion = extraer(ruta_entrada, hoja=hoja, formato_fecha=formato_fecha)
    df = extraccion.df.copy()

    if df.empty:
        LOGGER.warning("El archivo %s no contiene registros.", ruta_entrada.name)

    observaciones: list[dict[str, Any]] = [
        observacion.como_dict() for observacion in extraccion.observaciones
    ]

    usuarios_unicos: set[str] = set()
    usuarios_en_maestro: set[str] = set()
    usuarios_pendientes: dict[str, dict[str, Any]] = {}
    asignaciones: dict[str, dict[str, Any]] = {}
    preexistentes: dict[str, dict[str, Any]] = {}

    areas_asignadas = 0
    registros_pendientes = 0
    preservadas = 0
    sin_usuario = 0
    total_horas = 0.0
    vistos: dict[str, int] = {}

    filas: list[dict[str, Any]] = []

    for _, fila_serie in df.iterrows():
        fila = fila_serie.to_dict()
        fila.setdefault("_observaciones", [])
        if not isinstance(fila["_observaciones"], list):
            fila["_observaciones"] = list(fila["_observaciones"] or [])

        usuario_original = fila.get(C_SOLICITANTE)
        clave = normalizar_usuario(usuario_original)
        area_existente = limpiar_area(fila.get(C_AREA))

        usuario_valido = clave is not None
        usuario_conocido = False
        area_maestro: str | None = None
        origen_area = "vacia"

        if usuario_valido:
            usuarios_unicos.add(clave)
            area_maestro = maestro.buscar(clave)
            if area_maestro:
                usuario_conocido = True
                usuarios_en_maestro.add(clave)
                fila[C_AREA] = area_maestro
                origen_area = "automatica"
                areas_asignadas += 1
                item = asignaciones.setdefault(
                    clave,
                    {
                        "clave": clave,
                        "usuario": usuario_original or clave,
                        "area": area_maestro,
                        "filas": [],
                    },
                )
                item["filas"].append(fila.get("_fila_origen"))
            else:
                item = usuarios_pendientes.setdefault(
                    clave,
                    {
                        "clave": clave,
                        "usuario": usuario_original or clave,
                        "filas": [],
                        "area_preexistente": area_existente or "",
                    },
                )
                item["filas"].append(fila.get("_fila_origen"))

                if area_existente:
                    origen_area = "preexistente"
                    preservadas += 1
                    fila[C_AREA] = area_existente
                    pre = preexistentes.setdefault(
                        clave,
                        {
                            "clave": clave,
                            "usuario": usuario_original or clave,
                            "area": area_existente,
                            "filas": [],
                        },
                    )
                    pre["filas"].append(fila.get("_fila_origen"))
                    _agregar_observacion(
                        fila,
                        observaciones,
                        C_SOLICITANTE,
                        "El solicitante no está registrado en la base de áreas; "
                        "se conservó el área que traía el archivo.",
                        usuario_original,
                        "Registre al solicitante en la base de usuarios y áreas.",
                    )
                else:
                    registros_pendientes += 1
                    _agregar_observacion(
                        fila,
                        observaciones,
                        C_AREA,
                        "El solicitante no tiene un área asignada.",
                        usuario_original,
                        "Asigne un área al solicitante y reprocese o reimporte.",
                    )
        else:
            sin_usuario += 1
            fila[C_AREA] = area_existente

        # Duplicados exactos dentro del mismo archivo: se conservan, marcados.
        huella = huella_fila(fila)
        duplicado = huella in vistos
        vistos[huella] = vistos.get(huella, 0) + 1

        estado = _estado_para(
            fila,
            usuario_valido=usuario_valido,
            tiene_area=limpiar_area(fila.get(C_AREA)) is not None,
            usuario_conocido=usuario_conocido,
            duplicado=duplicado,
        )
        fila["_huella"] = huella
        fila["_estado"] = estado
        fila["_area_origen"] = origen_area
        fila["_id_tecnico"] = uuid.uuid4().hex

        horas = fila.get(C_HORAS)
        if isinstance(horas, (int, float)):
            total_horas += float(horas)

        filas.append(fila)

    df_procesado = pd.DataFrame(filas)
    for columna in COLUMNAS_OFICIALES:
        if columna not in df_procesado.columns:
            df_procesado[columna] = None

    if "_id_tecnico" not in df_procesado.columns:
        df_procesado["_id_tecnico"] = []
    if "_estado" not in df_procesado.columns:
        df_procesado["_estado"] = []

    conteos = df_procesado["_estado"].value_counts().to_dict() if len(df_procesado) else {}

    destino = resolver_salida(ruta_entrada, ruta_salida, sobrescribir)

    resumen = {
        "Total de registros": len(df_procesado),
        "Registros válidos": int(conteos.get(ESTADO_VALIDO, 0)),
        "Registros observados": int(conteos.get(ESTADO_OBSERVADO, 0)),
        "Registros pendientes de asignación": int(
            conteos.get(ESTADO_PENDIENTE, 0)
        ),
        "Registros duplicados": int(conteos.get(ESTADO_DUPLICADO, 0)),
        "Registros con error": int(conteos.get(ESTADO_ERROR, 0)),
        "Total de horas": round(total_horas, 2),
        "Solicitantes únicos": len(usuarios_unicos),
        "Áreas asignadas automáticamente": areas_asignadas,
        "Áreas preexistentes preservadas": preservadas,
    }

    meta = {
        "ID de ejecución": ejecucion,
        "Archivo original": ruta_entrada.name,
        "Ruta original": str(ruta_entrada.resolve()),
        "Hash SHA-256 original": _hash_archivo(ruta_entrada),
        "Hoja procesada": extraccion.hoja,
        "Fila de encabezado": extraccion.indice_encabezado + 1,
        "Periodo detectado": extraccion.periodo or "No determinado",
        "Fecha y hora": datetime.now().astimezone().isoformat(timespec="seconds"),
        "Correspondencias en base de áreas": len(maestro),
        "Estructura oficial": "11 columnas: " + ", ".join(COLUMNAS_OFICIALES),
        "Filas vacías omitidas": extraccion.filas_vacias_omitidas,
    }

    escribir_procesado(
        df_procesado.loc[:, list(COLUMNAS_OFICIALES)],
        destino,
        observaciones=observaciones,
        extras=extraccion.extras,
        resumen=resumen,
        meta=meta,
    )

    ruta_json = ruta_manifiesto(destino)
    datos_manifiesto: dict[str, Any] = {
        "version": 2,
        "id_ejecucion": ejecucion,
        "generado": datetime.now().astimezone().isoformat(timespec="seconds"),
        "archivo_entrada": str(ruta_entrada.resolve()),
        "nombre_entrada": ruta_entrada.name,
        "hash_entrada": meta["Hash SHA-256 original"],
        "archivo_salida": str(destino.resolve()),
        "hoja": extraccion.hoja,
        "indice_encabezado": extraccion.indice_encabezado,
        "periodo": extraccion.periodo,
        "columnas_oficiales": list(COLUMNAS_OFICIALES),
        "mapeo_columnas": extraccion.mapeo,
        "maestro_correspondencias": len(maestro),
        "fecha_archivo_ns": ruta_entrada.stat().st_mtime_ns,
        "resumen": resumen,
        "filas": [
            {
                "fila": int(fila.get("_fila_origen") or 0),
                "id_tecnico": str(fila.get("_id_tecnico") or ""),
                "huella": str(fila.get("_huella") or ""),
                "estado": str(fila.get("_estado") or ""),
                "area_origen": str(fila.get("_area_origen") or ""),
                "datos": fila_a_dict(fila),
            }
            for fila in filas
        ],
        "observaciones": observaciones,
        "asignaciones_automaticas": list(asignaciones.values()),
        "usuarios_pendientes": list(usuarios_pendientes.values()),
        "areas_preexistentes": list(preexistentes.values()),
    }

    archivo_manifiesto: str | None = None
    try:
        escribir_json_atomico(datos_manifiesto, ruta_json)
        archivo_manifiesto = str(ruta_json)
    except ErrorArchivo as exc:
        LOGGER.error(
            "El archivo se generó, pero no se pudo escribir el manifiesto '%s': %s",
            ruta_json,
            exc,
        )

    estadisticas = EstadisticasProcesamiento(
        archivo_entrada=ruta_entrada.name,
        hoja=extraccion.hoja,
        archivo_salida=str(destino),
        registros_totales=len(df_procesado),
        usuarios_unicos=len(usuarios_unicos),
        usuarios_en_maestro=len(usuarios_en_maestro),
        areas_asignadas=areas_asignadas,
        usuarios_pendientes=len(usuarios_pendientes),
        registros_pendientes=registros_pendientes,
        areas_existentes_preservadas=preservadas,
        registros_sin_usuario=sin_usuario,
        archivo_manifiesto=archivo_manifiesto,
        id_ejecucion=ejecucion,
        registros_validos=int(conteos.get(ESTADO_VALIDO, 0)),
        registros_observados=int(conteos.get(ESTADO_OBSERVADO, 0)),
        registros_duplicados=int(conteos.get(ESTADO_DUPLICADO, 0)),
        registros_error=int(conteos.get(ESTADO_ERROR, 0)),
        total_horas=round(total_horas, 2),
        periodo=extraccion.periodo or "",
        filas_vacias_omitidas=extraccion.filas_vacias_omitidas,
        observaciones=len(observaciones),
    )

    if registrar_auditoria:
        try:
            auditoria.registrar_procesamiento(
                estadisticas,
                archivo_original=str(ruta_entrada.resolve()),
                usuario_ejecutor=usuario_ejecutor,
                periodo=extraccion.periodo,
                mensajes=(
                    f"Observaciones: {len(observaciones)} | "
                    f"Filas vacías omitidas: {extraccion.filas_vacias_omitidas}"
                ),
            )
        except Exception as exc:  # noqa: BLE001 - no debe tumbar el proceso
            LOGGER.error("No se pudo registrar la auditoría: %s", exc)

    LOGGER.info(
        "Procesado %s: %s registros, %s válidos, %s observados, %s pendientes, "
        "%s duplicados, %s errores",
        ruta_entrada.name,
        len(df_procesado),
        estadisticas.registros_validos,
        estadisticas.registros_observados,
        estadisticas.registros_pendientes,
        estadisticas.registros_duplicados,
        estadisticas.registros_error,
    )
    return estadisticas
