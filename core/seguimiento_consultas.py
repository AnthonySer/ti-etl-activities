"""Estados y resumen del seguimiento de consultas SQL por empresa.

Módulo puro, sin Tkinter ni SQL: la ventana de seguimiento y el repositorio
comparten los mismos estados para no mostrar una empresa con error como
«completada».
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

ESTADO_PENDIENTE = "PENDIENTE"
ESTADO_CONSULTANDO = "CONSULTANDO"
ESTADO_COMPLETADA = "COMPLETADA"
ESTADO_SIN_RESULTADOS = "SIN RESULTADOS"
ESTADO_SIN_ESQUEMA = "SIN ESQUEMA COMPATIBLE"
ESTADO_ERROR_CONEXION = "ERROR DE CONEXIÓN"
ESTADO_ERROR_PERMISOS = "ERROR DE PERMISOS"
ESTADO_ERROR_CONSULTA = "ERROR DE CONSULTA"
ESTADO_CANCELADA = "CANCELADA"

ESTADOS_FINALES = frozenset(
    {
        ESTADO_COMPLETADA,
        ESTADO_SIN_RESULTADOS,
        ESTADO_SIN_ESQUEMA,
        ESTADO_ERROR_CONEXION,
        ESTADO_ERROR_PERMISOS,
        ESTADO_ERROR_CONSULTA,
        ESTADO_CANCELADA,
    }
)

ESTADOS_ERROR = frozenset(
    {
        ESTADO_ERROR_CONEXION,
        ESTADO_ERROR_PERMISOS,
        ESTADO_ERROR_CONSULTA,
    }
)

ETIQUETAS = {
    ESTADO_PENDIENTE: "Pendiente",
    ESTADO_CONSULTANDO: "Consultando",
    ESTADO_COMPLETADA: "Completada",
    ESTADO_SIN_RESULTADOS: "Sin resultados",
    ESTADO_SIN_ESQUEMA: "Sin esquema compatible",
    ESTADO_ERROR_CONEXION: "Error de conexión",
    ESTADO_ERROR_PERMISOS: "Error de permisos",
    ESTADO_ERROR_CONSULTA: "Error de consulta",
    ESTADO_CANCELADA: "Cancelada",
}


def etiqueta(estado: str) -> str:
    return ETIQUETAS.get(str(estado or "").upper(), str(estado or ""))


def estado_desde_error(categoria: str) -> str:
    """Traduce la categoría técnica del repositorio a un estado visual."""

    categoria = str(categoria or "").lower()
    if categoria in {"servidor", "conexion", "timeout", "autenticacion"}:
        return ESTADO_ERROR_CONEXION
    if categoria in {"permisos", "autorizacion"}:
        return ESTADO_ERROR_PERMISOS
    if categoria in {"tabla", "campos_insuficientes", "esquema"}:
        return ESTADO_SIN_ESQUEMA
    return ESTADO_ERROR_CONSULTA


def nuevo_evento(
    empresa: str,
    estado: str,
    *,
    resultados: int | None = None,
    duracion: float | None = None,
    detalle: str = "",
) -> dict[str, Any]:
    return {
        "empresa": str(empresa or ""),
        "estado": estado,
        "resultados": resultados,
        "duracion": duracion,
        "detalle": str(detalle or ""),
    }


@dataclass
class ResumenSeguimiento:
    """Acumula eventos por empresa y produce conteos reales."""

    total_empresas: int = 0
    eventos: dict[str, dict[str, Any]] = field(default_factory=dict)
    duracion_total: float = 0.0
    cancelada: bool = False
    mensaje: str = ""

    def registrar(self, evento: Mapping[str, Any]) -> dict[str, Any]:
        empresa = str(evento.get("empresa") or "")
        if not empresa:
            return dict(evento)
        anterior = self.eventos.get(empresa, {})
        combinado = dict(anterior)
        for clave, valor in evento.items():
            if clave in {"resultados", "duracion"} and valor is None:
                continue
            if clave == "detalle" and not str(valor or "").strip():
                continue
            combinado[clave] = valor
        self.eventos[empresa] = combinado
        if combinado.get("estado") == ESTADO_CANCELADA:
            self.cancelada = True
        return combinado

    def registrar_muchos(self, eventos: Iterable[Mapping[str, Any]]) -> None:
        for evento in eventos:
            self.registrar(evento)

    @property
    def procesadas(self) -> int:
        return sum(
            1
            for evento in self.eventos.values()
            if str(evento.get("estado")) in ESTADOS_FINALES
            and str(evento.get("estado")) != ESTADO_CANCELADA
        )

    @property
    def canceladas(self) -> int:
        return sum(
            1
            for evento in self.eventos.values()
            if str(evento.get("estado")) == ESTADO_CANCELADA
        )

    @property
    def pendientes(self) -> int:
        if not self.total_empresas:
            return 0
        return max(
            0,
            self.total_empresas
            - self.procesadas
            - self.canceladas,
        )

    @property
    def con_resultados(self) -> int:
        return sum(
            1
            for evento in self.eventos.values()
            if str(evento.get("estado")) == ESTADO_COMPLETADA
        )

    @property
    def sin_resultados(self) -> int:
        return sum(
            1
            for evento in self.eventos.values()
            if str(evento.get("estado")) == ESTADO_SIN_RESULTADOS
        )

    @property
    def sin_esquema(self) -> int:
        return sum(
            1
            for evento in self.eventos.values()
            if str(evento.get("estado")) == ESTADO_SIN_ESQUEMA
        )

    @property
    def errores(self) -> int:
        return sum(
            1
            for evento in self.eventos.values()
            if str(evento.get("estado")) in ESTADOS_ERROR
        )

    @property
    def empleados(self) -> int:
        total = 0
        for evento in self.eventos.values():
            try:
                total += int(evento.get("resultados") or 0)
            except (TypeError, ValueError):
                continue
        return total

    def porcentaje(self) -> int:
        if not self.total_empresas:
            return 0
        terminadas = self.procesadas + self.canceladas
        return min(100, int(round(terminadas * 100 / self.total_empresas)))

    def como_dict(self) -> dict[str, Any]:
        return {
            "total_empresas": self.total_empresas,
            "procesadas": self.procesadas,
            "pendientes": self.pendientes,
            "con_resultados": self.con_resultados,
            "sin_resultados": self.sin_resultados,
            "sin_esquema": self.sin_esquema,
            "errores": self.errores,
            "canceladas": self.canceladas,
            "empleados": self.empleados,
            "duracion_total": round(self.duracion_total, 2),
            "porcentaje": self.porcentaje(),
            "cancelada": self.cancelada,
            "mensaje": self.mensaje,
        }
