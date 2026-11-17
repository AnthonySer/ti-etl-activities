"""Capa pura de presentación para coincidencias Excel/SQL.

Este módulo no depende de Tkinter, no consulta SQL y no escribe archivos. Su
responsabilidad es normalizar los candidatos que ya obtuvieron la ventana de
revisión, para que pueda:

* conservar la procedencia y los identificadores originales;
* distinguir ACTIVO, INACTIVO y NO DISPONIBLE sin inferir estados;
* marcar cruces entre Excel y SQL solo cuando la identidad es verificable;
* contar y filtrar por fuente.

La evaluación de conflictos de área y la asignación siguen siendo
responsabilidad de ``database.resolucion.ResolverAreas`` y de los servicios de
asignación existentes. Este módulo no evalúa ni sobrescribe conflictos.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from core import seleccion_empleados as sel
from database.normalizador import normalizar_nombre

PESTANA_TODOS = "Todos"
PESTANA_EXCEL = "Excel maestro"
PESTANA_SQL = "Base de datos SQL"
PESTANAS = (PESTANA_TODOS, PESTANA_EXCEL, PESTANA_SQL)

PROCEDENCIA_EXCEL = PESTANA_EXCEL
PROCEDENCIA_SQL = PESTANA_SQL

ESTADO_ACTIVO = "ACTIVO"
ESTADO_INACTIVO = "INACTIVO"
ESTADO_NO_DISPONIBLE = "NO DISPONIBLE"

CRUCE_CONFIRMADO = "CONFIRMADA"
CRUCE_POSIBLE = "POSIBLE"
CRUCE_NINGUNO = ""

ORIGEN_MAESTRO = "MAESTRO"
ORIGEN_SQL = "SQL_SERVER"

# Códigos de estado que el resto del sistema ya reconoce y que no cubre
# ``seleccion_empleados.etiqueta_activo`` de forma explícita. Se alinean con
# ``Empleado.inactivo`` y ``database.repository._booleano``.
_ACTIVOS_ESPECIALES = {"A"}
_INACTIVOS_ESPECIALES = {"I", "INAC", "CESA"}

# Solo se inspeccionan campos de estado del empleado. No se usa ``estado``
# genérico para evitar confundir estados del flujo de revisión con el estado
# laboral del trabajador.
_CAMPOS_ESTADO = (
    "activo",
    "ACTIVO",
    "estado_empleado",
    "ESTADO_EMPLEADO",
)


def _texto(valor: Any) -> str:
    """Texto limpio y uniforme; ``None`` y valores ausentes quedan vacíos."""

    return sel.texto(valor, "")


def _clave(valor: Any) -> str:
    """Clave de comparación de nombres, áreas y etiquetas conocidas."""

    return normalizar_nombre(valor) or ""


def _etiqueta_estado(valor: Any) -> str:
    """Traduce un valor de estado a una de las tres etiquetas permitidas."""

    etiqueta = sel.etiqueta_activo(valor)
    if etiqueta == "Activo":
        return ESTADO_ACTIVO
    if etiqueta == "Inactivo":
        return ESTADO_INACTIVO

    normalizado = sel.normalizar_busqueda(valor)
    if normalizado in _ACTIVOS_ESPECIALES:
        return ESTADO_ACTIVO
    if normalizado in _INACTIVOS_ESPECIALES:
        return ESTADO_INACTIVO
    return ESTADO_NO_DISPONIBLE


def _etiqueta_conocida(valor: Any) -> str | None:
    """Estado afirmable de un valor, o ``None`` si no aporta información."""

    if _texto(valor) == "":
        return None
    etiqueta = _etiqueta_estado(valor)
    return None if etiqueta == ESTADO_NO_DISPONIBLE else etiqueta


def hay_contradiccion_estado(candidato: Mapping[str, Any]) -> bool:
    """Indica si los campos de estado de un registro se contradicen.

    Solo cuentan los valores que expresan un estado afirmable. Un campo vacío
    o con un valor desconocido no contradice a otro campo conocido; dos campos
    que afirman estados distintos (por ejemplo ``activo=True`` y
    ``ACTIVO=False``) sí lo hacen.
    """

    etiquetas = {
        etiqueta
        for etiqueta in (
            _etiqueta_conocida(candidato.get(campo))
            for campo in _CAMPOS_ESTADO
        )
        if etiqueta is not None
    }
    return len(etiquetas) > 1


def estado_candidato(candidato: Mapping[str, Any]) -> str:
    """Devuelve ACTIVO, INACTIVO o NO DISPONIBLE.

    Solo se afirma un estado si todos los campos disponibles coinciden. Si no
    hay información, si los datos se contradicen o si el candidato ya trae la
    marca ``estado_contradictorio``, se devuelve ``NO DISPONIBLE``: nunca se
    infiere un estado laboral.
    """

    if candidato.get("estado_contradictorio"):
        return ESTADO_NO_DISPONIBLE

    etiquetas = [
        _etiqueta_estado(candidato.get(campo)) for campo in _CAMPOS_ESTADO
    ]
    conocidas = [
        etiqueta for etiqueta in etiquetas
        if etiqueta != ESTADO_NO_DISPONIBLE
    ]
    if not conocidas:
        return ESTADO_NO_DISPONIBLE
    if all(etiqueta == ESTADO_ACTIVO for etiqueta in conocidas):
        return ESTADO_ACTIVO
    if all(etiqueta == ESTADO_INACTIVO for etiqueta in conocidas):
        return ESTADO_INACTIVO
    # Información contradictoria: no se puede afirmar el estado.
    return ESTADO_NO_DISPONIBLE


def es_inactivo(candidato: Mapping[str, Any]) -> bool:
    """Indica si el estado es inequívocamente INACTIVO."""

    return estado_candidato(candidato) == ESTADO_INACTIVO


def origen_candidato(candidato: Mapping[str, Any]) -> str:
    """Clasifica el candidato como Excel maestro o Base de datos SQL."""

    for campo in ("origen", "ORIGEN", "fuente", "FUENTE", "empresa", "EMPRESA"):
        if _clave(candidato.get(campo)) == _clave(ORIGEN_MAESTRO):
            return PROCEDENCIA_EXCEL
    return PROCEDENCIA_SQL


def _identidad_completa(candidato: Mapping[str, Any]) -> tuple[str, str] | None:
    """Identidad verificable ``(empresa, empleado)`` o ``None``.

    Se exige empresa y código de empleado. La etiqueta ``MAESTRO`` no es una
    empresa real y, por tanto, no permite confirmar identidad.
    """

    empresa, _fuente, empleado = sel.identidad(candidato)
    if not empresa or not empleado:
        return None
    if empresa == _clave(ORIGEN_MAESTRO):
        return None
    return (empresa, empleado)


def _claves_nombre(candidato: Mapping[str, Any]) -> set[str]:
    """Claves de nombre que toleran el orden nombre/apellido."""

    nombre = (
        candidato.get("nombre")
        or candidato.get("NOMBRE")
        or candidato.get("usuario")
        or candidato.get("USUARIO")
    )
    clave = _clave(nombre)
    if not clave:
        return set()
    claves = {clave}
    if " " in clave:
        claves.add(" ".join(sorted(clave.split())))
    return claves


def _nombres_coinciden(
    izquierda: Mapping[str, Any], derecha: Mapping[str, Any]
) -> bool:
    """Afinidad de nombre que nunca basta para confirmar identidad.

    Se acepta el mismo nombre normalizado (en cualquier orden) o que el nombre
    más corto esté contenido en el más largo como palabras completas. El
    segundo caso cubre el maestro, que suele guardar solo nombre y apellido,
    mientras SQL devuelve el nombre completo. Ambas ramas son solo indicios:
    el resultado se marca como posible coincidencia, nunca como confirmada.
    """

    claves_izq = _claves_nombre(izquierda)
    claves_der = _claves_nombre(derecha)
    if claves_izq & claves_der:
        return True
    for clave_izq in claves_izq:
        palabras_izq = set(clave_izq.split())
        if len(palabras_izq) < 2:
            continue
        for clave_der in claves_der:
            palabras_der = set(clave_der.split())
            if len(palabras_der) < 2:
                continue
            if palabras_izq <= palabras_der or palabras_der <= palabras_izq:
                return True
    return False


def _huella(candidato: Mapping[str, Any]) -> tuple[str, ...]:
    """Huella para eliminar solo duplicados idénticos dentro de una fuente."""

    empresa, fuente, empleado = sel.identidad(candidato)
    nombre = _clave(
        candidato.get("nombre")
        or candidato.get("NOMBRE")
        or candidato.get("usuario")
        or candidato.get("USUARIO")
    )
    area = _clave(
        candidato.get("area")
        or candidato.get("AREA")
        or candidato.get("departamento")
        or candidato.get("DEPARTAMENTO")
        or candidato.get("AREA_MAESTRO")
        or candidato.get("AREA_SQL")
    )
    return (
        str(candidato.get("procedencia") or ""),
        _clave(candidato.get("origen") or candidato.get("ORIGEN")),
        empresa,
        fuente,
        empleado,
        nombre,
        area,
    )


def _tabla_origen(candidato: Mapping[str, Any]) -> str:
    """Nombre de tabla real; no repite la empresa como si fuera una tabla."""

    tabla = _texto(candidato.get("TABLA") or candidato.get("tabla"))
    if tabla:
        return tabla

    # Una fila del maestro no proviene de una tabla SQL, aunque su ``fuente``
    # diga ``MAESTRO``.
    if candidato.get("procedencia") == PROCEDENCIA_EXCEL:
        return ""

    fuente = _texto(candidato.get("FUENTE") or candidato.get("fuente"))
    empresa = _texto(candidato.get("EMPRESA") or candidato.get("empresa"))
    if fuente and _clave(fuente) != _clave(empresa):
        return fuente
    return ""


def preparar_candidato(
    candidato: Mapping[str, Any],
    *,
    procedencia: str | None = None,
    area_maestro: str = "",
) -> dict[str, Any]:
    """Copia el candidato y agrega campos de presentación.

    Conserva todas las claves originales (identificadores, conflicto, motivo,
    estado de resolución, etc.). No modifica el diccionario recibido.
    """

    datos = dict(candidato)
    origen = procedencia or origen_candidato(datos)
    if origen not in (PROCEDENCIA_EXCEL, PROCEDENCIA_SQL):
        origen = PROCEDENCIA_SQL
    datos["procedencia"] = origen

    if not _texto(datos.get("origen") or datos.get("ORIGEN")):
        datos["origen"] = (
            ORIGEN_MAESTRO if origen == PROCEDENCIA_EXCEL else ORIGEN_SQL
        )

    datos["tabla_origen"] = _tabla_origen(datos)
    datos["estado_contradictorio"] = (
        bool(datos.get("estado_contradictorio"))
        or hay_contradiccion_estado(datos)
    )
    datos["estado_usuario"] = estado_candidato(datos)
    datos["cruce_fuentes"] = CRUCE_NINGUNO
    datos["coincide_entre_fuentes"] = False
    datos["es_posible_coincidencia"] = False

    area_maestro_param = _texto(area_maestro)
    if origen == PROCEDENCIA_EXCEL:
        area_maestro_final = (
            _texto(datos.get("AREA_MAESTRO"))
            or _texto(
                datos.get("area")
                or datos.get("AREA")
                or datos.get("departamento")
                or datos.get("DEPARTAMENTO")
            )
            or area_maestro_param
        )
        # Una fila del maestro nunca aporta área SQL.
        datos["AREA_SQL"] = ""
        if area_maestro_final:
            datos["AREA_MAESTRO"] = area_maestro_final
    else:
        area_sql = (
            _texto(datos.get("AREA_SQL"))
            or _texto(
                datos.get("DEPARTAMENTO")
                or datos.get("departamento")
                or datos.get("area")
                or datos.get("AREA")
            )
        )
        datos["AREA_SQL"] = area_sql
        if area_maestro_param:
            datos["AREA_MAESTRO"] = area_maestro_param

    return datos


def _aplicar_cruce(candidato: dict[str, Any], tipo: str) -> None:
    if tipo == CRUCE_CONFIRMADO:
        candidato["cruce_fuentes"] = CRUCE_CONFIRMADO
        candidato["coincide_entre_fuentes"] = True
        candidato["es_posible_coincidencia"] = False
        return
    if tipo == CRUCE_POSIBLE and candidato.get("cruce_fuentes") != CRUCE_CONFIRMADO:
        candidato["cruce_fuentes"] = CRUCE_POSIBLE
        candidato["coincide_entre_fuentes"] = False
        candidato["es_posible_coincidencia"] = True


def _conciliar_estado(
    izquierda: dict[str, Any], derecha: dict[str, Any]
) -> None:
    """Completa estado solo con identidad confirmada y sin contradicciones."""

    # Una contradicción interna de cualquiera de las dos fuentes no puede
    # quedar oculta por el estado de la otra.
    if izquierda.get("estado_contradictorio") or derecha.get(
        "estado_contradictorio"
    ):
        for candidato in (izquierda, derecha):
            candidato["estado_contradictorio"] = True
            candidato["estado_usuario"] = ESTADO_NO_DISPONIBLE
        return

    estado_izq = izquierda.get("estado_usuario")
    estado_der = derecha.get("estado_usuario")
    if estado_izq == ESTADO_NO_DISPONIBLE and estado_der != ESTADO_NO_DISPONIBLE:
        izquierda["estado_usuario"] = estado_der
    elif estado_der == ESTADO_NO_DISPONIBLE and estado_izq != ESTADO_NO_DISPONIBLE:
        derecha["estado_usuario"] = estado_izq
    elif (
        estado_izq != ESTADO_NO_DISPONIBLE
        and estado_der != ESTADO_NO_DISPONIBLE
        and estado_izq != estado_der
    ):
        # Misma empresa y empleado, pero estados contradictorios: no se infiere.
        for candidato in (izquierda, derecha):
            candidato["estado_contradictorio"] = True
            candidato["estado_usuario"] = ESTADO_NO_DISPONIBLE


def _marcar_cruces(candidatos: list[dict[str, Any]]) -> None:
    """Marca cruces Excel/SQL por identidad o, en su defecto, por nombre."""

    excel = [
        candidato
        for candidato in candidatos
        if candidato.get("procedencia") == PROCEDENCIA_EXCEL
    ]
    sql = [
        candidato
        for candidato in candidatos
        if candidato.get("procedencia") == PROCEDENCIA_SQL
    ]

    for izquierda in excel:
        identidad_izq = _identidad_completa(izquierda)
        for derecha in sql:
            identidad_der = _identidad_completa(derecha)

            if identidad_izq is not None and identidad_der is not None:
                if identidad_izq != identidad_der:
                    # Identidades completas y distintas: no es el mismo
                    # empleado de la misma empresa.
                    continue
                tipo = CRUCE_CONFIRMADO
            elif _nombres_coinciden(izquierda, derecha):
                # Sin identidad verificable solo se puede afirmar una
                # posible coincidencia por nombre.
                tipo = CRUCE_POSIBLE
            else:
                continue

            _aplicar_cruce(izquierda, tipo)
            _aplicar_cruce(derecha, tipo)
            if tipo == CRUCE_CONFIRMADO:
                _conciliar_estado(izquierda, derecha)


def _fusionar_campo_estado(
    base: dict[str, Any], clave: str, valor: Any
) -> bool:
    """Combina un campo de estado y avisa si aporta una contradicción."""

    actual = base.get(clave)
    texto_actual = _texto(actual)
    texto_nuevo = _texto(valor)

    if not texto_nuevo:
        return False
    if not texto_actual:
        base[clave] = valor
        return False

    etiqueta_actual = _etiqueta_conocida(actual)
    etiqueta_nueva = _etiqueta_conocida(valor)

    if (
        etiqueta_actual is not None
        and etiqueta_nueva is not None
        and etiqueta_actual != etiqueta_nueva
    ):
        # Dos valores afirmables distintos: se conserva el primero para no
        # perder el dato, pero la fila queda marcada como contradictoria.
        return True
    if etiqueta_actual is None and etiqueta_nueva is not None:
        # El valor previo no informaba estado; se completa con el conocida.
        base[clave] = valor
    return False


def _fusionar_duplicado(base: dict[str, Any], nuevo: dict[str, Any]) -> None:
    """Completa un duplicado idéntico sin perder información disponible.

    Si dos filas de la misma fuente describen el mismo registro, se conserva
    una sola, pero se completan los campos ausentes con la fila más rica. Los
    valores de estado se fusionan semánticamente: coincidentes se conservan,
    conocidos completan vacíos y contradictorios marcan la fila como
    ``NO DISPONIBLE``. El estado se recalcula después de la fusión.
    """

    conflicto = bool(base.get("estado_contradictorio"))

    for clave, valor in nuevo.items():
        if clave in {
            "estado_usuario",
            "estado_contradictorio",
            "cruce_fuentes",
            "coincide_entre_fuentes",
            "es_posible_coincidencia",
            "tabla_origen",
        }:
            if clave == "estado_contradictorio":
                conflicto = conflicto or bool(valor)
            continue
        if clave in _CAMPOS_ESTADO:
            conflicto = _fusionar_campo_estado(base, clave, valor) or conflicto
            continue
        if clave not in base or _texto(base.get(clave)) == "":
            base[clave] = valor

    conflicto = conflicto or hay_contradiccion_estado(base)
    base["estado_contradictorio"] = conflicto
    base["estado_usuario"] = (
        ESTADO_NO_DISPONIBLE if conflicto else estado_candidato(base)
    )
    if not base.get("tabla_origen"):
        base["tabla_origen"] = _tabla_origen(base)


def consolidar(
    candidatos: Iterable[Mapping[str, Any]],
    *,
    area_maestro: str = "",
) -> list[dict[str, Any]]:
    """Une Excel y SQL sin perder filas de fuentes distintas.

    Solo elimina duplicados idénticos dentro de la misma fuente. Nunca
    descarta una fila de Excel porque exista una fila SQL parecida, ni
    viceversa. Si un duplicado trae campos que el otro no tenía, se completan
    antes de descartarlo.
    """

    preparados: list[dict[str, Any]] = []
    posiciones: dict[tuple[str, ...], int] = {}

    for candidato in candidatos:
        if not isinstance(candidato, Mapping):
            continue
        datos = preparar_candidato(candidato, area_maestro=area_maestro)
        huella = _huella(datos)
        posicion = posiciones.get(huella)
        if posicion is not None:
            _fusionar_duplicado(preparados[posicion], datos)
            continue
        posiciones[huella] = len(preparados)
        preparados.append(datos)

    _marcar_cruces(preparados)
    return preparados


def _estado_efectivo(candidato: Mapping[str, Any]) -> str:
    """Estado consolidado para conteos, respetando la marca de contradicción."""

    if candidato.get("estado_contradictorio"):
        return ESTADO_NO_DISPONIBLE
    estado = candidato.get("estado_usuario")
    if estado in (ESTADO_ACTIVO, ESTADO_INACTIVO, ESTADO_NO_DISPONIBLE):
        return estado
    return estado_candidato(candidato)


def resumen(candidatos: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Conteos de coincidencias para el encabezado de la ventana.

    Los estados se cuentan por fila/coincidencia, no por persona única, porque
    una misma persona puede aparecer en varias empresas o fuentes. Los cruces
    también se cuentan por fila marcada.
    """

    lista = list(candidatos)
    return {
        "total": len(lista),
        "excel": sum(
            1 for candidato in lista
            if candidato.get("procedencia") == PROCEDENCIA_EXCEL
        ),
        "sql": sum(
            1 for candidato in lista
            if candidato.get("procedencia") == PROCEDENCIA_SQL
        ),
        "activos": sum(
            1 for candidato in lista
            if _estado_efectivo(candidato) == ESTADO_ACTIVO
        ),
        "inactivos": sum(
            1 for candidato in lista
            if _estado_efectivo(candidato) == ESTADO_INACTIVO
        ),
        "no_disponibles": sum(
            1 for candidato in lista
            if _estado_efectivo(candidato) == ESTADO_NO_DISPONIBLE
        ),
        "filas_cruce_confirmado": sum(
            1 for candidato in lista
            if candidato.get("cruce_fuentes") == CRUCE_CONFIRMADO
        ),
        "filas_cruce_posible": sum(
            1 for candidato in lista
            if candidato.get("cruce_fuentes") == CRUCE_POSIBLE
        ),
    }


def filtrar(
    candidatos: Iterable[Mapping[str, Any]],
    pestana: str = PESTANA_TODOS,
) -> list[Mapping[str, Any]]:
    """Filtra por pestaña conservando el orden recibido."""

    if pestana == PESTANA_TODOS:
        return list(candidatos)
    if pestana not in (PESTANA_EXCEL, PESTANA_SQL):
        raise ValueError(f"Pestaña desconocida: {pestana!r}")
    return [
        candidato
        for candidato in candidatos
        if candidato.get("procedencia") == pestana
    ]


def texto_cruce(candidato: Mapping[str, Any]) -> str:
    """Etiqueta legible para la columna de cruce entre fuentes."""

    tipo = candidato.get("cruce_fuentes")
    if tipo == CRUCE_CONFIRMADO:
        return "Confirmada (empresa + empleado)"
    if tipo == CRUCE_POSIBLE:
        return "Posible (sin identidad)"
    return ""


__all__ = [
    "CRUCE_CONFIRMADO",
    "CRUCE_NINGUNO",
    "CRUCE_POSIBLE",
    "ESTADO_ACTIVO",
    "ESTADO_INACTIVO",
    "ESTADO_NO_DISPONIBLE",
    "PESTANA_EXCEL",
    "PESTANA_SQL",
    "PESTANA_TODOS",
    "PESTANAS",
    "PROCEDENCIA_EXCEL",
    "PROCEDENCIA_SQL",
    "consolidar",
    "es_inactivo",
    "estado_candidato",
    "filtrar",
    "hay_contradiccion_estado",
    "origen_candidato",
    "preparar_candidato",
    "resumen",
    "texto_cruce",
]
