"""Repositorio de empleados y catálogos corporativos.

Cada empresa (esquema) se consulta de forma independiente: si una falla, las
demás continúan y el error se informa sin bloquear el procesamiento.

Antes de construir el ``SELECT`` se leen las columnas reales del esquema. Así
una empresa sin ``U_CORREO_CORP``, sin ``PUESTO`` o con una tabla mínima no
genera un error de SQL Server: simplemente omite los campos que no existen.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from config.sqlserver import EMPRESAS, ConfiguracionSQL
from core import busqueda_solicitantes as busqueda
from core import seguimiento_consultas as seguimiento
from core.normalizador import limpiar_area
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
    if texto in {"1", "true", "si", "sí", "s", "activo", "acti", "yes", "y"}:
        return True
    if texto in {
        "0",
        "false",
        "no",
        "n",
        "inactivo",
        "inac",
        "cesado",
        "cesa",
        "baja",
        "retirado",
    }:
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
    base_datos: str = ""
    esquema: str = ""
    tabla: str = ""

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
    def correo(self) -> str:
        return self.correo_corp or self.email

    @property
    def inactivo(self) -> bool:
        if self.activo is False:
            return True
        estado = normalizar_nombre(self.estado_empleado)
        return estado in {"INACTIVO", "CESADO", "BAJA", "RETIRADO", "CESA", "INAC"}

    @property
    def tiene_departamento(self) -> bool:
        # "N/A", "NULL" o espacios no son un departamento utilizable.
        return bool(limpiar_area(self.departamento))

    @property
    def identificador(self) -> tuple[str, str, str]:
        return (self.empresa, self.fuente or self.tabla, self.codigo)

    def como_dict(self) -> dict[str, Any]:
        """Claves históricas + claves lógicas uniformes del plan."""

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
            "FUENTE": self.fuente or self.tabla,
            "BASE_DATOS": self.base_datos,
            "ESQUEMA": self.esquema or self.empresa,
            "TABLA": self.tabla,
            # Estructura lógica uniforme solicitada por el plan.
            "empleado": self.codigo,
            "nombre": self.nombre_completo,
            "empresa": self.empresa,
            "fuente": self.fuente or self.tabla,
            "departamento": self.departamento,
            "puesto": self.puesto,
            "activo": self.activo,
            "correo": self.correo,
        }

    @classmethod
    def desde_fila(cls, empresa: str, fila: Mapping[str, Any]) -> "Empleado":
        return cls(
            empresa=empresa,
            codigo=_texto(fila.get("EMPLEADO") or fila.get("empleado")),
            nombre=_texto(
                fila.get("NOMBRE")
                or fila.get("NOMBRE_DERIVADO")
                or fila.get("nombre")
            ),
            primer_apellido=_texto(fila.get("PRIMER_APELLIDO")),
            segundo_apellido=_texto(fila.get("SEGUNDO_APELLIDO")),
            nombre_pila=_texto(fila.get("NOMBRE_PILA")),
            departamento_codigo=_texto(fila.get("DEPARTAMENTO_CODIGO")),
            departamento=_texto(
                fila.get("DEPARTAMENTO")
                or fila.get("DEPARTAMENTO_DESCRIPCION")
                or fila.get("departamento")
            ),
            puesto_codigo=_texto(fila.get("PUESTO_CODIGO")),
            puesto=_texto(
                fila.get("PUESTO")
                or fila.get("PUESTO_DESCRIPCION")
                or fila.get("puesto")
            ),
            estado_empleado=_texto(fila.get("ESTADO_EMPLEADO")),
            activo=_booleano(fila.get("ACTIVO")),
            email=_texto(fila.get("E_MAIL")),
            correo_corp=_texto(fila.get("U_CORREO_CORP")),
            fuente=_texto(fila.get("FUENTE")) or "EMPLEADO",
            base_datos=_texto(fila.get("BASE_DATOS")),
            esquema=_texto(fila.get("ESQUEMA")) or empresa,
            tabla=_texto(fila.get("TABLA")) or "EMPLEADO",
        )

    @classmethod
    def desde_candidato(cls, candidato: Mapping[str, Any]) -> "Empleado":
        """Reconstruye el registro completo desde un candidato de la revisión."""

        def valor(*nombres: str) -> Any:
            for nombre in nombres:
                if nombre in candidato and candidato[nombre] not in (None, ""):
                    return candidato[nombre]
            return ""

        activo = valor("ACTIVO", "activo")
        return cls(
            empresa=_texto(valor("EMPRESA", "empresa")),
            codigo=_texto(valor("EMPLEADO", "empleado", "CODIGO_EMPLEADO")),
            nombre=_texto(valor("NOMBRE", "nombre")),
            primer_apellido=_texto(valor("PRIMER_APELLIDO")),
            segundo_apellido=_texto(valor("SEGUNDO_APELLIDO")),
            nombre_pila=_texto(valor("NOMBRE_PILA")),
            departamento_codigo=_texto(valor("DEPARTAMENTO_CODIGO")),
            departamento=_texto(valor("DEPARTAMENTO", "departamento")),
            puesto_codigo=_texto(valor("PUESTO_CODIGO")),
            puesto=_texto(valor("PUESTO", "puesto")),
            estado_empleado=_texto(valor("ESTADO_EMPLEADO")),
            activo=(
                activo
                if isinstance(activo, bool) or activo is None or activo == ""
                else _booleano(activo)
            ),
            email=_texto(valor("E_MAIL")),
            correo_corp=_texto(valor("U_CORREO_CORP")),
            fuente=_texto(valor("FUENTE", "fuente", "TABLA", "tabla")) or "EMPLEADO",
            base_datos=_texto(valor("BASE_DATOS")),
            esquema=_texto(valor("ESQUEMA")) or _texto(valor("EMPRESA", "empresa")),
            tabla=_texto(valor("TABLA", "tabla")) or "EMPLEADO",
        )


@dataclass
class ResultadoConsulta:
    empleados: list[Empleado] = field(default_factory=list)
    errores: list[dict[str, str]] = field(default_factory=list)
    empresas_consultadas: list[str] = field(default_factory=list)
    empresas_sin_datos: list[str] = field(default_factory=list)
    empresas_sin_esquema: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    por_consulta: dict[str, list[Empleado]] = field(default_factory=dict)
    cancelada: bool = False
    duracion_total: float = 0.0
    eventos: list[dict[str, Any]] = field(default_factory=list)

    @property
    def empresas_con_error(self) -> list[str]:
        return [error["empresa"] for error in self.errores]

    def resumen(self) -> dict[str, Any]:
        return {
            "empleados": len(self.empleados),
            "consultadas": len(self.empresas_consultadas),
            "errores": len(self.errores),
            "sin_datos": len(self.empresas_sin_datos),
            "sin_esquema": len(self.empresas_sin_esquema),
            "cancelada": self.cancelada,
            "duracion_total": round(self.duracion_total, 2),
        }


def _clasificar_error_consulta(exc: Exception, empresa: str) -> dict[str, str]:
    """Traduce el error de una empresa conservando la categoría técnica."""

    categoria = getattr(exc, "categoria", "consulta")
    mensaje = str(exc)
    texto = mensaje.upper()
    if "INVALID COLUMN NAME" in texto or "42S22" in texto:
        categoria = "columna"
    elif "INVALID OBJECT NAME" in texto or "42S02" in texto or "208" in texto:
        categoria = "tabla"
    elif "PERMISSION" in texto or "229" in texto or "DENIED" in texto:
        categoria = "permisos"
    elif "INCORRECT SYNTAX" in texto or "SYNTAX" in texto or "102" in texto:
        categoria = "sintaxis"
    return {"empresa": empresa, "categoria": categoria, "mensaje": mensaje}


def _avisar(al_progreso: Callable[[dict[str, Any]], None] | None, evento: dict[str, Any]) -> None:
    """Entrega un evento a la ventana de seguimiento sin romper la consulta."""

    if al_progreso is None:
        return
    try:
        al_progreso(evento)
    except Exception:  # noqa: BLE001 - la UI nunca debe tumbar la consulta
        LOGGER.debug("No se pudo entregar el evento de seguimiento", exc_info=True)


def _cancelado(debe_cancelar: Callable[[], bool] | None) -> bool:
    if debe_cancelar is None:
        return False
    try:
        return bool(debe_cancelar())
    except Exception:  # noqa: BLE001
        return False


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
        self._capacidades: dict[str, dict[str, Any]] = {}

    def _empresas(self, empresas: Sequence[str] | None) -> list[str]:
        if empresas is None:
            return self.config.empresas_activas()
        seleccion = {str(empresa).strip().upper() for empresa in empresas}
        return [empresa for empresa in EMPRESAS if empresa in seleccion]

    def _leer_capacidades(self, empresa: str) -> dict[str, Any]:
        """Columnas reales del esquema, cacheadas por repositorio."""

        if empresa in self._capacidades:
            return self._capacidades[empresa]

        sql = queries.sql_columnas_esquema(self.config.base_datos, empresa)
        try:
            with self._fabrica(self.config) as conexion:
                filas = self._ejecutor(conexion, sql, [empresa])
        except Exception as exc:  # noqa: BLE001 - se registra y se relanza
            # Se conserva el fallo en la caché para no volver a conectar ni
            # repetir la misma advertencia en cada lote de nombres.
            self._capacidades[empresa] = {
                "columnas": {},
                "disponible": False,
                "error": exc,
            }
            raise

        # Los dobles de prueba antiguos devuelven filas de empleados para
        # cualquier SQL. En ese caso no hay metadatos utilizables y se usa el
        # constructor heredado, sin romper la compatibilidad.
        columnas = queries.columnas_por_tabla(filas)
        capacidad = {
            "columnas": columnas,
            "disponible": bool(columnas),
            "error": None,
        }
        self._capacidades[empresa] = capacidad
        return capacidad

    def _consultar(
        self,
        empresas: Sequence[str],
        cantidad: int,
        parametros: list[Any] | None = None,
        *,
        consultas: Sequence[Mapping[str, Any]] | None = None,
        al_progreso: Callable[[dict[str, Any]], None] | None = None,
        debe_cancelar: Callable[[], bool] | None = None,
    ) -> ResultadoConsulta:
        """Consulta cada empresa de forma aislada y emite progreso real.

        El bucle exterior es la empresa para que la ventana de seguimiento
        reciba un solo evento final por empresa, incluso cuando la búsqueda se
        divide en varios lotes. Una empresa con error nunca se marca como
        completada.
        """

        resultado = ResultadoConsulta()
        vistos: set[tuple[str, str, str]] = set()
        mapas: dict[str, set[tuple[str, str, str]]] = {}
        inicio_total = time.perf_counter()

        if consultas is None:
            grupos: list[Sequence[Mapping[str, Any]] | None] = [None]
        else:
            lote = max(1, int(self.config.lote))
            preparadas = list(consultas)
            grupos = [
                preparadas[inicio : inicio + lote]
                for inicio in range(0, len(preparadas), lote)
            ]

        for indice_empresa, empresa in enumerate(empresas):
            if _cancelado(debe_cancelar):
                resultado.cancelada = True
                for pendiente in empresas[indice_empresa:]:
                    _avisar(
                        al_progreso,
                        seguimiento.nuevo_evento(
                            pendiente,
                            seguimiento.ESTADO_CANCELADA,
                            detalle="La consulta fue cancelada antes de procesar esta empresa.",
                        ),
                    )
                break

            _avisar(
                al_progreso,
                seguimiento.nuevo_evento(
                    empresa,
                    seguimiento.ESTADO_CONSULTANDO,
                    detalle="Obteniendo resultados…",
                ),
            )
            inicio_empresa = time.perf_counter()

            try:
                capacidad = self._leer_capacidades(empresa)
                if capacidad.get("error") is not None:
                    error = _clasificar_error_consulta(capacidad["error"], empresa)
                    resultado.errores.append(error)
                    _avisar(
                        al_progreso,
                        seguimiento.nuevo_evento(
                            empresa,
                            seguimiento.estado_desde_error(error["categoria"]),
                            resultados=0,
                            duracion=time.perf_counter() - inicio_empresa,
                            detalle=error["mensaje"],
                        ),
                    )
                    continue

                columnas = capacidad["columnas"] if capacidad["disponible"] else None

                if capacidad["disponible"]:
                    tabla_empleado = columnas.get(queries.TABLA_EMPLEADO, set())
                    if not tabla_empleado:
                        resultado.empresas_sin_esquema.append(empresa)
                        error = {
                            "empresa": empresa,
                            "categoria": "tabla",
                            "mensaje": (
                                "No existe la tabla EMPLEADO en el esquema "
                                f"{empresa}."
                            ),
                        }
                        resultado.errores.append(error)
                        _avisar(
                            al_progreso,
                            seguimiento.nuevo_evento(
                                empresa,
                                seguimiento.ESTADO_SIN_ESQUEMA,
                                resultados=0,
                                duracion=time.perf_counter() - inicio_empresa,
                                detalle=error["mensaje"],
                            ),
                        )
                        continue
                    if "EMPLEADO" not in tabla_empleado:
                        error = {
                            "empresa": empresa,
                            "categoria": "campos_insuficientes",
                            "mensaje": (
                                "La tabla EMPLEADO no tiene la columna "
                                "fundamental EMPLEADO."
                            ),
                        }
                        resultado.errores.append(error)
                        resultado.empresas_sin_esquema.append(empresa)
                        _avisar(
                            al_progreso,
                            seguimiento.nuevo_evento(
                                empresa,
                                seguimiento.ESTADO_SIN_ESQUEMA,
                                resultados=0,
                                duracion=time.perf_counter() - inicio_empresa,
                                detalle=error["mensaje"],
                            ),
                        )
                        continue

                filas_empresa = 0
                esquema_insuficiente = False

                for grupo in grupos:
                    if _cancelado(debe_cancelar):
                        resultado.cancelada = True
                        break

                    try:
                        if consultas is None:
                            sql, _esperados = queries.sql_empleados(
                                base_datos=self.config.base_datos,
                                esquema=empresa,
                                cantidad=cantidad,
                                columnas_disponibles=columnas,
                                columna_depto_empleado=self.config.columna_depto_empleado,
                                columna_depto_catalogo=self.config.columna_depto_catalogo,
                                columna_puesto_empleado=self.config.columna_puesto_empleado,
                                columna_puesto_catalogo=self.config.columna_puesto_catalogo,
                            )
                            parametros_grupo = parametros or []
                        else:
                            patrones = [str(item.get("patron") or "") for item in grupo or []]
                            sql, _esperados = queries.sql_empleados(
                                base_datos=self.config.base_datos,
                                esquema=empresa,
                                patrones=patrones,
                                columnas_disponibles=columnas,
                                columna_depto_empleado=self.config.columna_depto_empleado,
                                columna_depto_catalogo=self.config.columna_depto_catalogo,
                                columna_puesto_empleado=self.config.columna_puesto_empleado,
                                columna_puesto_catalogo=self.config.columna_puesto_catalogo,
                            )
                            parametros_grupo = patrones
                    except queries.EsquemaInsuficiente as exc:
                        error = {
                            "empresa": empresa,
                            "categoria": "campos_insuficientes",
                            "mensaje": str(exc),
                        }
                        resultado.errores.append(error)
                        resultado.empresas_sin_esquema.append(empresa)
                        _avisar(
                            al_progreso,
                            seguimiento.nuevo_evento(
                                empresa,
                                seguimiento.ESTADO_SIN_ESQUEMA,
                                resultados=0,
                                duracion=time.perf_counter() - inicio_empresa,
                                detalle=error["mensaje"],
                            ),
                        )
                        esquema_insuficiente = True
                        break

                    with self._fabrica(self.config) as conexion:
                        filas = self._ejecutor(conexion, sql, parametros_grupo)

                    for fila in filas:
                        fila = dict(fila)
                        fila.setdefault("BASE_DATOS", self.config.base_datos)
                        fila.setdefault("ESQUEMA", empresa)
                        fila.setdefault("TABLA", queries.TABLA_EMPLEADO)
                        empleado = Empleado.desde_fila(empresa, fila)
                        huella = empleado.identificador
                        if huella not in vistos:
                            vistos.add(huella)
                            resultado.empleados.append(empleado)
                        filas_empresa += 1
                        if consultas is not None:
                            for consulta in grupo or []:
                                clave = str(consulta.get("clave") or "")
                                if not clave or not busqueda.coincide_consulta(
                                    empleado, consulta
                                ):
                                    continue
                                mapa = mapas.setdefault(clave, set())
                                if huella in mapa:
                                    continue
                                mapa.add(huella)
                                resultado.por_consulta.setdefault(clave, []).append(
                                    empleado
                                )

                if esquema_insuficiente:
                    continue

                if resultado.cancelada:
                    for pendiente in empresas[indice_empresa:]:
                        _avisar(
                            al_progreso,
                            seguimiento.nuevo_evento(
                                pendiente,
                                seguimiento.ESTADO_CANCELADA,
                                resultados=0 if pendiente == empresa else None,
                                duracion=(
                                    time.perf_counter() - inicio_empresa
                                    if pendiente == empresa
                                    else None
                                ),
                                detalle="La consulta fue cancelada por el usuario.",
                            ),
                        )
                    break

                resultado.empresas_consultadas.append(empresa)
                if not filas_empresa:
                    resultado.empresas_sin_datos.append(empresa)
                _avisar(
                    al_progreso,
                    seguimiento.nuevo_evento(
                        empresa,
                        (
                            seguimiento.ESTADO_COMPLETADA
                            if filas_empresa
                            else seguimiento.ESTADO_SIN_RESULTADOS
                        ),
                        resultados=filas_empresa,
                        duracion=time.perf_counter() - inicio_empresa,
                        detalle=(
                            f"{filas_empresa} resultado(s)."
                            if filas_empresa
                            else "La consulta no devolvió empleados."
                        ),
                    ),
                )
            except ErrorConexionSQL as exc:
                LOGGER.warning("Empresa %s: %s", empresa, exc)
                error = _clasificar_error_consulta(exc, empresa)
                resultado.errores.append(error)
                _avisar(
                    al_progreso,
                    seguimiento.nuevo_evento(
                        empresa,
                        seguimiento.estado_desde_error(error["categoria"]),
                        resultados=0,
                        duracion=time.perf_counter() - inicio_empresa,
                        detalle=error["mensaje"],
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - una empresa no bloquea el resto
                LOGGER.exception("Error inesperado consultando la empresa %s", empresa)
                error = _clasificar_error_consulta(exc, empresa)
                resultado.errores.append(error)
                _avisar(
                    al_progreso,
                    seguimiento.nuevo_evento(
                        empresa,
                        seguimiento.estado_desde_error(error["categoria"]),
                        resultados=0,
                        duracion=time.perf_counter() - inicio_empresa,
                        detalle=error["mensaje"],
                    ),
                )

        resultado.duracion_total = time.perf_counter() - inicio_total
        return resultado

    def buscar_por_consultas(
        self,
        consultas: Iterable[Mapping[str, Any] | str],
        empresas: Sequence[str] | None = None,
        *,
        al_progreso: Callable[[dict[str, Any]], None] | None = None,
        debe_cancelar: Callable[[], bool] | None = None,
    ) -> ResultadoConsulta:
        """Busca varios solicitantes conservando el mapeo a sus candidatos.

        Cada consulta puede ser un ``PlanBusqueda.como_dict()`` o un nombre.
        Los patrones viajan como parámetros y el repositorio conserva, para
        cada solicitante, todos los empleados que cumplen su patrón. Así una
        misma persona en varias empresas no se pierde ni se fusiona.
        """

        preparadas: list[dict[str, Any]] = []
        vistos: set[str] = set()
        for item in consultas:
            if isinstance(item, Mapping):
                datos = dict(item)
            else:
                datos = busqueda.plan_automatico(item).como_dict()

            terminos = [
                str(termino).strip().upper()
                for termino in datos.get("terminos") or []
                if str(termino).strip()
            ]
            patron = str(datos.get("patron") or busqueda.patron_like(terminos))
            clave = str(
                datos.get("clave")
                or busqueda.normalizar_nombre(datos.get("solicitante"))
                or " ".join(terminos)
            )
            if not patron or not clave:
                continue
            huella = clave.replace(" ", "")
            if huella in vistos:
                continue
            vistos.add(huella)
            preparadas.append(
                {
                    "solicitante": str(datos.get("solicitante") or clave),
                    "clave": clave,
                    "terminos": terminos,
                    "patron": patron,
                    "modo": str(datos.get("modo") or ""),
                }
            )

        if not preparadas:
            return ResultadoConsulta()

        seleccion = self._empresas(empresas)
        return self._consultar(
            seleccion,
            0,
            [],
            consultas=preparadas,
            al_progreso=al_progreso,
            debe_cancelar=debe_cancelar,
        )

    def buscar_por_nombres(
        self,
        nombres: Iterable[str],
        empresas: Sequence[str] | None = None,
        *,
        al_progreso: Callable[[dict[str, Any]], None] | None = None,
        debe_cancelar: Callable[[], bool] | None = None,
    ) -> ResultadoConsulta:
        """Consulta por lotes los nombres solicitados (nunca uno por ticket).

        Se reutiliza el plan de búsqueda de la interfaz: una palabra usa
        ``%termino%``, dos palabras se invierten y tres o más conservan el
        orden original. El procesamiento automático no abre ventanas.
        """

        consultas: list[dict[str, Any]] = []
        vistos: set[str] = set()
        for nombre in nombres:
            plan = busqueda.plan_automatico(nombre)
            if not plan.terminos:
                continue
            huella = plan.clave.replace(" ", "")
            if huella in vistos:
                continue
            vistos.add(huella)
            consultas.append(plan.como_dict())

        if not consultas:
            return ResultadoConsulta()
        return self.buscar_por_consultas(
            consultas,
            empresas,
            al_progreso=al_progreso,
            debe_cancelar=debe_cancelar,
        )

    def buscar_por_patrones(
        self,
        patrones: Iterable[str],
        empresas: Sequence[str] | None = None,
        *,
        al_progreso: Callable[[dict[str, Any]], None] | None = None,
        debe_cancelar: Callable[[], bool] | None = None,
    ) -> ResultadoConsulta:
        """Compatibilidad: búsqueda directa por patrones ``LIKE`` ya escapados."""

        consultas = [
            {
                "solicitante": patron,
                "clave": patron,
                "terminos": [],
                "patron": patron,
            }
            for patron in patrones
            if str(patron or "").strip()
        ]
        if not consultas:
            return ResultadoConsulta()
        return self.buscar_por_consultas(
            consultas,
            empresas,
            al_progreso=al_progreso,
            debe_cancelar=debe_cancelar,
        )

    def obtener_todos(
        self,
        empresas: Sequence[str] | None = None,
    ) -> ResultadoConsulta:
        return self._consultar(self._empresas(empresas), 0, [])

    def inspeccionar_esquema(self, empresa: str) -> dict[str, Any]:
        """Devuelve las columnas reales de una empresa para verificación."""

        empresa = str(empresa).strip().upper()
        sql_tablas = queries.sql_tablas_empresa(self.config.base_datos, empresa)
        sql_columnas = queries.sql_columnas_esquema(self.config.base_datos, empresa)
        with self._fabrica(self.config) as conexion:
            tablas = self._ejecutor(conexion, sql_tablas, [empresa])
            columnas = self._ejecutor(conexion, sql_columnas, [empresa])
        agrupadas = queries.columnas_por_tabla(columnas)
        return {
            "empresa": empresa,
            "tablas": [str(fila.get("TABLE_NAME") or "") for fila in tablas],
            "columnas": {
                tabla: sorted(nombres)
                for tabla, nombres in sorted(agrupadas.items())
            },
        }
