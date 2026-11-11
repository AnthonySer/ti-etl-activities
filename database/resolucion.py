"""Resolución de usuarios de tickets contra empleados corporativos.

Implementa los cinco casos de la especificación:

* ``A`` coincidencia única con departamento: se asigna automáticamente.
* ``B`` varias coincidencias: nunca se elige sola; se envían a revisión.
* ``C`` usuario inexistente: queda pendiente con sugerencias aproximadas.
* ``D`` empleado sin departamento: no se inventa el área.
* ``E`` empleado inactivo: se muestra su estado y no se asigna en silencio.

Además protege las asignaciones manuales: si el área del maestro local o de la
procedencia es ``MANUAL`` y difiere del departamento corporativo, se reporta un
conflicto en lugar de sobrescribirla.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from core import procedencia
from core.normalizador import limpiar_area
from database.normalizador import (
    claves_empleado,
    indice_por_nombre,
    normalizar_nombre,
)
from database.repository import Empleado

CASO_A = "A"
CASO_B = "B"
CASO_C = "C"
CASO_D = "D"
CASO_E = "E"

ESTADO_VALIDADO = "Validado"
ESTADO_AMBIGUO = "Ambiguo"
ESTADO_SIN_COINCIDENCIAS = "Sin coincidencias"
ESTADO_SIN_DEPARTAMENTO = "Sin departamento"
ESTADO_INACTIVO = "Empleado inactivo"
ESTADO_CONFLICTO = "Conflicto con maestro"
ESTADO_MANUAL_PROTEGIDO = "Asignación manual protegida"
ESTADO_AREA_VACIA = "Área vacía"

ESTADOS_REVISION = (
    ESTADO_AMBIGUO,
    ESTADO_SIN_COINCIDENCIAS,
    ESTADO_SIN_DEPARTAMENTO,
    ESTADO_INACTIVO,
    ESTADO_CONFLICTO,
    ESTADO_MANUAL_PROTEGIDO,
    ESTADO_AREA_VACIA,
)


@dataclass
class ResultadoResolucion:
    usuario: str
    clave: str
    caso: str
    estado: str
    area: str = ""
    empleado: Empleado | None = None
    candidatos: list[Empleado] = field(default_factory=list)
    motivo: str = ""
    conflicto: bool = False
    area_anterior: str = ""
    origen_anterior: str = ""
    sugerencias: list[dict[str, Any]] = field(default_factory=list)
    filas: list[Any] = field(default_factory=list)

    @property
    def asignable(self) -> bool:
        return bool(self.area) and not self.conflicto

    @property
    def requiere_revision(self) -> bool:
        return self.estado in ESTADOS_REVISION or self.conflicto

    def empleado_dict(self) -> dict[str, Any]:
        return self.empleado.como_dict() if self.empleado else {}

    def candidatos_dict(self) -> list[dict[str, Any]]:
        return [candidato.como_dict() for candidato in self.candidatos]

    def como_dict(self) -> dict[str, Any]:
        return {
            "usuario": self.usuario,
            "clave": self.clave,
            "caso": self.caso,
            "estado": self.estado,
            "area": self.area,
            "empleado": self.empleado_dict(),
            "candidatos": self.candidatos_dict(),
            "motivo": self.motivo,
            "conflicto": self.conflicto,
            "area_anterior": self.area_anterior,
            "origen_anterior": self.origen_anterior,
            "sugerencias": self.sugerencias,
            "filas": list(self.filas),
            "asignable": self.asignable,
            "requiere_revision": self.requiere_revision,
        }


def _empleado_unico(candidatos: Iterable[Empleado]) -> list[Empleado]:
    vistos: set[tuple[str, str]] = set()
    unicos: list[Empleado] = []
    for empleado in candidatos:
        huella = (empleado.empresa, empleado.codigo)
        if huella in vistos:
            continue
        vistos.add(huella)
        unicos.append(empleado)
    return unicos


class ResolverAreas:
    """Resuelve nombres de solicitantes usando una lista de empleados."""

    def __init__(
        self,
        empleados: Sequence[Empleado],
        *,
        maestro: Any = None,
        cargar_procedencia: Callable[[], dict[str, Any]] | None = None,
        umbral_sugerencia: float = 0.82,
        limite_sugerencias: int = 5,
    ) -> None:
        self.empleados = list(empleados)
        self._indice = indice_por_nombre(self.empleados)
        self.maestro = maestro
        self._cargar_procedencia = cargar_procedencia or procedencia.cargar_procedencia
        self.umbral_sugerencia = umbral_sugerencia
        self.limite_sugerencias = limite_sugerencias
        self._procedencia = self._cargar_procedencia()["registros"]

    # ------------------------------------------------------------------
    def _area_maestro(self, usuario: str) -> str:
        if self.maestro is None:
            return ""
        try:
            return limpiar_area(self.maestro.buscar(usuario)) or ""
        except Exception:  # noqa: BLE001 - el maestro es opcional
            return ""

    def _area_procedencia(self, usuario: str) -> tuple[str, str]:
        registro = self._procedencia.get(normalizar_nombre(usuario)) or {}
        return (
            limpiar_area(registro.get("area")) or "",
            str(registro.get("origen") or ""),
        )

    def _sugerencias(self, clave: str) -> list[dict[str, Any]]:
        if not clave:
            return []
        resultados: list[tuple[float, Empleado]] = []
        for empleado in self.empleados:
            for otra in claves_empleado(
                nombre=empleado.nombre,
                nombre_pila=empleado.nombre_pila,
                primer_apellido=empleado.primer_apellido,
                segundo_apellido=empleado.segundo_apellido,
            ):
                if not otra:
                    continue
                ratio = difflib.SequenceMatcher(None, clave, otra).ratio()
                if clave in otra or otra in clave:
                    ratio = max(ratio, 0.9)
                if ratio >= self.umbral_sugerencia:
                    resultados.append((ratio, empleado))
                    break
        resultados.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "empresa": empleado.empresa,
                "empleado": empleado.codigo,
                "nombre": empleado.nombre_completo,
                "departamento": empleado.departamento,
                "similitud": round(ratio, 3),
            }
            for ratio, empleado in resultados[: self.limite_sugerencias]
        ]

    def _conflicto(
        self,
        resultado: ResultadoResolucion,
        area_sql: str,
        usuario: str,
    ) -> None:
        area_maestro = self._area_maestro(usuario)
        area_proc, origen_proc = self._area_procedencia(usuario)

        anterior = area_maestro or area_proc
        origen = procedencia.MANUAL if area_maestro else origen_proc
        if not anterior or not area_sql:
            return
        from core.normalizador import normalizar_area

        if normalizar_area(anterior) == normalizar_area(area_sql):
            resultado.area_anterior = anterior
            resultado.origen_anterior = origen
            return

        resultado.area_anterior = anterior
        resultado.origen_anterior = origen
        if origen == procedencia.MANUAL:
            resultado.estado = ESTADO_MANUAL_PROTEGIDO
        else:
            resultado.estado = ESTADO_CONFLICTO
        resultado.conflicto = True
        resultado.motivo = (
            f"El área local '{anterior}' ({origen or 'sin origen'}) difiere del "
            f"departamento corporativo '{area_sql}'. Se requiere revisión."
        )

    # ------------------------------------------------------------------
    def _resolver_empleado(
        self,
        texto: str,
        clave: str,
        empleado: Empleado,
    ) -> ResultadoResolucion:
        """Resuelve un empleado concreto, sin volver a elegir entre candidatos."""

        if not empleado.tiene_departamento:
            return ResultadoResolucion(
                usuario=texto,
                clave=clave,
                caso=CASO_D,
                estado=ESTADO_SIN_DEPARTAMENTO,
                empleado=empleado,
                motivo=(
                    "El empleado no tiene departamento en la base corporativa. "
                    "No se inventará un área."
                ),
            )

        if empleado.inactivo:
            return ResultadoResolucion(
                usuario=texto,
                clave=clave,
                caso=CASO_E,
                estado=ESTADO_INACTIVO,
                empleado=empleado,
                area=empleado.departamento,
                motivo=(
                    "El empleado figura como inactivo"
                    + (f" ({empleado.estado_empleado})" if empleado.estado_empleado else "")
                    + ". Revise su vigencia antes de asignar el área."
                ),
            )

        resultado = ResultadoResolucion(
            usuario=texto,
            clave=clave,
            caso=CASO_A,
            estado=ESTADO_VALIDADO,
            area=empleado.departamento,
            empleado=empleado,
            motivo="Coincidencia única con departamento corporativo.",
        )
        self._conflicto(resultado, empleado.departamento, texto)
        return resultado

    def resolver(self, usuario: Any) -> ResultadoResolucion:
        texto = str(usuario or "").strip()
        clave = normalizar_nombre(texto)
        if not clave:
            return ResultadoResolucion(
                usuario=texto,
                clave="",
                caso=CASO_C,
                estado=ESTADO_AREA_VACIA,
                motivo="El registro no tiene un solicitante válido.",
            )

        candidatos = _empleado_unico(self._indice.get(clave, []))
        if not candidatos:
            return ResultadoResolucion(
                usuario=texto,
                clave=clave,
                caso=CASO_C,
                estado=ESTADO_SIN_COINCIDENCIAS,
                motivo="No se encontró un empleado con ese nombre en la caché corporativa.",
                sugerencias=self._sugerencias(clave),
            )

        if len(candidatos) > 1:
            return ResultadoResolucion(
                usuario=texto,
                clave=clave,
                caso=CASO_B,
                estado=ESTADO_AMBIGUO,
                candidatos=candidatos,
                motivo=(
                    f"Se encontraron {len(candidatos)} empleados con el mismo "
                    "nombre. Seleccione la coincidencia correcta."
                ),
            )

        return self._resolver_empleado(texto, clave, candidatos[0])

    def resolver_candidato(
        self,
        usuario: Any,
        candidato: Empleado | dict[str, Any],
    ) -> ResultadoResolucion:
        """Resuelve el área usando únicamente el registro elegido por el usuario.

        No consulta otros candidatos ni reutiliza departamentos de otras
        empresas. La protección de asignaciones manuales del maestro se
        mantiene mediante ``_conflicto``.
        """

        texto = str(usuario or "").strip()
        clave = normalizar_nombre(texto)
        empleado = (
            candidato
            if isinstance(candidato, Empleado)
            else Empleado.desde_candidato(candidato)
        )
        return self._resolver_empleado(texto, clave or empleado.clave, empleado)

    def resolver_lote(
        self,
        usuarios: Iterable[Any],
    ) -> dict[str, ResultadoResolucion]:
        resultados: dict[str, ResultadoResolucion] = {}
        vistos: set[str] = set()
        for usuario in usuarios:
            texto = str(usuario or "").strip()
            clave = normalizar_nombre(texto)
            if not clave or clave in vistos:
                continue
            vistos.add(clave)
            resultados[clave] = self.resolver(texto)
        return resultados


def resolver_desde_cache(
    usuarios: Iterable[Any],
    *,
    maestro: Any = None,
    empleados: Sequence[Empleado] | None = None,
) -> dict[str, ResultadoResolucion]:
    """Atajo que carga la caché local y resuelve una lista de nombres."""

    if empleados is None:
        from database.synchronizer import cargar_empleados

        empleados = cargar_empleados()
    resolver = ResolverAreas(empleados, maestro=maestro)
    return resolver.resolver_lote(usuarios)


def registrar_asignacion(
    resultado: ResultadoResolucion,
    *,
    id_ejecucion: str = "",
    origen: str = procedencia.SQL_SERVER,
    nota: str = "",
) -> bool:
    """Persiste la procedencia cuando la resolución fue segura."""

    if not resultado.asignable or not resultado.area:
        return False
    registro = procedencia.registrar(
        resultado.usuario,
        resultado.area,
        origen,
        id_ejecucion=id_ejecucion,
        nota=nota or resultado.motivo,
    )
    return bool(registro)


def resumen_resoluciones(
    resultados: Iterable[ResultadoResolucion],
) -> dict[str, int]:
    conteo = {estado: 0 for estado in ESTADOS_REVISION}
    conteo[ESTADO_VALIDADO] = 0
    for resultado in resultados:
        conteo[resultado.estado] = conteo.get(resultado.estado, 0) + 1
    conteo["TOTAL"] = sum(
        valor for clave, valor in conteo.items() if clave != "TOTAL"
    )
    return conteo
