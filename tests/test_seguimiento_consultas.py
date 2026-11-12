"""Pruebas de estados y resumen del seguimiento de consultas (Fase 3)."""

from __future__ import annotations

import queue
import sys
import unittest
from pathlib import Path
from unittest import mock

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core import seguimiento_consultas as seguimiento  # noqa: E402


class PruebasEstados(unittest.TestCase):
    def test_categoria_de_error_se_traduce_sin_marcar_exito(self) -> None:
        self.assertEqual(
            seguimiento.estado_desde_error("servidor"),
            seguimiento.ESTADO_ERROR_CONEXION,
        )
        self.assertEqual(
            seguimiento.estado_desde_error("timeout"),
            seguimiento.ESTADO_ERROR_CONEXION,
        )
        self.assertEqual(
            seguimiento.estado_desde_error("permisos"),
            seguimiento.ESTADO_ERROR_PERMISOS,
        )
        self.assertEqual(
            seguimiento.estado_desde_error("tabla"),
            seguimiento.ESTADO_SIN_ESQUEMA,
        )
        self.assertEqual(
            seguimiento.estado_desde_error("desconocida"),
            seguimiento.ESTADO_ERROR_CONSULTA,
        )

    def test_error_nunca_es_estado_final_exitoso(self) -> None:
        self.assertNotIn(
            seguimiento.ESTADO_ERROR_CONEXION, {seguimiento.ESTADO_COMPLETADA}
        )
        self.assertTrue(
            seguimiento.ESTADO_ERROR_CONEXION in seguimiento.ESTADOS_ERROR
        )
        self.assertTrue(
            seguimiento.ESTADO_ERROR_CONEXION in seguimiento.ESTADOS_FINALES
        )


class PruebasResumen(unittest.TestCase):
    def _resumen(self) -> seguimiento.ResumenSeguimiento:
        resumen = seguimiento.ResumenSeguimiento(total_empresas=4)
        resumen.registrar(
            seguimiento.nuevo_evento(
                "SERVIAM",
                seguimiento.ESTADO_COMPLETADA,
                resultados=2,
                duracion=1.5,
            )
        )
        resumen.registrar(
            seguimiento.nuevo_evento(
                "FILASUR",
                seguimiento.ESTADO_SIN_RESULTADOS,
                resultados=0,
                duracion=0.5,
            )
        )
        resumen.registrar(
            seguimiento.nuevo_evento(
                "INKA",
                seguimiento.ESTADO_ERROR_CONEXION,
                resultados=0,
                duracion=2.0,
                detalle="Servidor no disponible",
            )
        )
        resumen.registrar(
            seguimiento.nuevo_evento(
                "POLYSOL",
                seguimiento.ESTADO_CANCELADA,
                detalle="Cancelada por el usuario.",
            )
        )
        resumen.duracion_total = 4.0
        return resumen

    def test_conteos_reales(self) -> None:
        datos = self._resumen().como_dict()
        self.assertEqual(datos["total_empresas"], 4)
        self.assertEqual(datos["procesadas"], 3)
        self.assertEqual(datos["pendientes"], 0)
        self.assertEqual(datos["con_resultados"], 1)
        self.assertEqual(datos["sin_resultados"], 1)
        self.assertEqual(datos["errores"], 1)
        self.assertEqual(datos["canceladas"], 1)
        self.assertEqual(datos["empleados"], 2)
        self.assertEqual(datos["porcentaje"], 100)
        self.assertEqual(datos["duracion_total"], 4.0)

    def test_pendientes_y_porcentaje_parcial(self) -> None:
        resumen = seguimiento.ResumenSeguimiento(total_empresas=4)
        resumen.registrar(
            seguimiento.nuevo_evento(
                "SERVIAM", seguimiento.ESTADO_CONSULTANDO
            )
        )
        resumen.registrar(
            seguimiento.nuevo_evento(
                "FILASUR",
                seguimiento.ESTADO_COMPLETADA,
                resultados=3,
            )
        )
        self.assertEqual(resumen.procesadas, 1)
        self.assertEqual(resumen.pendientes, 3)
        self.assertEqual(resumen.porcentaje(), 25)

    def test_evento_posterior_conserva_datos_previos(self) -> None:
        resumen = seguimiento.ResumenSeguimiento(total_empresas=1)
        resumen.registrar(
            seguimiento.nuevo_evento(
                "SERVIAM",
                seguimiento.ESTADO_CONSULTANDO,
                detalle="Obteniendo resultados…",
            )
        )
        combinado = resumen.registrar(
            seguimiento.nuevo_evento(
                "SERVIAM",
                seguimiento.ESTADO_COMPLETADA,
                resultados=5,
                duracion=1.25,
            )
        )
        self.assertEqual(combinado["estado"], seguimiento.ESTADO_COMPLETADA)
        self.assertEqual(combinado["resultados"], 5)
        self.assertEqual(combinado["duracion"], 1.25)
        self.assertEqual(combinado["detalle"], "Obteniendo resultados…")

    def test_evento_sin_empresa_se_ignora(self) -> None:
        resumen = seguimiento.ResumenSeguimiento(total_empresas=1)
        resumen.registrar(seguimiento.nuevo_evento("", seguimiento.ESTADO_ERROR_CONEXION))
        self.assertEqual(resumen.eventos, {})

    def test_cancelacion_se_refleja(self) -> None:
        resumen = seguimiento.ResumenSeguimiento(total_empresas=2)
        resumen.registrar(
            seguimiento.nuevo_evento("SERVIAM", seguimiento.ESTADO_CANCELADA)
        )
        self.assertTrue(resumen.cancelada)
        self.assertTrue(resumen.como_dict()["cancelada"])

    def test_sin_empresas_no_divide_entre_cero(self) -> None:
        resumen = seguimiento.ResumenSeguimiento()
        self.assertEqual(resumen.porcentaje(), 0)
        self.assertEqual(resumen.pendientes, 0)


class VentanaSeguimientoSinTk:
    """Contrato de hilo de la ventana, probado sin instanciar Tkinter."""

    @staticmethod
    def crear():
        from ventana_validacion import VentanaSeguimientoConsulta

        ventana = VentanaSeguimientoConsulta.__new__(VentanaSeguimientoConsulta)
        ventana._cola = queue.Queue()
        ventana._cancelar_solicitado = False
        return ventana


class PruebasContratoSeguimiento(unittest.TestCase):
    def test_encolar_es_seguro_desde_otro_hilo_y_no_toca_tkinter(self) -> None:
        ventana = VentanaSeguimientoSinTk.crear()
        evento = seguimiento.nuevo_evento(
            "SERVIAM", seguimiento.ESTADO_COMPLETADA, resultados=1
        )
        with mock.patch("tkinter.Misc.after", side_effect=AssertionError):
            import threading

            hilo = threading.Thread(target=ventana.encolar, args=(evento,))
            hilo.start()
            hilo.join()
        self.assertEqual(ventana._cola.qsize(), 1)
        self.assertEqual(ventana._cola.get_nowait()["empresa"], "SERVIAM")

    def test_debe_cancelar_refleja_la_solicitud(self) -> None:
        ventana = VentanaSeguimientoSinTk.crear()
        self.assertFalse(ventana.debe_cancelar)
        ventana._cancelar_solicitado = True
        self.assertTrue(ventana.debe_cancelar)

    def test_formato_de_tiempo(self) -> None:
        from ventana_validacion import VentanaSeguimientoConsulta

        formato = VentanaSeguimientoConsulta._formato_tiempo
        self.assertEqual(formato(None), "—")
        self.assertEqual(formato("no"), "—")
        self.assertEqual(formato(1.234), "1.23 s")
        self.assertEqual(formato(61.5), "1 min 1.5 s")

    def test_texto_resumen_incluye_conteos_y_mensaje(self) -> None:
        from ventana_validacion import VentanaSeguimientoConsulta

        texto = VentanaSeguimientoConsulta._texto_resumen(
            {
                "total_empresas": 2,
                "procesadas": 2,
                "pendientes": 0,
                "con_resultados": 1,
                "sin_resultados": 1,
                "sin_esquema": 0,
                "errores": 0,
                "canceladas": 0,
                "empleados": 7,
                "duracion_total": 2.5,
                "mensaje": "Listo",
            }
        )
        self.assertIn("Empleados encontrados: 7", texto)
        self.assertIn("Duración total: 2.50 s", texto)
        self.assertIn("Listo", texto)


class PruebasVentanasTk(unittest.TestCase):
    """Pruebas de humo de las ventanas nuevas (se omiten sin entorno gráfico)."""

    def setUp(self) -> None:
        import tkinter as tk

        try:
            self.raiz = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover - sin entorno gráfico
            self.skipTest(f"Tk no disponible: {exc}")
        self.raiz.withdraw()

    def tearDown(self) -> None:
        try:
            self.raiz.destroy()
        except Exception:  # noqa: BLE001
            pass

    def test_seguimiento_recibe_eventos_y_muestra_resumen(self) -> None:
        from ventana_validacion import VentanaSeguimientoConsulta

        ventana = VentanaSeguimientoConsulta(
            self.raiz,
            solicitantes=["Percy Martinez"],
            ordenes=["MARTINEZ → PERCY"],
            empresas=["SERVIAM", "FILASUR"],
        )
        ventana.withdraw()
        try:
            ventana.encolar(
                seguimiento.nuevo_evento(
                    "SERVIAM", seguimiento.ESTADO_CONSULTANDO
                )
            )
            ventana.encolar(
                seguimiento.nuevo_evento(
                    "SERVIAM",
                    seguimiento.ESTADO_COMPLETADA,
                    resultados=2,
                    duracion=1.0,
                    detalle="Consulta exitosa",
                )
            )
            ventana._drenar_cola()
            self.assertEqual(ventana._var_procesadas.get(), "1")
            self.assertEqual(ventana._var_pendientes.get(), "1")
            self.assertEqual(ventana._var_porcentaje.get(), "50%")
            valores = ventana._tabla.item(
                ventana._iids["SERVIAM"], "values"
            )
            self.assertEqual(valores[1], "Completada")
            self.assertEqual(valores[2], "2")
            self.assertIn("1.00 s", valores[3])

            ventana.finalizar(
                resumen={"duracion_total": 1.5, "cancelada": False},
                mensaje="Consulta terminada.",
            )
            self.assertIn("Consulta finalizada", ventana._var_estado.get())
            self.assertEqual(str(ventana._btn_cancelar["state"]), "disabled")
            self.assertIn("Empleados encontrados: 2", ventana._var_resumen.get())
            self.assertIn("Consulta terminada.", ventana._var_resumen.get())
        finally:
            ventana.destroy()

    def test_organizador_reordena_restaura_y_confirma(self) -> None:
        from core import busqueda_solicitantes as busqueda
        from ventana_validacion import VentanaOrdenTerminos

        plan = busqueda.plan_busqueda("Juan Carlos Perez")
        with mock.patch("tkinter.Toplevel.grab_set", return_value=None):
            ventana = VentanaOrdenTerminos(self.raiz, plan)
        ventana.withdraw()
        try:
            self.assertEqual(
                ventana._terminos, ["JUAN", "CARLOS", "PEREZ"]
            )
            ventana._lista.selection_set(0)
            ventana._bajar()
            self.assertEqual(
                ventana._terminos, ["CARLOS", "JUAN", "PEREZ"]
            )
            self.assertIn(
                "%CARLOS%JUAN%PEREZ%", ventana._var_patron.get()
            )
            ventana._restaurar()
            self.assertEqual(
                ventana._terminos, ["JUAN", "CARLOS", "PEREZ"]
            )
            self.assertIn(
                "%JUAN%CARLOS%PEREZ%", ventana._var_patron.get()
            )
            ventana._confirmar()
            self.assertEqual(
                ventana._resultado, ["JUAN", "CARLOS", "PEREZ"]
            )
        finally:
            try:
                ventana.destroy()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    unittest.main()
