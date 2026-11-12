"""Pruebas de la planificación de búsquedas de solicitantes (Fase 1)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core import busqueda_solicitantes as busqueda  # noqa: E402


class PruebasNormalizacion(unittest.TestCase):
    def test_una_palabra_usa_patron_automatico(self) -> None:
        plan = busqueda.plan_busqueda("Percy")
        self.assertEqual(plan.terminos, ("PERCY",))
        self.assertEqual(plan.modo, busqueda.MODO_AUTOMATICA)
        self.assertEqual(plan.patron, "%PERCY%")
        self.assertFalse(plan.requiere_orden_manual)

    def test_dos_palabras_se_invierten_automaticamente(self) -> None:
        plan = busqueda.plan_busqueda("Percy Martinez")
        self.assertEqual(plan.terminos, ("MARTINEZ", "PERCY"))
        self.assertEqual(plan.modo, busqueda.MODO_INVERTIDA)
        self.assertEqual(plan.patron, "%MARTINEZ%PERCY%")

    def test_tres_palabras_requieren_orden_manual(self) -> None:
        plan = busqueda.plan_busqueda("Juan Carlos Perez")
        self.assertEqual(plan.terminos, ("JUAN", "CARLOS", "PEREZ"))
        self.assertEqual(plan.modo, busqueda.MODO_MANUAL)
        self.assertTrue(plan.requiere_orden_manual)
        self.assertEqual(plan.patron, "%JUAN%CARLOS%PEREZ%")

    def test_cuatro_palabras_conservan_orden_inicial(self) -> None:
        plan = busqueda.plan_busqueda("Maria del Carmen Rojas")
        self.assertEqual(
            plan.terminos, ("MARIA", "DEL", "CARMEN", "ROJAS")
        )
        self.assertEqual(plan.modo, busqueda.MODO_MANUAL)

    def test_tildes_signos_y_espacios_no_alteran_el_original(self) -> None:
        original = "  José   María  Núñez-Pérez "
        plan = busqueda.plan_busqueda(original)
        self.assertEqual(
            plan.terminos, ("JOSE", "MARIA", "NUNEZ", "PEREZ")
        )
        # El valor original se conserva sin tildes perdidas ni mutaciones.
        self.assertIn("José", plan.solicitante)
        self.assertEqual(plan.solicitante, "José María Núñez-Pérez")

    def test_valor_vacio_no_produce_plan(self) -> None:
        plan = busqueda.plan_busqueda("   ")
        self.assertEqual(plan.terminos, ())
        self.assertEqual(plan.patron, "")

    def test_orden_manual_valido(self) -> None:
        plan = busqueda.plan_busqueda("Juan Carlos Perez")
        reordenado = plan.con_orden(["PEREZ", "JUAN", "CARLOS"])
        self.assertEqual(reordenado.terminos, ("PEREZ", "JUAN", "CARLOS"))
        self.assertEqual(reordenado.modo, busqueda.MODO_MANUAL)
        self.assertEqual(reordenado.patron, "%PEREZ%JUAN%CARLOS%")

    def test_orden_manual_no_permite_agregar_ni_quitar(self) -> None:
        plan = busqueda.plan_busqueda("Juan Carlos Perez")
        with self.assertRaises(ValueError):
            plan.con_orden(["JUAN", "PEREZ"])
        with self.assertRaises(ValueError):
            plan.con_orden(["JUAN", "CARLOS", "PEREZ", "EXTRA"])

    def test_no_genera_permutaciones(self) -> None:
        plan = busqueda.plan_busqueda("Juan Carlos Perez")
        self.assertEqual(len(plan.terminos), 3)
        self.assertEqual(plan.modo, busqueda.MODO_MANUAL)


class PruebasEscapadoLike(unittest.TestCase):
    def test_escapa_comodines_de_sql_server(self) -> None:
        self.assertEqual(busqueda.escapar_like("50%_[]\\"), "50\\%\\_\\[\\]\\\\")

    def test_patron_escapa_comodines_y_conserva_orden(self) -> None:
        patron = busqueda.patron_like(["MARTINEZ", "50%"])
        self.assertEqual(patron, "%MARTINEZ%50\\%%")

    def test_patron_vacio(self) -> None:
        self.assertEqual(busqueda.patron_like([]), "")
        self.assertEqual(busqueda.patron_like(["  "]), "")

    def test_patron_visible_es_el_mismo_patron_sql(self) -> None:
        self.assertEqual(
            busqueda.patron_visible(["PEREZ", "JUAN"]), "%PEREZ%JUAN%"
        )


class PruebasCoincidencia(unittest.TestCase):
    def test_coincide_terminos_en_orden(self) -> None:
        self.assertTrue(
            busqueda.coincide_terminos("MARTINEZ PERCY", ["MARTINEZ", "PERCY"])
        )
        self.assertFalse(
            busqueda.coincide_terminos("PERCY MARTINEZ", ["MARTINEZ", "PERCY"])
        )

    def test_no_acepta_permutaciones(self) -> None:
        self.assertFalse(
            busqueda.coincide_terminos(
                "PEREZ CARLOS JUAN", ["JUAN", "CARLOS", "PEREZ"]
            )
        )
        self.assertTrue(
            busqueda.coincide_terminos(
                "JUAN CARLOS PEREZ", ["JUAN", "CARLOS", "PEREZ"]
            )
        )

    def test_una_palabra_coincide_por_contener(self) -> None:
        empleado = type("Empleado", (), {"nombre_completo": "PERCY MARTINEZ"})()
        self.assertTrue(
            busqueda.coincide_consulta(empleado, {"terminos": ["PERCY"]})
        )
        self.assertTrue(
            busqueda.coincide_consulta(empleado, {"terminos": ["MARTINEZ"]})
        )
        self.assertFalse(
            busqueda.coincide_consulta(empleado, {"terminos": ["JUAN"]})
        )

    def test_sin_terminos_no_coincide(self) -> None:
        empleado = type("Empleado", (), {"nombre_completo": "PERCY"})()
        self.assertFalse(busqueda.coincide_consulta(empleado, {"terminos": []}))

    def test_terminos_uniformes_acepta_listas(self) -> None:
        self.assertEqual(
            busqueda.terminos_uniformes(["José  Pérez", "Ana"]),
            ["JOSE", "PEREZ", "ANA"],
        )


if __name__ == "__main__":
    unittest.main()
