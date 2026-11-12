"""Acciones reales de la tabla «Registros que necesitan revisión».

La columna «ACCIONES» no debe mostrar botones ficticios. Este módulo concentra
qué acciones tienen sentido para cada registro y permite probar esa decisión
sin abrir Tkinter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

ACCION_CONSULTAR = "consultar_sql"
ACCION_ASIGNAR = "asignar_area"
ACCION_REVISAR = "revisar_coincidencias"
ACCION_DETALLES = "ver_detalles"
ACCION_VALIDAR = "validar"


@dataclass(frozen=True)
class AccionRevision:
    identificador: str
    etiqueta: str
    detalle: str
    requiere_seleccion: bool = True


ACCIONES_REVISION: tuple[AccionRevision, ...] = (
    AccionRevision(
        ACCION_CONSULTAR,
        "Consultar SQL",
        "Busca el solicitante en las empresas configuradas.",
    ),
    AccionRevision(
        ACCION_ASIGNAR,
        "Asignar área",
        "Registra manualmente el área del solicitante.",
    ),
    AccionRevision(
        ACCION_REVISAR,
        "Revisar coincidencias",
        "Abre la selección manual del empleado y la empresa.",
    ),
    AccionRevision(
        ACCION_DETALLES,
        "Ver detalles",
        "Muestra el estado, la selección y el historial del registro.",
    ),
    AccionRevision(
        ACCION_VALIDAR,
        "Validar registro",
        "Marca el registro como validado si ya tiene área.",
    ),
)


def _texto(valor: Any) -> str:
    return str(valor or "").strip()


def tiene_solicitante(registro: Mapping[str, Any]) -> bool:
    return bool(_texto(registro.get("usuario")))


def tiene_candidatos(registro: Mapping[str, Any]) -> bool:
    return bool(registro.get("candidatos"))


def tiene_area(registro: Mapping[str, Any]) -> bool:
    return bool(_texto(registro.get("area_actual")))


def ya_validado(registro: Mapping[str, Any]) -> bool:
    """Evita repetir la validación y duplicar el historial."""

    if not tiene_area(registro):
        return False
    if registro.get("validado"):
        return True
    return _texto(registro.get("estado")).upper() == "VALIDADO"


def accion_disponible(accion: str, registro: Mapping[str, Any]) -> bool:
    """Indica si una acción tiene una operación real para este registro."""

    if accion == ACCION_CONSULTAR:
        return tiene_solicitante(registro)
    if accion == ACCION_ASIGNAR:
        return tiene_solicitante(registro)
    if accion == ACCION_REVISAR:
        return tiene_solicitante(registro) and tiene_candidatos(registro)
    if accion == ACCION_DETALLES:
        return bool(registro)
    if accion == ACCION_VALIDAR:
        return (
            tiene_solicitante(registro)
            and tiene_area(registro)
            and not ya_validado(registro)
        )
    return False


def acciones_disponibles(registro: Mapping[str, Any]) -> list[AccionRevision]:
    return [
        accion
        for accion in ACCIONES_REVISION
        if accion_disponible(accion.identificador, registro)
    ]


def texto_acciones(registro: Mapping[str, Any]) -> str:
    """Texto de la celda de acciones (abre el menú real de la fila)."""

    disponibles = acciones_disponibles(registro)
    if not disponibles:
        return "—"
    return f"⋯ Acciones ({len(disponibles)})"


def resumen_validacion(registros: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Conteo para explicar cuántos registros se pueden validar y cuántos no."""

    total = 0
    validables = 0
    omitidos = 0
    ya_validados = 0
    for registro in registros:
        total += 1
        if not tiene_area(registro):
            omitidos += 1
        elif ya_validado(registro):
            ya_validados += 1
        else:
            validables += 1
    return {
        "total": total,
        "validables": validables,
        "omitidos": omitidos,
        "ya_validados": ya_validados,
    }
