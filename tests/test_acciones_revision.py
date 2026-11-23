"""Pruebas de disponibilidad y texto de las acciones de revisión (Fase 4)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core import acciones_revision as acciones  # noqa: E402


def registro(**cambios):
    datos = {
        "usuario": "PERCY MARTINEZ",
        "area_actual": "SISTEMAS",
        "candidatos": [{"EMPRESA": "SERVIAM", "EMPLEADO": "1"}],
        "estado": "Pendiente",
        "validado": False,
    }
    datos.update(cambios)
    return datos


class PruebasAccionesReales(unittest.TestCase):
    def test_registro_completo_tiene_todas_las_acciones(self) -> None:
        disponibles = acciones.acciones_disponibles(registro())
        self.assertEqual(
            {accion.identificador for accion in disponibles},
            {
                acciones.ACCION_CONSULTAR,
                acciones.ACCION_ASIGNAR,
                acciones.ACCION_REVISAR,
                acciones.ACCION_DETALLES,
                acciones.ACCION_VALIDAR,
            },
        )

    def test_sin_candidatos_no_ofrece_revisar(self) -> None:
        self.assertFalse(
            acciones.accion_disponible(
                acciones.ACCION_REVISAR, registro(candidatos=[])
            )
        )

    def test_sin_area_no_ofrece_validar(self) -> None:
        self.assertFalse(
            acciones.accion_disponible(
                acciones.ACCION_VALIDAR, registro(area_actual="")
            )
        )

    def test_ya_validado_no_ofrece_validar_de_nuevo(self) -> None:
        self.assertFalse(
            acciones.accion_disponible(
                acciones.ACCION_VALIDAR, registro(validado=True)
            )
        )
        self.assertFalse(
            acciones.accion_disponible(
                acciones.ACCION_VALIDAR,
                registro(estado="Validado", validado=False),
            )
        )

    def test_sin_solicitante_solo_ofrece_detalles(self) -> None:
        datos = registro(usuario="", area_actual="", candidatos=[])
        disponibles = [
            accion.identificador for accion in acciones.acciones_disponibles(datos)
        ]
        self.assertEqual(disponibles, [acciones.ACCION_DETALLES])

    def test_accion_desconocida_no_esta_disponible(self) -> None:
        self.assertFalse(acciones.accion_disponible("borrar_todo", registro()))

    def test_texto_de_celda_refleja_cantidad_real(self) -> None:
        self.assertEqual(acciones.texto_acciones(registro()), "⋯ Acciones (5)")
        self.assertEqual(
            acciones.texto_acciones(
                registro(usuario="", area_actual="", candidatos=[])
            ),
            "⋯ Acciones (1)",
        )

    def test_texto_de_celda_sin_acciones(self) -> None:
        self.assertEqual(acciones.texto_acciones({}), "—")

    def test_resumen_de_validacion(self) -> None:
        resumen = acciones.resumen_validacion(
            [
                registro(usuario="A"),
                registro(usuario="B", area_actual=""),
                registro(usuario="C", area_actual="  "),
            ]
        )
        self.assertEqual(
            resumen,
            {
                "total": 3,
                "validables": 1,
                "omitidos": 2,
                "ya_validados": 0,
            },
        )

    def test_resumen_separa_ya_validados(self) -> None:
        resumen = acciones.resumen_validacion(
            [
                registro(usuario="A", validado=True),
                registro(usuario="B"),
            ]
        )
        self.assertEqual(resumen["validables"], 1)
        self.assertEqual(resumen["ya_validados"], 1)
        self.assertEqual(resumen["omitidos"], 0)


class PruebasValidacionSinDuplicados(unittest.TestCase):
    def _ventana(self):
        from ventana_validacion import VentanaValidacion

        ventana = VentanaValidacion.__new__(VentanaValidacion)
        ventana.revision = mock.MagicMock()
        ventana.revision.actualizar.return_value = {}
        ventana._refrescar = mock.MagicMock()
        ventana._var_resumen = SimpleNamespace(set=mock.MagicMock())
        return ventana

    @mock.patch("ventana_validacion.messagebox")
    def test_no_repite_validacion_de_registros_ya_validados(self, mb) -> None:
        ventana = self._ventana()
        ventana._validar_registros([registro(validado=True)])
        ventana.revision.actualizar.assert_not_called()
        ventana.revision.guardar.assert_not_called()
        mb.showinfo.assert_called_once()

    @mock.patch("ventana_validacion.messagebox")
    def test_valida_solo_pendientes_y_reporta_ya_validados(self, mb) -> None:
        ventana = self._ventana()
        ventana._validar_registros(
            [
                registro(usuario="A"),
                registro(usuario="B", validado=True),
            ]
        )
        self.assertEqual(ventana.revision.actualizar.call_count, 1)
        ventana.revision.guardar.assert_called_once()
        mensaje = ventana._var_resumen.set.call_args[0][0]
        self.assertIn("Registros marcados como validados: 1", mensaje)
        self.assertIn("Ya validados: 1", mensaje)

    @mock.patch("ventana_validacion.messagebox")
    def test_validacion_parcial_pide_confirmacion(self, mb) -> None:
        mb.askyesno.return_value = False
        ventana = self._ventana()
        ventana._validar_registros(
            [
                registro(usuario="A"),
                registro(usuario="B", area_actual=""),
            ]
        )
        mb.askyesno.assert_called_once()
        ventana.revision.actualizar.assert_not_called()


class _TablaFalsa:
    """Doble mínimo de ``ttk.Treeview`` para probar la selección masiva."""

    def __init__(self, filas) -> None:
        self.filas = tuple(filas)
        self._seleccion: tuple[str, ...] = ()
        self.foco: str | None = None

    def get_children(self):
        return self.filas

    def selection_set(self, *iids) -> None:
        self._seleccion = tuple(iids)

    def selection_remove(self, *iids) -> None:
        self._seleccion = tuple(
            iid for iid in self._seleccion if iid not in iids
        )

    def selection(self):
        return self._seleccion

    def focus(self, iid=None) -> None:
        self.foco = iid


class PruebasSeleccionMasiva(unittest.TestCase):
    def _ventana(self):
        from ventana_validacion import VentanaValidacion

        ventana = VentanaValidacion.__new__(VentanaValidacion)
        ventana._tabla = _TablaFalsa(("r1", "r2", "r3"))
        ventana._var_resumen = SimpleNamespace(set=mock.MagicMock())
        ventana._var_seleccion = SimpleNamespace(set=mock.MagicMock())
        return ventana

    def test_seleccionar_todos_marca_todas_las_filas_visibles(self) -> None:
        ventana = self._ventana()
        ventana._seleccionar_todos()
        self.assertEqual(ventana._tabla.selection(), ("r1", "r2", "r3"))
        self.assertEqual(ventana._tabla.foco, "r1")
        self.assertIn(
            "3 registros seleccionados",
            ventana._var_resumen.set.call_args[0][0],
        )

    def test_limpiar_seleccion_quita_todas_las_filas(self) -> None:
        ventana = self._ventana()
        ventana._tabla.selection_set("r1", "r3")
        ventana._limpiar_seleccion()
        self.assertEqual(ventana._tabla.selection(), ())


class PruebasAsignacionMasiva(unittest.TestCase):
    def _ventana(self):
        from ventana_validacion import VentanaValidacion

        ventana = VentanaValidacion.__new__(VentanaValidacion)
        ventana.revision = mock.MagicMock()
        ventana._refrescar = mock.MagicMock()
        ventana._asignar_en_maestro = mock.MagicMock(return_value=True)
        ventana._var_resumen = SimpleNamespace(set=mock.MagicMock())
        return ventana

    @mock.patch("ventana_validacion.messagebox")
    def test_asigna_la_misma_area_a_todos_los_registros(self, mb) -> None:
        mb.askyesno.return_value = True
        ventana = self._ventana()
        registros = [registro(usuario="A"), registro(usuario="B")]

        ventana._aplicar_asignacion_masiva(registros, "SISTEMAS")

        self.assertEqual(ventana._asignar_en_maestro.call_count, 2)
        self.assertEqual(ventana.revision.asignar_manual.call_count, 2)
        ventana.revision.asignar_manual.assert_any_call("A", "SISTEMAS")
        ventana.revision.asignar_manual.assert_any_call("B", "SISTEMAS")
        ventana.revision.guardar.assert_called_once()
        ventana._refrescar.assert_called_once()
        mensaje = ventana._var_resumen.set.call_args[0][0]
        self.assertIn("2 de 2", mensaje)

    @mock.patch("ventana_validacion.messagebox")
    def test_cancelar_confirmacion_no_modifica_nada(self, mb) -> None:
        mb.askyesno.return_value = False
        ventana = self._ventana()

        ventana._aplicar_asignacion_masiva(
            [registro(usuario="A")], "SISTEMAS"
        )

        ventana._asignar_en_maestro.assert_not_called()
        ventana.revision.asignar_manual.assert_not_called()
        ventana.revision.guardar.assert_not_called()

    @mock.patch("ventana_validacion.messagebox")
    def test_informa_cuando_algun_registro_no_se_pudo_asignar(self, mb) -> None:
        mb.askyesno.return_value = True
        ventana = self._ventana()
        ventana._asignar_en_maestro.side_effect = [True, False]

        ventana._aplicar_asignacion_masiva(
            [registro(usuario="A"), registro(usuario="B")], "SISTEMAS"
        )

        self.assertEqual(ventana.revision.asignar_manual.call_count, 1)
        mensaje = ventana._var_resumen.set.call_args[0][0]
        self.assertIn("1 de 2", mensaje)
        self.assertIn("No asignados: 1", mensaje)
        mb.showwarning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
