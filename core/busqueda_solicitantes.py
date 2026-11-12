"""Planificación segura de búsquedas de solicitantes.

Este módulo es puro: no depende de Tkinter ni de SQL Server. Convierte el texto
del Excel en términos de búsqueda, decide el modo (una palabra, dos palabras o
orden manual) y genera patrones ``LIKE`` parametrizables.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

from database.normalizador import normalizar_nombre

MODO_AUTOMATICA = "automatica"
MODO_INVERTIDA = "invertida"
MODO_MANUAL = "manual"

_CARACTERES_LIKE = ("\\", "%", "_", "[", "]")


def normalizar_terminos(valor: Any) -> list[str]:
    """Divide el solicitante en términos sin tildes, signos ni vacíos.

    El valor original del Excel no se modifica: esta función solo produce el
    criterio de búsqueda.
    """

    clave = normalizar_nombre(valor)
    if not clave:
        return []
    return [termino for termino in clave.split() if termino]


def escapar_like(termino: Any) -> str:
    """Escapa los comodines de SQL Server dentro de un valor de ``LIKE``."""

    texto = str(termino or "")
    for caracter in _CARACTERES_LIKE:
        texto = texto.replace(caracter, "\\" + caracter)
    return texto


def patron_like(terminos: Iterable[Any]) -> str:
    """Genera ``%T1%T2%...%`` conservando el orden recibido.

    Los términos se escapan para que ``%``, ``_``, ``[`` y ``]`` se busquen
    como texto literal. El SQL debe declarar ``ESCAPE '\\'``.
    """

    limpios = [escapar_like(termino) for termino in terminos if str(termino or "").strip()]
    if not limpios:
        return ""
    return "%" + "%".join(limpios) + "%"


def patron_visible(terminos: Iterable[Any]) -> str:
    """Patrón legible para la vista previa de la ventana de ordenamiento."""

    return patron_like(terminos)


def descripcion_orden(terminos: Iterable[Any]) -> str:
    return " → ".join(str(termino) for termino in terminos)


def _texto_original(valor: Any) -> str:
    if valor is None:
        return ""
    return " ".join(str(valor).strip().split())


@dataclass(frozen=True)
class PlanBusqueda:
    """Consulta preparada para un solicitante concreto."""

    solicitante: str
    terminos: tuple[str, ...]
    modo: str = MODO_AUTOMATICA
    terminos_originales: tuple[str, ...] = ()

    @property
    def clave(self) -> str:
        return normalizar_nombre(self.solicitante) or " ".join(self.terminos)

    @property
    def patron(self) -> str:
        return patron_like(self.terminos)

    @property
    def requiere_orden_manual(self) -> bool:
        return self.modo == MODO_MANUAL

    def como_dict(self) -> dict[str, Any]:
        return {
            "solicitante": self.solicitante,
            "clave": self.clave,
            "terminos": list(self.terminos),
            "patron": self.patron,
            "modo": self.modo,
        }

    def con_orden(self, terminos: Sequence[Any]) -> "PlanBusqueda":
        """Valida el orden elegido y devuelve un plan manual.

        El usuario solo puede reordenar los términos detectados; no puede
        agregar, quitar ni alterar palabras desde la interfaz.
        """

        propuestos = [str(termino or "").strip().upper() for termino in terminos]
        propuestos = [termino for termino in propuestos if termino]
        if sorted(propuestos) != sorted(self.terminos):
            raise ValueError(
                "El nuevo orden debe contener exactamente los mismos términos."
            )
        return replace(self, terminos=tuple(propuestos), modo=MODO_MANUAL)


def plan_busqueda(solicitante: Any) -> PlanBusqueda:
    """Decide el comportamiento según la cantidad de palabras.

    - Una palabra: búsqueda automática ``%termino%``.
    - Dos palabras: se invierte el orden automáticamente.
    - Tres o más: se mantiene el orden original y la interfaz ofrece el
      ordenamiento manual antes de consultar.
    """

    original = _texto_original(solicitante)
    terminos = normalizar_terminos(original)
    if len(terminos) == 1:
        return PlanBusqueda(
            solicitante=original,
            terminos=tuple(terminos),
            modo=MODO_AUTOMATICA,
            terminos_originales=tuple(terminos),
        )
    if len(terminos) == 2:
        return PlanBusqueda(
            solicitante=original,
            terminos=(terminos[1], terminos[0]),
            modo=MODO_INVERTIDA,
            terminos_originales=tuple(terminos),
        )
    modo = MODO_MANUAL if len(terminos) >= 3 else MODO_AUTOMATICA
    return PlanBusqueda(
        solicitante=original,
        terminos=tuple(terminos),
        modo=modo,
        terminos_originales=tuple(terminos),
    )


def plan_automatico(solicitante: Any) -> PlanBusqueda:
    """Plan sin interacción para procesos que no pueden abrir ventanas.

    Una y dos palabras se comportan igual que en la interfaz; tres o más
    conservan el orden original para no adivinar ni multiplicar permutaciones.
    """

    return plan_busqueda(solicitante)


def coincide_terminos(nombre: Any, terminos: Iterable[Any]) -> bool:
    """Comprueba que los términos aparezcan en el nombre en ese orden."""

    buscados = [str(termino or "").strip().upper() for termino in terminos]
    buscados = [termino for termino in buscados if termino]
    if not buscados:
        return False
    partes = normalizar_nombre(nombre).split()
    if not partes:
        return False
    posicion = 0
    for termino in buscados:
        encontrado = -1
        for indice in range(posicion, len(partes)):
            if partes[indice] == termino:
                encontrado = indice
                break
        if encontrado < 0:
            return False
        posicion = encontrado + 1
    return True


def coincide_consulta(empleado: Any, consulta: Mapping[str, Any]) -> bool:
    """Indica si un empleado corresponde al plan de una consulta."""

    terminos = [str(termino or "").strip().upper() for termino in consulta.get("terminos") or []]
    terminos = [termino for termino in terminos if termino]
    nombre = getattr(empleado, "nombre_completo", "")
    if len(terminos) == 1:
        # El SQL usa %termino%: también coincide con una palabra contenida.
        return terminos[0] in normalizar_nombre(nombre)
    return coincide_terminos(nombre, terminos)


def terminos_uniformes(texto: Any) -> list[str]:
    """Alias público para normalizar listas de términos en pruebas y UI."""

    if isinstance(texto, (list, tuple, set)):
        salida: list[str] = []
        for item in texto:
            salida.extend(normalizar_terminos(item))
        return salida
    return normalizar_terminos(texto)


def sin_tildes(texto: Any) -> str:  # pragma: no cover - utilidad de apoyo
    descompuesto = unicodedata.normalize("NFKD", str(texto or "").upper())
    return "".join(c for c in descompuesto if not unicodedata.combining(c))
