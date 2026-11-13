"""Consolidación de coincidencias para usuarios sin área.

Combina tres orígenes sin mezclarlos:

* la caché local de empleados (``database.synchronizer``),
* la consulta SQL por lotes de las empresas autorizadas,
* el maestro ``USUARIO -> ÁREA``.

El resultado se agrupa por el usuario original del Excel. Nunca asigna áreas
automáticamente: solo presenta candidatos exactos y aproximados para revisión
manual. Los índices se conservan durante la ejecución y se invalidan cuando
cambia el maestro o la caché.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from core import similitud
from database.normalizador import claves_empleado, normalizar_nombre
from database.repository import Empleado

LOGGER = logging.getLogger("automatizador")

ORIGEN_CACHE = "CACHE"
ORIGEN_SQL = "SQL_SERVER"
ORIGEN_MAESTRO = "MAESTRO"

CAMPOS_CANDIDATO = (
    "usuario_original",
    "nombre",
    "empresa",
    "empleado",
    "departamento",
    "area",
    "puesto",
    "activo",
    "estado",
    "correo",
    "fuente",
    "origen",
    "tipo",
    "similitud",
    "coincide",
    "clave",
)


@dataclass
class GrupoCoincidencias:
    """Candidatos consolidados para un usuario original."""

    usuario: str
    clave: str
    candidatos: list[dict[str, Any]] = field(default_factory=list)
    errores: list[dict[str, Any]] = field(default_factory=list)

    @property
    def exactos(self) -> list[dict[str, Any]]:
        return [item for item in self.candidatos if item.get("tipo") == similitud.EXACTA]

    @property
    def aproximados(self) -> list[dict[str, Any]]:
        return [item for item in self.candidatos if item.get("tipo") != similitud.EXACTA]

    def como_dict(self) -> dict[str, Any]:
        return {
            "usuario": self.usuario,
            "clave": self.clave,
            "candidatos": list(self.candidatos),
            "exactos": len(self.exactos),
            "aproximados": len(self.aproximados),
            "errores": list(self.errores),
        }


def _estado_activo(valor: Any) -> str:
    if valor is True:
        return "Activo"
    if valor is False:
        return "Inactivo"
    return "Desconocido"


def _activo_bool(valor: Any) -> bool | None:
    if isinstance(valor, bool):
        return valor
    texto = normalizar_nombre(valor)
    if texto in {"1", "SI", "TRUE", "ACTIVO", "A"}:
        return True
    if texto in {"0", "NO", "FALSE", "INACTIVO", "I"}:
        return False
    return None


def _claves(valor: Any) -> set[str]:
    clave = normalizar_nombre(valor)
    if not clave:
        return set()
    claves = {clave}
    if " " in clave:
        claves.add(" ".join(sorted(clave.split())))
    return claves


def _base_candidato(
    *,
    usuario: str,
    nombre: str,
    empresa: str,
    empleado: str = "",
    departamento: str = "",
    area: str = "",
    puesto: str = "",
    activo: Any = None,
    correo: str = "",
    fuente: str = "",
    origen: str = "",
    clave: str = "",
) -> dict[str, Any]:
    activo_bool = _activo_bool(activo)
    return {
        "usuario_original": usuario,
        "nombre": nombre,
        "empresa": empresa,
        "empleado": empleado,
        "departamento": departamento,
        "area": area or departamento,
        "puesto": puesto,
        "activo": activo_bool,
        "estado": _estado_activo(activo_bool),
        "correo": correo,
        "fuente": fuente or origen,
        "origen": origen,
        "tipo": similitud.SIN_COINCIDENCIA,
        "similitud": 0.0,
        "coincide": False,
        "clave": clave or normalizar_nombre(nombre),
        # Alias históricos para las tablas existentes de la interfaz.
        "EMPRESA": empresa,
        "EMPLEADO": empleado,
        "NOMBRE": nombre,
        "DEPARTAMENTO": departamento,
        "AREA": area or departamento,
        "PUESTO": puesto,
        "CORREO": correo,
        "ACTIVO": activo_bool,
    }


def candidato_desde_empleado(
    empleado: Empleado,
    usuario: str = "",
    *,
    origen: str = ORIGEN_SQL,
) -> dict[str, Any]:
    """Convierte un ``Empleado`` en candidato comparable y visible."""

    return _base_candidato(
        usuario=usuario,
        nombre=empleado.nombre_completo,
        empresa=empleado.empresa,
        empleado=empleado.codigo,
        departamento=empleado.departamento,
        puesto=empleado.puesto,
        activo=empleado.activo,
        correo=empleado.correo,
        fuente=empleado.fuente or empleado.tabla,
        origen=origen,
        clave=empleado.clave,
    )


def candidato_desde_maestro(
    usuario_maestro: str,
    area: str,
    usuario_original: str = "",
) -> dict[str, Any]:
    """Convierte una fila del maestro en candidato de área."""

    return _base_candidato(
        usuario=usuario_original or usuario_maestro,
        nombre=usuario_maestro,
        empresa=ORIGEN_MAESTRO,
        empleado="",
        departamento=area,
        area=area,
        activo=None,
        fuente=ORIGEN_MAESTRO,
        origen=ORIGEN_MAESTRO,
        clave=normalizar_nombre(usuario_maestro),
    )


def _puntuar(usuario_clave: str, candidato: dict[str, Any]) -> dict[str, Any]:
    """Calcula tipo y similitud de un candidato respecto del usuario."""

    previo = candidato.pop("_puntaje", None)
    claves_candidato = _claves(candidato.get("clave") or candidato.get("nombre"))
    if usuario_clave in claves_candidato:
        puntaje = 1.0
    elif previo is not None:
        # La búsqueda en el maestro ya calculó la similitud; se reutiliza para
        # no repetir el trabajo más costoso del proceso.
        puntaje = float(previo)
    else:
        puntaje = similitud.comparar(usuario_clave, candidato.get("nombre"))
        # Variantes de orden nombre/apellido ya se resuelven en ``comparar``.
        if usuario_clave and usuario_clave in claves_candidato:
            puntaje = max(puntaje, 1.0)

    tipo = similitud.clasificar(puntaje)
    candidato["similitud"] = round(puntaje, 3)
    candidato["tipo"] = tipo
    candidato["coincide"] = tipo != similitud.SIN_COINCIDENCIA
    return candidato


class ConsolidadorCoincidencias:
    """Búsqueda exacta y aproximada con caché por ejecución."""

    def __init__(
        self,
        *,
        maestro: Any = None,
        repositorio: Any = None,
        cargar_empleados: Callable[[], Sequence[Empleado]] | None = None,
        umbrales: similitud.UmbralesSimilitud | None = None,
        limite: int = 8,
        umbral: float = 0.72,
    ) -> None:
        self.maestro = maestro
        self.repositorio = repositorio
        self._cargar_empleados = cargar_empleados
        self.umbrales = umbrales or similitud.UmbralesSimilitud()
        self.limite = max(1, int(limite))
        self.umbral = float(umbral)
        self._empleados: list[Empleado] | None = None
        self._indice_local: dict[str, list[Empleado]] | None = None
        self._indice_maestro: list[dict[str, Any]] | None = None
        self._cache_sql: dict[str, list[dict[str, Any]]] = {}
        self._ultima_consulta: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Índices
    # ------------------------------------------------------------------
    def invalidar(
        self,
        *,
        empleados: bool = True,
        maestro: bool = True,
        sql: bool = True,
    ) -> None:
        """Invalida los índices temporales cuando cambia un origen."""

        if empleados:
            self._empleados = None
            self._indice_local = None
        if maestro:
            self._indice_maestro = None
        if sql:
            self._cache_sql.clear()
            self._ultima_consulta.clear()

    def _cargar(self) -> list[Empleado]:
        if self._empleados is None:
            if self._cargar_empleados is None:
                from database.synchronizer import cargar_empleados

                self._cargar_empleados = cargar_empleados
            try:
                self._empleados = list(self._cargar_empleados() or [])
            except Exception as exc:  # noqa: BLE001 - la caché es opcional
                LOGGER.warning("No se pudo cargar la caché de empleados: %s", exc)
                self._empleados = []
        return self._empleados

    def _indice_de_cache(self) -> dict[str, list[Empleado]]:
        if self._indice_local is None:
            indice: dict[str, list[Empleado]] = {}
            for empleado in self._cargar():
                for clave in claves_empleado(
                    nombre=empleado.nombre,
                    nombre_pila=empleado.nombre_pila,
                    primer_apellido=empleado.primer_apellido,
                    segundo_apellido=empleado.segundo_apellido,
                ):
                    if clave:
                        indice.setdefault(clave, []).append(empleado)
            self._indice_local = indice
        return self._indice_local

    def _cargar_maestro(self) -> list[dict[str, Any]]:
        if self._indice_maestro is not None:
            return self._indice_maestro
        if self.maestro is None:
            self._indice_maestro = []
            return self._indice_maestro

        candidatos: list[dict[str, Any]] = []
        try:
            df = self.maestro.como_dataframe()
        except Exception as exc:  # noqa: BLE001 - el maestro es opcional
            LOGGER.warning("No se pudo leer el maestro para coincidencias: %s", exc)
            self._indice_maestro = []
            return self._indice_maestro

        for _, fila in df.iterrows():
            usuario = str(fila.get("USUARIO") or "").strip()
            area = str(fila.get("AREA_USUARIO") or "").strip()
            if not usuario or not area:
                continue
            candidatos.append(candidato_desde_maestro(usuario, area))
        self._indice_maestro = candidatos
        return candidatos

    def _repositorio_activo(self) -> Any:
        if self.repositorio is not None:
            return self.repositorio
        try:
            from config.sqlserver import cargar_configuracion
            from database.connection import controlador_disponible

            config = cargar_configuracion()
            if not (config.habilitado and config.configurada and controlador_disponible()):
                return None
            from database.repository import RepositorioEmpleados

            self.repositorio = RepositorioEmpleados(config)
            return self.repositorio
        except Exception as exc:  # noqa: BLE001 - sin SQL se usan caché y maestro
            LOGGER.info("Consulta SQL no disponible para coincidencias: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Búsqueda
    # ------------------------------------------------------------------
    def _consultar_sql(
        self,
        claves: Sequence[str],
        originales: Mapping[str, str],
        empresas: Sequence[str] | None,
        *,
        al_progreso: Callable[[dict[str, Any]], None] | None = None,
        debe_cancelar: Callable[[], bool] | None = None,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        faltantes = [clave for clave in claves if clave and clave not in self._cache_sql]
        if not faltantes:
            return {clave: self._cache_sql[clave] for clave in claves}, []

        repositorio = self._repositorio_activo()
        if repositorio is None:
            return {}, []

        resultado: dict[str, list[dict[str, Any]]] = {}
        errores: list[dict[str, Any]] = []
        try:
            consulta = repositorio.buscar_por_nombres(
                [originales[clave] for clave in faltantes],
                empresas,
                al_progreso=al_progreso,
                debe_cancelar=debe_cancelar,
            )
        except Exception as exc:  # noqa: BLE001 - nunca bloquea la revisión
            LOGGER.warning("Falló la búsqueda SQL de coincidencias: %s", exc)
            return {}, [{"empresa": "", "mensaje": str(exc), "categoria": "consulta"}]

        errores.extend(getattr(consulta, "errores", []) or [])
        self._ultima_consulta = {
            "empleados": len(getattr(consulta, "empleados", []) or []),
            "errores": len(errores),
            "consultas": len(faltantes),
            "cancelada": bool(getattr(consulta, "cancelada", False)),
        }
        por_consulta = getattr(consulta, "por_consulta", {}) or {}
        for clave in faltantes:
            empleados = por_consulta.get(clave) or []
            # Un plan de búsqueda puede normalizar distinto; se acepta también
            # la clave sin espacios para no perder candidatos.
            if not empleados:
                for otra_clave, lista in por_consulta.items():
                    if otra_clave.replace(" ", "") == clave.replace(" ", ""):
                        empleados = lista
                        break
            candidatos = [
                candidato_desde_empleado(
                    empleado,
                    originales[clave],
                    origen=ORIGEN_SQL,
                )
                for empleado in empleados
            ]
            self._cache_sql[clave] = candidatos
            resultado[clave] = candidatos

        for clave in claves:
            if clave in self._cache_sql:
                resultado.setdefault(clave, self._cache_sql[clave])
        return resultado, errores

    def buscar(
        self,
        usuarios: Iterable[Any],
        empresas: Sequence[str] | None = None,
        *,
        usar_sql: bool = True,
        limite: int | None = None,
        umbral: float | None = None,
        al_progreso: Callable[[dict[str, Any]], None] | None = None,
        debe_cancelar: Callable[[], bool] | None = None,
    ) -> dict[str, GrupoCoincidencias]:
        """Devuelve ``{usuario_original: GrupoCoincidencias}``.

        Los usuarios se deduplican por clave normalizada, pero se conserva el
        texto original de cada uno. No se elimina ningún registro del Excel.
        """

        limite = max(1, int(limite or self.limite))
        umbral = float(self.umbral if umbral is None else umbral)

        grupos: dict[str, GrupoCoincidencias] = {}
        originales: dict[str, str] = {}
        for usuario in usuarios:
            texto = str(usuario or "").strip()
            if not texto:
                continue
            clave = normalizar_nombre(texto)
            if not clave:
                continue
            originales.setdefault(clave, texto)
            grupos.setdefault(clave, GrupoCoincidencias(usuario=texto, clave=clave))

        if not grupos:
            return {}

        indice_cache = self._indice_de_cache()
        maestro = self._cargar_maestro()
        candidatos_sql: dict[str, list[dict[str, Any]]] = {}
        errores_sql: list[dict[str, Any]] = []
        if usar_sql:
            candidatos_sql, errores_sql = self._consultar_sql(
                list(grupos),
                originales,
                empresas,
                al_progreso=al_progreso,
                debe_cancelar=debe_cancelar,
            )

        for clave, grupo in grupos.items():
            candidatos: list[dict[str, Any]] = []
            vistos: set[tuple[str, str, str, str]] = set()

            def agregar(item: dict[str, Any]) -> None:
                huella = (
                    str(item.get("origen") or ""),
                    str(item.get("empresa") or ""),
                    str(item.get("empleado") or ""),
                    str(item.get("nombre") or "").upper(),
                )
                if huella in vistos:
                    return
                vistos.add(huella)
                candidatos.append(item)

            # 1) Coincidencias exactas de la caché local.
            for empleado in indice_cache.get(clave, []):
                agregar(
                    candidato_desde_empleado(
                        empleado,
                        originales[clave],
                        origen=ORIGEN_CACHE,
                    )
                )
            # 2) Resultados SQL por lotes.
            for item in candidatos_sql.get(clave, []):
                agregar(dict(item))
            # 3) Coincidencias del maestro (exactas y aproximadas).
            for item in maestro:
                if _claves(item.get("nombre")) & {clave, " ".join(sorted(clave.split()))}:
                    agregar({**item, "usuario_original": originales[clave]})
                else:
                    puntaje = similitud.comparar(clave, item.get("nombre"))
                    if puntaje >= umbral:
                        agregar(
                            {
                                **item,
                                "usuario_original": originales[clave],
                                "_puntaje": puntaje,
                            }
                        )

            puntuados: list[dict[str, Any]] = []
            for item in candidatos:
                puntuado = _puntuar(clave, dict(item))
                if puntuado["tipo"] == similitud.SIN_COINCIDENCIA:
                    continue
                if puntuado["similitud"] < umbral and puntuado["tipo"] != similitud.EXACTA:
                    continue
                puntuados.append(puntuado)

            # Exactas primero, luego por similitud y empresa para no mezclar.
            puntuados.sort(
                key=lambda item: (
                    0 if item.get("tipo") == similitud.EXACTA else 1,
                    -float(item.get("similitud") or 0.0),
                    str(item.get("empresa") or ""),
                    str(item.get("nombre") or ""),
                )
            )
            grupo.candidatos = puntuados[:limite]
            if errores_sql:
                grupo.errores = list(errores_sql)

        return grupos

    def buscar_para_pendientes(
        self,
        pendientes: Iterable[Mapping[str, Any]],
        empresas: Sequence[str] | None = None,
        *,
        usar_sql: bool = True,
        limite: int | None = None,
        umbral: float | None = None,
    ) -> list[dict[str, Any]]:
        """Consolida una lista de pendientes del manifiesto para la interfaz."""

        lista = [item for item in pendientes if isinstance(item, Mapping)]
        usuarios = [str(item.get("usuario") or item.get("clave") or "") for item in lista]
        grupos = self.buscar(
            usuarios,
            empresas,
            usar_sql=usar_sql,
            limite=limite,
            umbral=umbral,
        )
        resultado: list[dict[str, Any]] = []
        for item in lista:
            usuario = str(item.get("usuario") or item.get("clave") or "")
            clave = normalizar_nombre(usuario)
            grupo = grupos.get(clave)
            resultado.append(
                {
                    "usuario": usuario,
                    "clave": clave,
                    "filas": len(item.get("filas") or [])
                    if isinstance(item.get("filas"), list)
                    else int(item.get("filas") or 0),
                    "area_preexistente": str(item.get("area_preexistente") or ""),
                    "candidatos": grupo.candidatos if grupo else [],
                    "exactos": len(grupo.exactos) if grupo else 0,
                    "aproximados": len(grupo.aproximados) if grupo else 0,
                    "errores": grupo.errores if grupo else [],
                }
            )
        return resultado

    @property
    def ultima_consulta(self) -> dict[str, Any]:
        return dict(self._ultima_consulta)


def consolidar_coincidencias(
    usuarios: Iterable[Any],
    *,
    maestro: Any = None,
    repositorio: Any = None,
    empresas: Sequence[str] | None = None,
    usar_sql: bool = True,
    limite: int = 8,
    umbral: float = 0.72,
    cargar_empleados: Callable[[], Sequence[Empleado]] | None = None,
) -> dict[str, GrupoCoincidencias]:
    """Atajo de una sola ejecución."""

    consolidador = ConsolidadorCoincidencias(
        maestro=maestro,
        repositorio=repositorio,
        cargar_empleados=cargar_empleados,
        limite=limite,
        umbral=umbral,
    )
    return consolidador.buscar(
        usuarios,
        empresas,
        usar_sql=usar_sql,
        limite=limite,
        umbral=umbral,
    )
