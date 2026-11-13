"""Pruebas de las fases 2 a 6 del plan de optimización.

Trabajan en carpetas temporales y no requieren SQL Server: los repositorios y
las funciones de persistencia se inyectan como dobles de prueba.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core import asignacion_asistida, catalogo_areas, diagnostico, eventos, similitud  # noqa: E402
from core import operaciones  # noqa: E402
from core.integridad_excel import (  # noqa: E402
    ErrorCambioExterno,
    RegistroHuellas,
    guardar_maestro_seguro,
    huella_archivo,
)
from core.maestro import Maestro  # noqa: E402
from database import coincidencias  # noqa: E402
from database.repository import Empleado, ResultadoConsulta  # noqa: E402


def crear_maestro(areas: dict[str, str]) -> Maestro:
    df = pd.DataFrame(
        {
            "USUARIO": list(areas),
            "AREA_USUARIO": list(areas.values()),
        }
    )
    return Maestro(df)


class PruebasSimilitud(unittest.TestCase):
    def test_tildes_mayusculas_y_espacios(self) -> None:
        self.assertEqual(similitud.comparar("  josé   pérez ", "JOSE PEREZ"), 1.0)

    def test_orden_de_nombres(self) -> None:
        self.assertEqual(
            similitud.comparar("LUIS PEREZ GARCIA", "GARCIA LUIS PEREZ"), 1.0
        )
        self.assertEqual(
            similitud.clasificar(1.0, similitud.UmbralesSimilitud()), similitud.EXACTA
        )

    def test_error_de_escritura(self) -> None:
        puntaje = similitud.comparar("ANA TORRE", "ANA TORRES")
        self.assertGreaterEqual(puntaje, 0.9)
        self.assertEqual(
            similitud.clasificar(puntaje, similitud.UmbralesSimilitud()), similitud.ALTA
        )

    def test_levenshtein_normalizada(self) -> None:
        self.assertEqual(similitud.levenshtein("casa", "caza"), 1)
        self.assertAlmostEqual(similitud.similitud_levenshtein("casa", "caza"), 0.75)

    def test_nombres_distintos_no_se_fusionan(self) -> None:
        self.assertLess(similitud.comparar("ANA TORRES", "LUIS PEREZ"), 0.5)
        self.assertEqual(
            similitud.clasificar(0.1, similitud.UmbralesSimilitud()),
            similitud.SIN_COINCIDENCIA,
        )

    def test_buscar_similares_ordena_y_limita(self) -> None:
        candidatos = ["ANA TORRES", "ANA TORREZ", "LUIS PEREZ"]
        resultados = similitud.buscar_similares(
            "ANA TORRE", candidatos, limite=2, umbral=0.7
        )
        self.assertEqual(len(resultados), 2)
        self.assertEqual(resultados[0][2], "ANA TORRES")
        self.assertTrue(all(puntaje >= 0.7 for puntaje, _tipo, _ in resultados))


class RepositorioFalso:
    def __init__(self, por_consulta: dict[str, list[Empleado]] | None = None) -> None:
        self.por_consulta = por_consulta or {}
        self.llamadas = 0

    def buscar_por_nombres(self, nombres, empresas=None, **_kwargs) -> ResultadoConsulta:
        self.llamadas += 1
        resultado = ResultadoConsulta()
        for nombre in nombres:
            clave = coincidencias.normalizar_nombre(nombre)
            empleados = self.por_consulta.get(clave, [])
            resultado.por_consulta[clave] = list(empleados)
            resultado.empleados.extend(empleados)
        return resultado


class PruebasCoincidencias(unittest.TestCase):
    def test_consolida_cache_sql_y_maestro_sin_mezclar_empresas(self) -> None:
        empleado = Empleado(
            empresa="SERVIAM",
            codigo="001",
            nombre="ANA TORRES",
            departamento="SISTEMAS",
            activo=True,
        )
        maestro = crear_maestro({"ANA TORRES": "SISTEMAS"})
        consolidador = coincidencias.ConsolidadorCoincidencias(
            maestro=maestro,
            repositorio=RepositorioFalso({"ANA TORRES": [empleado]}),
            cargar_empleados=lambda: [],
        )
        grupos = consolidador.buscar(["ANA TORRES"], usar_sql=True)
        grupo = grupos[coincidencias.normalizar_nombre("ANA TORRES")]
        self.assertTrue(grupo.candidatos)
        self.assertEqual(grupo.candidatos[0]["tipo"], similitud.EXACTA)
        # El resultado exacto conserva empresa y usuario original.
        exactos = [item for item in grupo.candidatos if item["tipo"] == similitud.EXACTA]
        self.assertTrue(any(item["empresa"] == "SERVIAM" for item in exactos))
        self.assertTrue(any(item["empresa"] == "MAESTRO" for item in exactos))
        self.assertTrue(all(item["usuario_original"] == "ANA TORRES" for item in exactos))

    def test_nombre_parecido_no_se_marca_como_exacto(self) -> None:
        maestro = crear_maestro({"ANA TORRES": "SISTEMAS"})
        consolidador = coincidencias.ConsolidadorCoincidencias(
            maestro=maestro,
            repositorio=RepositorioFalso(),
            cargar_empleados=lambda: [],
        )
        grupos = consolidador.buscar(["ANA TORRE"], usar_sql=False)
        grupo = grupos[coincidencias.normalizar_nombre("ANA TORRE")]
        self.assertEqual(grupo.candidatos[0]["tipo"], similitud.ALTA)
        self.assertFalse(grupo.exactos)

    def test_no_repite_consulta_sql_en_la_misma_ejecucion(self) -> None:
        repo = RepositorioFalso()
        consolidador = coincidencias.ConsolidadorCoincidencias(
            maestro=crear_maestro({}),
            repositorio=repo,
            cargar_empleados=lambda: [],
        )
        consolidador.buscar(["ANA TORRES"])
        consolidador.buscar(["ANA TORRES"])
        self.assertEqual(repo.llamadas, 1)

    def test_invalidar_permite_consultar_de_nuevo(self) -> None:
        repo = RepositorioFalso()
        consolidador = coincidencias.ConsolidadorCoincidencias(
            maestro=crear_maestro({}),
            repositorio=repo,
            cargar_empleados=lambda: [],
        )
        consolidador.buscar(["ANA TORRES"])
        consolidador.invalidar(sql=True)
        consolidador.buscar(["ANA TORRES"])
        self.assertEqual(repo.llamadas, 2)

    def test_buscar_para_pendientes_conserva_filas_y_orden(self) -> None:
        consolidador = coincidencias.ConsolidadorCoincidencias(
            maestro=crear_maestro({"ANA TORRES": "SISTEMAS"}),
            repositorio=None,
            cargar_empleados=lambda: [],
        )
        pendientes = [
            {"usuario": "ANA TORRE", "filas": [1, 2], "area_preexistente": ""},
            {"usuario": "ANA TORRES", "filas": [3], "area_preexistente": ""},
            {"usuario": "LUIS PEREZ", "filas": [4], "area_preexistente": ""},
        ]
        resultado = consolidador.buscar_para_pendientes(
            pendientes, usar_sql=False
        )
        self.assertEqual(
            [item["usuario"] for item in resultado],
            ["ANA TORRE", "ANA TORRES", "LUIS PEREZ"],
        )
        self.assertEqual(resultado[0]["filas"], 2)
        # "ANA TORRE" solo tiene coincidencias aproximadas; "ANA TORRES" es exacta.
        self.assertEqual(resultado[0]["exactos"], 0)
        self.assertGreaterEqual(resultado[0]["aproximados"], 1)
        self.assertGreaterEqual(resultado[1]["exactos"], 1)


class PruebasCatalogoAreas(unittest.TestCase):
    def setUp(self) -> None:
        self.maestro = crear_maestro({"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"})

    def test_catalogo_unico(self) -> None:
        self.assertEqual(
            catalogo_areas.catalogo_desde_maestro(self.maestro),
            ["CONTABILIDAD", "SISTEMAS"],
        )

    def test_duplicado_por_tildes_y_mayusculas(self) -> None:
        resultado = catalogo_areas.validar_area("sistemás", maestro=self.maestro)
        self.assertTrue(resultado.valido)
        self.assertTrue(resultado.existente)
        self.assertEqual(resultado.area_reutilizable, "SISTEMAS")

    def test_area_parecida_requiere_confirmacion(self) -> None:
        resultado = catalogo_areas.validar_area("SISTEMA", maestro=self.maestro)
        self.assertTrue(resultado.valido)
        self.assertTrue(resultado.similares)
        self.assertTrue(resultado.requiere_confirmacion)

    def test_area_vacia_no_es_valida(self) -> None:
        resultado = catalogo_areas.validar_area("   ", maestro=self.maestro)
        self.assertFalse(resultado.valido)

    def test_crear_area_requiere_confirmacion(self) -> None:
        llamadas = []

        def persistir(*args, **kwargs):
            llamadas.append((args, kwargs))
            return SimpleNamespace(registradas=1)

        resultado = catalogo_areas.crear_area_para_usuario(
            self.maestro,
            "NUEVO USUARIO",
            "TESORERIA",
            confirmar=False,
            persistir=persistir,
        )
        self.assertFalse(resultado.creada)
        self.assertFalse(llamadas)

        resultado = catalogo_areas.crear_area_para_usuario(
            self.maestro,
            "NUEVO USUARIO",
            "TESORERIA",
            confirmar=True,
            persistir=persistir,
        )
        self.assertTrue(resultado.creada)
        self.assertEqual(len(llamadas), 1)

    def test_crear_area_reutiliza_duplicado(self) -> None:
        llamadas = []

        def persistir(_maestro, usuario, area, **_kwargs):
            llamadas.append((usuario, area))
            return None

        resultado = catalogo_areas.crear_area_para_usuario(
            self.maestro,
            "NUEVO USUARIO",
            "sistemas",
            confirmar=True,
            persistir=persistir,
        )
        self.assertFalse(resultado.creada)
        self.assertEqual(llamadas, [("NUEVO USUARIO", "SISTEMAS")])


class PruebasAsignacionAsistida(unittest.TestCase):
    def test_usuario_inactivo_requiere_confirmacion_expresa(self) -> None:
        preparacion = asignacion_asistida.preparar_asignacion(
            "ANA TORRE",
            {
                "nombre": "ANA TORRES",
                "empresa": "SERVIAM",
                "departamento": "SISTEMAS",
                "activo": False,
            },
        )
        self.assertTrue(preparacion.inactivo)
        self.assertIn("inactivo", preparacion.mensaje.lower())

        llamadas = []
        resultado = asignacion_asistida.ejecutar_asignacion(
            crear_maestro({}),
            preparacion,
            confirmar=True,
            permitir_inactivo=False,
            persistir=lambda *args, **kwargs: llamadas.append(1),
        )
        self.assertFalse(resultado.ejecutada)
        self.assertFalse(llamadas)

        resultado = asignacion_asistida.ejecutar_asignacion(
            crear_maestro({}),
            preparacion,
            confirmar=True,
            permitir_inactivo=True,
            persistir=lambda *args, **kwargs: llamadas.append(1),
        )
        self.assertTrue(resultado.ejecutada)
        self.assertEqual(llamadas, [1])

    def test_asignacion_normal_requiere_confirmacion(self) -> None:
        preparacion = asignacion_asistida.preparar_asignacion(
            "ANA TORRES", {"departamento": "SISTEMAS", "activo": True}
        )
        resultado = asignacion_asistida.ejecutar_asignacion(
            crear_maestro({}), preparacion, confirmar=False
        )
        self.assertFalse(resultado.ejecutada)


class PruebasOperaciones(unittest.TestCase):
    def setUp(self) -> None:
        self.temporal = tempfile.TemporaryDirectory()
        self.ruta = Path(self.temporal.name) / "operaciones.json"
        self.diario = operaciones.DiarioOperaciones(self.ruta)

    def tearDown(self) -> None:
        self.temporal.cleanup()

    def test_reintento_idempotente_no_duplica(self) -> None:
        llamadas = []

        def escribir() -> str:
            llamadas.append(1)
            return "ok"

        _, resultado, ejecutada = self.diario.ejecutar(
            "ASIGNAR", escribir, usuario="ANA TORRES", area_nueva="SISTEMAS"
        )
        self.assertTrue(ejecutada)
        self.assertEqual(resultado, "ok")

        _, _, ejecutada = self.diario.ejecutar(
            "ASIGNAR", escribir, usuario="ANA TORRES", area_nueva="SISTEMAS"
        )
        self.assertFalse(ejecutada)
        self.assertEqual(len(llamadas), 1)
        self.assertEqual(self.diario.resumen()["TOTAL"], 1)

    def test_error_se_registra_y_puede_reintentarse(self) -> None:
        def falla() -> None:
            raise RuntimeError("sin permisos")

        with self.assertRaises(RuntimeError):
            self.diario.ejecutar(
                "SINCRONIZAR", falla, usuario="ANA TORRES", area_nueva="SISTEMAS"
            )
        pendientes = self.diario.pendientes()
        self.assertEqual(len(pendientes), 1)
        self.assertEqual(pendientes[0].estado, operaciones.ERROR)

        clave = pendientes[0].clave
        operacion, resultado, ejecutada = self.diario.reintentar(
            clave, lambda: "recuperado"
        )
        self.assertTrue(ejecutada)
        self.assertEqual(resultado, "recuperado")
        self.assertEqual(operacion.estado, operaciones.COMPLETADO)

    def test_estado_requiere_revision_no_se_reintenta(self) -> None:
        operacion = self.diario.registrar(
            "SINCRONIZAR", usuario="ANA TORRES", area_nueva="SISTEMAS"
        )
        self.diario.marcar(operacion.clave, operaciones.REQUIERE_REVISION)
        _, _, ejecutada = self.diario.reintentar(operacion.clave, lambda: "no")
        self.assertFalse(ejecutada)

    def test_diario_no_guarda_credenciales(self) -> None:
        self.diario.registrar(
            "CONSULTA",
            usuario="ANA TORRES",
            detalle="consulta SQL",
            empresa="SERVIAM",
        )
        texto = self.ruta.read_text(encoding="utf-8")
        self.assertNotIn("password", texto.lower())
        self.assertNotIn("contrasena", texto.lower())


class MaestroFalso:
    def __init__(self, ruta: Path) -> None:
        self.ruta = ruta
        self.llamadas = 0

    def como_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame({"USUARIO": ["ANA TORRES"], "AREA_USUARIO": ["SISTEMAS"]})

    def guardar(self, ruta_xlsx=None, crear_backup=True):
        self.llamadas += 1
        destino = Path(ruta_xlsx or self.ruta)
        destino.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"USUARIO": ["ANA TORRES"], "AREA_USUARIO": ["SISTEMAS"]}).to_excel(
            destino, index=False, engine="openpyxl"
        )
        return SimpleNamespace(ruta_xlsx=destino)


class PruebasIntegridadExcel(unittest.TestCase):
    def setUp(self) -> None:
        self.temporal = tempfile.TemporaryDirectory()
        self.carpeta = Path(self.temporal.name)
        self.ruta = self.carpeta / "maestro.xlsx"
        self.registro = RegistroHuellas(self.carpeta / "huellas.json")
        self.diario = operaciones.DiarioOperaciones(self.carpeta / "operaciones.json")
        self.maestro = MaestroFalso(self.ruta)
        pd.DataFrame({"USUARIO": ["ANA TORRES"], "AREA_USUARIO": ["SISTEMAS"]}).to_excel(
            self.ruta, index=False, engine="openpyxl"
        )
        self.registro.guardar(self.ruta)

    def tearDown(self) -> None:
        self.temporal.cleanup()

    def test_huella_detecta_cambio_externo(self) -> None:
        self.assertFalse(self.registro.comparar(self.ruta)["cambio_externo"])
        with self.ruta.open("ab") as archivo:
            archivo.write(b"cambio")
        self.assertTrue(self.registro.comparar(self.ruta)["cambio_externo"])

    def test_guardar_bloquea_cambio_externo_sin_confirmacion(self) -> None:
        with self.ruta.open("ab") as archivo:
            archivo.write(b"cambio")
        with self.assertRaises(ErrorCambioExterno):
            guardar_maestro_seguro(
                self.maestro,
                self.ruta,
                registro=self.registro,
                diario=self.diario,
            )
        self.assertEqual(self.maestro.llamadas, 0)

    def test_guardar_confirmado_actualiza_huella(self) -> None:
        with self.ruta.open("ab") as archivo:
            archivo.write(b"cambio")
        guardar_maestro_seguro(
            self.maestro,
            self.ruta,
            confirmar_cambio_externo=True,
            registro=self.registro,
            diario=self.diario,
        )
        self.assertEqual(self.maestro.llamadas, 1)
        self.assertFalse(self.registro.comparar(self.ruta)["cambio_externo"])
        self.assertEqual(self.diario.resumen()["COMPLETADO"], 1)

    def test_huella_es_estable(self) -> None:
        primera = huella_archivo(self.ruta)
        segunda = huella_archivo(self.ruta)
        self.assertEqual(primera["sha256"], segunda["sha256"])
        self.assertEqual(
            primera["sha256"], hashlib.sha256(self.ruta.read_bytes()).hexdigest()
        )


class PruebasDiagnostico(unittest.TestCase):
    def test_diagnostico_sin_credenciales(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            diario = operaciones.DiarioOperaciones(Path(carpeta) / "operaciones.json")
            operacion = diario.registrar(
                "ASIGNAR",
                usuario="ANA TORRES",
                area_nueva="SISTEMAS",
                detalle="prueba",
            )
            diario.marcar(operacion.clave, operaciones.ERROR, error="fallo controlado")
            datos = diagnostico.diagnosticar(diario=diario)
            self.assertGreaterEqual(datos.operaciones["TOTAL"], 1)
            self.assertTrue(datos.alertas)
            markdown = datos.como_markdown()
            self.assertIn("Diagnóstico del sistema", markdown)
            self.assertNotIn("password", markdown.lower())

    def test_historial_por_usuario(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            diario = operaciones.DiarioOperaciones(Path(carpeta) / "operaciones.json")
            diario.registrar("ASIGNAR", usuario="ANA TORRES", area_nueva="SISTEMAS")
            diario.registrar("ASIGNAR", usuario="LUIS PEREZ", area_nueva="CONTABILIDAD")
            historial = diagnostico.operaciones_por_usuario(
                "ana torres", diario=diario
            )
            self.assertEqual(len(historial), 1)
            self.assertEqual(historial[0]["usuario"], "ANA TORRES")


class PruebasEventos(unittest.TestCase):
    def test_publicacion_aisla_errores_y_desuscribe(self) -> None:
        bus = eventos.BusEventos()
        recibidos = []

        def falla(_evento) -> None:
            raise RuntimeError("fallo del suscriptor")

        cancelar = bus.suscribir(eventos.ASIGNACION_REALIZADA, falla)
        bus.suscribir(eventos.ASIGNACION_REALIZADA, recibidos.append)
        bus.publicar(eventos.ASIGNACION_REALIZADA, usuario="ANA", area="SISTEMAS")
        self.assertEqual(len(recibidos), 1)
        cancelar()
        bus.publicar(eventos.ASIGNACION_REALIZADA, usuario="LUIS", area="CONTABILIDAD")
        self.assertEqual(len(recibidos), 2)

    def test_evento_desconocido_falla(self) -> None:
        with self.assertRaises(ValueError):
            eventos.BusEventos().publicar("NO_EXISTE")


if __name__ == "__main__":
    unittest.main()
