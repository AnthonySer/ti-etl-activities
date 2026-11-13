"""Pruebas de integración de la copia y ensamblado del Anexo 5."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

from docx import Document

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.actualizador_graficos_docx import SerieGraficoDocx
from core.actualizador_tablas_docx import localizar_tablas_docx
from core.generador_docx import generar_anexo5_docx


PLANTILLA = BASE_DIR / "plantillas" / "anexo_5_plantilla.docx"


class TestGeneradorDocx(unittest.TestCase):
    def test_ensambla_sobre_copia_y_protege_la_plantilla(self):
        with tempfile.TemporaryDirectory(prefix="anexo5-generador-") as carpeta:
            salida = Path(carpeta) / "anexo5_sintetico.docx"
            hash_plantilla = hashlib.sha256(PLANTILLA.read_bytes()).hexdigest()
            filas = {
                "resumen_tipo_atencion": [("Incidencia", "2", "100%")],
                "proyectos": [],
                "tickets_tipo": [("Incidencias", "2", "100%")],
                "areas": [("Área sintética", "2", "100%")],
                "detalle": [("Incidencia", "Detalle sintético", "01/09/2026", "Usuario", "Área sintética", "2")],
            }
            totales = {
                "resumen_tipo_atencion": ("TOTAL", "2", "100%"),
                "proyectos": ("TOTAL GENERAL", "0", "0%"),
                "tickets_tipo": ("TOTAL GENERAL", "2", "100%"),
                "areas": ("TOTAL GENERAL", "2", "100%"),
            }
            series = {
                1: SerieGraficoDocx(("Incidencia", "Proyecto", "Solicitud"), (2, 0, 0)),
                2: SerieGraficoDocx((), ()),
                3: SerieGraficoDocx(("Incidencias", "Solicitudes"), (2, 0)),
                4: SerieGraficoDocx(("Área sintética",), (2,)),
            }
            resultado = generar_anexo5_docx(
                PLANTILLA,
                salida,
                reemplazos_texto={"agosto 2026": "septiembre 2026"},
                filas_por_tabla=filas,
                totales_por_tabla=totales,
                series_por_grafico=series,
            )

            self.assertEqual(resultado.ruta_salida, salida.resolve())
            self.assertTrue(salida.is_file())
            self.assertEqual(hashlib.sha256(PLANTILLA.read_bytes()).hexdigest(), hash_plantilla)
            with ZipFile(salida) as paquete:
                self.assertIsNone(paquete.testzip())
                self.assertEqual(sum(name.startswith("word/charts/chart") and name.endswith(".xml") for name in paquete.namelist()), 4)
            documento = Document(salida)
            self.assertEqual(len(documento.inline_shapes), 4)
            texto = "\n".join(p.text for p in documento.paragraphs)
            self.assertNotIn("agosto 2026", texto)
            tablas = localizar_tablas_docx(documento)
            self.assertEqual(len(tablas["areas"].rows), 3)
            self.assertEqual(len(tablas["detalle"].rows), 2)


if __name__ == "__main__":
    unittest.main()
