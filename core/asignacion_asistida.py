"""Asignación asistida desde una coincidencia SQL o del maestro.

El flujo exige confirmación explícita y advierte cuando el empleado está
inactivo o cuando no hay información confiable sobre su estado. Asignar un
área solo escribe la correspondencia ``USUARIO -> ÁREA``: nunca modifica el
estado del trabajador ni reactiva su cuenta en SQL.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Mapping

from core import eventos
from core.normalizador import limpiar_area, limpiar_usuario

ESTADO_ACTIVO = "ACTIVO"
ESTADO_INACTIVO = "INACTIVO"
ESTADO_NO_DISPONIBLE = "NO DISPONIBLE"

# Códigos alineados con ``Empleado.inactivo``, ``database.repository._booleano``
# y ``core.coincidencias_vista``. La comparación no distingue tildes ni
# mayúsculas.
_ACTIVOS = {"1", "SI", "S", "TRUE", "ACTIVO", "ACTIVA", "A", "YES", "Y"}
_INACTIVOS = {
    "0",
    "NO",
    "N",
    "FALSE",
    "INACTIVO",
    "INACTIVA",
    "I",
    "INAC",
    "CESA",
    "CESADO",
    "CESADA",
    "BAJA",
    "RETIRADO",
    "RETIRADA",
}

# Solo se inspeccionan campos de estado del empleado; el campo ``estado`` del
# flujo de revisión no se usa para evitar confusiones.
_CAMPOS_ESTADO = (
    "activo",
    "ACTIVO",
    "estado_empleado",
    "ESTADO_EMPLEADO",
)


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
    # Se agrega al final para no alterar el orden posicional histórico.
    estado: str = ESTADO_NO_DISPONIBLE

    def __post_init__(self) -> None:
        """Mantiene coherentes ``estado``, ``activo`` e ``inactivo``.

        ``preparar_asignacion`` ya entrega los tres valores alineados; esta
        normalización protege a las construcciones manuales y a las llamadas
        históricas que solo indicaban ``activo`` o ``inactivo``.
        """

        if self.estado == ESTADO_INACTIVO:
            self.activo = False
            self.inactivo = True
            return
        if self.estado == ESTADO_ACTIVO:
            self.activo = True
            self.inactivo = False
            return
        if self.inactivo or self.activo is False:
            self.estado = ESTADO_INACTIVO
            self.activo = False
            self.inactivo = True
        elif self.activo is True:
            self.estado = ESTADO_ACTIVO
            self.inactivo = False
        else:
            self.estado = ESTADO_NO_DISPONIBLE
            self.activo = None
            self.inactivo = False

    @property
    def no_disponible(self) -> bool:
        """Indica que no hay información confiable sobre el estado laboral."""

        return self.estado == ESTADO_NO_DISPONIBLE

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
            "estado": self.estado,
            "no_disponible": self.no_disponible,
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


def _clave_estado(valor: Any) -> str:
    """Normaliza un valor de estado sin tildes ni espacios repetidos."""

    if valor is None:
        return ""
    texto = unicodedata.normalize("NFKD", str(valor).strip().upper())
    texto = "".join(
        caracter for caracter in texto if not unicodedata.combining(caracter)
    )
    return " ".join(texto.split())


def _estado_desde_valor(valor: Any) -> str:
    """Traduce un único valor a ACTIVO, INACTIVO o NO DISPONIBLE."""

    if isinstance(valor, bool):
        return ESTADO_ACTIVO if valor else ESTADO_INACTIVO
    texto = _clave_estado(valor)
    if not texto:
        return ESTADO_NO_DISPONIBLE
    numero = None
    try:
        numero = int(float(texto))
    except (TypeError, ValueError):
        numero = None
    if numero == 1:
        return ESTADO_ACTIVO
    if numero == 0:
        return ESTADO_INACTIVO
    if texto in _ACTIVOS:
        return ESTADO_ACTIVO
    if texto in _INACTIVOS:
        return ESTADO_INACTIVO
    return ESTADO_NO_DISPONIBLE


def _estado_candidato(candidato: Mapping[str, Any]) -> str:
    """Estado solo si todos los campos disponibles coinciden.

    Sin información, con datos contradictorios o con la marca
    ``estado_contradictorio`` se devuelve ``NO DISPONIBLE``: nunca se asume
    ACTIVO ni INACTIVO por defecto.
    """

    if candidato.get("estado_contradictorio"):
        return ESTADO_NO_DISPONIBLE

    estados = {
        _estado_desde_valor(candidato.get(campo))
        for campo in _CAMPOS_ESTADO
    }
    conocidos = estados - {ESTADO_NO_DISPONIBLE}
    if not conocidos:
        return ESTADO_NO_DISPONIBLE
    if len(conocidos) == 1:
        return next(iter(conocidos))
    return ESTADO_NO_DISPONIBLE


def _activo(valor: Any) -> bool | None:
    """Compatibilidad histórica: ``True``, ``False`` o ``None``."""

    estado = _estado_desde_valor(valor)
    if estado == ESTADO_ACTIVO:
        return True
    if estado == ESTADO_INACTIVO:
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
    estado = _estado_candidato(datos)
    activo = (
        True
        if estado == ESTADO_ACTIVO
        else False
        if estado == ESTADO_INACTIVO
        else None
    )
    inactivo = estado == ESTADO_INACTIVO
    no_disponible = estado == ESTADO_NO_DISPONIBLE

    mensaje = f"Se asignará '{area_final}' a '{texto}'."
    if inactivo:
        mensaje = (
            f"El usuario encontrado '{nombre or texto}' figura como inactivo. "
            "¿Está seguro de asignar un área a un usuario inactivo? "
            "La asignación no reactivará su cuenta."
        )
    elif not area_final:
        mensaje = "La coincidencia seleccionada no tiene un área asignable."
    elif no_disponible:
        mensaje = (
            f"No hay información confiable sobre el estado de "
            f"'{nombre or texto}'. Se asignará '{area_final}' sin asumir ni "
            "modificar su estado."
        )

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
        estado=estado,
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
    if (
        preparacion.inactivo or preparacion.estado == ESTADO_INACTIVO
    ) and not permitir_inactivo:
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
            "estado": preparacion.estado,
        },
    )
    return ResultadoAsignacionAsistida(
        ejecutada=True,
        preparacion=preparacion,
        resultado=resultado,
        mensaje=f"Asignación guardada: {preparacion.usuario} → {preparacion.area}",
    )
