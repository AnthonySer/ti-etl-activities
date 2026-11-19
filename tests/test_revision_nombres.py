"""Pruebas de nombres visibles, numeración y sobrescritura segura de revisiones.

Se usan exclusivamente carpetas temporales: nunca se toca la carpeta real de
``output/revision`` ni archivos del usuario.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import configuracion as cfg  # noqa: E402
from core import revision as svc_revision  # noqa: E402
from core.archivos import ErrorArchivo  # noqa: E402
from core.maestro import Maestro  # noqa: E402
from core.procesador import procesar_archivo  # noqa: E402


def escribir_tabla(df: pd.DataFrame, ruta: Path) -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(ruta, engine="openpyxl") as libro:
        df.to_excel(libro, sheet_name="DATOS", index=False)
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


class BaseRevisiones(unittest.TestCase):
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
        "OPERACIONES_JSON",
        "HUELLAS_EXCEL_JSON",
        "LOCK_SISTEMA",
    )

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory(prefix="tickets_rev_")
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
        cfg.OPERACIONES_JSON = cfg.DATA_DIR / "operaciones.json"
        cfg.HUELLAS_EXCEL_JSON = cfg.DATA_DIR / "huellas_excel.json"
        cfg.LOCK_SISTEMA = cfg.DATA_DIR / ".sistema.lock"
        cfg.asegurar_directorios()

    def tearDown(self) -> None:
        for campo, valor in self._originales.items():
            setattr(cfg, campo, valor)
        self._temporal.cleanup()

    def crear_revision(
        self,
        nombre_original: str,
        *,
        nombre_revision: str = "",
        id_ejecucion: str = "",
        registros: list[dict] | None = None,
    ) -> Path:
        """Crea un JSON de revisión mínimo válido en la carpeta temporal."""

        identificador = id_ejecucion or f"REV_{nombre_original}_{nombre_revision}"
        datos = {
            "version": 1,
            "id_ejecucion": identificador,
            "id_revision": svc_revision._limpiar_id(identificador),
            "archivo_original": str(cfg.INPUT_DIR / nombre_original),
            "nombre_original": nombre_original,
            "archivo_procesado": "",
            "archivo_manifiesto": "",
            "fecha_creacion": "2026-11-01T10:00:00-05:00",
            "actualizado": "2026-11-01T10:00:00-05:00",
            "total_registros": 2,
            "areas_validadas": 0,
            "registros": registros if registros is not None else [],
            "historial": [],
            "generacion": {},
            "resumen": {
                "total_registros": 2,
                "areas_validadas": 0,
                "por_revisar": 2,
            },
        }
        if nombre_revision:
            datos["nombre_revision"] = nombre_revision
        # El nombre del archivo se deriva del id, igual que en producción.
        ruta = svc_revision.carpeta_revisiones() / (
            f"revision_{svc_revision._limpiar_id(identificador)}.json"
        )
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
        return ruta


class PruebasNumeracion(BaseRevisiones):
    def test_reutiliza_primer_numero_disponible(self) -> None:
        self.crear_revision("tickets.xlsx")
        self.crear_revision("tickets.xlsx", nombre_revision="tickets (1).xlsx")
        self.crear_revision("tickets.xlsx", nombre_revision="tickets (3).xlsx")
        self.assertEqual(
            svc_revision.siguiente_nombre_revision("tickets.xlsx"),
            "tickets (2).xlsx",
        )

    def test_sin_revisiones_empieza_en_uno(self) -> None:
        self.assertEqual(
            svc_revision.siguiente_nombre_revision("tickets.xlsx", []),
            "tickets (1).xlsx",
        )

    def test_nombre_que_ya_tiene_sufijo_no_se_duplica(self) -> None:
        self.crear_revision("tickets.xlsx")
        self.crear_revision("tickets.xlsx", nombre_revision="tickets (1).xlsx")
        # Aunque el usuario procese "tickets (1).xlsx", se numera sobre la base.
        self.assertEqual(
            svc_revision.siguiente_nombre_revision("tickets (1).xlsx"),
            "tickets (2).xlsx",
        )
        self.assertEqual(
            svc_revision.nombre_sin_sufijo("tickets (1).xlsx"), "tickets.xlsx"
        )

    def test_coincidencia_solo_por_nombre_e_insensible_a_mayusculas(self) -> None:
        self.crear_revision("TICKETS.XLSX")
        encontradas = svc_revision.revisiones_con_nombre("tickets.xlsx")
        self.assertEqual(len(encontradas), 1)

    def test_nombres_independientes(self) -> None:
        ruta = self.crear_revision(
            "tickets.xlsx", nombre_revision="tickets (1).xlsx"
        )
        revision = svc_revision.Revision.cargar(ruta)
        self.assertEqual(revision.nombre_original, "tickets.xlsx")
        self.assertEqual(revision.nombre_revision, "tickets (1).xlsx")
        self.assertEqual(revision.nombre_visible, "tickets (1).xlsx")
        self.assertTrue(revision.id_revision)
        self.assertNotEqual(revision.id_revision, revision.nombre_original)

    def test_json_antiguo_sin_etiqueta_es_compatible(self) -> None:
        ruta = self.crear_revision("legacy.xlsx")
        revision = svc_revision.Revision.cargar(ruta)
        self.assertEqual(revision.nombre_revision, "legacy.xlsx")
        listado = svc_revision.listar_revisiones()
        item = next(x for x in listado if x["ruta"] == str(ruta))
        self.assertEqual(item["nombre_revision"], "legacy.xlsx")
        self.assertTrue(item["id_revision"])


class PruebasSobrescrituraSegura(BaseRevisiones):
    def test_sobrescribe_varias_revisiones_del_mismo_nombre(self) -> None:
        anteriores = [
            self.crear_revision("tickets.xlsx"),
            self.crear_revision("tickets.xlsx", nombre_revision="tickets (1).xlsx"),
            self.crear_revision("tickets.xlsx", nombre_revision="tickets (2).xlsx"),
        ]
        nueva = self.crear_revision(
            "tickets.xlsx", nombre_revision="tickets.xlsx", id_ejecucion="REV_NUEVA"
        )
        eliminadas, errores = svc_revision.sobrescribir_revisiones(
            nueva, [str(r) for r in anteriores]
        )
        self.assertEqual(errores, [])
        self.assertEqual(len(eliminadas), 3)
        self.assertTrue(nueva.is_file())
        for ruta in anteriores:
            self.assertFalse(ruta.exists())

    def test_conserva_anteriores_si_la_nueva_no_carga(self) -> None:
        anterior = self.crear_revision("tickets.xlsx")
        nueva = svc_revision.carpeta_revisiones() / "revision_CORRUPTA.json"
        nueva.write_text("{ esto no es json", encoding="utf-8")
        with self.assertRaises(ErrorArchivo):
            svc_revision.sobrescribir_revisiones(nueva, [str(anterior)])
        self.assertTrue(anterior.is_file())

    def test_conserva_anteriores_si_la_nueva_no_se_guarda(self) -> None:
        anterior = self.crear_revision("tickets.xlsx")
        inexistente = svc_revision.carpeta_revisiones() / "revision_FANTASMA.json"
        with self.assertRaises(ErrorArchivo):
            svc_revision.sobrescribir_revisiones(inexistente, [str(anterior)])
        self.assertTrue(anterior.is_file())

    def test_conserva_anteriores_si_la_nueva_esta_incompleta(self) -> None:
        anterior = self.crear_revision("tickets.xlsx")
        nueva = svc_revision.carpeta_revisiones() / "revision_INCOMPLETA.json"
        nueva.write_text(json.dumps({"version": 1}), encoding="utf-8")
        with self.assertRaises(ErrorArchivo):
            svc_revision.sobrescribir_revisiones(nueva, [str(anterior)])
        self.assertTrue(anterior.is_file())

    def test_no_elimina_la_revision_abierta(self) -> None:
        abierta = self.crear_revision("tickets.xlsx")
        otra = self.crear_revision("tickets.xlsx", nombre_revision="tickets (1).xlsx")
        nueva = self.crear_revision(
            "tickets.xlsx", id_ejecucion="REV_NUEVA_ABIERTA"
        )
        eliminadas, errores = svc_revision.sobrescribir_revisiones(
            nueva, [str(abierta), str(otra)], abierta=str(abierta)
        )
        self.assertEqual(eliminadas, [str(otra)])
        self.assertEqual(len(errores), 1)
        self.assertIn("abierta", errores[0])
        self.assertTrue(abierta.is_file())

    def test_eliminar_rechaza_rutas_fuera_de_la_carpeta(self) -> None:
        ajeno = self.raiz / "tickets.xlsx"
        ajeno.write_text("no soy una revision", encoding="utf-8")
        eliminadas, errores = svc_revision.eliminar_revisiones([str(ajeno)])
        self.assertEqual(eliminadas, [])
        self.assertEqual(len(errores), 1)
        self.assertTrue(ajeno.is_file())

    def test_nueva_revision_no_se_borra_a_si_misma(self) -> None:
        nueva = self.crear_revision("tickets.xlsx", id_ejecucion="REV_UNICA")
        eliminadas, errores = svc_revision.sobrescribir_revisiones(
            nueva, [str(nueva)]
        )
        self.assertEqual((eliminadas, errores), ([], []))
        self.assertTrue(nueva.is_file())


class PruebasIntegracionEtiqueta(BaseRevisiones):
    def test_procesar_archivo_conserva_nombre_original_y_etiqueta(self) -> None:
        entrada = escribir_tabla(datos_base(), cfg.INPUT_DIR / "tickets.xlsx")
        maestro = Maestro(
            pd.DataFrame(
                [{"USUARIO": "ANA TORRES", "AREA_USUARIO": "SISTEMAS"}],
                columns=["USUARIO", "AREA_USUARIO"],
            )
        )
        maestro.guardar(cfg.MAESTRO_XLSX)
        estadisticas = procesar_archivo(
            entrada,
            maestro,
            modo=cfg.MODO_NORMAL,
            permitir_pendientes=True,
            crear_revision=True,
            registrar_auditoria=False,
            revision_nombre="tickets (1).xlsx",
        )
        self.assertIsNotNone(estadisticas.ruta_revision)
        revision = svc_revision.Revision.cargar(estadisticas.ruta_revision)
        self.assertEqual(revision.nombre_original, "tickets.xlsx")
        self.assertEqual(revision.nombre_revision, "tickets (1).xlsx")
        self.assertEqual(Path(revision.archivo_original).name, "tickets.xlsx")
        # La etiqueta es solo visual: no altera el Excel del usuario.
        self.assertTrue(Path(revision.archivo_original).is_file())


if __name__ == "__main__":
    unittest.main()
