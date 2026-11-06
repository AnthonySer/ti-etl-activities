"""Consolidación de los archivos históricos en la base maestra inicial."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from core.archivos import (
    COLUMNA_AREA,
    COLUMNA_USUARIO,
    mapear_columnas,
    validar_columnas,
)
from core.normalizador import (
    limpiar_area,
    limpiar_usuario,
    normalizar_area,
    normalizar_usuario,
)


@dataclass
class EstadisticasConsolidacion:
    nombre_septiembre: str
    nombre_agosto: str
    registros_septiembre: int
    registros_agosto: int
    usuarios_unicos: int
    correspondencias_validas: int
    usuarios_sin_area: int
    duplicados_eliminados: int
    conflictos_detectados: int
    conflictos_resueltos: int
    filas_usuario_invalido: int = 0
    filas_area_invalida: int = 0
    conflictos_internos: int = 0
    duplicados_validos: int = 0
    incidencias: list[str] = field(default_factory=list)


@dataclass
class _Fuente:
    etiqueta: str
    registros: int
    mapa: dict[str, dict[str, str]]
    usuarios: list[str]
    filas_usuario_invalido: int
    filas_area_invalida: int
    pares_validos: int
    conflictos_internos: int
    incidencias: list[str]


def _preparar_fuente(df: pd.DataFrame, etiqueta: str) -> _Fuente:
    df = mapear_columnas(df)
    validar_columnas(df, (COLUMNA_USUARIO, COLUMNA_AREA))

    mapa: dict[str, dict[str, str]] = {}
    usuarios: list[str] = []
    vistos: set[str] = set()
    filas_usuario_invalido = 0
    filas_area_invalida = 0
    pares_validos = 0
    conflictos_internos = 0
    conflictos_vistos: set[str] = set()
    incidencias: list[str] = []

    for _, fila in df.iterrows():
        clave = normalizar_usuario(fila[COLUMNA_USUARIO])
        if clave is None:
            filas_usuario_invalido += 1
            continue

        if clave not in vistos:
            vistos.add(clave)
            usuarios.append(clave)

        area = limpiar_area(fila[COLUMNA_AREA])
        if area is None:
            filas_area_invalida += 1
            continue

        pares_validos += 1
        area_clave = normalizar_area(area)

        if clave in mapa:
            if area_clave != mapa[clave]["area_clave"]:
                if clave not in conflictos_vistos:
                    conflictos_vistos.add(clave)
                    conflictos_internos += 1
                    incidencias.append(
                        f"[{etiqueta}] {mapa[clave]['usuario']}: "
                        f"'{mapa[clave]['area']}' / '{area}' "
                        "(se conserva la primera área válida del archivo)"
                    )
            continue

        mapa[clave] = {
            "usuario": limpiar_usuario(fila[COLUMNA_USUARIO]) or clave,
            "area": area,
            "area_clave": area_clave,
        }

    return _Fuente(
        etiqueta=etiqueta,
        registros=len(df),
        mapa=mapa,
        usuarios=usuarios,
        filas_usuario_invalido=filas_usuario_invalido,
        filas_area_invalida=filas_area_invalida,
        pares_validos=pares_validos,
        conflictos_internos=conflictos_internos,
        incidencias=incidencias,
    )


def consolidar(
    df_septiembre: pd.DataFrame,
    df_agosto: pd.DataFrame,
    nombre_septiembre: str = "consulta_septiembre_2026.xlsx",
    nombre_agosto: str = "consulta_agosto_2026.xlsx",
) -> tuple[pd.DataFrame, EstadisticasConsolidacion]:
    """Consolida ambas fuentes aplicando prioridad de septiembre."""

    septiembre = _preparar_fuente(df_septiembre, "septiembre")
    agosto = _preparar_fuente(df_agosto, "agosto")

    # Orden estable: primero los usuarios de septiembre, luego los exclusivos de agosto.
    orden: list[str] = list(septiembre.usuarios)
    vistos = set(orden)
    for clave in agosto.usuarios:
        if clave not in vistos:
            vistos.add(clave)
            orden.append(clave)

    filas: list[dict[str, str]] = []
    incidencias = septiembre.incidencias + agosto.incidencias
    conflictos = 0

    for clave in orden:
        sep = septiembre.mapa.get(clave)
        ago = agosto.mapa.get(clave)

        if sep and ago and sep["area_clave"] != ago["area_clave"]:
            conflictos += 1
            incidencias.append(
                f"[CONFLICTO] {sep['usuario']}: "
                f"septiembre='{sep['area']}' | agosto='{ago['area']}' "
                "-> se conserva septiembre"
            )

        elegido = sep or ago

        # Sin área válida en ninguna fuente: no se incorpora como correspondencia.
        if elegido is None:
            continue

        filas.append(
            {
                COLUMNA_USUARIO: elegido["usuario"],
                COLUMNA_AREA: elegido["area"],
            }
        )

    resultado = pd.DataFrame(filas, columns=[COLUMNA_USUARIO, COLUMNA_AREA])

    filas_con_usuario_valido = (
        septiembre.registros - septiembre.filas_usuario_invalido
    ) + (agosto.registros - agosto.filas_usuario_invalido)

    estadisticas = EstadisticasConsolidacion(
        nombre_septiembre=nombre_septiembre,
        nombre_agosto=nombre_agosto,
        registros_septiembre=septiembre.registros,
        registros_agosto=agosto.registros,
        usuarios_unicos=len(orden),
        correspondencias_validas=len(resultado),
        usuarios_sin_area=len(orden) - len(resultado),
        duplicados_eliminados=max(0, filas_con_usuario_valido - len(orden)),
        conflictos_detectados=conflictos,
        conflictos_resueltos=conflictos,
        filas_usuario_invalido=(
            septiembre.filas_usuario_invalido + agosto.filas_usuario_invalido
        ),
        filas_area_invalida=(
            septiembre.filas_area_invalida + agosto.filas_area_invalida
        ),
        conflictos_internos=(
            septiembre.conflictos_internos + agosto.conflictos_internos
        ),
        duplicados_validos=max(
            0,
            septiembre.pares_validos + agosto.pares_validos - len(resultado),
        ),
        incidencias=incidencias,
    )

    return resultado, estadisticas


def localizar_fuentes(
    ruta_septiembre: Path | str | None = None,
    ruta_agosto: Path | str | None = None,
) -> tuple[Path, Path]:
    """Resuelve las rutas de los dos archivos históricos."""

    from config import configuracion as cfg
    from core.archivos import ErrorArchivo, localizar_archivo

    def resolver(valor: Path | str | None, nombre: str, etiqueta: str) -> Path:
        if valor:
            ruta = Path(valor)
            if not ruta.is_absolute():
                ruta = cfg.BASE_DIR / ruta
            if not ruta.exists():
                raise ErrorArchivo(f"No existe el archivo de {etiqueta}: {ruta}")
            return ruta

        encontrado = localizar_archivo(nombre)
        if encontrado is None:
            raise ErrorArchivo(
                f"No se encontró '{nombre}' ({etiqueta}). "
                f"Colóquelo en '{cfg.INPUT_DIR}' o indique la ruta con --septiembre/--agosto."
            )
        return encontrado

    return (
        resolver(ruta_septiembre, cfg.ARCHIVO_SEPTIEMBRE, "septiembre"),
        resolver(ruta_agosto, cfg.ARCHIVO_AGOSTO, "agosto"),
    )
