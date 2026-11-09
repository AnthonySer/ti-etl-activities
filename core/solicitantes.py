"""Detección asistida de variantes de solicitantes sin alterar los datos fuente."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd

from config import configuracion as cfg
from core import auditoria
from core.esquema import C_SOLICITANTE

LOGGER = logging.getLogger("automatizador")

_SOLICITANTES_VACIO: dict[str, Any] = {
    "version": 1,
    "equivalencias": {},
    "separados": [],
    "pendientes": [],
}
_GENERICOS = (
    "VARIOS USUARIOS",
    "SISTEMAS SERVIAM",
    "ALICORP",
)


def clave_solicitante(valor: Any) -> str:
    """Normaliza mayúsculas, tildes, espacios y caracteres especiales."""

    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    texto = unicodedata.normalize("NFKD", str(valor).upper())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in texto).split())


def es_solicitante_generico(valor: Any) -> bool:
    clave = clave_solicitante(valor)
    return any(clave == patron or clave.startswith(patron + " ") for patron in _GENERICOS)


def _ruta_equivalencias(ruta: Path | str | None = None) -> Path:
    return Path(ruta) if ruta is not None else Path(cfg.DATA_DIR) / "solicitantes_equivalencias.json"


def cargar_equivalencias(ruta: Path | str | None = None) -> dict[str, Any]:
    """Lee las equivalencias confirmadas y las decisiones de mantener separados."""

    destino = _ruta_equivalencias(ruta)
    if not destino.is_file():
        return {
            **_SOLICITANTES_VACIO,
            "equivalencias": {},
            "separados": [],
            "pendientes": [],
        }
    try:
        datos = json.loads(destino.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OSError(f"No se pudo leer el archivo de equivalencias '{destino}': {exc}") from exc
    if not isinstance(datos, dict):
        raise ValueError(f"El archivo de equivalencias '{destino}' no tiene un objeto JSON.")
    equivalencias = datos.get("equivalencias", {})
    separados = datos.get("separados", [])
    pendientes = datos.get("pendientes", [])
    if (
        not isinstance(equivalencias, dict)
        or not isinstance(separados, list)
        or not isinstance(pendientes, list)
    ):
        raise ValueError(f"El archivo de equivalencias '{destino}' tiene una estructura inválida.")
    return {
        "version": 1,
        "equivalencias": {
            clave_solicitante(alias): str(canonico).strip()
            for alias, canonico in equivalencias.items()
            if clave_solicitante(alias) and str(canonico).strip()
        },
        "separados": [
            sorted({clave_solicitante(item) for item in par if clave_solicitante(item)})
            for par in separados
            if isinstance(par, list) and len(par) == 2
        ],
        "pendientes": [
            [
                clave_solicitante(par[0]),
                clave_solicitante(par[1]) if len(par) > 1 else "",
            ]
            for par in pendientes
            if isinstance(par, list) and par and clave_solicitante(par[0])
        ],
    }


def _guardar_equivalencias(datos: dict[str, Any], ruta: Path | str | None = None) -> Path:
    destino = _ruta_equivalencias(ruta)
    destino.parent.mkdir(parents=True, exist_ok=True)
    descriptor, nombre_temporal = tempfile.mkstemp(
        prefix=f".{destino.stem}_", suffix=".tmp", dir=str(destino.parent)
    )
    temporal = Path(nombre_temporal)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as archivo:
            json.dump(datos, archivo, ensure_ascii=False, indent=2)
            archivo.write("\n")
        os.replace(temporal, destino)
    except Exception:
        temporal.unlink(missing_ok=True)
        raise
    return destino


def resolver_canonico(valor: Any, ruta: Path | str | None = None) -> str:
    """Devuelve el nombre canónico sugerido, sin modificar el valor original."""

    original = "" if valor is None else str(valor).strip()
    return cargar_equivalencias(ruta)["equivalencias"].get(
        clave_solicitante(valor), original
    )


def _similitud(a: str, b: str) -> float:
    completa = SequenceMatcher(None, a, b).ratio()
    tokens_a = a.split()
    tokens_b = b.split()
    tokens = []
    for token_a in tokens_a:
        for token_b in tokens_b:
            if token_a == token_b:
                continue
            menor, mayor = sorted((token_a, token_b), key=len)
            if len(menor) >= 4:
                tokens.append(SequenceMatcher(None, token_a, token_b).ratio())
            elif len(menor) >= 3 and mayor.startswith(menor):
                tokens.append(0.75)
    return max([completa, *tokens])


def detectar_solicitantes(
    maestro: pd.DataFrame,
    adicionales: pd.DataFrame | None = None,
    *,
    ruta_equivalencias: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Agrupa solicitantes y sugiere variantes sin fusionar registros."""

    if C_SOLICITANTE not in maestro.columns:
        raise ValueError(f"Falta la columna obligatoria '{C_SOLICITANTE}' en el maestro.")
    if adicionales is not None and C_SOLICITANTE not in adicionales.columns:
        raise ValueError(f"Falta la columna obligatoria '{C_SOLICITANTE}' en los nuevos datos.")

    frecuencias: dict[str, dict[str, int]] = {}
    conteos_maestro: dict[str, int] = {}
    conteos_adicionales: dict[str, int] = {}
    for tabla, conteos in (
        (maestro, conteos_maestro),
        (adicionales, conteos_adicionales),
    ):
        if tabla is None:
            continue
        for valor in tabla[C_SOLICITANTE].tolist():
            clave = clave_solicitante(valor)
            display = "" if valor is None else str(valor).strip()
            if not clave or not display:
                continue
            conteos[clave] = conteos.get(clave, 0) + 1
            variantes = frecuencias.setdefault(clave, {})
            variantes[display] = variantes.get(display, 0) + 1

    datos_equivalencias = cargar_equivalencias(ruta_equivalencias)
    equivalencias: dict[str, str] = datos_equivalencias["equivalencias"]
    separados = {tuple(par) for par in datos_equivalencias["separados"]}
    pendientes = {tuple(par) for par in datos_equivalencias["pendientes"]}
    claves = list(frecuencias)
    resultado: list[dict[str, Any]] = []

    for clave in claves:
        variantes = frecuencias[clave]
        display = max(variantes, key=variantes.get)
        tickets = conteos_maestro.get(clave, conteos_adicionales.get(clave, 0))
        generico = es_solicitante_generico(display)
        candidato = ""
        nivel = "Sin coincidencia"
        confianza = 0.0
        canonico = equivalencias.get(clave)
        candidato_clave = ""

        if generico:
            accion = "Mantener separado"
        elif canonico:
            candidato = canonico
            nivel = "Coincidencia exacta"
            confianza = 1.0
            accion = "Unificado"
        elif len(variantes) > 1:
            candidato = next((nombre for nombre in variantes if nombre != display), display)
            nivel = "Coincidencia exacta"
            confianza = 1.0
            candidato_clave = clave
            accion = "Pendiente"
        else:
            opciones = []
            for otra in claves:
                if otra == clave or es_solicitante_generico(max(frecuencias[otra], key=frecuencias[otra].get)):
                    continue
                if tuple(sorted((clave, otra))) in separados:
                    continue
                score = _similitud(clave, otra)
                if score >= 0.68:
                    opciones.append((score, max(frecuencias[otra], key=frecuencias[otra].get)))
            if opciones:
                confianza, candidato = max(opciones)
                nivel = "Posible coincidencia"
                candidato_clave = clave_solicitante(candidato)
            accion = "Pendiente"
        if accion == "Pendiente" and (clave, candidato_clave) in pendientes:
            accion = "Pendiente revisado"

        resultado.append(
            {
                "solicitante": display,
                "clave": clave,
                "coincidencia": candidato,
                "tickets": int(tickets),
                "nivel": nivel,
                "confianza": round(confianza, 3),
                "accion": accion,
                "generico": generico,
                "variantes": tuple(variantes),
            }
        )
    return resultado


def unificar_solicitante(
    solicitante: str,
    canonico: str,
    *,
    ruta_equivalencias: Path | str | None = None,
) -> Path:
    """Guarda la equivalencia aprobada; no cambia el valor original del maestro."""

    clave = clave_solicitante(solicitante)
    canonico = str(canonico).strip()
    if not clave or not canonico or not clave_solicitante(canonico):
        raise ValueError("El solicitante y su nombre canónico deben tener contenido.")
    if es_solicitante_generico(solicitante) or es_solicitante_generico(canonico):
        raise ValueError("Los solicitantes genéricos no se pueden unificar.")
    datos = cargar_equivalencias(ruta_equivalencias)
    datos["equivalencias"][clave] = canonico
    datos["separados"] = [
        par for par in datos["separados"] if clave not in par
    ]
    datos["pendientes"] = [
        par for par in datos["pendientes"] if clave not in par
    ]
    ruta = _guardar_equivalencias(datos, ruta_equivalencias)
    auditoria.registrar_evento(
        "EQUIVALENCIA_SOLICITANTE",
        archivo_salida=str(ruta),
        mensajes=json.dumps(
            {"accion": "UNIFICAR", "solicitante": solicitante, "canonico": canonico},
            ensure_ascii=False,
        ),
    )
    return ruta


def mantener_separados(
    solicitante: str,
    otro: str,
    *,
    ruta_equivalencias: Path | str | None = None,
) -> Path:
    """Persiste la decisión de no fusionar dos nombres y audita la decisión."""

    clave_a, clave_b = clave_solicitante(solicitante), clave_solicitante(otro)
    if not clave_a or not clave_b or clave_a == clave_b:
        raise ValueError("Se requieren dos solicitantes distintos para separarlos.")
    datos = cargar_equivalencias(ruta_equivalencias)
    datos["separados"] = sorted(
        {tuple(par) for par in datos["separados"]} | {tuple(sorted((clave_a, clave_b)))}
    )
    datos["equivalencias"].pop(clave_a, None)
    for alias, canonico in list(datos["equivalencias"].items()):
        if clave_solicitante(canonico) == clave_a and alias == clave_b:
            datos["equivalencias"].pop(alias)
    datos["pendientes"] = [
        par for par in datos["pendientes"] if clave_a not in par
    ]
    ruta = _guardar_equivalencias(datos, ruta_equivalencias)
    auditoria.registrar_evento(
        "EQUIVALENCIA_SOLICITANTE",
        archivo_salida=str(ruta),
        mensajes=json.dumps(
            {"accion": "MANTENER_SEPARADOS", "solicitantes": [solicitante, otro]},
            ensure_ascii=False,
        ),
    )
    return ruta


def dejar_pendiente(
    solicitante: str,
    candidato: str = "",
    *,
    ruta_equivalencias: Path | str | None = None,
) -> Path:
    """Persiste una revisión pendiente sin crear equivalencia."""

    clave = clave_solicitante(solicitante)
    if not clave:
        raise ValueError("El solicitante no puede estar vacío.")
    par = [clave, clave_solicitante(candidato)]
    datos = cargar_equivalencias(ruta_equivalencias)
    datos["pendientes"] = [
        pendiente for pendiente in datos["pendientes"] if pendiente != par
    ]
    datos["pendientes"].append(par)
    ruta = _guardar_equivalencias(datos, ruta_equivalencias)
    auditoria.registrar_evento(
        "EQUIVALENCIA_SOLICITANTE",
        archivo_salida=str(ruta),
        mensajes=json.dumps(
            {"accion": "PENDIENTE", "solicitante": solicitante, "candidato": candidato},
            ensure_ascii=False,
        ),
    )
    return ruta


def revertir_equivalencia(
    solicitante: str,
    *,
    ruta_equivalencias: Path | str | None = None,
) -> Path:
    """Elimina una equivalencia confirmada sin tocar datos históricos."""

    clave = clave_solicitante(solicitante)
    datos = cargar_equivalencias(ruta_equivalencias)
    anterior = datos["equivalencias"].pop(clave, None)
    if anterior is None:
        raise ValueError(f"No existe una equivalencia confirmada para '{solicitante}'.")
    datos["pendientes"] = [
        par for par in datos["pendientes"] if clave not in par
    ]
    ruta = _guardar_equivalencias(datos, ruta_equivalencias)
    auditoria.registrar_evento(
        "EQUIVALENCIA_SOLICITANTE",
        archivo_salida=str(ruta),
        mensajes=json.dumps(
            {"accion": "REVERTIR", "solicitante": solicitante, "canonico": anterior},
            ensure_ascii=False,
        ),
    )
    return ruta
