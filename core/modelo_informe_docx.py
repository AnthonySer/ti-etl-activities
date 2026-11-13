"""Modelos de lectura y cálculo para el Anexo 5 DOCX.

Este módulo mantiene los valores de la fuente separados de las proyecciones
estadísticas. No define reglas de negocio sobre proyectos, áreas pendientes o
filas con datos inválidos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any


CategoriaInforme = str
MesInforme = tuple[int, int]


@dataclass(frozen=True)
class HallazgoInforme:
    codigo: str
    severidad: str
    mensaje: str
    filas: tuple[int, ...] = ()
    valor: str | None = None


@dataclass(frozen=True)
class RegistroInforme:
    """Una fila fuente preservada, con derivados tipados para calcular."""

    fila_origen: int
    valores_originales: tuple[tuple[str, Any], ...]
    detalle: Any
    fecha_original: Any
    fecha: date | None
    solicitante_original: Any
    area_original: Any
    atencion_original: Any
    proyecto_original: Any
    horas_original: Any
    horas: Decimal | None
    actividad_original: Any
    categoria: str | None
    estado_fecha: str
    estado_horas: str


@dataclass(frozen=True)
class PeriodoDetectado:
    anio: int
    mes: int
    filas: tuple[int, ...]


@dataclass(frozen=True)
class DatosInforme:
    ruta_fuente: Path
    hash_fuente_sha256: str
    hoja: str
    fila_encabezado: int
    columnas_oficiales: tuple[str, ...]
    registros: tuple[RegistroInforme, ...]
    periodos_detectados: tuple[PeriodoDetectado, ...]
    hallazgos: tuple[HallazgoInforme, ...]


@dataclass(frozen=True)
class FilaCategoriaInforme:
    categoria: str
    cantidad_registros: int
    horas_conocidas: Decimal
    filas_sin_horas: int
    filas_con_horas_invalidas: int


@dataclass(frozen=True)
class FilaAreaInforme:
    area_original: str | None
    cantidades_por_categoria: tuple[tuple[str, int], ...]
    total_registros: int


@dataclass(frozen=True)
class EstadisticasInforme:
    ruta_fuente: Path
    hash_fuente_sha256: str
    periodo: MesInforme | None
    periodo_indeterminado: bool
    filas_fuente: int
    filas_analizadas: int
    filas_sin_fecha: int
    filas_fecha_vacia: int
    filas_fecha_invalida: int
    filas_excluidas_por_periodo: int
    filas_sin_solicitante: int
    filas_sin_area: int
    usuarios_sin_area: tuple[tuple[str | None, int], ...]
    actividades_proyecto: int
    nombres_proyecto_distintos_exactos: tuple[str, ...]
    horas_y_filas_por_proyecto_exacto: tuple[tuple[str | None, Decimal, int, int], ...]
    filas_por_categoria: tuple[FilaCategoriaInforme, ...]
    horas_conocidas_por_categoria: tuple[tuple[str, Decimal], ...]
    horas_totales_completas: Decimal | None
    porcentajes_horas_por_categoria: tuple[tuple[str, Decimal], ...] | None
    areas_todos_los_registros: tuple[FilaAreaInforme, ...]
    areas_sin_proyectos: tuple[FilaAreaInforme, ...]
    areas_incidencias_solicitudes: tuple[FilaAreaInforme, ...]
    hallazgos: tuple[HallazgoInforme, ...]


class ErrorLecturaInforme(ValueError):
    """La fuente no cumple el contrato técnico de lectura del informe."""


class ErrorPeriodoInforme(ValueError):
    """El período solicitado no existe en la fuente leída."""
