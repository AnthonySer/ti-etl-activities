"""Repositorio de empleados y catálogos corporativos.

La búsqueda principal usa una sentencia parametrizada ``UNION ALL`` tras
inspeccionar los metadatos. Las lecturas por empresa se limitan a enriquecer
los códigos encontrados; obtener/sincronizar catálogos conserva el recorrido
adaptativo existente.

Antes de construir el ``SELECT`` se leen las columnas reales del esquema. Así
una empresa sin ``U_CORREO_CORP``, sin ``PUESTO`` o con una tabla mínima no
genera un error de SQL Server: simplemente omite los campos que no existen.
"""

from __future__ import annotations

import hashlib
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
    ejecutar_consulta_manual,
)
from database.normalizador import claves_empleado, normalizar_nombre

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
    esquema_origen: str = ""
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
            "ESQUEMA_ORIGEN": self.esquema_origen,
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
            esquema_origen=_texto(fila.get("ESQUEMA_ORIGEN")),
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
            esquema_origen=_texto(valor("ESQUEMA_ORIGEN")),
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
        self._capacidades_consolidadas: dict[str, dict[str, Any]] | None = None
        self._cache_departamentos: dict[tuple[str, str], dict[str, Any] | None] = {}

    def _obtener_departamento(
        self, empresa: str, codigo: str, capacidades: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Consulta descripción del departamento con caché por fuente y código."""

        codigo = _texto(codigo)
        if not codigo:
            return None
        llave = (empresa, codigo)
        if llave in self._cache_departamentos:
            return self._cache_departamentos[llave]
        if empresa == "SERVIAM":
            sql, parametros = queries.sql_departamentos_serviam(codigo)
        else:
            sql, parametros = queries.sql_departamento_exactus(
                base_datos=self.config.base_datos,
                esquema=empresa,
                codigo=codigo,
                columnas_disponibles=capacidades,
            )
        with self._fabrica(self.config) as conexion:
            filas = self._ejecutor(conexion, sql, parametros)
        resultado = next(
            (dict(fila) for fila in filas
             if _texto(fila.get("DEPARTAMENTO")) == codigo),
            None,
        )
        self._cache_departamentos[llave] = resultado
        return resultado

    def _leer_capacidades_consolidadas(self) -> dict[str, dict[str, Any]]:
        """Obtiene metadatos de las 18 empresas mediante una sola lectura."""

        if self._capacidades_consolidadas is not None:
            return self._capacidades_consolidadas
        sql, parametros = queries.sql_columnas_multiempresa(self.config.base_datos)
        with self._fabrica(self.config) as conexion:
            filas = self._ejecutor(conexion, sql, parametros)
        capacidades: dict[str, dict[str, Any]] = {}
        for fila in filas:
            empresa = str(fila.get("EMPRESA") or fila.get("TABLE_SCHEMA") or "").upper()
            tabla = str(fila.get("TABLE_NAME") or "").upper()
            columna = str(fila.get("COLUMN_NAME") or "").upper()
            if empresa in EMPRESAS and tabla and columna:
                capacidades.setdefault(empresa, {}).setdefault(tabla, set()).add(columna)
        self._capacidades_consolidadas = capacidades
        return capacidades

    def _buscar_consolidado(
        self,
        consultas: Sequence[Mapping[str, Any]],
        *,
        al_progreso: Callable[[dict[str, Any]], None] | None = None,
        debe_cancelar: Callable[[], bool] | None = None,
    ) -> ResultadoConsulta:
        """Busca una vez con UNION ALL y luego enriquece solo los hits."""

        resultado = ResultadoConsulta()
        inicio_total = time.perf_counter()
        patrones = list(dict.fromkeys(str(item.get("patron") or "") for item in consultas))
        patrones = [patron for patron in patrones if patron]
        if not patrones:
            return resultado

        _avisar(al_progreso, seguimiento.nuevo_evento(
            "UNION ALL", seguimiento.ESTADO_CONSULTANDO,
            detalle="Verificando estructura y columnas de las empresas…",
        ))
        try:
            capacidades = self._leer_capacidades_consolidadas()
        except Exception as exc:  # noqa: BLE001 - consulta principal indivisible
            error = _clasificar_error_consulta(exc, "EXACTUS")
            resultado.errores.append(error)
            _avisar(al_progreso, seguimiento.nuevo_evento(
                "UNION ALL", seguimiento.estado_desde_error(error["categoria"]),
                resultados=0, detalle=error["mensaje"],
            ))
            for empresa in EMPRESAS:
                _avisar(al_progreso, seguimiento.nuevo_evento(
                    empresa, seguimiento.estado_desde_error(error["categoria"]),
                    resultados=0, detalle=error["mensaje"],
                ))
            return resultado

        _avisar(al_progreso, seguimiento.nuevo_evento(
            "UNION ALL", seguimiento.ESTADO_CONSULTANDO,
            detalle="Estructura verificada. Preparando búsqueda consolidada…",
        ))

        for empresa in EMPRESAS:
            _avisar(al_progreso, seguimiento.nuevo_evento(
                empresa, seguimiento.ESTADO_CONSULTANDO,
                detalle="Incluida en la búsqueda consolidada UNION ALL…",
            ))

        disponibles: list[str] = []
        for empresa in EMPRESAS:
            columnas = capacidades.get(empresa, {})
            empleado = columnas.get(queries.TABLA_EMPLEADO, set())
            columnas_nombre = {
                "NOMBRE", "NOMBRE_PILA", "PRIMER_APELLIDO", "SEGUNDO_APELLIDO"
            } & empleado
            if "EMPLEADO" in empleado and columnas_nombre:
                disponibles.append(empresa)
            else:
                resultado.empresas_sin_esquema.append(empresa)
                resultado.errores.append({
                    "empresa": empresa,
                    "categoria": "campos_insuficientes",
                    "mensaje": "No se encontró EMPLEADO con código y al menos una columna de nombre verificada; se omitió del UNION ALL.",
                })
                _avisar(al_progreso, seguimiento.nuevo_evento(
                    empresa, seguimiento.ESTADO_SIN_ESQUEMA, resultados=0,
                    detalle="No tiene las columnas necesarias para la búsqueda consolidada.",
                ))
        if not disponibles:
            _avisar(al_progreso, seguimiento.nuevo_evento(
                "UNION ALL", seguimiento.ESTADO_SIN_ESQUEMA,
                resultados=0,
                detalle="No hay empresas con columnas verificadas para buscar.",
            ))
            return resultado

        # Una sola búsqueda principal. Los metadatos no consultan ni buscan
        # empleados; las consultas por empresa posteriores se restringen a IDs.
        _avisar(al_progreso, seguimiento.nuevo_evento(
            "UNION ALL", seguimiento.ESTADO_CONSULTANDO,
            detalle=f"Buscando en {len(disponibles)} empresas…",
        ))
        try:
            filas_principales: list[dict[str, Any]] = []
            # SQL Server limita los parámetros posicionales por sentencia.
            # Cada patrón se repite por rama; 100 * 18 queda debajo de 2100.
            tamano_lote = max(1, min(int(self.config.lote), 100))
            for inicio in range(0, len(patrones), tamano_lote):
                if _cancelado(debe_cancelar):
                    resultado.cancelada = True
                    break
                patrones_lote = patrones[inicio : inicio + tamano_lote]
                sql, params = queries.sql_busqueda_consolidada(
                    base_datos=self.config.base_datos,
                    patrones=patrones_lote,
                    empresas=disponibles,
                    columnas_por_empresa=capacidades,
                )
                with self._fabrica(self.config) as conexion:
                    filas_principales.extend(self._ejecutor(conexion, sql, params))
        except Exception as exc:  # noqa: BLE001 - fallo del mecanismo principal
            error = _clasificar_error_consulta(exc, "UNION ALL")
            resultado.errores.append(error)
            _avisar(al_progreso, seguimiento.nuevo_evento(
                "UNION ALL", seguimiento.estado_desde_error(error["categoria"]),
                resultados=0, detalle=error["mensaje"],
            ))
            for empresa in disponibles:
                _avisar(al_progreso, seguimiento.nuevo_evento(
                    empresa, seguimiento.estado_desde_error(error["categoria"]),
                    resultados=0, detalle=error["mensaje"],
                ))
            return resultado
        if resultado.cancelada:
            for empresa in EMPRESAS:
                _avisar(al_progreso, seguimiento.nuevo_evento(
                    empresa, seguimiento.ESTADO_CANCELADA, resultados=0,
                    detalle="La búsqueda consolidada fue cancelada.",
                ))
            return resultado

        hits: dict[str, dict[str, dict[str, Any]]] = {empresa: {} for empresa in disponibles}
        for fila in filas_principales:
            empresa = str(fila.get("FUENTE") or "").strip().upper()
            if empresa not in hits:
                continue
            codigo = _texto(fila.get("EMPLEADO") or fila.get("CODIGO_EMPLEADO"))
            nombre = _texto(fila.get("NOMBRE"))
            if not codigo:
                continue
            registro = {"EMPLEADO": codigo, "NOMBRE": nombre, "FUENTE": empresa}
            hits[empresa].setdefault(codigo, registro)
        _avisar(al_progreso, seguimiento.nuevo_evento(
            "UNION ALL", seguimiento.ESTADO_COMPLETADA,
            resultados=len(filas_principales),
            duracion=time.perf_counter() - inicio_total,
            detalle=(
                f"Búsqueda principal finalizada: {len(filas_principales)} fila(s). "
                "Recuperando departamento y puesto de las coincidencias…"
            ),
        ))
        for empresa, por_codigo in hits.items():
            if not por_codigo:
                _avisar(al_progreso, seguimiento.nuevo_evento(
                    empresa, seguimiento.ESTADO_SIN_RESULTADOS,
                    resultados=0,
                    duracion=time.perf_counter() - inicio_total,
                    detalle="Sin coincidencias en la búsqueda consolidada.",
                ))
            else:
                _avisar(al_progreso, seguimiento.nuevo_evento(
                    empresa, seguimiento.ESTADO_CONSULTANDO,
                    resultados=len(por_codigo),
                    detalle=(
                        f"{len(por_codigo)} coincidencia(s); consultando datos "
                        "complementarios y departamento…"
                    ),
                ))
        # Asociar las filas a cada plan por su patrón y conservar candidatos
        # distintos de todas las empresas (incluidos códigos repetidos).
        candidatos_por_clave: dict[str, list[Empleado]] = {str(item.get("clave") or ""): [] for item in consultas}
        detalles_por_empresa: dict[str, dict[str, dict[str, Any]]] = {}
        for empresa, por_codigo in hits.items():
            if not por_codigo:
                continue
            try:
                capacidad = capacidades[empresa]
                patrones_codigo = list(por_codigo)
                if empresa == "SERVIAM":
                    # La vista MDA aporta campos de nombre y datos laborales;
                    # el código y FUENTE vienen del hit primario EXACTUS.
                    criterios_detalle: list[tuple[str, list[str]]] = []
                    for base in por_codigo.values():
                        plan = next((consulta for consulta in consultas
                                     if busqueda.coincide_terminos(
                                         base.get("NOMBRE"), consulta.get("terminos") or []
                                     )), None)
                        criterios_detalle.append((
                            str(base.get("NOMBRE") or ""),
                            list((plan or {}).get("terminos") or []),
                        ))
                    detalles: dict[str, dict[str, Any]] = {}
                    if criterios_detalle:
                        sql_detalle, params_detalle = queries.sql_empleados_serviam_estructurada(
                            criterios_detalle
                        )
                        with self._fabrica(self.config) as conexion:
                            filas_detalle = self._ejecutor(conexion, sql_detalle, params_detalle)
                        for fila_detalle in filas_detalle:
                            claves_fila = claves_empleado(
                                nombre=fila_detalle.get("NOMBRE"),
                                nombre_pila=fila_detalle.get("NOMBRE_PILA"),
                                primer_apellido=fila_detalle.get("PRIMER_APELLIDO"),
                                segundo_apellido=fila_detalle.get("SEGUNDO_APELLIDO"),
                            )
                            coincidencias = [
                                codigo for codigo, base in por_codigo.items()
                                if normalizar_nombre(base.get("NOMBRE")) in claves_fila
                            ]
                            if len(coincidencias) == 1:
                                detalles[coincidencias[0]] = dict(fila_detalle)
                    detalles_por_empresa[empresa] = detalles
                else:
                    sql_detalle, _ = queries.sql_empleados(
                        base_datos=self.config.base_datos,
                        esquema=empresa,
                        codigos=patrones_codigo,
                        columnas_disponibles=capacidad,
                        columna_depto_empleado=self.config.columna_depto_empleado,
                        columna_depto_catalogo=self.config.columna_depto_catalogo,
                        columna_puesto_empleado=self.config.columna_puesto_empleado,
                        columna_puesto_catalogo=self.config.columna_puesto_catalogo,
                    )
                    with self._fabrica(self.config) as conexion:
                        filas_detalle = self._ejecutor(conexion, sql_detalle, patrones_codigo)
                    detalles_por_empresa[empresa] = {
                        _texto(fila.get("EMPLEADO")): dict(fila) for fila in filas_detalle
                    }
            except Exception as exc:  # noqa: BLE001 - se conserva el hit primario
                error = _clasificar_error_consulta(exc, empresa)
                resultado.errores.append(error)
                detalles_por_empresa[empresa] = {}

        for empresa, por_codigo in hits.items():
            if not por_codigo:
                continue
            for codigo, base in por_codigo.items():
                detalle = dict(base)
                detalle.update(detalles_por_empresa.get(empresa, {}).get(codigo, {}))
                detalle["EMPLEADO"] = codigo
                detalle["NOMBRE"] = detalle.get("NOMBRE") or base["NOMBRE"]
                detalle["FUENTE"] = empresa
                detalle["ESQUEMA"] = empresa
                detalle["TABLA"] = queries.TABLA_EMPLEADO
                codigo_departamento = _texto(
                    detalle.get("DEPARTAMENTO_CODIGO") or detalle.get("DEPARTAMENTO")
                )
                if codigo_departamento:
                    try:
                        departamento = self._obtener_departamento(
                            empresa, codigo_departamento, capacidades.get(empresa, {})
                        )
                    except queries.EsquemaInsuficiente:
                        departamento = None
                    except Exception as exc:  # noqa: BLE001 - conserva el código
                        departamento = None
                        resultado.errores.append(
                            _clasificar_error_consulta(exc, f"{empresa}.DEPARTAMENTO")
                        )
                    if departamento:
                        descripcion = _texto(departamento.get("DESCRIPCION"))
                        if descripcion:
                            detalle["DEPARTAMENTO_CODIGO"] = codigo_departamento
                            detalle["DEPARTAMENTO"] = descripcion
                        if empresa == "SERVIAM":
                            detalle["ESQUEMA_ORIGEN"] = _texto(
                                departamento.get("ESQUEMA_ORIGEN")
                            )
                empleado = Empleado.desde_fila(empresa, detalle)
                resultado.empleados.append(empleado)
                for consulta in consultas:
                    clave = str(consulta.get("clave") or "")
                    if not clave:
                        continue
                    # La sentencia ya filtró esta fila con los patrones LIKE.
                    # En una búsqueda de un solo usuario no se debe descartar
                    # otra vez por el orden textual de NOMBRE: SERVIAM puede
                    # devolver NOMBRE y partes estructuradas en orden distinto.
                    coincide = len(consultas) == 1 or busqueda.coincide_consulta(
                        empleado, consulta
                    )
                    if not coincide:
                        # Para SERVIAM, también comparar sus campos estructurados.
                        terminos = consulta.get("terminos") or []
                        estructurado = " ".join(str(detalle.get(campo) or "") for campo in (
                            "NOMBRE_PILA", "PRIMER_APELLIDO", "SEGUNDO_APELLIDO"
                        ))
                        if empresa != "SERVIAM" or not busqueda.coincide_terminos(estructurado, terminos):
                            continue
                    resultado.por_consulta.setdefault(clave, []).append(empleado)

            _avisar(al_progreso, seguimiento.nuevo_evento(
                empresa,
                seguimiento.ESTADO_COMPLETADA,
                resultados=len(por_codigo),
                duracion=time.perf_counter() - inicio_total,
                detalle=(
                    f"{len(por_codigo)} empleado(s) enriquecido(s) con los datos "
                    "disponibles."
                ),
            ))

        resultado.empresas_consultadas = disponibles
        resultado.empresas_sin_datos = [empresa for empresa in disponibles if not hits[empresa]]
        resultado.duracion_total = time.perf_counter() - inicio_total
        return resultado

    def _empresas(self, empresas: Sequence[str] | None) -> list[str]:
        if empresas is None:
            return self.config.empresas_activas()
        seleccion = {str(empresa).strip().upper() for empresa in empresas}
        return [empresa for empresa in EMPRESAS if empresa in seleccion]

    def ejecutar_sql_manual(
        self, sql: str, *, limite_filas: int = 500, timeout_segundos: int = 15
    ) -> dict[str, Any]:
        """Ejecuta una consulta manual de lectura con la conexión configurada."""

        huella = hashlib.sha256(sql.encode("utf-8", errors="replace")).hexdigest()[:16]
        try:
            with self._fabrica(self.config) as conexion:
                resultado = ejecutar_consulta_manual(
                    conexion, sql, limite_filas=limite_filas,
                    timeout_segundos=timeout_segundos,
                )
        except Exception as exc:  # noqa: BLE001 - deja rastro sin almacenar el SQL
            LOGGER.warning(
                "SQL manual de solo lectura fallido: huella=%s categoria=%s",
                huella, getattr(exc, "categoria", type(exc).__name__),
            )
            raise
        LOGGER.info(
            "SQL manual de solo lectura ejecutado: huella=%s filas=%s truncado=%s",
            huella, len(resultado.get("filas") or []), resultado.get("truncado"),
        )
        return resultado

    def buscar_serviam_manual(self, nombre: str) -> ResultadoConsulta:
        """Busca directamente en la vista oficial MDA de empleados SERVIAM."""

        plan = busqueda.plan_automatico(nombre)
        criterio = [(str(nombre or ""), list(plan.terminos))]
        sql, parametros = queries.sql_empleados_serviam_estructurada(criterio)
        resultado = ResultadoConsulta(empresas_consultadas=["SERVIAM"])
        with self._fabrica(self.config) as conexion:
            filas = self._ejecutor(conexion, sql, parametros)
        vistos: set[tuple[str, str, str]] = set()
        for fila in filas:
            empleado = Empleado.desde_fila("SERVIAM", {
                **dict(fila), "FUENTE": "SERVIAM", "ESQUEMA": "MDA",
                "TABLA": "VW_EMPLEADOS_MDA",
            })
            huella = empleado.identificador
            if not empleado.codigo:
                huella = (
                    empleado.empresa,
                    empleado.fuente or empleado.tabla,
                    "|".join((
                        normalizar_nombre(empleado.nombre_completo),
                        normalizar_nombre(empleado.departamento),
                        normalizar_nombre(empleado.correo),
                    )),
                )
            if huella in vistos:
                continue
            vistos.add(huella)
            resultado.empleados.append(empleado)
            resultado.por_consulta.setdefault(plan.clave, []).append(empleado)
        resultado.empresas_sin_datos = [] if resultado.empleados else ["SERVIAM"]
        return resultado

    def buscar_departamento_manual(
        self, empresa: str, codigo: str
    ) -> list[dict[str, Any]]:
        """Consulta el catálogo del esquema elegido o la vista MDA de SERVIAM."""

        empresa = str(empresa or "").strip().upper()
        if empresa == "SERVIAM":
            sql, parametros = queries.sql_departamentos_serviam(codigo)
        else:
            capacidades = self._leer_capacidades(empresa)
            if not capacidades.get("disponible"):
                raise queries.EsquemaInsuficiente(
                    f"No se pudieron verificar las columnas de {empresa}."
                )
            sql, parametros = queries.sql_departamento_exactus(
                base_datos=self.config.base_datos,
                esquema=empresa,
                codigo=codigo,
                columnas_disponibles=capacidades["columnas"],
            )
        with self._fabrica(self.config) as conexion:
            return self._ejecutor(conexion, sql, parametros)

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

        # La búsqueda principal siempre consolida las 18 empresas. La
        # selección configurada deja de controlar este lookup; las consultas
        # por empresa se reservan para enriquecer los IDs ya encontrados.
        return self._buscar_consolidado(
            preparadas, al_progreso=al_progreso, debe_cancelar=debe_cancelar
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
