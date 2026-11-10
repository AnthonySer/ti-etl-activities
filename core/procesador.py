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
from core import auditoria, procedencia
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
from core.procesado import (
    copiar_revision,
    escribir_procesado,
    respaldar_si_existe,
    resolver_salida,
)
from core.retroalimentacion import ruta_manifiesto
from core.revision import Revision
from database.normalizador import normalizar_nombre

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
    modo_generacion: str = cfg.MODO_LEGACY
    areas_sql: int = 0
    conflictos: int = 0
    usuarios_conflicto: int = 0
    ruta_revision: str | None = None
    ruta_copia_revision: str | None = None
    generacion_parcial: bool = False
    advertencias: int = 0

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


def _sql_configurada(usar_sql: bool | None) -> Any | None:
    """Devuelve la configuración SQL solo si la integración puede usarse."""

    if usar_sql is False:
        return None
    try:
        from config.sqlserver import cargar_configuracion
        from database.connection import controlador_disponible

        config = cargar_configuracion()
    except Exception as exc:  # noqa: BLE001 - la integración es opcional
        LOGGER.warning("No se pudo cargar la configuración SQL: %s", exc)
        return None
    if usar_sql is True:
        return config
    if config.habilitado and config.configurada and controlador_disponible():
        return config
    return None


def _resolver_pendientes_sql(
    pendientes: dict[str, dict[str, Any]],
    filas: list[dict[str, Any]],
    maestro: Maestro,
    config: Any,
    ejecucion: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], int, int, list[dict[str, str]]]:
    """Resuelve pendientes contra SQL Server en un solo lote.

    Devuelve ``(resoluciones, conflictos, areas_asignadas, filas_asignadas,
    errores)``. Nunca lanza: un fallo de SQL Server deja los pendientes intactos.
    """

    from database.repository import RepositorioEmpleados
    from database.resolucion import ESTADO_VALIDADO, ResolverAreas
    from database.synchronizer import Sincronizador, cargar_empleados

    errores: list[dict[str, str]] = []
    nombres = [str(item.get("usuario") or "") for item in pendientes.values()]
    nombres = [nombre for nombre in nombres if nombre]
    if not nombres:
        return {}, [], 0, 0, errores

    try:
        empleados = cargar_empleados()
        resolver = ResolverAreas(empleados, maestro=maestro)
        resoluciones = resolver.resolver_lote(nombres)

        faltantes = [
            resultado.usuario
            for resultado in resoluciones.values()
            if resultado.caso == "C"
        ]
        if faltantes:
            repositorio = RepositorioEmpleados(config)
            consulta = repositorio.buscar_por_nombres(faltantes)
            errores.extend(consulta.errores)
            if consulta.empleados:
                Sincronizador(
                    config, repositorio=repositorio
                ).actualizar_desde_busqueda(consulta.empleados)
                empleados = cargar_empleados()
                resolver = ResolverAreas(empleados, maestro=maestro)
                resoluciones = resolver.resolver_lote(nombres)
    except Exception as exc:  # noqa: BLE001 - SQL no debe bloquear el ETL
        LOGGER.warning("No se pudo resolver contra SQL Server: %s", exc)
        return {}, [], 0, 0, errores

    # Índice de filas por clave de usuario (normalización del procesador).
    filas_por_clave: dict[str, list[dict[str, Any]]] = {}
    for fila in filas:
        clave = normalizar_usuario(fila.get(C_SOLICITANTE))
        if clave:
            filas_por_clave.setdefault(clave, []).append(fila)

    areas_asignadas = 0
    filas_asignadas = 0
    conflictos: list[dict[str, Any]] = []

    for clave, item in list(pendientes.items()):
        clave_sql = normalizar_nombre(item.get("usuario"))
        resultado = resoluciones.get(clave_sql)
        if resultado is None:
            continue

        if resultado.conflicto or resultado.estado in {
            "Conflicto con maestro",
            "Asignación manual protegida",
        }:
            conflicto = resultado.como_dict()
            conflicto["filas"] = list(item.get("filas") or [])
            conflictos.append(conflicto)
            continue

        if resultado.caso != "A" or not resultado.asignable:
            continue

        for fila in filas_por_clave.get(clave, []):
            fila[C_AREA] = resultado.area
            fila["_area_origen"] = "sql_server"
            if fila.get("_estado") == ESTADO_PENDIENTE:
                tiene_error = any(
                    str(observacion.get("severidad")) == "error"
                    for observacion in fila.get("_observaciones") or []
                )
                fila["_estado"] = ESTADO_OBSERVADO if tiene_error else ESTADO_VALIDADO

        procedencia.registrar(
            item.get("usuario"),
            resultado.area,
            procedencia.SQL_SERVER,
            id_ejecucion=ejecucion,
            nota=resultado.motivo,
        )
        areas_asignadas += 1
        filas_asignadas += len(item.get("filas") or [])
        pendientes.pop(clave, None)

    return resoluciones, conflictos, areas_asignadas, filas_asignadas, errores


def _crear_revision(
    datos_manifiesto: dict[str, Any],
    resoluciones: dict[str, Any],
    conflictos: list[dict[str, Any]],
    *,
    archivo_procesado: str,
    archivo_manifiesto: str,
) -> str | None:
    try:
        revision = Revision.desde_manifiesto(
            datos_manifiesto,
            resoluciones={
                clave: resultado.como_dict()
                for clave, resultado in resoluciones.items()
            },
            archivo_procesado=archivo_procesado,
            archivo_manifiesto=archivo_manifiesto,
            conflictos=conflictos,
        )
        return str(revision.guardar())
    except Exception as exc:  # noqa: BLE001 - la revisión no bloquea la salida
        LOGGER.error("No se pudo guardar el estado de revisión: %s", exc)
        return None


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
    *,
    modo: str | None = None,
    permitir_pendientes: bool = True,
    usar_sql: bool | None = None,
    crear_revision: bool = False,
    crear_respaldo: bool = False,
    crear_copia_revision: bool = False,
) -> EstadisticasProcesamiento:
    """Procesa un Excel, asigna áreas y genera el archivo de 11 columnas.

    ``modo=None`` conserva el comportamiento histórico (5 hojas y hoja
    ``Tickets``) para no romper la consola ni los archivos existentes. La
    interfaz usa ``modo='normal'`` o ``modo='detalle'`` con la hoja canónica
    ``Consulta``.
    """

    ruta_entrada = Path(ruta_entrada)
    maestro = maestro or Maestro()
    ejecucion = id_ejecucion or auditoria.nueva_ejecucion()
    modo_efectivo = str(modo or cfg.MODO_LEGACY).lower()
    if modo_efectivo not in (cfg.MODO_LEGACY, cfg.MODO_NORMAL, cfg.MODO_DETALLE):
        raise ErrorArchivo(f"Modo de generación no válido: {modo}")

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
    legacy_registros: list[dict[str, str]] = []

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
                legacy_registros.append(
                    {
                        "usuario": usuario_original or clave,
                        "area": area_maestro,
                        "origen": procedencia.MANUAL,
                        "id_ejecucion": ejecucion,
                        "nota": "Área registrada en el maestro local.",
                    }
                )
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
                    legacy_registros.append(
                        {
                            "usuario": usuario_original or clave,
                            "area": area_existente,
                            "origen": procedencia.LEGACY,
                            "id_ejecucion": ejecucion,
                            "nota": "Área conservada del archivo de tickets.",
                        }
                    )
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

    resoluciones: dict[str, Any] = {}
    conflictos: list[dict[str, Any]] = []
    areas_sql = 0
    errores_sql: list[dict[str, str]] = []

    config_sql = _sql_configurada(usar_sql)
    if legacy_registros:
        try:
            procedencia.registrar_lote(
                legacy_registros, id_ejecucion=ejecucion
            )
        except Exception as exc:  # noqa: BLE001 - trazabilidad no bloquea
            LOGGER.warning("No se pudo registrar la procedencia local: %s", exc)
    if config_sql is not None and usuarios_pendientes:
        (
            resoluciones,
            conflictos,
            areas_sql,
            filas_sql,
            errores_sql,
        ) = _resolver_pendientes_sql(
            usuarios_pendientes, filas, maestro, config_sql, ejecucion
        )
        if areas_sql:
            areas_asignadas += areas_sql
            registros_pendientes = max(0, registros_pendientes - filas_sql)
        for error in errores_sql:
            LOGGER.warning(
                "SQL Server (%s): %s",
                error.get("empresa", ""),
                error.get("mensaje", ""),
            )

    if (
        modo_efectivo == cfg.MODO_NORMAL
        and registros_pendientes > 0
        and not permitir_pendientes
    ):
        raise ErrorArchivo(
            "No se puede generar el Excel completo: existen "
            f"{registros_pendientes} registros sin área validada. "
            "Use 'Generar Excel con pendientes' o complete la validación."
        )

    df_procesado = pd.DataFrame(filas)
    for columna in COLUMNAS_OFICIALES:
        if columna not in df_procesado.columns:
            df_procesado[columna] = None

    if "_id_tecnico" not in df_procesado.columns:
        df_procesado["_id_tecnico"] = []
    if "_estado" not in df_procesado.columns:
        df_procesado["_estado"] = []

    conteos = df_procesado["_estado"].value_counts().to_dict() if len(df_procesado) else {}

    if modo_efectivo == cfg.MODO_DETALLE and ruta_salida is None:
        ruta_salida = (
            cfg.OUTPUT_DIR / f"{ruta_entrada.stem}_procesado_detalle.xlsx"
        )
    destino = resolver_salida(ruta_entrada, ruta_salida, sobrescribir)
    respaldo_destino = crear_respaldo or modo_efectivo in (
        cfg.MODO_NORMAL,
        cfg.MODO_DETALLE,
    )

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
        "Áreas obtenidas de SQL Server": areas_sql,
        "Conflictos de área detectados": len(conflictos),
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
        "Modo de generación": modo_efectivo,
        "Generación parcial": "Sí" if registros_pendientes > 0 else "No",
    }

    escribir_procesado(
        df_procesado.loc[:, list(COLUMNAS_OFICIALES)],
        destino,
        observaciones=observaciones,
        extras=extraccion.extras,
        resumen=resumen,
        meta=meta,
        modo=modo_efectivo,
        crear_respaldo=respaldo_destino,
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
        "modo_generacion": modo_efectivo,
        "hoja_principal": (
            cfg.HOJA_CONSULTA
            if modo_efectivo in (cfg.MODO_NORMAL, cfg.MODO_DETALLE)
            else cfg.HOJA_DETALLE
        ),
        "generacion_parcial": registros_pendientes > 0,
        "resoluciones_sql": {
            clave: resultado.como_dict()
            for clave, resultado in resoluciones.items()
        },
        "conflictos": conflictos,
        "errores_sql": errores_sql,
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

    ruta_copia_revision: str | None = None
    if crear_copia_revision or modo_efectivo in (cfg.MODO_NORMAL, cfg.MODO_DETALLE):
        try:
            ruta_copia_revision = str(copiar_revision(destino))
        except Exception as exc:  # noqa: BLE001 - la copia no bloquea la salida
            LOGGER.error("No se pudo crear la copia de revisión: %s", exc)

    ruta_revision: str | None = None
    if crear_revision and (usuarios_pendientes or conflictos):
        ruta_revision = _crear_revision(
            datos_manifiesto,
            resoluciones,
            conflictos,
            archivo_procesado=str(destino),
            archivo_manifiesto=archivo_manifiesto or "",
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
        modo_generacion=modo_efectivo,
        areas_sql=areas_sql,
        conflictos=len(conflictos),
        usuarios_conflicto=len(conflictos),
        ruta_revision=ruta_revision,
        ruta_copia_revision=ruta_copia_revision,
        generacion_parcial=registros_pendientes > 0,
        advertencias=len(errores_sql),
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
