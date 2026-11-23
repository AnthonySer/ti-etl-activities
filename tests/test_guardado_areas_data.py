"""Pruebas del guardado de áreas de la revisión en ``data``."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from interfaz import AplicacionTk  # noqa: E402


class PruebasGuardadoAreasRevisionEnData(unittest.TestCase):
    def _app(self):
        app = object.__new__(AplicacionTk)
        app._maestro = mock.MagicMock()
        app._maestro_listo = True
        app._refrescar_usuarios = mock.MagicMock()
        app._refrescar_pendientes = mock.MagicMock()
        app._refrescar_panel = mock.MagicMock()
        app._refrescar_auditoria = mock.MagicMock()
        return app

    def test_guarda_todo_el_lote_sin_procesar_excel(self) -> None:
        app = self._app()
        resultado_asignaciones = SimpleNamespace(
            registradas=1, actualizadas=1, sin_cambios=0
        )
        with mock.patch("interfaz.bloqueo") as bloqueo_ctx, mock.patch(
            "interfaz.svc_asignaciones.asignar",
            return_value=resultado_asignaciones,
        ) as asignar:
            resultado = app._guardar_areas_revision_en_data(
                [
                    {"usuario": "ANA", "area": "SISTEMAS"},
                    {"usuario": "LUIS", "area": "CONTABILIDAD"},
                    {"usuario": "", "area": "IGNORAR"},
                ]
            )

        self.assertEqual(bloqueo_ctx.call_count, 1)
        asignar.assert_called_once()
        argumentos, opciones = asignar.call_args
        self.assertTrue(opciones.get("guardar"))
        self.assertEqual(len(argumentos[1]), 2)
        self.assertEqual(resultado["guardadas"], 2)
        self.assertEqual(resultado["nuevas"], 1)
        self.assertEqual(resultado["actualizadas"], 1)
        self.assertEqual(resultado["fallidas"], [])

    def test_error_se_devuelve_sin_propagar_excepcion(self) -> None:
        app = self._app()
        with mock.patch("interfaz.bloqueo"), mock.patch(
            "interfaz.svc_asignaciones.asignar",
            side_effect=RuntimeError("maestro ocupado"),
        ):
            resultado = app._guardar_areas_revision_en_data(
                [{"usuario": "ANA", "area": "SISTEMAS"}]
            )

        self.assertIn("maestro ocupado", resultado["error"])
        self.assertEqual(resultado["guardadas"], 0)

    def test_sin_registros_no_escribe(self) -> None:
        app = self._app()
        with mock.patch(
            "interfaz.svc_asignaciones.asignar"
        ) as asignar:
            resultado = app._guardar_areas_revision_en_data([])

        asignar.assert_not_called()
        self.assertEqual(resultado["guardadas"], 0)
        self.assertTrue(resultado["error"])


if __name__ == "__main__":
    unittest.main()
