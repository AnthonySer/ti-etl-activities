"""Pruebas unitarias para filtrado de áreas, detección y unificación de duplicados y autocompletado."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from core.esquema import C_AREA
from core.procesado import _tabla_area
from ventana_validacion import VentanaValidacion, filtrar_areas


class PruebasFiltradoYAutocompletado(unittest.TestCase):
    def setUp(self) -> None:
        self.areas = [
            "ADMINISTRACIÓN",
            "CONTABILIDAD",
            "RECURSOS HUMANOS",
            "SISTEMAS",
            "TI - SISTEMAS",
            "LOGÍSTICA",
            "OPERACIONES",
        ]

    def test_filtrar_areas_prefijo_por_defecto(self) -> None:
        # Por defecto mantiene compatibilidad con startswith
        self.assertEqual(filtrar_areas(self.areas, "SI"), ["SISTEMAS"])
        self.assertEqual(filtrar_areas(self.areas, "TI"), ["TI - SISTEMAS"])

    def test_filtrar_areas_contiene_subcadena(self) -> None:
        # Con contiene=True busca en cualquier posición
        resultados = filtrar_areas(self.areas, "sis", contiene=True)
        # Prioriza el que empieza con 'sis' ('SISTEMAS') y luego 'TI - SISTEMAS'
        self.assertEqual(resultados, ["SISTEMAS", "TI - SISTEMAS"])

        resultados_hum = filtrar_areas(self.areas, "hum", contiene=True)
        self.assertEqual(resultados_hum, ["RECURSOS HUMANOS"])

    def test_filtrar_registros_por_area_especifica(self) -> None:
        registros = [
            {"usuario": "ANA", "area_actual": "SISTEMAS", "estado": "Validado"},
            {"usuario": "JUAN", "area_actual": "CONTABILIDAD", "estado": "Validado"},
            {"usuario": "PEDRO", "area_actual": "", "estado": "Pendiente"},
        ]

        # Filtrar por SISTEMAS
        filtrados_sis = VentanaValidacion._filtrar_registros(registros, "", "Todos", "SISTEMAS")
        self.assertEqual(len(filtrados_sis), 1)
        self.assertEqual(filtrados_sis[0]["usuario"], "ANA")

        # Filtrar por Sin área
        filtrados_sin = VentanaValidacion._filtrar_registros(registros, "", "Todos", "(Sin área)")
        self.assertEqual(len(filtrados_sin), 1)
        self.assertEqual(filtrados_sin[0]["usuario"], "PEDRO")

        # Todas las áreas
        filtrados_todas = VentanaValidacion._filtrar_registros(registros, "", "Todos", "Todas")
        self.assertEqual(len(filtrados_todas), 3)


class PruebasConsolidacionReporteExcel(unittest.TestCase):
    def test_tabla_area_unifica_variantes_mayusculas_y_espacios(self) -> None:
        df = pd.DataFrame({
            C_AREA: [
                "SISTEMAS",
                "Sistemas",
                "sistemas ",
                "CONTABILIDAD",
                "Contabilidad",
                "LOGISTICA",
            ]
        })
        tabla = _tabla_area(df)

        # Debe tener SISTEMAS (3 tickets), CONTABILIDAD (2 tickets), LOGISTICA (1 ticket) y TOTAL GENERAL
        filas = tabla.to_dict(orient="records")
        areas_generadas = [f["ÁREAS"] for f in filas]

        self.assertIn("SISTEMAS", areas_generadas)
        self.assertIn("CONTABILIDAD", areas_generadas)
        self.assertIn("LOGISTICA", areas_generadas)
        self.assertIn("TOTAL GENERAL", areas_generadas)

        # No debe haber filas duplicadas para Sistemas ni Contabilidad
        self.assertEqual(len([a for a in areas_generadas if "SISTEMA" in a.upper()]), 1)
        self.assertEqual(len([a for a in areas_generadas if "CONTABILIDAD" in a.upper()]), 1)

        # Comprobar conteos exactos
        tickets_sistemas = next(f["TICKETS"] for f in filas if f["ÁREAS"] == "SISTEMAS")
        self.assertEqual(tickets_sistemas, 3)

        tickets_conta = next(f["TICKETS"] for f in filas if f["ÁREAS"] == "CONTABILIDAD")
        self.assertEqual(tickets_conta, 2)


class PruebasUnificacionRevision(unittest.TestCase):
    def test_aplicar_unificacion_actualiza_registros_en_revision(self) -> None:
        ventana = object.__new__(VentanaValidacion)
        ventana.revision = SimpleNamespace(
            registros=[
                {"usuario": "ANA", "area_actual": "Sistemas"},
                {"usuario": "LUIS", "area_actual": "SISTEMAS"},
                {"usuario": "CARLOS", "area_actual": "sistemas "},
                {"usuario": "MARIA", "area_actual": "CONTABILIDAD"},
            ],
            actualizar=MagicMock(),
            guardar=MagicMock(),
        )
        ventana._refrescar = MagicMock()

        mapeo = {"Sistemas": "SISTEMAS", "sistemas ": "SISTEMAS"}
        with patch("ventana_validacion.messagebox.showinfo"):
            ventana._aplicar_unificacion_areas(mapeo)

        # Se debió llamar actualizar para ANA y CARLOS con SISTEMAS
        self.assertEqual(ventana.revision.actualizar.call_count, 2)
        ventana.revision.actualizar.assert_any_call("ANA", area="SISTEMAS")
        ventana.revision.actualizar.assert_any_call("CARLOS", area="SISTEMAS")
        ventana.revision.guardar.assert_called_once()
        ventana._refrescar.assert_called_once()


if __name__ == "__main__":
    unittest.main()
