"""Pruebas de tablas/textos sobre copias temporales de la plantilla Anexo 5."""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from lxml import etree

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.actualizador_tablas_docx import (
    actualizar_tablas_docx,
    localizar_tablas_docx,
)
from core.actualizador_textos_docx import (
    ErrorMarcadorDocx,
    reemplazar_marcadores_docx,
)


PLANTILLA = BASE_DIR / "plantillas" / "anexo_5_plantilla.docx"


def filas_detalle(cantidad):
    return [
        (
            "Incidencia",
            f"Detalle sintético {i:03d}",
            f"01/09/2026",
            f"Usuario {i:03d}",
            f"Área {i % 22:02d}",
            "1.25",
        )
        for i in range(cantidad)
    ]


class TestActualizadorTablasDocxFase2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="anexo5-tablas-")
        self.addCleanup(self.tmp.cleanup)
        self.copia = Path(self.tmp.name) / "copia_prueba.docx"
        shutil.copy2(PLANTILLA, self.copia)
        self.hash_plantilla = hashlib.sha256(PLANTILLA.read_bytes()).hexdigest()

    def test_actualiza_cinco_tablas_y_preserva_estilos_y_graficos(self):
        documento = Document(self.copia)
        tablas = localizar_tablas_docx(documento)
        estilos = {k: t.style.name for k, t in tablas.items()}
        encabezados = {k: [c.text for c in t.rows[0].cells] for k, t in tablas.items()}
        grid_areas = etree.tostring(tablas["areas"]._tbl.tblGrid)
        fila_area_molde = tablas["areas"].rows[1]._tr
        props_fila = etree.tostring(fila_area_molde.trPr) if fila_area_molde.trPr is not None else None
        props_celdas = [
            etree.tostring(celda._tc.tcPr) if celda._tc.tcPr is not None else None
            for celda in tablas["areas"].rows[1].cells
        ]

        detalle = filas_detalle(250)
        resultado = actualizar_tablas_docx(
            documento,
            {
                "resumen_tipo_atencion": [
                    ("Incidencia", "148.50", "34.8%"),
                    ("Proyecto", "83.50", "19.6%"),
                    ("Solicitud", "194.75", "45.6%"),
                ],
                "proyectos": [(f"PROYECTO SINTÉTICO {i:02d}", "1.00", "1.0%") for i in range(23)],
                "tickets_tipo": [("Incidencias", "88", "60.7%"), ("Solicitudes", "57", "39.3%")],
                "areas": [(f"Área {i:02d}", str(i + 1), "4.5%") for i in range(22)],
                "detalle": detalle,
            },
            {
                "resumen_tipo_atencion": ("TOTAL", "426.75", "100%"),
                "proyectos": ("TOTAL GENERAL", "23.00", "100.0%"),
                "tickets_tipo": ("TOTAL GENERAL", "145", "100%"),
                "areas": ("TOTAL GENERAL", "145", "100%"),
            },
        )

        documento.save(self.copia)
        with zipfile.ZipFile(self.copia) as paquete:
            self.assertIsNone(paquete.testzip())
            self.assertIn("word/header1.xml", paquete.namelist())
            self.assertIn("word/media/image1.jpg", paquete.namelist())
            self.assertEqual(sum(n.startswith("word/charts/chart") and n.endswith(".xml") for n in paquete.namelist()), 4)
        reabierto = Document(self.copia)
        nuevas = localizar_tablas_docx(reabierto)
        self.assertEqual(len(reabierto.inline_shapes), 4)
        self.assertEqual(dict(resultado.filas_datos)["areas"], 22)
        self.assertEqual(dict(resultado.filas_datos)["detalle"], 250)
        self.assertEqual([len(nuevas[k].rows) for k in nuevas], [5, 25, 4, 24, 251])
        self.assertEqual({k: t.style.name for k, t in nuevas.items()}, estilos)
        self.assertEqual({k: [c.text for c in t.rows[0].cells] for k, t in nuevas.items()}, encabezados)
        self.assertEqual(etree.tostring(nuevas["areas"]._tbl.tblGrid), grid_areas)
        nueva_fila_area = nuevas["areas"].rows[1]._tr
        self.assertEqual(etree.tostring(nueva_fila_area.trPr) if nueva_fila_area.trPr is not None else None, props_fila)
        self.assertEqual(
            [etree.tostring(celda._tc.tcPr) if celda._tc.tcPr is not None else None for celda in nuevas["areas"].rows[1].cells],
            props_celdas,
        )
        self.assertEqual(nuevas["areas"].rows[-2].cells[0].text, "Área 21")
        self.assertEqual(nuevas["detalle"].rows[1].cells[1].text, "Detalle sintético 000")
        self.assertEqual(nuevas["detalle"].rows[-1].cells[1].text, "Detalle sintético 249")
        texto_tablas = "\n".join(c.text for t in nuevas.values() for r in t.rows for c in r.cells)
        self.assertNotIn("MEJORA SW ENVASADO", texto_tablas)
        self.assertNotIn("PRODUCCION", texto_tablas)
        self.assertEqual(hashlib.sha256(PLANTILLA.read_bytes()).hexdigest(), self.hash_plantilla)

    def test_permite_reducir_tablas_y_dejar_cero_detalles(self):
        documento = Document(self.copia)
        resultado = actualizar_tablas_docx(
            documento,
            {"areas": [("Área única", "1", "100%")], "detalle": []},
            {"areas": ("TOTAL GENERAL", "1", "100%")},
        )
        documento.save(self.copia)
        reabierto = Document(self.copia)
        tablas = localizar_tablas_docx(reabierto)
        self.assertEqual(len(tablas["areas"].rows), 3)
        self.assertEqual(len(tablas["detalle"].rows), 1)
        self.assertEqual(tablas["detalle"].rows[0].cells[0].text, "TIPO DE ATENCION")
        self.assertEqual(resultado.filas_datos, (("areas", 1), ("detalle", 0)))
        self.assertEqual(hashlib.sha256(PLANTILLA.read_bytes()).hexdigest(), self.hash_plantilla)


class TestActualizadorTextosDocxFase2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="anexo5-textos-")
        self.addCleanup(self.tmp.cleanup)
        self.copia = Path(self.tmp.name) / "copia_textos.docx"
        shutil.copy2(PLANTILLA, self.copia)

    def test_reemplaza_marcadores_y_conserva_resaltado(self):
        documento = Document(self.copia)
        resultado = reemplazar_marcadores_docx(
            documento,
            {
                "agosto de 2026": "septiembre de 2026",
                "agosto 2026": "septiembre 2026",
                "479.5 horas": "426.75 horas",
                "9 proyectos": "7 proyectos",
                "129 tickets": "145 tickets",
            },
        )
        documento.save(self.copia)
        reabierto = Document(self.copia)
        self.assertEqual(resultado["agosto de 2026"], 4)
        self.assertEqual(resultado["agosto 2026"], 1)
        self.assertEqual(resultado["479.5 horas"], 1)
        texto = "\n".join(p.text for p in reabierto.paragraphs)
        for obsoleto in ("agosto 2026", "agosto de 2026", "479.5 horas", "9 proyectos", "129 tickets"):
            self.assertNotIn(obsoleto, texto)
        resaltados = {
            run.text
            for p in reabierto.paragraphs
            for run in p.runs
            if run.font.highlight_color == WD_COLOR_INDEX.YELLOW
        }
        self.assertIn("septiembre 2026", resaltados)
        self.assertIn("septiembre de 2026", resaltados)
        self.assertIn("426.75 horas", resaltados)
        self.assertIn("7 proyectos", resaltados)
        self.assertIn("145 tickets", resaltados)

    def test_marcador_dividido_en_runs_y_formato_del_run_se_conservan(self):
        documento = Document()
        parrafo = documento.add_paragraph()
        parrafo.add_run("Período: ")
        marcado = parrafo.add_run("agosto")
        marcado.font.highlight_color = WD_COLOR_INDEX.YELLOW
        parrafo.add_run(" de ")
        parrafo.add_run("2026")

        resultado = reemplazar_marcadores_docx(documento, {"agosto de 2026": "septiembre de 2026"})

        self.assertEqual(parrafo.text, "Período: septiembre de 2026")
        self.assertEqual(resultado["agosto de 2026"], 1)
        self.assertEqual(parrafo.runs[1].font.highlight_color, WD_COLOR_INDEX.YELLOW)

    def test_falla_si_falta_un_marcador_requerido(self):
        documento = Document()
        documento.add_paragraph("Sin texto variable")
        with self.assertRaises(ErrorMarcadorDocx):
            reemplazar_marcadores_docx(documento, {"agosto de 2026": "septiembre de 2026"})


if __name__ == "__main__":
    unittest.main()
