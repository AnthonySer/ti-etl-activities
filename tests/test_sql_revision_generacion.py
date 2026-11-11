"""Pruebas de la integración SQL Server, resolución, revisión y generación.

Todas las pruebas usan carpetas temporales y dobles de prueba: nunca se conecta
ni se escribe en la base corporativa real.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pandas as pd
from openpyxl import load_workbook

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import configuracion as cfg  # noqa: E402
from config.sqlserver import ConfiguracionSQL  # noqa: E402
from core import revision as svc_revision  # noqa: E402
from core.archivos import ErrorArchivo  # noqa: E402
from core.esquema import COLUMNAS_OFICIALES  # noqa: E402
from core.maestro import Maestro  # noqa: E402
from core.procesado import rotar_respaldos  # noqa: E402
from core.procesador import procesar_archivo  # noqa: E402
from database import connection, queries  # noqa: E402
from database.connection import (  # noqa: E402
    ErrorConexionSQL,
    conectar,
    validar_solo_lectura,
)
from database.repository import Empleado, RepositorioEmpleados  # noqa: E402
from database.resolucion import (  # noqa: E402
    CASO_A,
    CASO_B,
    CASO_C,
    CASO_D,
    CASO_E,
    ESTADO_AMBIGUO,
    ESTADO_INACTIVO,
    ESTADO_MANUAL_PROTEGIDO,
    ESTADO_SIN_COINCIDENCIAS,
    ESTADO_SIN_DEPARTAMENTO,
    ESTADO_VALIDADO,
    ResolverAreas,
)
from database.synchronizer import _alertas_contra_maestro  # noqa: E402
from main import construir_parser, ejecutar_consola  # noqa: E402


def escribir_tabla(df: pd.DataFrame, ruta: Path, hoja: str = "DATOS") -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(ruta, engine="openpyxl") as libro:
        df.to_excel(libro, sheet_name=hoja, index=False)
    return ruta


def datos_base() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "DETALLE": "SOPORTE IMPRESORA",
                "FECHA": "2026-09-01",
                "USUARIO": "ANA TORRES",
                "AREA_USUARIO": None,
                "ATENCION": "Mesa de Ayuda",
                "REQUERIMIENTO": "Soporte",
                "RESPONSABLE": "CIRO HUAMANI",
                "SUBTIPO": "Software oficina",
                "PROYECTO": "MESA DE TRABAJO",
                "HORAS": 2.0,
                "ACTIVIDAD": "Incidencia",
            },
            {
                "DETALLE": "CONFIGURACION EXCEL",
                "FECHA": "2026-09-02",
                "USUARIO": "LUIS PEREZ",
                "AREA_USUARIO": None,
                "ATENCION": "Gestion",
                "REQUERIMIENTO": "Gestion",
                "RESPONSABLE": "PAUL ARANDA",
                "SUBTIPO": "Ofimatica",
                "PROYECTO": "PORTAL",
                "HORAS": 1.5,
                "ACTIVIDAD": "Requerimiento",
            },
        ]
    )


def crear_maestro(areas: dict[str, str]) -> Maestro:
    df = pd.DataFrame(
        [{"USUARIO": usuario, "AREA_USUARIO": area} for usuario, area in areas.items()],
        columns=["USUARIO", "AREA_USUARIO"],
    )
    maestro = Maestro(df)
    if areas:
        maestro.guardar(cfg.MAESTRO_XLSX)
    return maestro


def hash_archivo(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


class BaseTemporal(unittest.TestCase):
    CAMPOS_RUTA = (
        "INPUT_DIR",
        "OUTPUT_DIR",
        "REVISION_DIR",
        "DATA_DIR",
        "BACKUPS_DIR",
        "LOGS_DIR",
        "REPORTES_DIR",
        "MAESTRO_XLSX",
        "MAESTRO_CSV",
        "AUDITORIA_CSV",
        "ESTADO_UI_JSON",
        "LOCK_SISTEMA",
    )

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory(prefix="tickets_sql_")
        self.raiz = Path(self._temporal.name)
        self._originales = {campo: getattr(cfg, campo) for campo in self.CAMPOS_RUTA}
        cfg.INPUT_DIR = self.raiz / "input"
        cfg.OUTPUT_DIR = self.raiz / "output"
        cfg.REVISION_DIR = cfg.OUTPUT_DIR / "revision"
        cfg.DATA_DIR = self.raiz / "data"
        cfg.BACKUPS_DIR = cfg.DATA_DIR / "backups"
        cfg.LOGS_DIR = self.raiz / "logs"
        cfg.REPORTES_DIR = self.raiz / "reportes"
        cfg.MAESTRO_XLSX = cfg.DATA_DIR / "maestro_areas.xlsx"
        cfg.MAESTRO_CSV = cfg.DATA_DIR / "maestro_areas.csv"
        cfg.AUDITORIA_CSV = cfg.DATA_DIR / "auditoria_ejecuciones.csv"
        cfg.ESTADO_UI_JSON = cfg.DATA_DIR / "estado_ui.json"
        cfg.LOCK_SISTEMA = cfg.DATA_DIR / ".sistema.lock"
        cfg.asegurar_directorios()

    def tearDown(self) -> None:
        for campo, valor in self._originales.items():
            setattr(cfg, campo, valor)
        self._temporal.cleanup()

    def preparar(self, areas: dict[str, str] | None = None, nombre: str = "consulta.xlsx"):
        entrada = escribir_tabla(datos_base(), cfg.INPUT_DIR / nombre)
        maestro = crear_maestro(areas or {})
        return entrada, maestro

    def config_sql(self) -> ConfiguracionSQL:
        return ConfiguracionSQL(
            habilitado=True,
            servidor="10.0.0.1",
            puerto=1433,
            base_datos="EXACTUS",
            autenticacion="sql",
            usuario="lector",
            contrasena="secreto",
            empresas=["CALCA", "INKA"],
            timeout=5,
            lote=50,
        )


# ---------------------------------------------------------------------------
# Conexión SQL
# ---------------------------------------------------------------------------
class PruebasConexionSQL(unittest.TestCase):
    def test_configuracion_incompleta_no_conecta(self) -> None:
        config = ConfiguracionSQL(habilitado=False)
        with self.assertRaises(ErrorConexionSQL) as ctx:
            with conectar(config):
                pass
        self.assertEqual(ctx.exception.categoria, "configuracion")

    def test_error_de_autenticacion_se_clasifica_y_cierra(self) -> None:
        config = ConfiguracionSQL(
            habilitado=True,
            servidor="10.0.0.1",
            base_datos="EXACTUS",
            autenticacion="sql",
            usuario="lector",
            contrasena="secreto",
        )
        falso = mock.MagicMock()
        falso.drivers.return_value = ["ODBC Driver 17 for SQL Server"]
        falso.connect.side_effect = Exception("28000 Login failed for user 'lector'")
        with mock.patch.object(connection, "pyodbc", falso):
            with self.assertRaises(ErrorConexionSQL) as ctx:
                with conectar(config):
                    pass
        self.assertEqual(ctx.exception.categoria, "autenticacion")
        self.assertNotIn("secreto", str(ctx.exception))

    def test_timeout_se_clasifica(self) -> None:
        config = ConfiguracionSQL(
            habilitado=True,
            servidor="10.0.0.1",
            base_datos="EXACTUS",
            autenticacion="windows",
            timeout=3,
        )
        falso = mock.MagicMock()
        falso.drivers.return_value = ["ODBC Driver 17 for SQL Server"]
        falso.connect.side_effect = Exception("HYT00 Query timeout expired")
        with mock.patch.object(connection, "pyodbc", falso):
            with self.assertRaises(ErrorConexionSQL) as ctx:
                with conectar(config):
                    pass
        self.assertEqual(ctx.exception.categoria, "timeout")

    def test_cierra_la_conexion_exitosa(self) -> None:
        config = ConfiguracionSQL(
            habilitado=True,
            servidor="10.0.0.1",
            base_datos="EXACTUS",
            autenticacion="windows",
        )
        conexion = mock.MagicMock()
        falso = mock.MagicMock()
        falso.drivers.return_value = ["ODBC Driver 17 for SQL Server"]
        falso.connect.return_value = conexion
        with mock.patch.object(connection, "pyodbc", falso):
            with conectar(config) as abierta:
                self.assertIs(abierta, conexion)
        conexion.close.assert_called_once()

    def test_solo_lectura_rechaza_modificaciones(self) -> None:
        with self.assertRaises(ErrorConexionSQL):
            validar_solo_lectura("DELETE FROM EMPLEADO")
        with self.assertRaises(ErrorConexionSQL):
            validar_solo_lectura("SELECT 1; DROP TABLE EMPLEADO")
        validar_solo_lectura("SELECT EMPLEADO FROM [EXACTUS].[INKA].[EMPLEADO]")


# ---------------------------------------------------------------------------
# Repositorio por lotes y aislamiento por empresa
# ---------------------------------------------------------------------------
class PruebasRepositorioSQL(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ConfiguracionSQL(
            habilitado=True,
            servidor="10.0.0.1",
            base_datos="EXACTUS",
            autenticacion="windows",
            empresas=["CALCA", "INKA"],
            lote=2,
        )
        self.llamadas: list[str] = []

    def _fabrica(self, config):
        @contextmanager
        def contexto():
            yield object()

        return contexto()

    def test_busqueda_por_lotes_consulta_cada_empresa(self) -> None:
        def ejecutor(conexion, sql, parametros):
            if "INFORMATION_SCHEMA.COLUMNS" in sql:
                return [
                    {"TABLE_NAME": "EMPLEADO", "COLUMN_NAME": columna}
                    for columna in ("EMPLEADO", "NOMBRE", "ACTIVO")
                ]
            self.llamadas.append(sql)
            empresa = "CALCA" if "[CALCA]" in sql else "INKA"
            return [
                {
                    "EMPLEADO": f"{empresa}-1",
                    "NOMBRE": "ANA TORRES",
                    "DEPARTAMENTO_DESCRIPCION": "SISTEMAS",
                    "ACTIVO": 1,
                }
            ]

        repo = RepositorioEmpleados(
            self.config, fabrica_conexion=self._fabrica, ejecutor=ejecutor
        )
        resultado = repo.buscar_por_nombres(["ANA TORRES", "LUIS PEREZ", "ANA TORRES"])
        self.assertEqual(len(self.llamadas), 2)  # un lote x dos empresas
        self.assertEqual(len(resultado.empleados), 2)
        self.assertEqual(resultado.errores, [])

    def test_una_empresa_con_error_no_bloquea_a_las_demas(self) -> None:
        def fabrica(config):
            @contextmanager
            def contexto():
                yield object()

            return contexto()

        def ejecutor(conexion, sql, parametros):
            if "INFORMATION_SCHEMA.COLUMNS" in sql:
                return [
                    {"TABLE_NAME": "EMPLEADO", "COLUMN_NAME": columna}
                    for columna in ("EMPLEADO", "NOMBRE", "ACTIVO")
                ]
            if "[INKA]" in sql:
                raise ErrorConexionSQL("Esquema inexistente", "servidor")
            return [
                {
                    "EMPLEADO": "C-1",
                    "NOMBRE": "ANA TORRES",
                    "DEPARTAMENTO_DESCRIPCION": "SISTEMAS",
                    "ACTIVO": 1,
                }
            ]

        repo = RepositorioEmpleados(
            self.config, fabrica_conexion=fabrica, ejecutor=ejecutor
        )
        resultado = repo.buscar_por_nombres(["ANA TORRES"])
        self.assertEqual(len(resultado.empleados), 1)
        self.assertEqual(resultado.empleados[0].empresa, "CALCA")
        self.assertEqual(len(resultado.errores), 1)
        self.assertEqual(resultado.errores[0]["empresa"], "INKA")

    def test_consulta_adaptativa_omite_columnas_ausentes(self) -> None:
        sql, parametros = queries.sql_empleados(
            base_datos="EXACTUS",
            esquema="CALCA",
            cantidad=2,
            columnas_disponibles={
                "EMPLEADO": {"EMPLEADO", "NOMBRE", "ACTIVO"},
            },
        )
        self.assertIn("'EMPLEADO' AS FUENTE", sql)
        self.assertIn("E.[NOMBRE] AS NOMBRE", sql)
        self.assertIn("NULL AS DEPARTAMENTO_CODIGO", sql)
        self.assertNotIn("U_CORREO_CORP", sql)
        self.assertNotIn("LEFT JOIN", sql)
        self.assertEqual(parametros, 2)

    def test_consulta_adaptativa_rechaza_nombre_insuficiente(self) -> None:
        with self.assertRaises(queries.EsquemaInsuficiente):
            queries.sql_empleados(
                base_datos="EXACTUS",
                esquema="CALCA",
                columnas_disponibles={"EMPLEADO": {"EMPLEADO", "ACTIVO"}},
            )


# ---------------------------------------------------------------------------
# Resolución de nombres
# ---------------------------------------------------------------------------
def empleado(
    nombre: str,
    departamento: str = "SISTEMAS",
    *,
    empresa: str = "INKA",
    codigo: str = "E-1",
    activo: bool | None = True,
    estado: str = "",
) -> Empleado:
    return Empleado(
        empresa=empresa,
        codigo=codigo,
        nombre=nombre,
        departamento=departamento,
        activo=activo,
        estado_empleado=estado,
    )


def resolver(usuarios, empleados, maestro=None):
    return ResolverAreas(
        empleados,
        maestro=maestro,
        cargar_procedencia=lambda: {"registros": {}},
    ).resolver_lote(usuarios)


class PruebasResolucion(unittest.TestCase):
    def test_coincidencia_unica_asigna_departamento(self) -> None:
        resultados = resolver(["ANA TORRES"], [empleado("ANA TORRES", "SISTEMAS")])
        resultado = resultados["ANA TORRES"]
        self.assertEqual(resultado.caso, CASO_A)
        self.assertEqual(resultado.estado, ESTADO_VALIDADO)
        self.assertEqual(resultado.area, "SISTEMAS")
        self.assertTrue(resultado.asignable)

    def test_tildes_espacios_y_puntuacion(self) -> None:
        from database.normalizador import normalizar_nombre

        empleados = [empleado("JOSÉ  PÉREZ", "CONTABILIDAD", codigo="E-9")]
        for variante in ("JOSE PEREZ", "  josé pérez ", "JOSÉ PÉREZ."):
            resultados = resolver([variante], empleados)
            resultado = resultados[normalizar_nombre(variante)]
            self.assertEqual(resultado.caso, CASO_A, variante)
            self.assertEqual(resultado.area, "CONTABILIDAD")

    def test_coincidencia_multiple_no_asigna(self) -> None:
        empleados = [
            empleado("ANA TORRES", "SISTEMAS", empresa="INKA", codigo="1"),
            empleado("ANA TORRES", "CONTABILIDAD", empresa="CALCA", codigo="2"),
        ]
        resultado = resolver(["ANA TORRES"], empleados)["ANA TORRES"]
        self.assertEqual(resultado.caso, CASO_B)
        self.assertEqual(resultado.estado, ESTADO_AMBIGUO)
        self.assertFalse(resultado.asignable)
        self.assertEqual(len(resultado.candidatos), 2)

    def test_sin_coincidencias(self) -> None:
        resultado = resolver(["DESCONOCIDO"], [empleado("ANA TORRES")])[
            "DESCONOCIDO"
        ]
        self.assertEqual(resultado.caso, CASO_C)
        self.assertEqual(resultado.estado, ESTADO_SIN_COINCIDENCIAS)
        self.assertFalse(resultado.area)

    def test_empleado_sin_departamento(self) -> None:
        resultado = resolver(["ANA TORRES"], [empleado("ANA TORRES", "")])[
            "ANA TORRES"
        ]
        self.assertEqual(resultado.caso, CASO_D)
        self.assertEqual(resultado.estado, ESTADO_SIN_DEPARTAMENTO)
        self.assertFalse(resultado.area)

    def test_empleado_inactivo_no_se_descarta(self) -> None:
        resultado = resolver(
            ["ANA TORRES"],
            [empleado("ANA TORRES", "SISTEMAS", activo=False, estado="CESADO")],
        )["ANA TORRES"]
        self.assertEqual(resultado.caso, CASO_E)
        self.assertEqual(resultado.estado, ESTADO_INACTIVO)
        self.assertIn("inactivo", resultado.motivo)

    def test_asignacion_manual_se_protege(self) -> None:
        maestro = Maestro(
            pd.DataFrame(
                [{"USUARIO": "ANA TORRES", "AREA_USUARIO": "CONTABILIDAD"}],
                columns=["USUARIO", "AREA_USUARIO"],
            )
        )
        resultado = resolver(
            ["ANA TORRES"], [empleado("ANA TORRES", "SISTEMAS")], maestro
        )["ANA TORRES"]
        self.assertEqual(resultado.estado, ESTADO_MANUAL_PROTEGIDO)
        self.assertTrue(resultado.conflicto)
        self.assertFalse(resultado.asignable)
        self.assertEqual(resultado.area_anterior, "CONTABILIDAD")


# ---------------------------------------------------------------------------
# Revisiones
# ---------------------------------------------------------------------------
class PruebasRevision(BaseTemporal):
    def test_revision_persiste_y_se_reanuda(self) -> None:
        entrada, maestro = self.preparar({"ANA TORRES": "SISTEMAS"})
        estadisticas = procesar_archivo(
            entrada,
            maestro,
            modo=cfg.MODO_NORMAL,
            permitir_pendientes=True,
            crear_revision=True,
            registrar_auditoria=False,
        )
        self.assertIsNotNone(estadisticas.ruta_revision)
        revision = svc_revision.Revision.cargar(estadisticas.ruta_revision)
        self.assertGreater(revision.cantidad_pendientes, 0)

        revision.asignar_manual("LUIS PEREZ", "CONTABILIDAD")
        revision.guardar()
        recuperada = svc_revision.Revision.cargar(estadisticas.ruta_revision)
        self.assertEqual(recuperada.buscar("LUIS PEREZ")["area_actual"], "CONTABILIDAD")
        self.assertEqual(recuperada.cantidad_pendientes, 0)
        self.assertEqual(recuperada.resumen()["areas_validadas"] >= 1, True)

        listado = svc_revision.listar_revisiones()
        self.assertTrue(any(item["ruta"] == estadisticas.ruta_revision for item in listado))

    def test_sin_pendientes_no_crea_revision(self) -> None:
        entrada, maestro = self.preparar(
            {"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        estadisticas = procesar_archivo(
            entrada,
            maestro,
            modo=cfg.MODO_NORMAL,
            permitir_pendientes=False,
            crear_revision=True,
            registrar_auditoria=False,
        )
        self.assertIsNone(estadisticas.ruta_revision)


# ---------------------------------------------------------------------------
# Generación de Excel y copias de revisión
# ---------------------------------------------------------------------------
class PruebasGeneracion(BaseTemporal):
    def test_normal_una_sola_hoja_y_11_columnas(self) -> None:
        entrada, maestro = self.preparar(
            {"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        original = hash_archivo(entrada)
        estadisticas = procesar_archivo(
            entrada,
            maestro,
            modo=cfg.MODO_NORMAL,
            permitir_pendientes=False,
            registrar_auditoria=False,
        )
        self.assertEqual(hash_archivo(entrada), original)
        self.assertTrue(estadisticas.archivo_salida.endswith("consulta_procesado.xlsx"))
        libro = load_workbook(estadisticas.archivo_salida, read_only=True)
        try:
            self.assertEqual(libro.sheetnames, [cfg.HOJA_CONSULTA])
            encabezados = [celda.value for celda in next(libro[cfg.HOJA_CONSULTA].iter_rows(max_row=1))]
        finally:
            libro.close()
        self.assertEqual(encabezados, list(COLUMNAS_OFICIALES))

    def test_detalle_cinco_hojas_y_actualiza_canonico(self) -> None:
        entrada, maestro = self.preparar(
            {"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        estadisticas = procesar_archivo(
            entrada,
            maestro,
            modo=cfg.MODO_DETALLE,
            permitir_pendientes=False,
            registrar_auditoria=False,
        )
        self.assertTrue(Path(estadisticas.archivo_salida).is_file())
        self.assertIsNotNone(estadisticas.archivo_detalle)
        canonico = load_workbook(estadisticas.archivo_salida, read_only=True)
        detalle = load_workbook(estadisticas.archivo_detalle, read_only=True)
        try:
            self.assertEqual(canonico.sheetnames, [cfg.HOJA_CONSULTA])
            self.assertEqual(
                detalle.sheetnames,
                ["Consulta", "Observaciones", "Resumen", "Control", "Origen_Adicional"],
            )
        finally:
            canonico.close()
            detalle.close()
        self.assertTrue(Path(estadisticas.archivo_manifiesto or "").is_file())
        self.assertTrue(Path(estadisticas.archivo_manifiesto_detalle or "").is_file())

    def test_completo_se_bloquea_con_pendientes(self) -> None:
        entrada, maestro = self.preparar({"ANA TORRES": "SISTEMAS"})
        for modo in (cfg.MODO_NORMAL, cfg.MODO_DETALLE):
            with self.assertRaises(ErrorArchivo) as ctx:
                procesar_archivo(
                    entrada,
                    maestro,
                    modo=modo,
                    permitir_pendientes=False,
                    registrar_auditoria=False,
                )
            self.assertIn("Excel completo", str(ctx.exception))

    def test_parcial_registra_la_condicion(self) -> None:
        entrada, maestro = self.preparar({"ANA TORRES": "SISTEMAS"})
        estadisticas = procesar_archivo(
            entrada,
            maestro,
            modo=cfg.MODO_NORMAL,
            permitir_pendientes=True,
            registrar_auditoria=False,
        )
        self.assertTrue(estadisticas.generacion_parcial)
        import json

        manifiesto = json.loads(
            Path(estadisticas.archivo_manifiesto).read_text(encoding="utf-8")
        )
        self.assertTrue(manifiesto["generacion_parcial"])
        self.assertEqual(
            len(manifiesto["usuarios_pendientes"]), estadisticas.usuarios_pendientes
        )

    def test_respaldo_antes_de_sobrescribir(self) -> None:
        entrada, maestro = self.preparar(
            {"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        primera = procesar_archivo(
            entrada, maestro, modo=cfg.MODO_NORMAL, permitir_pendientes=False
        )
        procesar_archivo(
            entrada, maestro, modo=cfg.MODO_NORMAL, permitir_pendientes=False
        )
        copias = list(Path(cfg.BACKUPS_DIR).glob(f"{Path(primera.archivo_salida).stem}_*.xlsx"))
        self.assertGreaterEqual(len(copias), 1)

    def test_copia_de_revision_es_estable_y_se_reemplaza(self) -> None:
        entrada, maestro = self.preparar(
            {"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        primera = procesar_archivo(
            entrada, maestro, modo=cfg.MODO_NORMAL, permitir_pendientes=False
        )
        copia = Path(primera.ruta_copia_revision)
        self.assertTrue(copia.is_file())
        self.assertTrue(copia.name.endswith("_procesado_revision.xlsx"))
        self.assertTrue(
            copia.with_name(f"{copia.stem}{cfg.SUFIJO_MANIFIESTO}").is_file()
        )
        segunda = procesar_archivo(
            entrada, maestro, modo=cfg.MODO_NORMAL, permitir_pendientes=False
        )
        self.assertEqual(Path(segunda.ruta_copia_revision), copia)
        antiguas = list(copia.parent.glob("*_revision_2*.xlsx"))
        self.assertEqual(antiguas, [])

    def test_rotacion_conserva_diez_respaldos(self) -> None:
        destino = cfg.OUTPUT_DIR / "logico_procesado.xlsx"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"x")
        for indice in range(14):
            copia = cfg.BACKUPS_DIR / f"logico_procesado_2026010{indice}_120000.xlsx"
            copia.write_bytes(b"x")
            os.utime(copia, (1000 + indice, 1000 + indice))
        rotar_respaldos(destino, 10)
        self.assertEqual(
            len(list(cfg.BACKUPS_DIR.glob("logico_procesado_*.xlsx"))), 10
        )


# ---------------------------------------------------------------------------
# Alertas de sincronización
# ---------------------------------------------------------------------------
class PruebasAlertasSincronizacion(BaseTemporal):
    def test_cambio_de_departamento_genera_alerta(self) -> None:
        crear_maestro({"ANA TORRES": "CONTABILIDAD"})
        alertas = _alertas_contra_maestro(
            [
                {
                    "empresa": "INKA",
                    "empleado": "E-1",
                    "usuario": "ANA TORRES",
                    "anterior": "SISTEMAS",
                    "nuevo": "SISTEMAS",
                }
            ]
        )
        self.assertEqual(len(alertas), 1)
        self.assertEqual(alertas[0]["area_local"], "CONTABILIDAD")
        self.assertEqual(alertas[0]["departamento_corporativo"], "SISTEMAS")

    def test_sin_cambios_no_genera_alerta(self) -> None:
        self.assertEqual(_alertas_contra_maestro([]), [])


# ---------------------------------------------------------------------------
# Ventana de validación: contrato de generación
# ---------------------------------------------------------------------------
class PruebasVentanaGeneracion(unittest.TestCase):
    def _ventana(self, resultado):
        from ventana_validacion import VentanaValidacion

        ventana = VentanaValidacion.__new__(VentanaValidacion)
        ventana._generando = False
        ventana._var_resumen = SimpleNamespace(set=mock.MagicMock())
        ventana.revision = mock.MagicMock(cantidad_pendientes=0)
        ventana.revision.generacion = {}
        ventana._modo = lambda: cfg.MODO_NORMAL
        ventana._al_generar = mock.MagicMock(return_value=resultado)
        ventana.destroy = mock.MagicMock()
        return ventana

    def test_generacion_asincrona_mantiene_ventana_abierta(self) -> None:
        ventana = self._ventana(True)
        ventana._generar(False)
        self.assertTrue(ventana._generando)
        ventana.destroy.assert_not_called()
        ventana.generacion_terminada(True)
        self.assertFalse(ventana._generando)
        ventana.destroy.assert_called_once()

    def test_generacion_no_iniciada_permite_reintentar(self) -> None:
        ventana = self._ventana(False)
        ventana._generar(False)
        self.assertFalse(ventana._generando)
        ventana.destroy.assert_not_called()
        ventana._var_resumen.set.assert_called()

    def test_generacion_sincrona_cierra_la_ventana(self) -> None:
        ventana = self._ventana(None)
        ventana._generar(True)
        self.assertFalse(ventana._generando)
        ventana.destroy.assert_called_once()

    def test_fallo_informa_y_conserva_revision(self) -> None:
        ventana = self._ventana(True)
        ventana._generar(False)
        ventana.generacion_terminada(False)
        self.assertFalse(ventana._generando)
        ventana.destroy.assert_not_called()
        ventana._var_resumen.set.assert_called_with(
            "La generación falló. La revisión sigue disponible para reintentar."
        )


# ---------------------------------------------------------------------------
# Ventana de configuración: pestaña correcta y resumen de caché
# ---------------------------------------------------------------------------
class PruebasConfiguracionSQL(unittest.TestCase):
    def test_info_cache_cuenta_empresas_activas(self) -> None:
        from interfaz import VentanaConfiguracion

        ventana = VentanaConfiguracion.__new__(VentanaConfiguracion)
        ventana._sql = SimpleNamespace(
            empresas_activas=mock.MagicMock(return_value=["CALCA", "INKA"])
        )
        ventana._var_info_sync = SimpleNamespace(set=mock.MagicMock())
        with mock.patch("interfaz.resumen_sincronizacion", return_value={}):
            ventana._actualizar_info_cache()
        texto = ventana._var_info_sync.set.call_args[0][0]
        self.assertIn("Empresas configuradas: 2", texto)

    def test_ventana_sql_abre_en_la_pestana_de_conexion(self) -> None:
        import tkinter as tk

        from interfaz import VentanaConfiguracion

        try:
            raiz = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover - sin entorno gráfico
            self.skipTest(f"Tk no disponible: {exc}")
        raiz.withdraw()
        try:
            ventana = VentanaConfiguracion(raiz, seccion="sql")
            ventana.withdraw()
            try:
                actual = ventana._pestanas.select()
                self.assertEqual(
                    ventana._pestanas.tab(actual, "text"), "Conexión SQL Server"
                )
            finally:
                ventana.destroy()
        finally:
            raiz.destroy()


# ---------------------------------------------------------------------------
# Consola (CLI)
# ---------------------------------------------------------------------------
class PruebasCLI(BaseTemporal):
    def _args(self, *extra: str):
        parser = construir_parser()
        return parser.parse_args([*extra])

    def _ejecutar(self, args):
        salida = io.StringIO()
        error = io.StringIO()
        with mock.patch("core.procesador._sql_configurada", return_value=None):
            with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(error):
                codigo = ejecutar_consola(args)
        return codigo, salida.getvalue(), error.getvalue()

    def test_parser_usa_modo_normal_por_defecto(self) -> None:
        args = self._args()
        self.assertEqual(args.modo, cfg.MODO_NORMAL)
        self.assertFalse(args.permitir_pendientes)

    def test_cli_bloquea_generacion_con_pendientes(self) -> None:
        entrada, _ = self.preparar({"ANA TORRES": "SISTEMAS"})
        codigo, _, error = self._ejecutar(self._args(str(entrada)))
        self.assertEqual(codigo, 2)
        self.assertIn("GENERACIÓN CANCELADA", error)
        self.assertIn("--permitir-pendientes", error)
        self.assertFalse((cfg.OUTPUT_DIR / "consulta_procesado.xlsx").exists())

    def test_cli_permite_pendientes_con_bandera(self) -> None:
        entrada, _ = self.preparar({"ANA TORRES": "SISTEMAS"})
        codigo, salida, _ = self._ejecutar(
            self._args(str(entrada), "--permitir-pendientes")
        )
        self.assertEqual(codigo, 0)
        self.assertIn("Generación parcial : Sí", salida)
        self.assertTrue((cfg.OUTPUT_DIR / "consulta_procesado.xlsx").is_file())

    def test_cli_modo_detalle_genera_ambos_archivos(self) -> None:
        entrada, _ = self.preparar(
            {"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        codigo, salida, _ = self._ejecutar(
            self._args(str(entrada), "--modo", cfg.MODO_DETALLE)
        )
        self.assertEqual(codigo, 0)
        self.assertIn("Excel a detalle", salida)
        self.assertTrue((cfg.OUTPUT_DIR / "consulta_procesado.xlsx").is_file())
        self.assertTrue(
            (cfg.OUTPUT_DIR / "consulta_procesado_detalle.xlsx").is_file()
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
