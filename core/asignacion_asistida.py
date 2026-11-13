"""Asignación asistida desde una coincidencia SQL o del maestro.

El flujo exige confirmación explícita y advierte cuando el empleado está
inactivo. Asignar un área solo escribe la correspondencia ``USUARIO -> ÁREA``:
nunca modifica el estado del trabajador ni reactiva su cuenta en SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from core import eventos
from core.normalizador import limpiar_area, limpiar_usuario


@dataclass
class PreparacionAsignacion:
    usuario: str
    candidato: dict[str, Any] = field(default_factory=dict)
    empresa: str = ""
    nombre_encontrado: str = ""
    area: str = ""
    activo: bool | None = None
    inactivo: bool = False
    requiere_confirmacion: bool = True
    mensaje: str = ""

    def como_dict(self) -> dict[str, Any]:
        return {
            "usuario": self.usuario,
            "candidato": dict(self.candidato),
            "empresa": self.empresa,
            "nombre_encontrado": self.nombre_encontrado,
            "area": self.area,
            "activo": self.activo,
            "inactivo": self.inactivo,
            "requiere_confirmacion": self.requiere_confirmacion,
            "mensaje": self.mensaje,
        }


@dataclass
class ResultadoAsignacionAsistida:
    ejecutada: bool
    preparacion: PreparacionAsignacion
    resultado: Any = None
    mensaje: str = ""

    def como_dict(self) -> dict[str, Any]:
        return {
            "ejecutada": self.ejecutada,
            "preparacion": self.preparacion.como_dict(),
            "resultado": self.resultado,
            "mensaje": self.mensaje,
        }


def _activo(valor: Any) -> bool | None:
    if isinstance(valor, bool):
        return valor
    if valor in (None, ""):
        return None
    texto = str(valor).strip().upper()
    if texto in {"1", "SI", "SÍ", "TRUE", "ACTIVO", "A"}:
        return True
    if texto in {"0", "NO", "FALSE", "INACTIVO", "I"}:
        return False
    return None


def preparar_asignacion(
    usuario: Any,
    candidato: Mapping[str, Any] | None = None,
    *,
    area: Any = "",
) -> PreparacionAsignacion:
    """Prepara el resumen que debe confirmar la persona antes de escribir."""

    texto = limpiar_usuario(usuario) or ""
    datos = dict(candidato or {})
    nombre = str(
        datos.get("nombre")
        or datos.get("NOMBRE")
        or datos.get("usuario")
        or datos.get("USUARIO")
        or ""
    ).strip()
    empresa = str(datos.get("empresa") or datos.get("EMPRESA") or "").strip()
    area_final = limpiar_area(
        area
        or datos.get("area")
        or datos.get("AREA")
        or datos.get("departamento")
        or datos.get("DEPARTAMENTO")
        or ""
    ) or ""
    activo = _activo(datos.get("activo", datos.get("ACTIVO")))
    inactivo = activo is False

    mensaje = f"Se asignará '{area_final}' a '{texto}'."
    if inactivo:
        mensaje = (
            f"El usuario encontrado '{nombre or texto}' figura como inactivo. "
            "¿Está seguro de asignar un área a un usuario inactivo? "
            "La asignación no reactivará su cuenta."
        )
    elif not area_final:
        mensaje = "La coincidencia seleccionada no tiene un área asignable."

    return PreparacionAsignacion(
        usuario=texto,
        candidato=datos,
        empresa=empresa,
        nombre_encontrado=nombre,
        area=area_final,
        activo=activo,
        inactivo=inactivo,
        requiere_confirmacion=True,
        mensaje=mensaje,
    )


def ejecutar_asignacion(
    maestro: Any,
    preparacion: PreparacionAsignacion,
    *,
    confirmar: bool = False,
    permitir_inactivo: bool = False,
    ruta_xlsx: Any = None,
    usuario_ejecutor: str = "",
    persistir: Any = None,
) -> ResultadoAsignacionAsistida:
    """Ejecuta la asignación solo con confirmación explícita.

    Si el candidato está inactivo se exige además ``permitir_inactivo=True``.
    El estado del empleado nunca se modifica.
    """

    if not preparacion.usuario:
        return ResultadoAsignacionAsistida(
            ejecutada=False,
            preparacion=preparacion,
            mensaje="No hay un usuario válido para asignar.",
        )
    if not preparacion.area:
        return ResultadoAsignacionAsistida(
            ejecutada=False,
            preparacion=preparacion,
            mensaje="No hay un área válida para asignar.",
        )
    if not confirmar:
        return ResultadoAsignacionAsistida(
            ejecutada=False,
            preparacion=preparacion,
            mensaje="La asignación requiere confirmación del usuario.",
        )
    if preparacion.inactivo and not permitir_inactivo:
        return ResultadoAsignacionAsistida(
            ejecutada=False,
            preparacion=preparacion,
            mensaje=(
                "El usuario está inactivo. Confirme expresamente que desea "
                "asignarle un área sin reactivar su cuenta."
            ),
        )

    funcion = persistir
    if funcion is None:
        from core import asignaciones as svc_asignaciones

        funcion = svc_asignaciones.asignar_una

    resultado = funcion(
        maestro,
        preparacion.usuario,
        preparacion.area,
        guardar=True,
        ruta_xlsx=ruta_xlsx,
        usuario_ejecutor=usuario_ejecutor,
    )
    eventos.publicar_asignacion(
        preparacion.usuario,
        preparacion.area,
        origen=str(
            preparacion.candidato.get("origen")
            or preparacion.candidato.get("EMPRESA")
            or ""
        ),
        detalle="Asignación desde coincidencia",
        datos={
            "empresa": preparacion.empresa,
            "empleado": preparacion.candidato.get("empleado")
            or preparacion.candidato.get("EMPLEADO")
            or "",
            "inactivo": preparacion.inactivo,
        },
    )
    return ResultadoAsignacionAsistida(
        ejecutada=True,
        preparacion=preparacion,
        resultado=resultado,
        mensaje=f"Asignación guardada: {preparacion.usuario} → {preparacion.area}",
    )
