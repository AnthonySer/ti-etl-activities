"""Pruebas reales del Sistema Integral de Tickets TI - INKACROPS.

Todas las pruebas trabajan en carpetas temporales: nunca escriben en los
archivos reales del proyecto. Cubren esquema, ETL, procesamiento, reimportación,
consolidación, reportes, auditoría, bloqueos, estado, asignaciones e interfaz.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import pandas as pd
from openpyxl import load_workbook

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import configuracion as cfg  # noqa: E402
from core import asignaciones as svc_asignaciones  # noqa: E402
from core import auditoria  # noqa: E402
from core import estado as svc_estado  # noqa: E402
from core.archivos import ErrorArchivo, ErrorEstructura, leer_excel  # noqa: E402
from core.bloqueo import BloqueoSistema, ErrorBloqueo, esta_bloqueado  # noqa: E402
from core.consolidacion_tickets import consolidar_procesado  # noqa: E402
from core.esquema import (  # noqa: E402
    ALIAS_OFICIALES,
    C_ACTIVIDAD,
    C_AREA,
    C_ATENCION,
    C_DETALLE,
    C_FECHA,
    C_HORAS,
    C_PROYECTO,
    C_REQUERIMIENTO,
    C_RESPONSABLE,
    C_SOLICITANTE,
    C_SUBTIPO,
    COLUMNAS_OFICIALES,
    canonizar_encabezado,
    huella_fila,
    mapear_oficial,
    validar_estructura_exacta,
)
from core.etl import extraer, parsear_fecha, parsear_horas, texto_limpio  # noqa: E402
from core.maestro import Maestro  # noqa: E402
from core.maestro_tickets import MaestroTickets  # noqa: E402
from core.procesador import (  # noqa: E402
    ESTADO_DUPLICADO,
    ESTADO_ERROR,
    ESTADO_PENDIENTE,
    ESTADO_VALIDO,
    procesar_archivo,
)
from core.reimportacion import (  # noqa: E402
    TIPO_AMBIGUO,
    TIPO_AUSENTE,
    TIPO_DUPLICADO,
    TIPO_MODIFICADO,
    TIPO_NUEVO,
    analizar_reimportacion,
    aplicar_reimportacion,
    leer_corregido,
)
from core.reportes import (  # noqa: E402
    calcular_indicadores,
    distribuciones,
    generar_reporte,
    recurrencia_detalle,
)

ENCABEZADOS_ORIGEN = [
    "DETALLE",
    "FECHA",
    "USUARIO",
    "AREA_USUARIO",
    "ATENCION",
    "REQUERIMIENTO",
    "RESPONSABLE",
    "SUBT_TIPO",
    "PROYECTO",
    "HORAS",
    "ACTIVIDAD",
]


# ---------------------------------------------------------------------------
# Utilidades de prueba
# ---------------------------------------------------------------------------
def escribir_tabla(
    df: pd.DataFrame,
    ruta: Path,
    hoja: str = "DATOS",
    startrow: int = 0,
) -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(ruta, engine="openpyxl") as libro:
        df.to_excel(libro, sheet_name=hoja, index=False, startrow=startrow)
    return ruta


def datos_base(n: int = 3) -> pd.DataFrame:
    filas = [
        {
            "DETALLE": "SOPORTE IMPRESORA",
            "FECHA": date(2026, 9, 1),
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
            "FECHA": date(2026, 9, 2),
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
        {
            "DETALLE": "RED PLANTA ALMACEN",
            "FECHA": date(2026, 9, 3),
            "USUARIO": "ANA TORRES",
            "AREA_USUARIO": None,
            "ATENCION": "Mesa de Ayuda",
            "REQUERIMIENTO": "Incidencias",
            "RESPONSABLE": "MARCO CCANTO",
            "SUBTIPO": "Redes",
            "PROYECTO": "PLANTA",
            "HORAS": 3.25,
            "ACTIVIDAD": "Incidente",
        },
    ]
    if n <= len(filas):
        return pd.DataFrame(filas[:n])
    extra = filas[-1].copy()
    for indice in range(len(filas), n):
        nueva = extra.copy()
        nueva["DETALLE"] = f"CONSULTA ADICIONAL {indice + 1}"
        nueva["FECHA"] = date(2026, 9, 4 + (indice % 20))
        nueva["USUARIO"] = f"USUARIO {indice + 1}"
        nueva["HORAS"] = 1.0
        filas.append(nueva)
    return pd.DataFrame(filas)


def crear_maestro_areas(areas: dict[str, str]) -> Maestro:
    df = pd.DataFrame(
        [{"USUARIO": usuario, "AREA_USUARIO": area} for usuario, area in areas.items()],
        columns=["USUARIO", "AREA_USUARIO"],
    )
    maestro = Maestro(df)
    if areas:
        maestro.guardar(cfg.MAESTRO_XLSX)
    return maestro


def modificar_celda(ruta: Path, fila: int, columna: str, valor) -> None:
    libro = load_workbook(ruta)
    hoja = libro[cfg.HOJA_DETALLE]
    indice = None
    for posicion, celda in enumerate(hoja[1], start=1):
        if str(celda.value).strip() == columna:
            indice = posicion
            break
    if indice is None:
        raise AssertionError(f"No existe la columna {columna}")
    hoja.cell(row=fila, column=indice, value=valor)
    libro.save(ruta)
    libro.close()


def borrar_fila(ruta: Path, fila: int) -> None:
    libro = load_workbook(ruta)
    hoja = libro[cfg.HOJA_DETALLE]
    hoja.delete_rows(fila)
    libro.save(ruta)
    libro.close()


def agregar_fila(ruta: Path, valores: list) -> None:
    libro = load_workbook(ruta)
    hoja = libro[cfg.HOJA_DETALLE]
    hoja.append(valores)
    libro.save(ruta)
    libro.close()


def copia_corregida(ruta: Path, sufijo: str = "_corregido") -> Path:
    destino = ruta.with_name(f"{ruta.stem}{sufijo}{ruta.suffix}")
    shutil.copy2(ruta, destino)
    return destino


def hash_archivo(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


class BaseTemporal(unittest.TestCase):
    """Aísla todas las rutas configurables en una carpeta temporal."""

    CAMPOS_RUTA = (
        "INPUT_DIR",
        "OUTPUT_DIR",
        "DATA_DIR",
        "BACKUPS_DIR",
        "LOGS_DIR",
        "REPORTES_DIR",
        "MAESTRO_XLSX",
        "MAESTRO_CSV",
        "MAESTRO_TICKETS_XLSX",
        "MAESTRO_TICKETS_CSV",
        "HISTORIAL_CSV",
        "HISTORIAL_TICKETS_CSV",
        "AUDITORIA_CSV",
        "ESTADO_UI_JSON",
        "LOCK_SISTEMA",
    )

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory(prefix="tickets_pruebas_")
        self.raiz = Path(self._temporal.name)
        self._originales = {campo: getattr(cfg, campo) for campo in self.CAMPOS_RUTA}

        cfg.INPUT_DIR = self.raiz / "input"
        cfg.OUTPUT_DIR = self.raiz / "output"
        cfg.DATA_DIR = self.raiz / "data"
        cfg.BACKUPS_DIR = cfg.DATA_DIR / "backups"
        cfg.LOGS_DIR = self.raiz / "logs"
        cfg.REPORTES_DIR = self.raiz / "reportes"
        cfg.MAESTRO_XLSX = cfg.DATA_DIR / "maestro_areas.xlsx"
        cfg.MAESTRO_CSV = cfg.DATA_DIR / "maestro_areas.csv"
        cfg.MAESTRO_TICKETS_XLSX = cfg.DATA_DIR / "maestro_tickets.xlsx"
        cfg.MAESTRO_TICKETS_CSV = cfg.DATA_DIR / "maestro_tickets.csv"
        cfg.HISTORIAL_CSV = cfg.DATA_DIR / "maestro_historial.csv"
        cfg.HISTORIAL_TICKETS_CSV = cfg.DATA_DIR / "maestro_tickets_historial.csv"
        cfg.AUDITORIA_CSV = cfg.DATA_DIR / "auditoria_ejecuciones.csv"
        cfg.ESTADO_UI_JSON = cfg.DATA_DIR / "estado_ui.json"
        cfg.LOCK_SISTEMA = cfg.DATA_DIR / ".sistema.lock"
        cfg.asegurar_directorios()

    def tearDown(self) -> None:
        for campo, valor in self._originales.items():
            setattr(cfg, campo, valor)
        self._temporal.cleanup()

    def procesar(
        self,
        df: pd.DataFrame | None = None,
        *,
        nombre: str = "consulta_prueba.xlsx",
        areas: dict[str, str] | None = None,
        maestro: Maestro | None = None,
        **kwargs,
    ):
        datos = datos_base() if df is None else df
        entrada = escribir_tabla(datos, self.raiz / "input" / nombre)
        if maestro is None and areas is not None:
            maestro = crear_maestro_areas(areas)
        return procesar_archivo(
            entrada,
            maestro,
            registrar_auditoria=kwargs.pop("registrar_auditoria", False),
            **kwargs,
        )


# ---------------------------------------------------------------------------
# Esquema
# ---------------------------------------------------------------------------
class PruebasEsquema(unittest.TestCase):
    def test_alias_reconocidos(self) -> None:
        for oficial, alias in ALIAS_OFICIALES.items():
            for nombre in alias:
                self.assertEqual(canonizar_encabezado(nombre), oficial, nombre)

    def test_alias_reales_del_proyecto(self) -> None:
        casos = {
            "FECHA_REGISRO": C_FECHA,
            "USUARIO": C_SOLICITANTE,
            "AREA_USUARIO": C_AREA,
            "TIPO_ATENCION": C_ATENCION,
            "HORAS_TOTAL": C_HORAS,
            "GRUPO 1": C_ACTIVIDAD,
            "SUBT_TIPO": C_SUBTIPO,
        }
        for nombre, oficial in casos.items():
            self.assertEqual(canonizar_encabezado(nombre), oficial, nombre)

    def test_mapear_oficial_orden_y_extras(self) -> None:
        df = datos_base(2).rename(columns={"SUBTIPO": "SUBT_TIPO"})
        df["EMPRESA"] = ["ALICORP", "ALICORP"]
        detalle, extras, mapeo = mapear_oficial(df)
        self.assertEqual(list(detalle.columns), list(COLUMNAS_OFICIALES))
        self.assertIsNotNone(extras)
        self.assertIn("EMPRESA", extras.columns)
        self.assertNotIn("EMPRESA", detalle.columns)
        self.assertEqual(mapeo["USUARIO"], C_SOLICITANTE)
        self.assertEqual(mapeo["SUBT_TIPO"], C_SUBTIPO)
        self.assertEqual(detalle.loc[0, C_SUBTIPO], "Software oficina")

    def test_faltan_columnas(self) -> None:
        df = datos_base(1).drop(columns=["HORAS"])
        with self.assertRaises(ErrorEstructura):
            mapear_oficial(df)

    def test_columnas_duplicadas_con_datos(self) -> None:
        df = datos_base(1)
        df["SOLICITANTE"] = "OTRO USUARIO"
        with self.assertRaises(ErrorEstructura):
            mapear_oficial(df)

    def test_columna_duplicada_vacia_se_ignora(self) -> None:
        df = datos_base(1)
        df["SOLICITANTE"] = None
        detalle, _, _ = mapear_oficial(df)
        self.assertEqual(detalle.loc[0, C_SOLICITANTE], "ANA TORRES")

    def test_validar_estructura_exacta(self) -> None:
        validar_estructura_exacta(COLUMNAS_OFICIALES)
        with self.assertRaises(ErrorEstructura):
            validar_estructura_exacta(list(reversed(COLUMNAS_OFICIALES)))
        with self.assertRaises(ErrorEstructura):
            validar_estructura_exacta(list(COLUMNAS_OFICIALES) + ["Extra"])

    def test_huella_estable_entre_tipos(self) -> None:
        a = {"Fecha": date(2026, 9, 1), "Horas": 2.5}
        b = {"Fecha": "2026-09-01", "Horas": "2,5"}
        self.assertEqual(huella_fila(a), huella_fila(b))
        c = {"Fecha": "2026-09-02", "Horas": 2.5}
        self.assertNotEqual(huella_fila(a), huella_fila(c))


# ---------------------------------------------------------------------------
# ETL
# ---------------------------------------------------------------------------
class PruebasETL(BaseTemporal):
    def test_extrae_y_normaliza(self) -> None:
        ruta = escribir_tabla(datos_base(), self.raiz / "entrada.xlsx")
        resultado = extraer(ruta)
        self.assertEqual(len(resultado.df), 3)
        self.assertEqual(resultado.periodo, "2026-09-01/2026-09-03")
        self.assertEqual(
            list(resultado.df.columns[:11]), list(COLUMNAS_OFICIALES)
        )
        self.assertEqual(resultado.df.loc[0, C_SOLICITANTE], "ANA TORRES")
        self.assertAlmostEqual(float(resultado.df[C_HORAS].sum()), 6.75)

    def test_columnas_desordenadas_y_hoja_con_titulo(self) -> None:
        df = datos_base(2)
        df = df[list(reversed(list(df.columns)))]
        ruta = self.raiz / "desordenado.xlsx"
        with pd.ExcelWriter(ruta, engine="openpyxl") as libro:
            pd.DataFrame([["REPORTE DE TICKETS"], ["Generado por TI"]]).to_excel(
                libro, sheet_name="consulta_desordenado", header=False, index=False
            )
            df.to_excel(
                libro,
                sheet_name="consulta_desordenado",
                index=False,
                startrow=2,
            )
            pd.DataFrame({"X": [1]}).to_excel(
                libro, sheet_name="RESUMEN", index=False
            )
        resultado = extraer(ruta)
        self.assertEqual(resultado.hoja, "consulta_desordenado")
        self.assertEqual(resultado.indice_encabezado, 2)
        self.assertEqual(len(resultado.df), 2)
        self.assertEqual(list(resultado.df.columns[:11]), list(COLUMNAS_OFICIALES))

    def test_fechas_ambiguas_respetan_configuracion(self) -> None:
        valor = "03/04/2026"
        dd, aviso_dd = parsear_fecha(valor, "DD/MM/YYYY")
        mm, aviso_mm = parsear_fecha(valor, "MM/DD/YYYY")
        self.assertEqual(dd, date(2026, 4, 3))
        self.assertEqual(mm, date(2026, 3, 4))
        self.assertIsNotNone(aviso_dd)
        self.assertIsNotNone(aviso_mm)

    def test_fecha_invalida_no_se_convierte_en_nulo(self) -> None:
        valor, problema = parsear_fecha("31/02/2026")
        self.assertEqual(valor, "31/02/2026")
        self.assertIn("inválida", problema.lower())

    def test_horas_negativas_y_no_numericas(self) -> None:
        self.assertEqual(parsear_horas(-2)[0], -2.0)
        self.assertIn("negativas", parsear_horas(-2)[1].lower())
        self.assertEqual(parsear_horas("dos horas")[0], "dos horas")
        self.assertIsNotNone(parsear_horas("dos horas")[1])
        self.assertIsNone(parsear_horas("NULL")[0])

    def test_valores_nulos(self) -> None:
        for valor in ("", "NULL", "N/A", "  ", None, float("nan")):
            self.assertIsNone(texto_limpio(valor))

    def test_filas_vacias_se_omiten(self) -> None:
        df = datos_base(1)
        vacia = pd.DataFrame([[None] * len(df.columns)], columns=df.columns)
        df = pd.concat([df, vacia, datos_base(1)], ignore_index=True)
        ruta = escribir_tabla(df, self.raiz / "vacios.xlsx")
        resultado = extraer(ruta)
        self.assertEqual(len(resultado.df), 2)
        self.assertEqual(resultado.filas_vacias_omitidas, 1)

    def test_archivo_sin_estructura(self) -> None:
        ruta = escribir_tabla(
            pd.DataFrame({"A": [1], "B": [2]}), self.raiz / "invalido.xlsx"
        )
        with self.assertRaises(ErrorEstructura):
            extraer(ruta)

    def test_archivo_inexistente_y_extension(self) -> None:
        with self.assertRaises(ErrorArchivo):
            extraer(self.raiz / "no_existe.xlsx")
        ruta = self.raiz / "archivo.csv"
        ruta.write_text("DETALLE,FECHA\n", encoding="utf-8")
        with self.assertRaises(ErrorArchivo):
            extraer(ruta)

    def test_septiembre_referencia(self) -> None:
        ruta = BASE_DIR / "input" / cfg.ARCHIVO_SEPTIEMBRE
        if not ruta.is_file():
            ruta = cfg.PROYECTO_RAIZ / "2026" / "EXCEL" / cfg.ARCHIVO_SEPTIEMBRE
        if not ruta.is_file():
            self.skipTest("No está disponible el Excel de septiembre.")
        resultado = extraer(ruta)
        self.assertEqual(len(resultado.df), 179)
        self.assertAlmostEqual(float(resultado.df[C_HORAS].sum()), 426.75)
        self.assertEqual(
            resultado.df[C_ATENCION].value_counts().to_dict(),
            {"Mesa de Ayuda": 110, "Gestion": 41, "Proyectos": 28},
        )
        self.assertEqual(resultado.observaciones, [])


# ---------------------------------------------------------------------------
# Procesamiento
# ---------------------------------------------------------------------------
class PruebasProcesamiento(BaseTemporal):
    def test_genera_11_columnas_y_hojas_auxiliares(self) -> None:
        datos = datos_base()
        datos["EMPRESA"] = "ALICORP"
        estadisticas = self.procesar(
            datos,
            areas={"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"},
        )
        ruta = Path(estadisticas.archivo_salida)
        self.assertTrue(ruta.is_file())
        libro = load_workbook(ruta, read_only=True)
        try:
            self.assertEqual(
                libro.sheetnames,
                ["Tickets", "Observaciones", "Resumen", "Control", "Origen_Adicional"],
            )
            hoja = libro["Tickets"]
            self.assertEqual(hoja.max_column, 11)
            self.assertEqual(
                next(hoja.iter_rows(min_row=1, max_row=1, values_only=True)),
                COLUMNAS_OFICIALES,
            )
        finally:
            libro.close()

        df = leer_excel(ruta, sheet_name="Tickets")
        self.assertEqual(len(df), 3)
        self.assertFalse(any(str(c).startswith("_") for c in df.columns))
        self.assertEqual(df.loc[0, C_AREA], "SISTEMAS")
        self.assertEqual(estadisticas.registros_validos, 3)
        self.assertEqual(estadisticas.total_horas, 6.75)

    def test_manifiesto_v2(self) -> None:
        estadisticas = self.procesar(
            areas={"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        ruta = Path(estadisticas.archivo_manifiesto or "")
        self.assertTrue(ruta.is_file())
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        self.assertEqual(datos["version"], 2)
        self.assertEqual(len(datos["filas"]), 3)
        ids = [fila["id_tecnico"] for fila in datos["filas"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(
            [fila["estado"] for fila in datos["filas"]],
            [ESTADO_VALIDO, ESTADO_VALIDO, ESTADO_VALIDO],
        )
        self.assertEqual(
            datos["filas"][0]["datos"]["Area"], "SISTEMAS"
        )

    def test_usuario_nuevo_queda_pendiente(self) -> None:
        estadisticas = self.procesar(
            areas={"ANA TORRES": "SISTEMAS"}
        )
        self.assertEqual(estadisticas.registros_pendientes, 1)
        self.assertEqual(estadisticas.usuarios_pendientes, 1)
        df = leer_excel(estadisticas.archivo_salida, sheet_name="Tickets")
        self.assertEqual(df.loc[1, C_AREA], "CONTABILIDAD") if False else None
        self.assertTrue(pd.isna(df.loc[1, C_AREA]))

    def test_duplicado_se_conserva_y_marca(self) -> None:
        df = datos_base(1)
        df = pd.concat([df, df], ignore_index=True)
        estadisticas = self.procesar(
            df, areas={"ANA TORRES": "SISTEMAS"}
        )
        self.assertEqual(estadisticas.registros_totales, 2)
        self.assertEqual(estadisticas.registros_validos, 1)
        self.assertEqual(estadisticas.registros_duplicados, 1)

    def test_errores_y_observaciones(self) -> None:
        df = datos_base(2)
        df["FECHA"] = df["FECHA"].astype(object)
        df["HORAS"] = df["HORAS"].astype(object)
        df.loc[0, "FECHA"] = "31/02/2026"
        df.loc[1, "HORAS"] = "dos"
        estadisticas = self.procesar(
            df, areas={"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        self.assertEqual(estadisticas.registros_error, 1)
        self.assertGreaterEqual(estadisticas.registros_observados, 1)
        self.assertGreaterEqual(estadisticas.observaciones, 2)

    def test_archivo_vacio(self) -> None:
        df = pd.DataFrame(columns=ENCABEZADOS_ORIGEN)
        estadisticas = self.procesar(df, nombre="vacio.xlsx")
        self.assertEqual(estadisticas.registros_totales, 0)
        df_salida = leer_excel(estadisticas.archivo_salida, sheet_name="Tickets")
        self.assertTrue(df_salida.empty)
        self.assertEqual(list(df_salida.columns), list(COLUMNAS_OFICIALES))

    def test_no_sobrescribe_salida(self) -> None:
        primera = self.procesar(
            areas={"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        segunda = self.procesar(
            areas={"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        self.assertNotEqual(primera.archivo_salida, segunda.archivo_salida)
        self.assertTrue(Path(primera.archivo_salida).is_file())
        self.assertTrue(Path(segunda.archivo_salida).is_file())

    def test_original_no_se_modifica(self) -> None:
        entrada = escribir_tabla(datos_base(2), self.raiz / "input" / "original.xlsx")
        antes = hash_archivo(entrada)
        maestro = crear_maestro_areas({"ANA TORRES": "SISTEMAS"})
        procesar_archivo(entrada, maestro, registrar_auditoria=False)
        self.assertEqual(hash_archivo(entrada), antes)

    def test_no_permite_salida_igual_a_entrada(self) -> None:
        entrada = escribir_tabla(datos_base(1), self.raiz / "input" / "mismo.xlsx")
        with self.assertRaises(ErrorArchivo):
            procesar_archivo(
                entrada,
                Maestro(),
                ruta_salida=entrada,
                sobrescribir=True,
                registrar_auditoria=False,
            )


# ---------------------------------------------------------------------------
# Reimportación
# ---------------------------------------------------------------------------
class PruebasReimportacion(BaseTemporal):
    def _procesar_y_corregir(self):
        estadisticas = self.procesar(
            areas={"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        procesado = Path(estadisticas.archivo_salida)
        corregido = copia_corregida(procesado)
        return procesado, corregido

    def test_sin_cambios(self) -> None:
        _, corregido = self._procesar_y_corregir()
        resultado = analizar_reimportacion(corregido)
        self.assertEqual(resultado.resumen.get(TIPO_MODIFICADO, 0), 0)
        self.assertEqual(resultado.resumen.get("SIN_CAMBIOS", 0), 3)

    def test_modificaciones_detectan_campos(self) -> None:
        _, corregido = self._procesar_y_corregir()
        modificar_celda(corregido, 2, C_DETALLE, "SOPORTE IMPRESORA CORREGIDO")
        modificar_celda(corregido, 3, C_AREA, "SISTEMAS")
        modificar_celda(corregido, 4, C_HORAS, 9.5)
        resultado = analizar_reimportacion(corregido)
        self.assertEqual(resultado.resumen.get(TIPO_MODIFICADO, 0), 3)
        cambios = [c for c in resultado.cambios if c.tipo == TIPO_MODIFICADO]
        self.assertTrue(any(C_DETALLE in c.campos_cambiados for c in cambios))
        self.assertTrue(any(C_AREA in c.campos_cambiados for c in cambios))
        self.assertTrue(any(C_HORAS in c.campos_cambiados for c in cambios))

    def test_registro_nuevo(self) -> None:
        _, corregido = self._procesar_y_corregir()
        agregar_fila(
            corregido,
            [
                "TICKET NUEVO",
                datetime(2026, 9, 10),
                "NUEVO USUARIO",
                "SISTEMAS",
                "Mesa de Ayuda",
                "Soporte",
                "CIRO HUAMANI",
                "Redes",
                "PLANTA",
                2.0,
                "Incidencia",
            ],
        )
        resultado = analizar_reimportacion(corregido)
        self.assertEqual(resultado.resumen.get(TIPO_NUEVO, 0), 1)

    def test_ausente_no_se_elimina(self) -> None:
        procesado, corregido = self._procesar_y_corregir()
        borrar_fila(corregido, 4)
        resultado = analizar_reimportacion(corregido)
        self.assertEqual(resultado.resumen.get(TIPO_AUSENTE, 0), 1)
        self.assertEqual(resultado.resumen.get("SIN_CAMBIOS", 0), 2)

        maestro = Path(cfg.MAESTRO_TICKETS_XLSX)
        consolidar_procesado(procesado, ruta_maestro=maestro, crear_backup=False)
        aplicar_reimportacion(resultado, ruta_maestro=maestro)
        recuperado = MaestroTickets.cargar(maestro)
        self.assertEqual(len(recuperado), 3)

    def test_duplicado(self) -> None:
        procesado, corregido = self._procesar_y_corregir()
        df = leer_excel(procesado, sheet_name="Tickets")
        fila = [df.iloc[0][columna] for columna in COLUMNAS_OFICIALES]
        agregar_fila(corregido, fila)
        resultado = analizar_reimportacion(corregido)
        self.assertEqual(resultado.resumen.get(TIPO_DUPLICADO, 0), 1)

    def test_ambiguo_bloquea_aplicacion(self) -> None:
        df = datos_base(1)
        df = pd.concat([df, df], ignore_index=True)
        df.loc[0, "PROYECTO"] = "PROYECTO A"
        df.loc[1, "PROYECTO"] = "PROYECTO B"
        estadisticas = self.procesar(
            df, nombre="ambiguo.xlsx", areas={"ANA TORRES": "SISTEMAS"}
        )
        corregido = copia_corregida(Path(estadisticas.archivo_salida))
        modificar_celda(corregido, 2, C_PROYECTO, "PROYECTO C")
        modificar_celda(corregido, 3, C_PROYECTO, "PROYECTO D")
        resultado = analizar_reimportacion(corregido)
        self.assertGreaterEqual(resultado.resumen.get(TIPO_AMBIGUO, 0), 1)
        with self.assertRaises(ErrorArchivo):
            aplicar_reimportacion(resultado, ruta_maestro=cfg.MAESTRO_TICKETS_XLSX)

    def test_estructura_corregida_invalida(self) -> None:
        _, corregido = self._procesar_y_corregir()
        libro = load_workbook(corregido)
        libro[cfg.HOJA_DETALLE].delete_cols(11)
        libro.save(corregido)
        libro.close()
        with self.assertRaises(ErrorEstructura):
            leer_corregido(corregido)

    def test_aplicacion_actualiza_y_conserva(self) -> None:
        procesado, corregido = self._procesar_y_corregir()
        maestro = Path(cfg.MAESTRO_TICKETS_XLSX)
        consolidar_procesado(procesado, ruta_maestro=maestro, crear_backup=False)

        modificar_celda(corregido, 2, C_DETALLE, "SOPORTE IMPRESORA CORREGIDO")
        borrar_fila(corregido, 4)
        resultado = analizar_reimportacion(corregido)
        salida = aplicar_reimportacion(resultado, ruta_maestro=maestro)

        self.assertEqual(salida["actualizados"], 1)
        self.assertEqual(salida["total_maestro"], 3)
        recuperado = MaestroTickets.cargar(maestro).a_dataframe()
        self.assertEqual(
            recuperado.loc[0, C_DETALLE], "SOPORTE IMPRESORA CORREGIDO"
        )
        self.assertEqual(len(recuperado), 3)

    def test_reimportacion_repetida_es_idempotente(self) -> None:
        procesado, corregido = self._procesar_y_corregir()
        maestro = Path(cfg.MAESTRO_TICKETS_XLSX)
        consolidar_procesado(procesado, ruta_maestro=maestro, crear_backup=False)
        modificar_celda(corregido, 2, C_DETALLE, "SOPORTE CORREGIDO")
        primero = analizar_reimportacion(corregido)
        salida1 = aplicar_reimportacion(primero, ruta_maestro=maestro)
        segundo = analizar_reimportacion(corregido)
        salida2 = aplicar_reimportacion(segundo, ruta_maestro=maestro)
        self.assertEqual(salida1["nuevos"], 0)
        self.assertEqual(salida2["nuevos"], 0)
        self.assertEqual(salida2["actualizados"], 0)
        self.assertEqual(salida2["sin_cambios"], 3)

    def test_historial_campo_a_campo(self) -> None:
        procesado, corregido = self._procesar_y_corregir()
        maestro = Path(cfg.MAESTRO_TICKETS_XLSX)
        consolidar_procesado(procesado, ruta_maestro=maestro, crear_backup=False)
        modificar_celda(corregido, 2, C_DETALLE, "SOPORTE CORREGIDO")
        aplicar_reimportacion(
            analizar_reimportacion(corregido), ruta_maestro=maestro
        )
        historial = pd.read_csv(cfg.HISTORIAL_TICKETS_CSV, encoding="utf-8-sig")
        self.assertIn(C_DETALLE, set(historial["CAMPO"]))
        self.assertIn("SOPORTE CORREGIDO", set(historial["VALOR_NUEVO"]))


# ---------------------------------------------------------------------------
# Consolidación
# ---------------------------------------------------------------------------
class PruebasConsolidacion(BaseTemporal):
    def test_maestro_inexistente_se_crea(self) -> None:
        estadisticas = self.procesar(
            areas={"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        self.assertFalse(Path(cfg.MAESTRO_TICKETS_XLSX).exists())
        salida = consolidar_procesado(
            estadisticas.archivo_salida, crear_backup=False
        )
        self.assertEqual(salida["nuevos"], 3)
        self.assertEqual(salida["total_maestro"], 3)
        self.assertTrue(Path(cfg.MAESTRO_TICKETS_XLSX).is_file())
        self.assertTrue(Path(cfg.MAESTRO_TICKETS_CSV).is_file())

    def test_consolidacion_idempotente(self) -> None:
        estadisticas = self.procesar(
            areas={"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        consolidar_procesado(estadisticas.archivo_salida, crear_backup=False)
        segunda = consolidar_procesado(
            estadisticas.archivo_salida, crear_backup=False
        )
        self.assertEqual(segunda["nuevos"], 0)
        self.assertEqual(segunda["sin_cambios"], 3)
        self.assertEqual(segunda["total_maestro"], 3)

    def test_conserva_historicos_entre_periodos(self) -> None:
        enero = datos_base(2)
        enero["FECHA"] = [date(2026, 1, 5), date(2026, 1, 6)]
        febrero = datos_base(2)
        febrero["FECHA"] = [date(2026, 2, 5), date(2026, 2, 6)]
        febrero["DETALLE"] = ["TICKET FEBRERO 1", "TICKET FEBRERO 2"]

        primero = self.procesar(
            enero,
            nombre="consulta_enero_2026.xlsx",
            areas={"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"},
        )
        consolidar_procesado(primero.archivo_salida, crear_backup=False)
        segundo = self.procesar(
            febrero,
            nombre="consulta_febrero_2026.xlsx",
            areas={"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"},
        )
        salida = consolidar_procesado(segundo.archivo_salida, crear_backup=False)
        self.assertEqual(salida["total_maestro"], 4)
        maestro = MaestroTickets.cargar().a_dataframe()
        self.assertEqual(len(maestro), 4)

    def test_detalle_y_control_alineados(self) -> None:
        estadisticas = self.procesar(
            areas={"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        consolidar_procesado(estadisticas.archivo_salida, crear_backup=False)
        maestro = MaestroTickets.cargar()
        self.assertEqual(len(maestro), 3)
        self.assertEqual(maestro.validar(), [])
        self.assertEqual(len(maestro.ids), len(set(maestro.ids)))
        self.assertEqual(
            list(maestro.a_dataframe().columns), list(COLUMNAS_OFICIALES)
        )

    def test_crea_backup_y_restaura(self) -> None:
        estadisticas = self.procesar(
            areas={"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        consolidar_procesado(estadisticas.archivo_salida, crear_backup=False)
        segunda = consolidar_procesado(
            estadisticas.archivo_salida, crear_backup=True
        )
        self.assertTrue(segunda["rutas"].respaldos)
        copias = list(Path(cfg.BACKUPS_DIR).glob("maestro_tickets_*.xlsx"))
        self.assertTrue(copias)

        # Modificación directa (fuera del sistema) para comprobar la restauración.
        libro = load_workbook(cfg.MAESTRO_TICKETS_XLSX)
        hoja = libro[cfg.HOJA_MAESTRO]
        hoja.cell(row=2, column=1, value="ALTERADO MANUALMENTE")
        libro.save(cfg.MAESTRO_TICKETS_XLSX)
        libro.close()
        MaestroTickets.restaurar_backup(copias[-1])
        restaurado = MaestroTickets.cargar().a_dataframe()
        self.assertNotEqual(
            restaurado.loc[0, C_DETALLE], "ALTERADO MANUALMENTE"
        )

    def test_error_de_escritura_no_deja_maestro_parcial(self) -> None:
        estadisticas = self.procesar(
            areas={"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        consolidar_procesado(estadisticas.archivo_salida, crear_backup=False)
        antes_xlsx = hash_archivo(Path(cfg.MAESTRO_TICKETS_XLSX))
        antes_csv = hash_archivo(Path(cfg.MAESTRO_TICKETS_CSV))

        maestro = MaestroTickets.cargar()
        df = maestro.a_dataframe()
        df.loc[0, C_DETALLE] = "CAMBIO QUE NO DEBE PERSISTIR"
        nuevo = MaestroTickets(df, maestro.control())

        original_replace = mock.DEFAULT
        llamadas = {"n": 0}

        def replace_falla(origen, destino):
            llamadas["n"] += 1
            if llamadas["n"] == 2:
                raise PermissionError("archivo bloqueado en prueba")
            return original_replace(origen, destino)

        with mock.patch("core.maestro_tickets.os.replace", side_effect=replace_falla):
            # os.replace se usa para el reemplazo final; el primer destino es xlsx.
            with self.assertRaises(ErrorArchivo):
                nuevo.guardar(crear_backup=False)

        self.assertEqual(hash_archivo(Path(cfg.MAESTRO_TICKETS_XLSX)), antes_xlsx)
        self.assertEqual(hash_archivo(Path(cfg.MAESTRO_TICKETS_CSV)), antes_csv)
        recuperado = MaestroTickets.cargar().a_dataframe()
        self.assertNotEqual(
            recuperado.loc[0, C_DETALLE], "CAMBIO QUE NO DEBE PERSISTIR"
        )


# ---------------------------------------------------------------------------
# Reportes
# ---------------------------------------------------------------------------
class PruebasReportes(BaseTemporal):
    def _datos_septiembre(self) -> pd.DataFrame:
        ruta = BASE_DIR / "input" / cfg.ARCHIVO_SEPTIEMBRE
        if not ruta.is_file():
            ruta = cfg.PROYECTO_RAIZ / "2026" / "EXCEL" / cfg.ARCHIVO_SEPTIEMBRE
        if not ruta.is_file():
            self.skipTest("No está disponible el Excel de septiembre.")
        return extraer(ruta).df

    def test_indicadores_septiembre(self) -> None:
        df = self._datos_septiembre()
        indicadores = calcular_indicadores(df)
        self.assertEqual(indicadores["Total de tickets"], 179)
        self.assertAlmostEqual(indicadores["Horas registradas"], 426.75)
        self.assertEqual(indicadores["Promedio de tickets por día"], 8.1)
        self.assertEqual(indicadores["Promedio de horas por día"], 19.4)
        self.assertEqual(indicadores["Responsables"], 5)
        self.assertEqual(indicadores["Capacidad nominal estimada (h)"], 880.0)
        self.assertEqual(indicadores["Imputación de horas (%)"], 48.5)

        atencion = distribuciones(df)["Por atencion"].set_index("Grupo")["Tickets"]
        self.assertEqual(atencion["Mesa de Ayuda"], 110)
        self.assertEqual(atencion["Gestion"], 41)
        self.assertEqual(atencion["Proyectos"], 28)

    def test_reporte_excel_y_markdown(self) -> None:
        df = pd.DataFrame(
            [
                {
                    C_DETALLE: "ERROR IMPRESORA",
                    C_FECHA: date(2026, 9, 1),
                    C_SOLICITANTE: "ANA TORRES",
                    C_AREA: "SISTEMAS",
                    C_ATENCION: "Mesa de Ayuda",
                    C_REQUERIMIENTO: "Soporte",
                    C_RESPONSABLE: "CIRO",
                    C_SUBTIPO: "Hardware",
                    C_PROYECTO: "PLANTA",
                    C_HORAS: 2.0,
                    C_ACTIVIDAD: "Incidencia",
                },
                {
                    C_DETALLE: "DESARROLLO PORTAL",
                    C_FECHA: date(2026, 9, 2),
                    C_SOLICITANTE: "LUIS PEREZ",
                    C_AREA: "CONTABILIDAD",
                    C_ATENCION: "Gestion",
                    C_REQUERIMIENTO: "Portal",
                    C_RESPONSABLE: "PAUL",
                    C_SUBTIPO: "Aplicacion",
                    C_PROYECTO: "PORTAL",
                    C_HORAS: 3.0,
                    C_ACTIVIDAD: "Solicitud",
                },
                {
                    C_DETALLE: "AVANCE PROYECTO CRM",
                    C_FECHA: date(2026, 9, 3),
                    C_SOLICITANTE: "PEDRO SOTO",
                    C_AREA: "SISTEMAS",
                    C_ATENCION: "Proyectos",
                    C_REQUERIMIENTO: "CRM",
                    C_RESPONSABLE: "MARIO",
                    C_SUBTIPO: "Software",
                    C_PROYECTO: "CRM",
                    C_HORAS: 5.0,
                    C_ACTIVIDAD: "Proyecto",
                },
                {
                    C_DETALLE: "CONSULTA RENDIMIENTO",
                    C_FECHA: date(2026, 9, 4),
                    C_SOLICITANTE: "MARIA LOPEZ",
                    C_AREA: "TI",
                    C_ATENCION: "Mesa de Ayuda",
                    C_REQUERIMIENTO: "Consulta",
                    C_RESPONSABLE: "ANA",
                    C_SUBTIPO: "Soporte",
                    C_PROYECTO: None,
                    C_HORAS: 1.5,
                    C_ACTIVIDAD: "Incidencia",
                },
            ]
        )
        salida = self.raiz / "reportes" / "reporte.xlsx"
        resultado = generar_reporte(df, ruta_salida=salida)
        self.assertTrue(Path(resultado["xlsx"]).is_file())
        self.assertTrue(Path(resultado["markdown"]).is_file())
        self.assertTrue(
            Path(resultado["markdown"]).read_text(encoding="utf-8").startswith("#")
        )
        libro = load_workbook(resultado["xlsx"])
        try:
            self.assertEqual(
                libro.sheetnames,
                ["RESUMEN", "PROYECTOS", "ACTIVIDAD", "AREA", "DETALLE"],
            )
            self.assertEqual(
                next(libro["RESUMEN"].iter_rows(min_row=1, max_row=1, values_only=True)),
                ("TIPO DE ATENCION", "TIEMPO(H)", "%"),
            )
            self.assertEqual(
                [fila[0] for fila in libro["RESUMEN"].iter_rows(min_row=2, max_row=4, values_only=True)],
                ["Incidencia", "Proyecto", "Solicitud"],
            )
            self.assertEqual(
                next(libro["DETALLE"].iter_rows(min_row=1, max_row=1, values_only=True)),
                ("TIPO DE ATENCION", "DETALLE", "FECHA", "USUARIO", "AREA_USUARIO", "HORAS"),
            )
            self.assertEqual(libro["DETALLE"].max_column, 6)
            self.assertEqual(len(libro["DETALLE"]._charts), 0)
            for nombre in ("RESUMEN", "PROYECTOS", "ACTIVIDAD", "AREA"):
                self.assertEqual(len(libro[nombre]._charts), 1)
                self.assertIsNone(libro[nombre]._charts[0].title)
        finally:
            libro.close()

    def test_recurrencia_solo_detalle_identico(self) -> None:
        filas = []
        for indice in range(3):
            filas.append(
                {
                    C_DETALLE: "NO ENCIENDE EQUIPO",
                    C_FECHA: date(2026, 9, 1 + indice),
                    C_SOLICITANTE: "ANA",
                    C_AREA: "SISTEMAS",
                    C_ATENCION: "Mesa de Ayuda",
                    C_REQUERIMIENTO: "Soporte",
                    C_RESPONSABLE: "CIRO",
                    C_SUBTIPO: "Hardware",
                    C_PROYECTO: "PLANTA",
                    C_HORAS: 1.0,
                    C_ACTIVIDAD: "Incidencia",
                }
            )
        filas.append(
            {
                **filas[0],
                C_DETALLE: "NO ENCIENDE EL EQUIPO DE CONTABILIDAD",
            }
        )
        df = pd.DataFrame(filas)
        recurrencia = recurrencia_detalle(df, minimo=3)
        self.assertEqual(len(recurrencia), 1)
        self.assertEqual(int(recurrencia.loc[0, "Tickets"]), 3)
        self.assertIn("NO ENCIENDE EQUIPO", recurrencia.loc[0, "Detalle recurrente"])

    def test_nota_de_limitaciones(self) -> None:
        df = self._datos_septiembre()
        indicadores = calcular_indicadores(df)
        self.assertIn("SLA", indicadores["Nota"])
        self.assertIn("esfuerzo declarado", indicadores["Nota"])


# ---------------------------------------------------------------------------
# Auditoría, estado, bloqueo y asignaciones
# ---------------------------------------------------------------------------
class PruebasServicios(BaseTemporal):
    def test_auditoria_registra_y_deduplica(self) -> None:
        auditoria.registrar_evento(
            "PROCESAMIENTO",
            archivo_original="entrada.xlsx",
            archivo_salida="salida.xlsx",
            id_ejecucion="EJEC-1",
        )
        auditoria.registrar_evento(
            "PROCESAMIENTO",
            archivo_original="entrada.xlsx",
            archivo_salida="salida.xlsx",
            id_ejecucion="EJEC-1",
        )
        historial = auditoria.cargar_auditoria()
        self.assertEqual(len(historial), 1)
        self.assertEqual(historial.iloc[0]["TIPO"], "PROCESAMIENTO")

    def test_estado_persistente(self) -> None:
        svc_estado.guardar_estado(
            svc_estado.EstadoUI(total_registros=10, registros_validos=8)
        )
        recuperado = svc_estado.cargar_estado()
        self.assertEqual(recuperado.total_registros, 10)
        self.assertEqual(recuperado.registros_validos, 8)
        self.assertTrue(recuperado.actualizado)

    def test_bloqueo_exclusivo_y_liberacion(self) -> None:
        ruta = self.raiz / "prueba.lock"
        with BloqueoSistema(ruta, "prueba"):
            self.assertTrue(esta_bloqueado(ruta))
            with self.assertRaises(ErrorBloqueo):
                BloqueoSistema(ruta, "otra").tomar()
        self.assertFalse(esta_bloqueado(ruta))
        self.assertFalse(ruta.exists())

    def test_asignaciones_persisten_y_sugieren(self) -> None:
        maestro = crear_maestro_areas({"ANA TORRES": "SISTEMAS"})
        resultado = svc_asignaciones.asignar_una(
            maestro, "LUIS PEREZ", "CONTABILIDAD"
        )
        self.assertEqual(resultado.registradas, 1)
        self.assertTrue(Path(cfg.MAESTRO_XLSX).is_file())
        recargado = Maestro.cargar()
        self.assertEqual(recargado.buscar("luis perez"), "CONTABILIDAD")

        actualizado = svc_asignaciones.asignar_una(
            maestro, "LUIS PEREZ", "TESORERIA"
        )
        self.assertEqual(actualizado.actualizadas, 1)
        self.assertEqual(maestro.buscar("LUIS PEREZ"), "TESORERIA")

        sugerencias = svc_asignaciones.sugerencias_usuario("ANA TORRE", maestro)
        self.assertTrue(sugerencias)
        self.assertEqual(sugerencias[0]["usuario"], "ANA TORRES")

    def test_asignacion_rechaza_vacios(self) -> None:
        maestro = crear_maestro_areas({"ANA TORRES": "SISTEMAS"})
        with self.assertRaises(ErrorEstructura):
            svc_asignaciones.asignar_una(maestro, "", "SISTEMAS")
        with self.assertRaises(ErrorEstructura):
            svc_asignaciones.asignar_una(maestro, "ANA TORRES", "")

    def test_pendientes_desde_manifiesto(self) -> None:
        estadisticas = self.procesar(areas={"ANA TORRES": "SISTEMAS"})
        pendientes = svc_asignaciones.usuarios_pendientes(
            estadisticas.archivo_manifiesto, Maestro.cargar()
        )
        self.assertEqual(len(pendientes), 1)
        self.assertEqual(pendientes[0]["usuario"], "LUIS PEREZ")
        self.assertEqual(pendientes[0]["filas"], 1)
        self.assertTrue(pendientes[0]["sugerencias"] is not None)

    def test_auditoria_de_procesamiento(self) -> None:
        self.procesar(
            areas={"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"},
            registrar_auditoria=True,
        )
        historial = auditoria.cargar_auditoria()
        self.assertEqual(len(historial), 1)
        self.assertEqual(historial.iloc[0]["TIPO"], "PROCESAMIENTO")
        self.assertEqual(int(historial.iloc[0]["LEIDOS"]), 3)

    def test_auditor_sistema_sin_fallos(self) -> None:
        estadisticas = self.procesar(
            areas={"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        consolidar_procesado(estadisticas.archivo_salida, crear_backup=False)
        import auditar_sistema

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            codigo = auditar_sistema.main(
                [
                    "--maestro",
                    str(cfg.MAESTRO_XLSX),
                    "--maestro-tickets",
                    str(cfg.MAESTRO_TICKETS_XLSX),
                    "--procesado",
                    estadisticas.archivo_salida,
                    "--auditoria-csv",
                    str(cfg.AUDITORIA_CSV),
                    "--historial",
                    str(cfg.HISTORIAL_TICKETS_CSV),
                ]
            )
        self.assertEqual(codigo, 0, buffer.getvalue())


# ---------------------------------------------------------------------------
# Integridad del flujo
# ---------------------------------------------------------------------------
class PruebasIntegridad(BaseTemporal):
    def test_estados_contabilizados(self) -> None:
        df = datos_base(4)
        df.loc[1, "USUARIO"] = "DESCONOCIDO"
        df.loc[1, "AREA_USUARIO"] = None
        df.loc[2, "FECHA"] = "31/02/2026"
        df.loc[3] = df.loc[0]
        estadisticas = self.procesar(
            df, areas={"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        suma = (
            estadisticas.registros_validos
            + estadisticas.registros_observados
            + estadisticas.registros_pendientes
            + estadisticas.registros_duplicados
            + estadisticas.registros_error
        )
        self.assertEqual(suma, estadisticas.registros_totales)
        self.assertEqual(estadisticas.registros_pendientes, 1)
        self.assertEqual(estadisticas.registros_error, 1)
        self.assertEqual(estadisticas.registros_duplicados, 1)

    def test_flujo_completo(self) -> None:
        estadisticas = self.procesar(
            areas={"ANA TORRES": "SISTEMAS", "LUIS PEREZ": "CONTABILIDAD"}
        )
        procesado = Path(estadisticas.archivo_salida)
        consolidado = consolidar_procesado(procesado, crear_backup=False)
        self.assertEqual(consolidado["total_maestro"], 3)

        corregido = copia_corregida(procesado)
        modificar_celda(corregido, 2, C_DETALLE, "SOPORTE CORREGIDO")
        aplicar_reimportacion(
            analizar_reimportacion(corregido),
            ruta_maestro=cfg.MAESTRO_TICKETS_XLSX,
        )
        maestro = MaestroTickets.cargar()
        self.assertEqual(len(maestro), 3)
        self.assertEqual(
            maestro.a_dataframe().loc[0, C_DETALLE], "SOPORTE CORREGIDO"
        )
        self.assertEqual(
            list(maestro.a_dataframe().columns), list(COLUMNAS_OFICIALES)
        )

    def test_validacion_septiembre_completa(self) -> None:
        ruta = BASE_DIR / "input" / cfg.ARCHIVO_SEPTIEMBRE
        if not ruta.is_file():
            ruta = cfg.PROYECTO_RAIZ / "2026" / "EXCEL" / cfg.ARCHIVO_SEPTIEMBRE
        if not ruta.is_file():
            self.skipTest("No está disponible el Excel de septiembre.")
        import validar_septiembre

        salida = self.raiz / "output" / "validacion_septiembre.xlsx"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            codigo = validar_septiembre.validar(ruta, salida, sobrescribir=True)
        self.assertEqual(codigo, 0, buffer.getvalue())
        self.assertIn("PASS", buffer.getvalue())


# ---------------------------------------------------------------------------
# Interfaz
# ---------------------------------------------------------------------------
class PruebasInterfaz(BaseTemporal):
    def test_ventana_principal(self) -> None:
        try:
            import tkinter as tk

            import interfaz
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"Tkinter no disponible: {exc}")
        try:
            app = interfaz.AplicacionTk()
        except tk.TclError as exc:
            self.skipTest(f"Sin entorno gráfico: {exc}")
        try:
            app.update_idletasks()
            self.assertIn("INKACROPS", app.title())
            self.assertEqual(len(app._cuaderno.tabs()), 7)
            self.assertTrue(app._tabla_preview["columns"])
        finally:
            app.destruir()


if __name__ == "__main__":
    unittest.main(verbosity=2)
