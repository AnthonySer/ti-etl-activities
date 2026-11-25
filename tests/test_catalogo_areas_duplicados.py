"""Pruebas puntuales de detección de áreas duplicadas o parecidas.

No usan el maestro real: construyen un ``Maestro`` ficticio en memoria y
verifican que la comparación ignore mayúsculas, tildes y espacios, sin alterar
los nombres originales.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core import catalogo_areas  # noqa: E402
from core.maestro import Maestro  # noqa: E402


def crear_maestro(areas: list[str]) -> Maestro:
    return Maestro(
        pd.DataFrame(
            {
                "USUARIO": [f"USUARIO {indice}" for indice in range(len(areas))],
                "AREA_USUARIO": areas,
            }
        )
    )


class PruebasPosiblesDuplicados(unittest.TestCase):
    def test_detecta_variante_con_tildes_mayusculas_y_espacios(self) -> None:
        areas = ["ADMINISTRACION", "SISTEMAS"]

        coincidencias = catalogo_areas.buscar_posibles_duplicados(
            "  administración  ", areas
        )

        self.assertEqual(len(coincidencias), 1)
        self.assertEqual(coincidencias[0].nombre, "ADMINISTRACION")
        self.assertAlmostEqual(coincidencias[0].similitud, 1.0)
        self.assertEqual(coincidencias[0].tipo, "exacta")

    def test_detecta_espacios_internos_adicionales(self) -> None:
        coincidencias = catalogo_areas.buscar_posibles_duplicados(
            "CREDITOS   Y  COBRANZAS", ["CREDITOS Y COBRANZAS"]
        )

        self.assertEqual(
            [item.nombre for item in coincidencias],
            ["CREDITOS Y COBRANZAS"],
        )

    def test_devuelve_todas_las_coincidencias_ordenadas(self) -> None:
        areas = ["ADMINISTRACION", "ADMINISTRATIVO", "SISTEMAS"]

        coincidencias = catalogo_areas.buscar_posibles_duplicados(
            "ADMINISTRACIÓN", areas, umbral=0.70
        )
        nombres = [item.nombre for item in coincidencias]

        self.assertEqual(nombres[0], "ADMINISTRACION")
        self.assertIn("ADMINISTRATIVO", nombres)
        self.assertNotIn("SISTEMAS", nombres)
        self.assertEqual(
            [item.similitud for item in coincidencias],
            sorted(
                [item.similitud for item in coincidencias],
                reverse=True,
            ),
        )

    def test_umbral_configurable_descarta_similares_lejanos(self) -> None:
        coincidencias = catalogo_areas.buscar_posibles_duplicados(
            "SISTEMAS", ["SISTEMA OPERATIVO"], umbral=0.99
        )

        self.assertEqual(coincidencias, [])

    def test_no_confunde_areas_diferentes(self) -> None:
        coincidencias = catalogo_areas.buscar_posibles_duplicados(
            "TESORERIA", ["SISTEMAS", "CONTABILIDAD"]
        )

        self.assertEqual(coincidencias, [])

    def test_buscar_similares_por_defecto_no_incluye_exactos(self) -> None:
        """Mantiene el contrato anterior de ``buscar_similares``."""

        similares = catalogo_areas.buscar_similares(
            "administración", ["ADMINISTRACION"]
        )

        self.assertEqual(similares, [])

    def test_no_modifica_los_nombres_originales(self) -> None:
        areas = ["ADMINISTRACION", "SISTEMAS"]

        catalogo_areas.buscar_posibles_duplicados("ADMINISTRACIÓN", areas)

        self.assertEqual(areas, ["ADMINISTRACION", "SISTEMAS"])

    def test_catalogo_desde_maestro_deduplica_variantes(self) -> None:
        maestro = crear_maestro(["ADMINISTRACION", "ADMINISTRACIÓN", "SISTEMAS"])

        self.assertEqual(
            catalogo_areas.catalogo_desde_maestro(maestro),
            ["ADMINISTRACION", "SISTEMAS"],
        )

    def test_detecta_duplicado_desde_maestro_ficticio(self) -> None:
        maestro = crear_maestro(["ADMINISTRACION", "SISTEMAS"])

        coincidencias = catalogo_areas.buscar_posibles_duplicados(
            "ADMINISTRACIÓN", maestro=maestro
        )

        self.assertEqual([item.nombre for item in coincidencias], ["ADMINISTRACION"])

    def test_coincidencia_literal_solo_acepta_el_mismo_texto(self) -> None:
        from core.catalogo_areas import AreaSimilar
        from ventana_validacion import _coincidencia_literal

        coincidencias = [
            AreaSimilar("ADMINISTRACION", 1.0, "exacta"),
            AreaSimilar("ADMINISTRATIVO", 0.94, "alta"),
        ]

        self.assertEqual(
            _coincidencia_literal("ADMINISTRACION", coincidencias),
            "ADMINISTRACION",
        )
        self.assertIsNone(
            _coincidencia_literal("ADMINISTRACIÓN", coincidencias)
        )


if __name__ == "__main__":
    unittest.main()
