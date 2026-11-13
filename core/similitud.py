"""Motor de similitud textual para nombres de usuarios.

Este módulo es puro: no abre Excel, no consulta SQL y no modifica registros.
Solo compara cadenas ya normalizadas y devuelve un puntaje entre 0 y 1 junto
con una clasificación configurable.

Algoritmos incluidos:

* Jaro-Winkler, adecuado para errores de escritura y prefijos compartidos.
* Distancia de Levenshtein normalizada, para diferencias puntuales.
* Comparación por tokens, insensible al orden de nombres y apellidos.

Los umbrales por defecto se ajustan con ``UmbralesSimilitud`` o mediante las
variables de configuración ``UMBRAL_SIMILITUD_ALTA`` e
``UMBRAL_SIMILITUD_INTERMEDIA`` cuando existen en ``config.configuracion``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol, Sequence

from database.normalizador import normalizar_nombre

EXACTA = "exacta"
ALTA = "alta"
INTERMEDIA = "intermedia"
SIN_COINCIDENCIA = "sin_coincidencia"

CLASIFICACIONES = (EXACTA, ALTA, INTERMEDIA, SIN_COINCIDENCIA)


@dataclass(frozen=True)
class UmbralesSimilitud:
    """Umbrales configurables para clasificar un puntaje."""

    exacta: float = 0.999
    alta: float = 0.88
    intermedia: float = 0.72

    def __post_init__(self) -> None:
        if not 0.0 <= self.intermedia <= self.alta <= self.exacta <= 1.0:
            raise ValueError(
                "Los umbrales deben cumplir 0 <= intermedia <= alta <= exacta <= 1."
            )

    @classmethod
    def desde_configuracion(cls, config: Any = None) -> "UmbralesSimilitud":
        """Crea umbrales a partir de ``config.configuracion`` si está disponible."""

        if config is None:
            try:
                from config import configuracion as cfg
            except Exception:  # noqa: BLE001 - el módulo debe funcionar aislado
                return cls()
            config = cfg

        alta = getattr(config, "UMBRAL_SIMILITUD_ALTA", cls.alta)
        intermedia = getattr(config, "UMBRAL_SIMILITUD_INTERMEDIA", cls.intermedia)
        exacta = getattr(config, "UMBRAL_SIMILITUD_EXACTA", cls.exacta)
        try:
            return cls(
                exacta=float(exacta),
                alta=float(alta),
                intermedia=float(intermedia),
            )
        except (TypeError, ValueError):
            return cls()


UMBRALES_POR_DEFECTO = UmbralesSimilitud()


def normalizar(valor: Any) -> str:
    """Clave de comparación: mayúsculas, sin tildes, sin puntuación."""

    return normalizar_nombre(valor)


def _limitar(puntaje: float) -> float:
    return round(max(0.0, min(1.0, float(puntaje))), 4)


def jaro(a: str, b: str) -> float:
    """Similitud de Jaro entre dos cadenas."""

    if a == b:
        return 1.0
    if not a or not b:
        return 0.0

    largo_a, largo_b = len(a), len(b)
    ventana = max(largo_a, largo_b) // 2 - 1
    ventana = max(0, ventana)

    coincidencias_a = [False] * largo_a
    coincidencias_b = [False] * largo_b
    coincidencias = 0

    for indice_a, caracter in enumerate(a):
        inicio = max(0, indice_a - ventana)
        fin = min(indice_a + ventana + 1, largo_b)
        for indice_b in range(inicio, fin):
            if coincidencias_b[indice_b] or b[indice_b] != caracter:
                continue
            coincidencias_a[indice_a] = True
            coincidencias_b[indice_b] = True
            coincidencias += 1
            break

    if coincidencias == 0:
        return 0.0

    transposiciones = 0
    indice_b = 0
    for indice_a in range(largo_a):
        if not coincidencias_a[indice_a]:
            continue
        while not coincidencias_b[indice_b]:
            indice_b += 1
        if a[indice_a] != b[indice_b]:
            transposiciones += 1
        indice_b += 1

    transposiciones //= 2
    return (
        coincidencias / largo_a
        + coincidencias / largo_b
        + (coincidencias - transposiciones) / coincidencias
    ) / 3.0


def jaro_winkler(a: str, b: str, escala: float = 0.1) -> float:
    """Jaro-Winkler: premia prefijos comunes (errores de escritura típicos)."""

    a, b = normalizar(a), normalizar(b)
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    base = jaro(a, b)
    prefijo = 0
    for caracter_a, caracter_b in zip(a, b):
        if caracter_a != caracter_b or prefijo >= 4:
            break
        prefijo += 1
    return _limitar(base + prefijo * escala * (1.0 - base))


def levenshtein(a: str, b: str) -> int:
    """Distancia de edición clásica entre dos cadenas."""

    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    anterior = list(range(len(b) + 1))
    for indice_a, caracter_a in enumerate(a, 1):
        actual = [indice_a]
        for indice_b, caracter_b in enumerate(b, 1):
            costo = 0 if caracter_a == caracter_b else 1
            actual.append(
                min(
                    actual[indice_b - 1] + 1,
                    anterior[indice_b] + 1,
                    anterior[indice_b - 1] + costo,
                )
            )
        anterior = actual
    return anterior[-1]


def similitud_levenshtein(a: str, b: str) -> float:
    """Distancia de Levenshtein normalizada al rango 0..1."""

    a, b = normalizar(a), normalizar(b)
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return _limitar(1.0 - levenshtein(a, b) / max(len(a), len(b)))


def _tokens(texto: str) -> list[str]:
    return [token for token in normalizar(texto).split(" ") if token]


def similitud_tokens(a: str, b: str) -> float:
    """Similitud por tokens, insensible al orden.

    Cada token de ``a`` se empareja con el token más parecido de ``b``. Se
    combinan precisión y cobertura (F1) para no premiar listas incompletas.
    """

    tokens_a, tokens_b = _tokens(a), _tokens(b)
    if not tokens_a or not tokens_b:
        return 0.0
    if tokens_a == tokens_b:
        return 1.0
    if sorted(tokens_a) == sorted(tokens_b):
        return 1.0

    usados: set[int] = set()
    emparejados = 0.0
    for token_a in tokens_a:
        mejor, mejor_indice = 0.0, -1
        for indice, token_b in enumerate(tokens_b):
            if indice in usados:
                continue
            puntaje = max(
                jaro_winkler(token_a, token_b),
                similitud_levenshtein(token_a, token_b),
            )
            if puntaje > mejor:
                mejor, mejor_indice = puntaje, indice
        if mejor_indice >= 0 and mejor >= 0.75:
            usados.add(mejor_indice)
            emparejados += mejor

    precision = emparejados / len(tokens_a)
    cobertura = emparejados / len(tokens_b)
    if precision + cobertura == 0:
        return 0.0
    return _limitar(2 * precision * cobertura / (precision + cobertura))


def comparar(a: Any, b: Any) -> float:
    """Puntaje combinado de dos nombres.

    Se toma el mejor indicador controlado. La igualdad exacta de tokens en
    distinto orden devuelve 1.0; una contención clara (nombre incompleto) no
    supera el umbral de coincidencia exacta.
    """

    clave_a, clave_b = normalizar(a), normalizar(b)
    if not clave_a or not clave_b:
        return 0.0
    if clave_a == clave_b:
        return 1.0

    tokens_a, tokens_b = clave_a.split(), clave_b.split()
    if sorted(tokens_a) == sorted(tokens_b):
        return 1.0

    jw = jaro_winkler(clave_a, clave_b)
    lev = similitud_levenshtein(clave_a, clave_b)

    # Descarte temprano: si ambas métricas de caracteres son bajas y no hay
    # contención, la comparación por tokens tampoco puede superar el umbral.
    # Esto evita calcular Jaro-Winkler y Levenshtein por cada par de tokens en
    # la mayoría de nombres no relacionados.
    if jw < 0.55 and lev < 0.55 and clave_a not in clave_b and clave_b not in clave_a:
        return _limitar(max(jw, lev * 0.97))

    tok = similitud_tokens(clave_a, clave_b)

    # Contención: "ANA TORRE" dentro de "ANA TORRES" o un nombre incompleto.
    contencion = 0.0
    if clave_a in clave_b or clave_b in clave_a:
        menor = min(len(tokens_a), len(tokens_b))
        mayor = max(len(tokens_a), len(tokens_b))
        proporcion = menor / mayor if mayor else 0.0
        if menor == mayor:
            contencion = 0.90
        else:
            contencion = 0.80 + 0.10 * proporcion

    return _limitar(max(jw, lev * 0.97, tok, contencion))


def clasificar(
    puntaje: float,
    umbrales: UmbralesSimilitud | None = None,
) -> str:
    """Clasifica un puntaje en exacta, alta, intermedia o sin coincidencia."""

    umbrales = umbrales or UMBRALES_POR_DEFECTO
    valor = float(puntaje)
    if valor >= umbrales.exacta:
        return EXACTA
    if valor >= umbrales.alta:
        return ALTA
    if valor >= umbrales.intermedia:
        return INTERMEDIA
    return SIN_COINCIDENCIA


def clasificar_nombre(
    consulta: Any,
    candidato: Any,
    umbrales: UmbralesSimilitud | None = None,
) -> tuple[float, str]:
    """Devuelve ``(puntaje, clasificación)`` para dos nombres."""

    puntaje = comparar(consulta, candidato)
    return puntaje, clasificar(puntaje, umbrales)


class ConNombre(Protocol):  # pragma: no cover - protocolo de apoyo
    def __getitem__(self, clave: str) -> Any: ...


def _nombre_de(candidato: Any) -> str:
    if isinstance(candidato, str):
        return candidato
    if isinstance(candidato, dict):
        for clave in ("nombre", "NOMBRE", "usuario", "USUARIO", "clave"):
            valor = candidato.get(clave)
            if valor:
                return str(valor)
    for atributo in ("nombre", "usuario", "clave"):
        valor = getattr(candidato, atributo, None)
        if valor:
            return str(valor)
    return str(candidato or "")


def buscar_similares(
    consulta: Any,
    candidatos: Iterable[Any],
    *,
    limite: int = 5,
    umbral: float = 0.72,
    umbrales: UmbralesSimilitud | None = None,
) -> list[tuple[float, str, Any]]:
    """Ordena candidatos por similitud y descarta los irrelevantes.

    Devuelve tuplas ``(puntaje, clasificación, candidato)``. No fusiona
    identidades: solo propone opciones para revisión humana.
    """

    umbrales = umbrales or UmbralesSimilitud(
        alta=max(0.88, umbral),
        intermedia=umbral,
    )
    encontrados: list[tuple[float, Any]] = []
    for candidato in candidatos:
        puntaje = comparar(consulta, _nombre_de(candidato))
        if puntaje >= umbral:
            encontrados.append((puntaje, candidato))

    encontrados.sort(key=lambda item: item[0], reverse=True)
    resultado: list[tuple[float, str, Any]] = []
    for puntaje, candidato in encontrados[: max(1, int(limite))]:
        resultado.append((puntaje, clasificar(puntaje, umbrales), candidato))
    return resultado


def resumen_clasificaciones(puntajes: Sequence[float]) -> dict[str, int]:
    """Cuenta puntajes por clasificación (útil para pruebas y diagnósticos)."""

    conteo = {clave: 0 for clave in CLASIFICACIONES}
    for puntaje in puntajes:
        conteo[clasificar(puntaje)] += 1
    return conteo
