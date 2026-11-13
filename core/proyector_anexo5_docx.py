"""Proyecta las estadísticas Fase 1 a las cinco tablas y cuatro gráficos.

Las decisiones aplicadas aquí son las confirmadas por el usuario. El módulo da
formato a valores ya calculados; no vuelve a calcular estadísticas.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping, Sequence

from core.actualizador_graficos_docx import SerieGraficoDocx
from core.lector_informe_docx import _vacio
from core.modelo_informe_docx import DatosInforme, EstadisticasInforme, RegistroInforme


_MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)
_ETIQUETAS_ACTIVIDAD = {"Incidencia": "Incidencias", "Solicitud": "Solicitud"}
_ETIQUETA_AREA_VACIA = "(SIN ÁREA)"


@dataclass(frozen=True)
class ContenidoAnexo5:
    reemplazos_texto: Mapping[str, str]
    filas_por_tabla: Mapping[str, Sequence[Sequence[object]]]
    totales_por_tabla: Mapping[str, Sequence[object] | None]
    series_por_grafico: Mapping[int, SerieGraficoDocx]
    advertencias: tuple[str, ...]


class ErrorProyeccionAnexo5(ValueError):
    """Los datos seleccionados no permiten una proyección inequívoca."""


def _numero(valor: Decimal | int | float | None) -> str:
    if valor is None:
        return ""
    numero = valor if isinstance(valor, Decimal) else Decimal(str(valor))
    texto = format(numero, "f")
    return texto.rstrip("0").rstrip(".") if "." in texto else texto


def _porcentaje(valor: Decimal | int | float, total: Decimal) -> str:
    if total == 0:
        porcentaje = Decimal("0")
    else:
        porcentaje = Decimal(str(valor)) / total * Decimal("100")
    return f"{porcentaje.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def _categoria_visible(categoria: str | None, actividad_original: object) -> str:
    if categoria is not None:
        return categoria
    actividad = str(actividad_original).strip() if not _vacio(actividad_original) else ""
    return "Sin clasificar: " + (actividad or "(VACÍO)")


def _registro_detalle(registro: RegistroInforme) -> tuple[str, str, str, str, str, str]:
    fecha = registro.fecha.strftime("%d/%m/%Y") if registro.fecha is not None else ""
    area = _ETIQUETA_AREA_VACIA if _vacio(registro.area_original) else str(registro.area_original)
    horas = _numero(registro.horas) if registro.horas is not None else ""
    return (
        _categoria_visible(registro.categoria, registro.actividad_original),
        "" if _vacio(registro.detalle) else str(registro.detalle),
        fecha,
        "" if _vacio(registro.solicitante_original) else str(registro.solicitante_original),
        area,
        horas,
    )


def preparar_contenido_anexo5(
    datos: DatosInforme,
    estadisticas: EstadisticasInforme,
) -> ContenidoAnexo5:
    """Prepara valores Word conservando el orden fuente y las reglas aprobadas.

    Proyectos se agrupan por el valor exacto del campo, forman parte del detalle
    y se excluyen de la distribución por área. Se conservan filas sin área y
    categorías desconocidas con una etiqueta visible. Las horas faltantes
    quedan vacías por fila; los agregados muestran la suma de horas conocidas
    como parcial y generan advertencias. Las actividades sin proyecto se
    excluyen del resumen de proyectos y se advierten. Filas sin fecha se excluyen
    del período escogido y se advierten.
    """

    if estadisticas.hash_fuente_sha256 != datos.hash_fuente_sha256:
        raise ErrorProyeccionAnexo5("Las estadísticas no corresponden al Excel seleccionado.")
    if estadisticas.periodo is None or estadisticas.periodo_indeterminado:
        raise ErrorProyeccionAnexo5("Seleccione un único mes para el Anexo 5.")
    anio, mes = estadisticas.periodo
    periodo_texto = f"{_MESES[mes - 1]} {anio}"
    incluidos = tuple(
        registro for registro in datos.registros
        if registro.fecha is not None and (registro.fecha.year, registro.fecha.month) == estadisticas.periodo
    )
    if len(incluidos) != estadisticas.filas_analizadas:
        raise ErrorProyeccionAnexo5("Las filas analizadas no coinciden con el período preparado.")

    horas_por_categoria = dict(estadisticas.horas_conocidas_por_categoria)
    total_horas_conocidas = sum(horas_por_categoria.values(), Decimal("0"))
    total_horas_incompletas = sum(
        fila.filas_sin_horas + fila.filas_con_horas_invalidas
        for fila in estadisticas.filas_por_categoria
    )

    filas_tipo_horas = []
    cats_grafico_horas = []
    vals_grafico_horas = []
    for categoria in estadisticas.filas_por_categoria:
        horas = horas_por_categoria.get(categoria.categoria, Decimal("0"))
        etiqueta = categoria.categoria
        valor_visible = _numero(horas)
        if categoria.filas_sin_horas or categoria.filas_con_horas_invalidas:
            valor_visible += " (parcial)"
        filas_tipo_horas.append((etiqueta, valor_visible, _porcentaje(horas, total_horas_conocidas)))
        cats_grafico_horas.append(etiqueta)
        vals_grafico_horas.append(horas)

    grupos_proyecto = [
        (nombre, horas, cantidad, sin_horas)
        for nombre, horas, cantidad, sin_horas in estadisticas.horas_y_filas_por_proyecto_exacto
        if nombre is not None
    ]
    total_horas_proyectos = sum((horas for _, horas, _, _ in grupos_proyecto), Decimal("0"))
    filas_proyectos = []
    for nombre, horas, _, sin_horas in grupos_proyecto:
        etiqueta = nombre
        valor_visible = _numero(horas) + (" (parcial)" if sin_horas else "")
        filas_proyectos.append((etiqueta, valor_visible, _porcentaje(horas, total_horas_proyectos)))

    categorias_ticket = [
        fila for fila in estadisticas.filas_por_categoria
        if fila.categoria != "Proyecto"
    ]
    filas_tipo_actividad = []
    cats_grafico_actividad = []
    vals_grafico_actividad = []
    for fila in categorias_ticket:
        etiqueta = _ETIQUETAS_ACTIVIDAD.get(fila.categoria, fila.categoria)
        filas_tipo_actividad.append((etiqueta, str(fila.cantidad_registros), _porcentaje(fila.cantidad_registros, Decimal(estadisticas.filas_analizadas - estadisticas.actividades_proyecto))))
        cats_grafico_actividad.append(etiqueta)
        vals_grafico_actividad.append(fila.cantidad_registros)

    total_ticket_rows = sum(fila.cantidad_registros for fila in categorias_ticket)
    filas_areas = []
    cats_grafico_area = []
    vals_grafico_area = []
    total_areas = sum(fila.total_registros for fila in estadisticas.areas_sin_proyectos)
    for fila in estadisticas.areas_sin_proyectos:
        area = _ETIQUETA_AREA_VACIA if fila.area_original is None or not fila.area_original.strip() else fila.area_original
        filas_areas.append((area, str(fila.total_registros), _porcentaje(fila.total_registros, Decimal(total_areas))))
        cats_grafico_area.append(area)
        vals_grafico_area.append(fila.total_registros)

    filas_detalle = [_registro_detalle(registro) for registro in incluidos]
    proyectos_distintos = len(estadisticas.nombres_proyecto_distintos_exactos)

    horas_resumen = _numero(total_horas_conocidas)
    if total_horas_incompletas:
        horas_resumen += " horas (suma parcial; revisar horas vacías o inválidas)"
    else:
        horas_resumen += " horas"
    reemplazos = {
        "agosto 2026": periodo_texto,
        "479.5 horas": horas_resumen,
        "9 proyectos": f"{proyectos_distintos} proyectos",
        "129 tickets": f"{total_ticket_rows} tickets",
    }

    total_horas_visible = _numero(total_horas_conocidas)
    etiqueta_total_horas = "TOTAL PARCIAL" if total_horas_incompletas else "TOTAL"
    etiqueta_total_proyectos = "TOTAL GENERAL"
    if total_horas_incompletas or len(grupos_proyecto) < len(estadisticas.horas_y_filas_por_proyecto_exacto):
        etiqueta_total_proyectos = "TOTAL GENERAL (PARCIAL)"
    total_horas_proyectos_visible = _numero(total_horas_proyectos)
    if any(sin_horas for _, _, _, sin_horas in grupos_proyecto):
        total_horas_proyectos_visible += " (parcial)"

    advertencias: list[str] = []
    if estadisticas.filas_excluidas_por_periodo:
        filas_sin_fecha = sum(registro.estado_fecha != "valido" for registro in datos.registros)
        otras_fechas = max(0, estadisticas.filas_excluidas_por_periodo - filas_sin_fecha)
        if filas_sin_fecha:
            advertencias.append(f"Se excluyeron {filas_sin_fecha} filas sin fecha válida del período seleccionado.")
        if otras_fechas:
            advertencias.append(f"Se excluyeron {otras_fechas} filas pertenecientes a otros meses.")
    if total_horas_incompletas:
        advertencias.append(f"{total_horas_incompletas} filas tienen horas vacías o inválidas; los totales muestran solo horas conocidas.")
    proyectos_sin_nombre = sum(
        cantidad for nombre, _, cantidad, _ in estadisticas.horas_y_filas_por_proyecto_exacto
        if nombre is None
    )
    if proyectos_sin_nombre:
        advertencias.append(f"{proyectos_sin_nombre} actividades de proyecto sin nombre se excluyeron del resumen de proyectos.")
    if estadisticas.filas_sin_area:
        advertencias.append(f"{estadisticas.filas_sin_area} filas sin área aparecen como {_ETIQUETA_AREA_VACIA}.")
    desconocidas = sum(f.cantidad_registros for f in estadisticas.filas_por_categoria if f.categoria.startswith("Sin clasificar:"))
    if desconocidas:
        advertencias.append(f"{desconocidas} filas con categoría desconocida aparecen con una fila explícita.")

    return ContenidoAnexo5(
        reemplazos_texto=reemplazos,
        filas_por_tabla={
            "resumen_tipo_atencion": filas_tipo_horas,
            "proyectos": filas_proyectos,
            "tickets_tipo": filas_tipo_actividad,
            "areas": filas_areas,
            "detalle": filas_detalle,
        },
        totales_por_tabla={
            "resumen_tipo_atencion": (etiqueta_total_horas, total_horas_visible, "100%"),
            "proyectos": (etiqueta_total_proyectos, total_horas_proyectos_visible, "100%" if total_horas_proyectos else "0.0%"),
            "tickets_tipo": ("TOTAL GENERAL", str(total_ticket_rows), "100%" if total_ticket_rows else "0.0%"),
            "areas": ("TOTAL GENERAL", str(total_areas), "100%" if total_areas else "0.0%"),
        },
        series_por_grafico={
            1: SerieGraficoDocx(tuple(cats_grafico_horas), tuple(vals_grafico_horas)),
            2: SerieGraficoDocx(
                tuple(nombre for nombre, _, _, _ in grupos_proyecto),
                tuple(horas for _, horas, _, _ in grupos_proyecto),
            ),
            3: SerieGraficoDocx(tuple(cats_grafico_actividad), tuple(vals_grafico_actividad)),
            4: SerieGraficoDocx(tuple(cats_grafico_area), tuple(vals_grafico_area)),
        },
        advertencias=tuple(advertencias),
    )

