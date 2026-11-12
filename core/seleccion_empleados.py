"""Utilidades puras para la selección manual de empleados.

Este módulo no depende de Tkinter ni de SQL Server. Concentra el formato
uniforme de los candidatos, los filtros de la subventana, el ordenamiento y la
validación de una selección guardada. Así la lógica puede probarse sin abrir
ventanas y la interfaz se limita a presentar los datos.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

# Campos lógicos mínimos que debe exponer cualquier registro de empresa/fuente.
CAMPOS = (
    "empleado",
    "nombre",
    "empresa",
    "fuente",
    "departamento",
    "puesto",
    "activo",
    "correo",
)

_AUSENTES = {"", "NAN", "NONE", "NULL", "NO DISPONIBLE", "SIN DATO"}

# Se aceptan tanto las claves lógicas nuevas como las históricas en mayúsculas.
_ALIASES: dict[str, tuple[str, ...]] = {
    "empleado": ("empleado", "EMPLEADO", "codigo", "CODIGO_EMPLEADO"),
    "nombre": ("nombre", "NOMBRE", "nombre_completo", "NOMBRE_COMPLETO"),
    "empresa": ("empresa", "EMPRESA"),
    "fuente": ("fuente", "FUENTE", "tabla", "TABLA"),
    "departamento": ("departamento", "DEPARTAMENTO", "departamento_descripcion"),
    "puesto": ("puesto", "PUESTO", "puesto_descripcion"),
    "activo": ("activo", "ACTIVO"),
    "correo": ("correo", "CORREO", "correo_corp", "U_CORREO_CORP", "E_MAIL"),
}


def _crudo(candidato: Mapping[str, Any], campo: str) -> Any:
    for clave in _ALIASES.get(campo, (campo,)):
        if clave in candidato:
            valor = candidato[clave]
            if not _es_ausente(valor):
                return valor
    return None


def _es_ausente(valor: Any) -> bool:
    if valor is None:
        return True
    try:
        texto = str(valor).strip().upper()
    except Exception:  # pragma: no cover - valores exóticos de pandas
        return False
    return texto in _AUSENTES


def texto(valor: Any, ausente: str = "") -> str:
    """Convierte un valor de SQL/Excel en texto limpio y sin ``nan``."""

    if _es_ausente(valor):
        return ausente
    return str(valor).strip()


def etiqueta_activo(valor: Any) -> str:
    """Devuelve una etiqueta legible para el estado activo/inactivo."""

    if isinstance(valor, bool):
        return "Activo" if valor else "Inactivo"
    if _es_ausente(valor):
        return "No disponible"
    numero = None
    try:
        numero = int(float(str(valor).strip()))
    except (TypeError, ValueError):
        numero = None
    if numero == 1:
        return "Activo"
    if numero == 0:
        return "Inactivo"
    normalizado = normalizar_busqueda(valor)
    if normalizado in {"1", "SI", "S", "ACTIVO", "ACTIVA", "YES", "Y", "TRUE"}:
        return "Activo"
    if normalizado in {
        "0",
        "NO",
        "N",
        "INACTIVO",
        "INACTIVA",
        "CESADO",
        "CESADA",
        "BAJA",
        "RETIRADO",
        "FALSE",
    }:
        return "Inactivo"
    return texto(valor, "No disponible")


def activo_booleano(valor: Any) -> bool | None:
    etiqueta = etiqueta_activo(valor)
    if etiqueta == "Activo":
        return True
    if etiqueta == "Inactivo":
        return False
    return None


def valor(candidato: Mapping[str, Any], campo: str, ausente: str = "No disponible") -> str:
    """Valor de presentación uniforme para un campo lógico."""

    crudo = _crudo(candidato, campo)
    if campo == "activo":
        etiqueta = etiqueta_activo(crudo)
        return ausente if etiqueta == "No disponible" else etiqueta
    return texto(crudo, ausente)


def registro_uniforme(candidato: Mapping[str, Any]) -> dict[str, Any]:
    """Estructura lógica uniforme con valores listos para mostrar."""

    return {campo: valor(candidato, campo) for campo in CAMPOS}


def registro_trazable(
    candidato: Mapping[str, Any],
    *,
    area: str = "",
    tipo_resolucion: str = "manual",
    fecha: str | None = None,
) -> dict[str, Any]:
    """Selección normalizada que se conserva en la revisión y la procedencia."""

    registro = registro_uniforme(candidato)
    registro.update(
        {
            "area": texto(area),
            "tipo_resolucion": texto(tipo_resolucion, "manual"),
            "fecha": fecha or datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    )
    return registro


def normalizar_busqueda(valor: Any) -> str:
    """Mayúsculas, sin tildes y con espacios colapsados para filtrar."""

    if _es_ausente(valor):
        return ""
    descompuesto = unicodedata.normalize("NFKD", str(valor).upper())
    sin_tildes = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return " ".join(sin_tildes.split())


def _coincide(valor_campo: Any, filtro: str) -> bool:
    filtro = normalizar_busqueda(filtro)
    if not filtro:
        return True
    return filtro in normalizar_busqueda(valor_campo)


def filtrar_candidatos(
    candidatos: Iterable[Mapping[str, Any]],
    *,
    empleado: str = "",
    nombre: str = "",
    empresa: str = "",
    fuente: str = "",
    departamento: str = "",
    solo_activos: bool | None = None,
) -> list[Mapping[str, Any]]:
    """Filtra sin modificar los candidatos originales."""

    salida: list[Mapping[str, Any]] = []
    for candidato in candidatos:
        if not _coincide(_crudo(candidato, "empleado"), empleado):
            continue
        if not _coincide(_crudo(candidato, "nombre"), nombre):
            continue
        if not _coincide(_crudo(candidato, "empresa"), empresa):
            continue
        if not _coincide(_crudo(candidato, "fuente"), fuente):
            continue
        if not _coincide(_crudo(candidato, "departamento"), departamento):
            continue
        estado = activo_booleano(_crudo(candidato, "activo"))
        if solo_activos is True and estado is not True:
            continue
        if solo_activos is False and estado is not False:
            continue
        salida.append(candidato)
    return salida


def _clave_orden(candidato: Mapping[str, Any], campo: str) -> tuple[int, str]:
    crudo = _crudo(candidato, campo)
    if campo == "activo":
        etiqueta = etiqueta_activo(crudo)
        texto_orden = normalizar_busqueda(etiqueta)
        return (1 if etiqueta == "No disponible" else 0, texto_orden)
    texto_orden = texto(crudo)
    return (1 if not texto_orden else 0, normalizar_busqueda(texto_orden))


def ordenar_candidatos(
    candidatos: Sequence[Mapping[str, Any]],
    campo: str,
    *,
    descendente: bool = False,
) -> list[Mapping[str, Any]]:
    """Orden estable por columna; los datos ausentes quedan al final."""

    campo = str(campo or "empleado").lower()
    if campo not in CAMPOS:
        campo = "empleado"
    indice = {id(candidato): posicion for posicion, candidato in enumerate(candidatos)}
    presentes = [
        candidato
        for candidato in candidatos
        if _clave_orden(candidato, campo)[0] == 0
    ]
    ausentes = [
        candidato
        for candidato in candidatos
        if _clave_orden(candidato, campo)[0] != 0
    ]
    presentes.sort(
        key=lambda candidato: (
            _clave_orden(candidato, campo)[1],
            indice[id(candidato)],
        ),
        reverse=descendente,
    )
    return presentes + ausentes


def opciones(candidatos: Iterable[Mapping[str, Any]], campo: str) -> list[str]:
    """Valores únicos y ordenados para un filtro de la subventana."""

    valores = {
        valor(candidato, campo)
        for candidato in candidatos
        if valor(candidato, campo) != "No disponible"
    }
    return sorted(valores, key=normalizar_busqueda)


def identidad(candidato: Mapping[str, Any]) -> tuple[str, str, str]:
    """Identidad mínima del registro: empresa, fuente y código."""

    return (
        normalizar_busqueda(_crudo(candidato, "empresa")),
        normalizar_busqueda(_crudo(candidato, "fuente")),
        normalizar_busqueda(_crudo(candidato, "empleado")),
    )


def seleccion_valida(
    guardada: Mapping[str, Any] | None,
    actual: Mapping[str, Any] | None = None,
) -> bool:
    """Comprueba que una selección guardada siga siendo utilizable.

    Si se entrega el registro actual, además de exigir los campos mínimos se
    verifica que la identidad (empresa, fuente, código) y el departamento no
    hayan cambiado. Esto evita reutilizar ciegamente decisiones antiguas.
    """

    if not isinstance(guardada, Mapping) or not guardada:
        return False
    empresa, fuente, empleado = identidad(guardada)
    if not empresa or not empleado:
        return False
    if not fuente:
        return False
    if _es_ausente(_crudo(guardada, "departamento")):
        return False
    if actual is None:
        return True

    if identidad(actual) != (empresa, fuente, empleado):
        return False
    return normalizar_busqueda(_crudo(actual, "departamento")) == normalizar_busqueda(
        _crudo(guardada, "departamento")
    )


def seleccion_desde_candidato(
    candidato: Mapping[str, Any] | None,
    **kwargs: Any,
) -> dict[str, Any] | None:
    """Devuelve la selección trazable o ``None`` si el usuario canceló."""

    if candidato is None:
        return None
    return registro_trazable(candidato, **kwargs)
