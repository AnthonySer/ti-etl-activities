"""Repositorio de empleados y catálogos corporativos.

Cada empresa (esquema) se consulta de forma independiente: si una falla, las
demás continúan y el error se informa sin bloquear el procesamiento.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from config.sqlserver import EMPRESAS, ConfiguracionSQL
from database import queries
from database.connection import (
    ErrorConexionSQL,
    conectar,
    ejecutar_consulta,
)
from database.normalizador import normalizar_nombre

LOGGER = logging.getLogger("automatizador")


def _texto(valor: Any) -> str:
    if valor is None:
        return ""
    texto = str(valor).strip()
    return "" if texto.lower() in {"nan", "none", "null"} else texto


def _booleano(valor: Any) -> bool | None:
    if valor is None:
        return None
    texto = str(valor).strip().lower()
    if texto in {"1", "true", "si", "sí", "s", "activo", "yes", "y"}:
        return True
    if texto in {"0", "false", "no", "n", "inactivo", "cesado", "baja"}:
        return False
    return None


@dataclass
class Empleado:
    empresa: str
    codigo: str
    nombre: str = ""
    primer_apellido: str = ""
    segundo_apellido: str = ""
    nombre_pila: str = ""
    departamento_codigo: str = ""
    departamento: str = ""
    puesto_codigo: str = ""
    puesto: str = ""
    estado_empleado: str = ""
    activo: bool | None = None
    email: str = ""
    correo_corp: str = ""
    fuente: str = ""

    @property
    def nombre_completo(self) -> str:
        if self.nombre:
            return self.nombre
        return " ".join(
            parte
            for parte in (
                self.nombre_pila,
                self.primer_apellido,
                self.segundo_apellido,
            )
            if parte
        )

    @property
    def clave(self) -> str:
        return normalizar_nombre(self.nombre_completo)

    @property
    def inactivo(self) -> bool:
        if self.activo is False:
            return True
        estado = normalizar_nombre(self.estado_empleado)
        return estado in {"INACTIVO", "CESADO", "BAJA", "RETIRADO"}

    @property
    def tiene_departamento(self) -> bool:
        return bool(self.departamento.strip())

    def como_dict(self) -> dict[str, Any]:
        return {
            "EMPRESA": self.empresa,
            "EMPLEADO": self.codigo,
            "NOMBRE": self.nombre,
            "PRIMER_APELLIDO": self.primer_apellido,
            "SEGUNDO_APELLIDO": self.segundo_apellido,
            "NOMBRE_PILA": self.nombre_pila,
            "DEPARTAMENTO_CODIGO": self.departamento_codigo,
            "DEPARTAMENTO": self.departamento,
            "PUESTO_CODIGO": self.puesto_codigo,
            "PUESTO": self.puesto,
            "ESTADO_EMPLEADO": self.estado_empleado,
            "ACTIVO": "" if self.activo is None else ("1" if self.activo else "0"),
            "E_MAIL": self.email,
            "U_CORREO_CORP": self.correo_corp,
            "FUENTE": self.fuente,
        }

    @classmethod
    def desde_fila(cls, empresa: str, fila: dict[str, Any]) -> "Empleado":
        return cls(
            empresa=empresa,
            codigo=_texto(fila.get("EMPLEADO")),
            nombre=_texto(fila.get("NOMBRE")),
            primer_apellido=_texto(fila.get("PRIMER_APELLIDO")),
            segundo_apellido=_texto(fila.get("SEGUNDO_APELLIDO")),
            nombre_pila=_texto(fila.get("NOMBRE_PILA")),
            departamento_codigo=_texto(fila.get("DEPARTAMENTO_CODIGO")),
            departamento=_texto(fila.get("DEPARTAMENTO_DESCRIPCION"))
            or _texto(fila.get("DEPARTAMENTO")),
            puesto_codigo=_texto(fila.get("PUESTO_CODIGO")),
            puesto=_texto(fila.get("PUESTO_DESCRIPCION"))
            or _texto(fila.get("PUESTO")),
            estado_empleado=_texto(fila.get("ESTADO_EMPLEADO")),
            activo=_booleano(fila.get("ACTIVO")),
            email=_texto(fila.get("E_MAIL")),
            correo_corp=_texto(fila.get("U_CORREO_CORP")),
            fuente=_texto(fila.get("FUENTE")),
        )


@dataclass
class ResultadoConsulta:
    empleados: list[Empleado] = field(default_factory=list)
    errores: list[dict[str, str]] = field(default_factory=list)
    empresas_consultadas: list[str] = field(default_factory=list)

    @property
    def empresas_con_error(self) -> list[str]:
        return [error["empresa"] for error in self.errores]


class RepositorioEmpleados:
    """Acceso de solo lectura a EMPLEADO, DEPARTAMENTO y PUESTO."""

    def __init__(
        self,
        config: ConfiguracionSQL,
        *,
        fabrica_conexion: Callable[[ConfiguracionSQL], Any] | None = None,
        ejecutor: Callable[..., list[dict[str, Any]]] | None = None,
    ) -> None:
        self.config = config
        self._fabrica = fabrica_conexion or conectar
        self._ejecutor = ejecutor or ejecutar_consulta

    def _empresas(self, empresas: Sequence[str] | None) -> list[str]:
        if empresas is None:
            return self.config.empresas_activas()
        seleccion = {str(empresa).strip().upper() for empresa in empresas}
        return [empresa for empresa in EMPRESAS if empresa in seleccion]

    def _consultar(
        self,
        empresas: Sequence[str],
        cantidad: int,
        parametros: list[Any] | None = None,
    ) -> ResultadoConsulta:
        resultado = ResultadoConsulta()
        vistos: set[tuple[str, str]] = set()

        for empresa in empresas:
            try:
                sql, _esperados = queries.sql_empleados(
                    base_datos=self.config.base_datos,
                    esquema=empresa,
                    cantidad=cantidad,
                    columna_depto_empleado=self.config.columna_depto_empleado,
                    columna_depto_catalogo=self.config.columna_depto_catalogo,
                    columna_puesto_empleado=self.config.columna_puesto_empleado,
                    columna_puesto_catalogo=self.config.columna_puesto_catalogo,
                )
                with self._fabrica(self.config) as conexion:
                    filas = self._ejecutor(conexion, sql, parametros or [])
                for fila in filas:
                    empleado = Empleado.desde_fila(empresa, fila)
                    huella = (empleado.empresa, empleado.codigo)
                    if huella in vistos:
                        continue
                    vistos.add(huella)
                    resultado.empleados.append(empleado)
                resultado.empresas_consultadas.append(empresa)
            except ErrorConexionSQL as exc:
                LOGGER.warning("Empresa %s: %s", empresa, exc)
                resultado.errores.append(
                    {
                        "empresa": empresa,
                        "categoria": exc.categoria,
                        "mensaje": str(exc),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - una empresa no bloquea el resto
                LOGGER.exception("Error inesperado consultando la empresa %s", empresa)
                resultado.errores.append(
                    {
                        "empresa": empresa,
                        "categoria": "inesperado",
                        "mensaje": str(exc),
                    }
                )
        return resultado

    def buscar_por_nombres(
        self,
        nombres: Iterable[str],
        empresas: Sequence[str] | None = None,
    ) -> ResultadoConsulta:
        """Consulta por lotes los nombres solicitados (nunca uno por ticket)."""

        claves = []
        vistos: set[str] = set()
        for nombre in nombres:
            clave = normalizar_nombre(nombre).replace(" ", "")
            if clave and clave not in vistos:
                vistos.add(clave)
                claves.append(clave)
        if not claves:
            return ResultadoConsulta()

        seleccion = self._empresas(empresas)
        lote = max(1, int(self.config.lote))
        acumulado = ResultadoConsulta()

        for inicio in range(0, len(claves), lote):
            grupo = claves[inicio : inicio + lote]
            # La consulta tiene cuatro variantes del mismo nombre; los
            # parámetros se repiten en el mismo orden.
            parametros = list(grupo) * 4
            parcial = self._consultar(seleccion, len(grupo), parametros)
            acumulado.empleados.extend(parcial.empleados)
            acumulado.errores.extend(parcial.errores)
            for empresa in parcial.empresas_consultadas:
                if empresa not in acumulado.empresas_consultadas:
                    acumulado.empresas_consultadas.append(empresa)
        return acumulado

    def obtener_todos(
        self,
        empresas: Sequence[str] | None = None,
    ) -> ResultadoConsulta:
        return self._consultar(self._empresas(empresas), 0, [])

    def inspeccionar_esquema(self, empresa: str) -> dict[str, Any]:
        """Devuelve las columnas reales de una empresa para verificación."""

        empresa = str(empresa).strip().upper()
        sql_tablas = queries.sql_tablas_existen(self.config.base_datos, empresa)
        sql_columnas = queries.sql_columnas_esquema(self.config.base_datos, empresa)
        with self._fabrica(self.config) as conexion:
            tablas = self._ejecutor(
                conexion, sql_tablas, [self.config.base_datos, empresa]
            )
            columnas = self._ejecutor(conexion, sql_columnas, [empresa])
        agrupadas: dict[str, list[dict[str, Any]]] = {}
        for fila in columnas:
            tabla = str(fila.get("TABLE_NAME") or "")
            agrupadas.setdefault(tabla, []).append(fila)
        return {
            "empresa": empresa,
            "tablas": [str(fila.get("TABLE_NAME") or "") for fila in tablas],
            "columnas": agrupadas,
        }
