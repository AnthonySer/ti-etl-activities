from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

from core import eventos
from interfaz import AplicacionTk
from ventana_validacion import VentanaValidacion


class ControladorHilosFalso:
    def __init__(self) -> None:
        self.tareas = []

    def _ejecutar_hilo(self, tarea, callback, _descripcion) -> None:
        self.tareas.append((tarea, callback))


class ArbolFalso:
    def __init__(self) -> None:
        self.hilos_que_modificaron = []

    def get_children(self):
        return ()

    def delete(self, *_items) -> None:
        self.hilos_que_modificaron.append(threading.get_ident())

    def insert(self, _parent, _indice, *, values) -> None:
        self.hilos_que_modificaron.append(threading.get_ident())


class VentanaValidacionFalsa(VentanaValidacion):
    def winfo_exists(self) -> bool:
        return True

    def _pintar_registros_filtrados(self, registros, _config, resumen) -> None:
        self.resultados_pintados.append((threading.get_ident(), registros, resumen))


class PruebasRefrescoValidacion(unittest.TestCase):
    def crear_ventana(self):
        ventana = object.__new__(VentanaValidacionFalsa)
        ventana._padre = ControladorHilosFalso()
        ventana._tabla = ArbolFalso()
        ventana._iids = {}
        ventana._refresco_tabla_en_curso = False
        ventana._refresco_tabla_version = 0
        ventana._refresco_tabla_pendiente = None
        ventana._pintado_tabla_after_id = None
        ventana.after_cancel = MagicMock()
        ventana._var_busqueda = SimpleNamespace(value="ANA", get=lambda: ventana._var_busqueda.value)
        ventana._var_estado = SimpleNamespace(value="Todos", get=lambda: ventana._var_estado.value)
        ventana._var_resumen = SimpleNamespace(value="", set=lambda value: setattr(ventana._var_resumen, "value", value))
        ventana.revision = SimpleNamespace(registros=[
            {"usuario": "ANA TORRES", "empresa": "A", "ticket": "1", "area_actual": "TI", "estado": "Pendiente"},
            {"usuario": "LUIS PEREZ", "empresa": "B", "ticket": "2", "area_actual": "TI", "estado": "Pendiente"},
        ], total_registros=2, validado=lambda registro: bool(registro.get("validado")))
        ventana.resultados_pintados = []
        return ventana

    def ejecutar_tarea(self, tarea_callback) -> None:
        tarea, callback = tarea_callback
        salida = []
        hilo = threading.Thread(target=lambda: salida.append(tarea()))
        hilo.start()
        hilo.join(timeout=5)
        self.assertFalse(hilo.is_alive())
        callback(salida[0])

    def test_filtro_prepara_fuera_de_ui_y_pinta_en_hilo_principal(self) -> None:
        ventana = self.crear_ventana()
        hilo_ui = threading.get_ident()
        with patch("ventana_validacion.cargar_configuracion", return_value=object()):
            ventana._refrescar()
            self.ejecutar_tarea(ventana._padre.tareas.pop(0))

        self.assertEqual(ventana._tabla.hilos_que_modificaron, [hilo_ui])
        hilo_pintado, registros, resumen = ventana.resultados_pintados[0]
        self.assertEqual(hilo_pintado, hilo_ui)
        self.assertEqual([r["usuario"] for r in registros], ["ANA TORRES"])
        self.assertEqual(resumen["total_usuarios"], 2)
        self.assertEqual(resumen["por_revisar"], 2)

    def test_resultado_obsoleto_no_reemplaza_el_filtro_mas_reciente(self) -> None:
        ventana = self.crear_ventana()
        with patch("ventana_validacion.cargar_configuracion", return_value=object()):
            ventana._refrescar()
            ventana._var_busqueda.value = "LUIS"
            ventana._refrescar()
            self.ejecutar_tarea(ventana._padre.tareas.pop(0))
            self.assertEqual(ventana.resultados_pintados, [])
            self.assertEqual(len(ventana._padre.tareas), 1)
            self.ejecutar_tarea(ventana._padre.tareas.pop(0))

        self.assertEqual(
            [r["usuario"] for r in ventana.resultados_pintados[0][1]], ["LUIS PEREZ"]
        )

    def test_error_de_preparacion_limpia_estado_y_libera_control(self) -> None:
        ventana = self.crear_ventana()
        with patch("ventana_validacion.cargar_configuracion", side_effect=OSError("fallo")):
            ventana._refrescar()
            self.ejecutar_tarea(ventana._padre.tareas.pop(0))

        self.assertFalse(ventana._refresco_tabla_en_curso)
        self.assertIn("No se pudo actualizar", ventana._var_resumen.value)

    def test_filtrado_conserva_criterio_de_estado_y_busqueda(self) -> None:
        registros = [
            {"usuario": "ANA TORRES", "estado": "Pendiente"},
            {"usuario": "ANA TORRES", "estado": "Validado"},
        ]
        filtrados = VentanaValidacion._filtrar_registros(registros, "ana", "Validado")
        self.assertEqual(filtrados, [registros[1]])


class PruebasCoordinadorInterfaz(unittest.TestCase):
    def test_eventos_consecutivos_programan_cada_vista_una_vez(self) -> None:
        app = object.__new__(AplicacionTk)
        app._refrescar_usuarios = MagicMock()
        app._refrescar_maestro = MagicMock()
        app._refrescar_pendientes = MagicMock()
        app._refrescar_panel = MagicMock()
        app._solicitar_refresco_datos = MagicMock()
        temas = {
            eventos.ASIGNACION_REALIZADA,
            eventos.USUARIO_ACTUALIZADO,
            eventos.AREA_CREADA,
            eventos.MAESTRO_ACTUALIZADO,
            eventos.CATALOGO_AREAS_ACTUALIZADO,
            eventos.PROCESAMIENTO_FINALIZADO,
            eventos.CONSULTA_SQL_FINALIZADA,
        }

        app._aplicar_eventos(temas)

        app._refrescar_usuarios.assert_not_called()
        app._refrescar_maestro.assert_called_once_with(refrescar_solicitantes=False)
        app._refrescar_pendientes.assert_not_called()
        app._refrescar_panel.assert_called_once_with()
        app._solicitar_refresco_datos.assert_called_once_with(
            {"usuarios", "pendientes", "auditoria", "solicitantes"}
        )

    def test_refrescos_rapidos_se_coalescen_en_una_programacion(self) -> None:
        app = object.__new__(AplicacionTk)
        app._vistas_refresco_pendientes = set()
        app._refresco_cargando_maestro = False
        app._refresco_version = 0
        app._refresco_en_curso = False
        app._refresco_inicio_after_id = None
        app._ruta_adicional_solicitantes = None
        app.after = MagicMock(return_value="after-id")

        app._solicitar_refresco_datos({"usuarios"})
        app._solicitar_refresco_datos({"pendientes", "usuarios"})

        self.assertEqual(app._vistas_refresco_pendientes, {"usuarios", "pendientes"})
        self.assertEqual(app._refresco_version, 2)
        app.after.assert_called_once_with(40, app._iniciar_refresco_datos_programado)

    def test_pintado_se_reparte_en_bloques_del_hilo_principal(self) -> None:
        app = object.__new__(AplicacionTk)
        app._pintado_after_ids = {}
        app._pintado_versiones = {}
        app._tabla_fake = ArbolFalso()
        pendientes = []
        app.after_idle = lambda callback: pendientes.append(callback) or "idle-id"
        app.after = lambda _ms, callback, *args: pendientes.append(
            lambda: callback(*args)
        ) or f"after-{len(pendientes)}"
        app.after_cancel = MagicMock()
        filas = list(range(160))
        pintadas = []

        app._pintar_en_bloques(
            "prueba", app._tabla_fake, filas,
            lambda fila: (pintadas.append(fila), app._tabla_fake.insert("", "end", values=(fila,))),
        )
        primera_tanda = pendientes.pop(0)
        primera_tanda()
        self.assertEqual(len(pintadas), 150)
        segunda_tanda = pendientes.pop(0)
        segunda_tanda()

        self.assertEqual(pintadas, filas)
        self.assertTrue(all(hilo == threading.get_ident() for hilo in app._tabla_fake.hilos_que_modificaron))


if __name__ == "__main__":
    unittest.main()
