"""Catálogo de áreas del maestro y validación de nuevas áreas.

El catálogo no es un archivo aparte: se deriva del maestro
``USUARIO -> AREA_USUARIO``. Crear un área significa registrarla junto al
usuario que la utiliza, sin duplicar nombres con diferencias de mayúsculas,
tildes o espacios.

Antes de escribir se valida:

1. que el nombre no esté vacío;
2. que no exista un duplicado normalizado;
3. que no haya áreas parecidas que convenga reutilizar;
4. que el maestro pueda guardarse de forma transaccional.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from core import eventos, similitud
from core.archivos import ErrorEstructura
from core.normalizador import limpiar_area, normalizar_area

UMBRAL_AREA_SIMILAR = 0.80


@dataclass
class AreaSimilar:
    nombre: str
    similitud: float
    tipo: str

    def como_dict(self) -> dict[str, Any]:
        return {
            "nombre": self.nombre,
            "similitud": round(self.similitud, 3),
            "tipo": self.tipo,
        }


@dataclass
class ResultadoArea:
    valido: bool
    nombre: str = ""
    normalizado: str = ""
    existente: bool = False
    area_reutilizable: str = ""
    similares: list[AreaSimilar] = field(default_factory=list)
    requiere_confirmacion: bool = False
    mensaje: str = ""
    creada: bool = False
    asignacion: Any = None

    def como_dict(self) -> dict[str, Any]:
        return {
            "valido": self.valido,
            "nombre": self.nombre,
            "normalizado": self.normalizado,
            "existente": self.existente,
            "area_reutilizable": self.area_reutilizable,
            "similares": [item.como_dict() for item in self.similares],
            "requiere_confirmacion": self.requiere_confirmacion,
            "mensaje": self.mensaje,
            "creada": self.creada,
        }


def catalogo_desde_maestro(maestro: Any) -> list[str]:
    """Devuelve las áreas únicas del maestro conservando el texto visible."""

    try:
        df = maestro.como_dataframe()
    except Exception:  # noqa: BLE001 - un maestro vacío es válido
        return []

    areas: list[str] = []
    vistos: set[str] = set()
    for valor in df.get("AREA_USUARIO", []) if hasattr(df, "get") else []:
        area = limpiar_area(valor)
        if not area:
            continue
        clave = normalizar_area(area) or ""
        if clave in vistos:
            continue
        vistos.add(clave)
        areas.append(area)
    areas.sort(key=lambda item: normalizar_area(item) or "")
    return areas


def buscar_similares(
    nombre: Any,
    areas: Iterable[Any],
    *,
    umbral: float = UMBRAL_AREA_SIMILAR,
) -> list[AreaSimilar]:
    """Devuelve áreas parecidas, ordenadas de mayor a menor similitud."""

    clave = normalizar_area(nombre)
    if not clave:
        return []
    similares: list[AreaSimilar] = []
    vistos: set[str] = set()
    for valor in areas:
        area = limpiar_area(valor)
        if not area:
            continue
        otra = normalizar_area(area) or ""
        if not otra or otra in vistos:
            continue
        vistos.add(otra)
        if otra == clave:
            continue
        puntaje = similitud.comparar(clave, otra)
        if puntaje >= umbral:
            similares.append(
                AreaSimilar(
                    nombre=area,
                    similitud=puntaje,
                    tipo=similitud.clasificar(puntaje),
                )
            )
    similares.sort(key=lambda item: item.similitud, reverse=True)
    return similares


def validar_area(
    nombre: Any,
    *,
    areas: Sequence[Any] | None = None,
    maestro: Any = None,
    umbral: float = UMBRAL_AREA_SIMILAR,
) -> ResultadoArea:
    """Valida un nombre de área sin escribir nada."""

    area = limpiar_area(nombre)
    if not area:
        return ResultadoArea(
            valido=False,
            nombre="",
            mensaje="El nombre del área no puede estar vacío.",
        )

    if areas is None:
        areas = catalogo_desde_maestro(maestro)

    clave = normalizar_area(area) or ""
    for existente in areas:
        if normalizar_area(existente) == clave:
            return ResultadoArea(
                valido=True,
                nombre=area,
                normalizado=clave,
                existente=True,
                area_reutilizable=str(existente),
                mensaje=(
                    f"El área '{existente}' ya existe. Se reutilizará en lugar "
                    "de crear un duplicado."
                ),
            )

    similares = buscar_similares(area, areas, umbral=umbral)
    if similares:
        mejor = similares[0]
        return ResultadoArea(
            valido=True,
            nombre=area,
            normalizado=clave,
            area_reutilizable=mejor.nombre,
            similares=similares,
            requiere_confirmacion=True,
            mensaje=(
                f"Existe un área parecida: '{mejor.nombre}' "
                f"({mejor.similitud:.0%}). Revise si desea reutilizarla antes "
                "de registrar una nueva."
            ),
        )

    return ResultadoArea(
        valido=True,
        nombre=area,
        normalizado=clave,
        requiere_confirmacion=True,
        mensaje=f"El área '{area}' es nueva. Confirme para registrarla.",
    )


def validar_areas(
    nombres: Iterable[Any],
    *,
    areas: Sequence[Any] | None = None,
    maestro: Any = None,
    umbral: float = UMBRAL_AREA_SIMILAR,
) -> list[ResultadoArea]:
    """Valida una lista y también detecta duplicados dentro de la misma lista."""

    base = list(areas if areas is not None else catalogo_desde_maestro(maestro))
    resultados: list[ResultadoArea] = []
    for nombre in nombres:
        resultado = validar_area(nombre, areas=base, umbral=umbral)
        if resultado.valido and not resultado.existente:
            base.append(resultado.nombre)
        elif resultado.valido and resultado.existente:
            resultado.requiere_confirmacion = False
        resultados.append(resultado)
    return resultados


def crear_area_para_usuario(
    maestro: Any,
    usuario: Any,
    area: Any,
    *,
    confirmar: bool = False,
    reutilizar: bool = False,
    ruta_xlsx: Any = None,
    usuario_ejecutor: str = "",
    umbral: float = UMBRAL_AREA_SIMILAR,
    persistir: Any = None,
) -> ResultadoArea:
    """Valida y, con confirmación, asigna el área al usuario.

    No existe una tabla separada de áreas: registrar un área equivale a
    asignarla a un usuario del maestro. ``reutilizar=True`` acepta el área
    parecida sugerida en lugar de crear una variante nueva.
    """

    from core import asignaciones as svc_asignaciones

    nombre_usuario = limpiar_area(usuario)
    if not nombre_usuario:
        raise ErrorEstructura("Debe indicar el usuario al que se asignará el área.")

    resultado = validar_area(area, maestro=maestro, umbral=umbral)
    if not resultado.valido:
        return resultado

    reutilizada = False
    if resultado.existente:
        resultado.nombre = resultado.area_reutilizable or resultado.nombre
        resultado.requiere_confirmacion = True
        reutilizada = True
    elif reutilizar and resultado.similares:
        resultado.nombre = resultado.similares[0].nombre
        resultado.area_reutilizable = resultado.nombre
        resultado.requiere_confirmacion = True
        reutilizada = True

    if not confirmar:
        resultado.mensaje = (
            resultado.mensaje
            + " No se modificó el maestro: la operación requiere confirmación."
        )
        return resultado

    funcion = persistir or svc_asignaciones.asignar_una
    asignacion = funcion(
        maestro,
        nombre_usuario,
        resultado.nombre,
        guardar=True,
        ruta_xlsx=ruta_xlsx,
        usuario_ejecutor=usuario_ejecutor,
    )
    resultado.creada = not reutilizada
    resultado.asignacion = asignacion
    eventos.publicar_area_creada(
        resultado.nombre,
        usuario=nombre_usuario,
        origen="maestro",
        detalle="Área nueva" if resultado.creada else "Área reutilizada",
    )
    eventos.publicar(
        eventos.CATALOGO_AREAS_ACTUALIZADO,
        area=resultado.nombre,
        usuario=nombre_usuario,
        origen="maestro",
    )
    return resultado
