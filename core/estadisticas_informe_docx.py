"""Cálculo base de estadísticas Anexo 5 sin resolver reglas de negocio abiertas."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from core.lector_informe_docx import _categoria, _vacio
from core.modelo_informe_docx import (
    DatosInforme,
    ErrorPeriodoInforme,
    EstadisticasInforme,
    FilaAreaInforme,
    FilaCategoriaInforme,
    HallazgoInforme,
    MesInforme,
    RegistroInforme,
)


_CATEGORIAS = ("Incidencia", "Solicitud", "Proyecto")
_TICKETS_CANDIDATOS = frozenset({"Incidencia", "Solicitud"})


def _areas(registros: tuple[RegistroInforme, ...]) -> tuple[FilaAreaInforme, ...]:
    cantidades: dict[str | None, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for registro in registros:
        area = None if _vacio(registro.area_original) else str(registro.area_original)
        categoria = registro.categoria or (
            "Sin clasificar: " + str(registro.actividad_original or "(VACÍO)")
        )
        cantidades[area][categoria] += 1
    return tuple(
        FilaAreaInforme(
            area_original=area,
            cantidades_por_categoria=tuple(sorted(pares.items())),
            total_registros=sum(pares.values()),
        )
        for area, pares in sorted(cantidades.items(), key=lambda item: (item[0] is None, item[0] or ""))
    )


def calcular_estadisticas_informe(
    datos: DatosInforme,
    periodo: MesInforme | None = None,
) -> EstadisticasInforme:
    """Calcula conteos candidatos y horas conocidas a partir de una sola lectura.

    Si hay varios meses y no se especifica uno, presenta el agregado de la fuente
    como indeterminado y deja un hallazgo; no escoge un período silenciosamente.
    Devuelve dos distribuciones de áreas (todos los registros y solo categorías
    de ticket) para no decidir el tratamiento de proyectos antes de su aprobación.
    """

    periodos = tuple((p.anio, p.mes) for p in datos.periodos_detectados)
    hallazgos = list(datos.hallazgos)
    filas_fecha_vacia_fuente = sum(r.estado_fecha == "vacio" for r in datos.registros)
    filas_fecha_invalida_fuente = sum(r.estado_fecha == "invalido" for r in datos.registros)
    if periodo is not None:
        if periodo not in periodos:
            raise ErrorPeriodoInforme(f"El período {periodo[0]:04d}-{periodo[1]:02d} no aparece en el Excel.")
        incluidos = tuple(
            registro for registro in datos.registros
            if registro.fecha is not None and (registro.fecha.year, registro.fecha.month) == periodo
        )
        excluidos = len(datos.registros) - len(incluidos)
        if excluidos:
            hallazgos.append(HallazgoInforme(
                "FILAS_FUERA_DEL_PERIODO", "informativo", "Filas sin fecha válida o de otros meses no forman parte del período seleccionado.", (), str(excluidos)
            ))
        periodo_indeterminado = False
    else:
        if len(periodos) == 1:
            periodo = periodos[0]
            incluidos = tuple(
                registro for registro in datos.registros
                if registro.fecha is not None and (registro.fecha.year, registro.fecha.month) == periodo
            )
            excluidos = len(datos.registros) - len(incluidos)
            periodo_indeterminado = False
            if excluidos:
                hallazgos.append(HallazgoInforme(
                    "FILAS_SIN_FECHA_EN_PERIODO", "advertencia", "Hay filas sin fecha válida que no se asignaron al único mes detectado.", (), str(excluidos)
                ))
        else:
            incluidos = datos.registros
            excluidos = 0
            periodo_indeterminado = True
            hallazgos.append(HallazgoInforme(
                "PERIODO_INDETERMINADO", "pendiente_regla", "La fuente no contiene exactamente un período mensual identificable; seleccione uno para el informe.", (), ", ".join(f"{a:04d}-{m:02d}" for a, m in periodos) or None
            ))

    categorias: dict[str, list[RegistroInforme]] = {categoria: [] for categoria in _CATEGORIAS}
    desconocidas: dict[str, list[RegistroInforme]] = defaultdict(list)
    for registro in incluidos:
        if registro.categoria in categorias:
            categorias[registro.categoria].append(registro)
        else:
            desconocidas[str(registro.actividad_original or "(VACÍO)")].append(registro)

    filas_categoria: list[FilaCategoriaInforme] = []
    horas_conocidas: list[tuple[str, Decimal]] = []
    horas_totales_conocidas = Decimal("0")
    total_horas_desconocidas = 0
    for categoria in _CATEGORIAS:
        filas = categorias[categoria]
        conocidas = [registro.horas for registro in filas if registro.horas is not None]
        vacias = sum(registro.estado_horas == "vacio" for registro in filas)
        invalidas = sum(registro.estado_horas == "invalido" for registro in filas)
        suma = sum(conocidas, Decimal("0"))
        filas_categoria.append(FilaCategoriaInforme(categoria, len(filas), suma, vacias, invalidas))
        horas_conocidas.append((categoria, suma))
        horas_totales_conocidas += suma
        total_horas_desconocidas += vacias + invalidas
    for categoria, filas in sorted(desconocidas.items()):
        conocidas = [registro.horas for registro in filas if registro.horas is not None]
        vacias = sum(registro.estado_horas == "vacio" for registro in filas)
        invalidas = sum(registro.estado_horas == "invalido" for registro in filas)
        suma = sum(conocidas, Decimal("0"))
        filas_categoria.append(FilaCategoriaInforme(f"Sin clasificar: {categoria}", len(filas), suma, vacias, invalidas))
        horas_conocidas.append((f"Sin clasificar: {categoria}", suma))
        horas_totales_conocidas += suma
        total_horas_desconocidas += vacias + invalidas

    horas_completas = horas_totales_conocidas if total_horas_desconocidas == 0 else None
    porcentajes: tuple[tuple[str, Decimal], ...] | None
    if horas_completas is None or horas_completas == 0:
        porcentajes = None if horas_completas is None else tuple((k, Decimal("0")) for k, _ in horas_conocidas)
    else:
        porcentajes = tuple((k, v / horas_completas * 100) for k, v in horas_conocidas)

    tickets_candidato = tuple(registro for registro in incluidos if registro.categoria in _TICKETS_CANDIDATOS)
    proyectos = tuple(registro for registro in incluidos if registro.categoria == "Proyecto")
    nombres_proyecto = tuple(sorted({
        str(registro.proyecto_original)
        for registro in proyectos
        if not _vacio(registro.proyecto_original)
    }))
    proyectos_agrupados: dict[str | None, list[RegistroInforme]] = defaultdict(list)
    for registro in proyectos:
        nombre = None if _vacio(registro.proyecto_original) else str(registro.proyecto_original)
        proyectos_agrupados[nombre].append(registro)
    horas_y_filas_por_proyecto = tuple(
        (
            nombre,
            sum((registro.horas for registro in filas if registro.horas is not None), Decimal("0")),
            len(filas),
            sum(registro.estado_horas != "valido" for registro in filas),
        )
        for nombre, filas in sorted(
            proyectos_agrupados.items(), key=lambda item: (item[0] is None, item[0] or "")
        )
    )
    filas_sin_solicitante = sum(_vacio(registro.solicitante_original) for registro in incluidos)
    filas_sin_area = sum(_vacio(registro.area_original) for registro in incluidos)
    pendientes_area: dict[str | None, int] = defaultdict(int)
    for registro in incluidos:
        if _vacio(registro.area_original):
            usuario = None if _vacio(registro.solicitante_original) else str(registro.solicitante_original)
            pendientes_area[usuario] += 1
    return EstadisticasInforme(
        ruta_fuente=datos.ruta_fuente,
        hash_fuente_sha256=datos.hash_fuente_sha256,
        periodo=periodo,
        periodo_indeterminado=periodo_indeterminado,
        filas_fuente=len(datos.registros),
        filas_analizadas=len(incluidos),
        filas_sin_fecha=filas_fecha_vacia_fuente + filas_fecha_invalida_fuente,
        filas_fecha_vacia=filas_fecha_vacia_fuente,
        filas_fecha_invalida=filas_fecha_invalida_fuente,
        filas_excluidas_por_periodo=excluidos,
        filas_sin_solicitante=filas_sin_solicitante,
        filas_sin_area=filas_sin_area,
        usuarios_sin_area=tuple(sorted(pendientes_area.items(), key=lambda item: (item[0] is None, item[0] or ""))),
        actividades_proyecto=len(proyectos),
        nombres_proyecto_distintos_exactos=nombres_proyecto,
        horas_y_filas_por_proyecto_exacto=horas_y_filas_por_proyecto,
        filas_por_categoria=tuple(filas_categoria),
        horas_conocidas_por_categoria=tuple(horas_conocidas),
        horas_totales_completas=horas_completas,
        porcentajes_horas_por_categoria=porcentajes,
        areas_todos_los_registros=_areas(incluidos),
        areas_sin_proyectos=_areas(tuple(r for r in incluidos if r.categoria != "Proyecto")),
        areas_incidencias_solicitudes=_areas(tickets_candidato),
        hallazgos=tuple(hallazgos),
    )
