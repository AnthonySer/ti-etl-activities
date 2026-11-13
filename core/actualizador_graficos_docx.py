"""Actualiza cachés y libros incrustados de los cuatro gráficos Word nativos."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import BytesIO
import os
from pathlib import Path
import posixpath
import tempfile
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree
from openpyxl import Workbook, load_workbook


NS_CHART = "http://schemas.openxmlformats.org/drawingml/2006/chart"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
TIPO_PAQUETE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package"
TIPO_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_GRAFICOS = {
    1: ("RESUMEN", "TIEMPO(H)"),
    2: ("PROYECTOS", "TIEMPO(H)"),
    3: ("ACTIVIDAD", "TICKETS"),
    4: ("AREA", "TICKETS"),
}


class ErrorGraficosDocx(ValueError):
    """El paquete, serie o libro de datos no cumple el contrato OOXML."""


@dataclass(frozen=True)
class SerieGraficoDocx:
    categorias: tuple[str, ...]
    valores: tuple[Decimal | int | float, ...]
    nombre_serie: str | None = None


@dataclass(frozen=True)
class ResultadoGraficosDocx:
    ruta_salida: Path
    puntos_por_grafico: tuple[tuple[int, int], ...]
    libros_incrustados: tuple[str, ...]


def _sha256(ruta: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _decimal(valor: Any) -> Decimal:
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ErrorGraficosDocx(f"Valor de gráfica no numérico: {valor!r}.") from exc
    if not numero.is_finite() or numero < 0:
        raise ErrorGraficosDocx(f"Valor de gráfica no finito o negativo: {valor!r}.")
    return numero


def _actualizar_cache(referencia, valores: tuple[str, ...], *, numerico: bool) -> None:
    tag_cache = "numCache" if numerico else "strCache"
    cache = referencia.find(f"{{{NS_CHART}}}{tag_cache}")
    if cache is None:
        cache = etree.SubElement(referencia, f"{{{NS_CHART}}}{tag_cache}")
    cuenta = cache.find(f"{{{NS_CHART}}}ptCount")
    if cuenta is None:
        cuenta = etree.Element(f"{{{NS_CHART}}}ptCount")
        formato = cache.find(f"{{{NS_CHART}}}formatCode")
        indice = list(cache).index(formato) + 1 if formato is not None else 0
        cache.insert(indice, cuenta)
    cuenta.set("val", str(len(valores)))
    for punto in list(cache.findall(f"{{{NS_CHART}}}pt")):
        cache.remove(punto)
    for indice, valor in enumerate(valores):
        punto = etree.SubElement(cache, f"{{{NS_CHART}}}pt")
        punto.set("idx", str(indice))
        etree.SubElement(punto, f"{{{NS_CHART}}}v").text = valor


def _establecer_formula(referencia, formula: str) -> None:
    elemento = referencia.find(f"{{{NS_CHART}}}f")
    if elemento is None:
        raise ErrorGraficosDocx("La serie del gráfico no tiene fórmula de datos OOXML.")
    elemento.text = formula


def _actualizar_parte_grafico(
    xml: bytes,
    numero: int,
    serie: SerieGraficoDocx,
) -> bytes:
    raiz = etree.fromstring(xml)
    series = raiz.xpath("//c:pieChart/c:ser", namespaces={"c": NS_CHART})
    if len(series) != 1:
        raise ErrorGraficosDocx(f"Se esperaba una serie de pastel en chart{numero}.xml; se encontraron {len(series)}.")
    registro = series[0]
    nombre_hoja, nombre_defecto = _GRAFICOS[numero]
    nombre_serie = serie.nombre_serie or nombre_defecto

    ref_nombre = registro.find(f"{{{NS_CHART}}}tx/{{{NS_CHART}}}strRef")
    ref_categorias = registro.find(f"{{{NS_CHART}}}cat/{{{NS_CHART}}}strRef")
    ref_valores = registro.find(f"{{{NS_CHART}}}val/{{{NS_CHART}}}numRef")
    if ref_nombre is None or ref_categorias is None or ref_valores is None:
        raise ErrorGraficosDocx(f"Estructura de serie inesperada en chart{numero}.xml.")

    fin = max(len(serie.categorias) + 1, 2)
    _establecer_formula(ref_nombre, f"{nombre_hoja}!$B$1")
    _establecer_formula(ref_categorias, f"{nombre_hoja}!$A$2:$A${fin}")
    _establecer_formula(ref_valores, f"{nombre_hoja}!$B$2:$B${fin}")
    _actualizar_cache(ref_nombre, (nombre_serie,), numerico=False)
    _actualizar_cache(ref_categorias, serie.categorias, numerico=False)
    valores_xml = tuple(format(_decimal(valor), "f") for valor in serie.valores)
    _actualizar_cache(ref_valores, valores_xml, numerico=True)
    return etree.tostring(raiz, xml_declaration=True, encoding="UTF-8", standalone=True)


def _crear_libro(nombre_hoja: str, nombre_serie: str, serie: SerieGraficoDocx) -> bytes:
    libro = Workbook()
    hoja = libro.active
    hoja.title = nombre_hoja
    hoja.append(("Categoría", nombre_serie))
    for categoria, valor in zip(serie.categorias, serie.valores):
        hoja.append((categoria, float(_decimal(valor))))
    hoja.column_dimensions["A"].width = 36
    hoja.column_dimensions["B"].width = 16
    salida = BytesIO()
    libro.save(salida)
    return salida.getvalue()


def _actualizar_relacion(xml: bytes, numero: int, archivo_libro: str) -> bytes:
    raiz = etree.fromstring(xml)
    grafico = f"chart{numero}.xml"
    partes = raiz.findall(f"{{{NS_PACKAGE_REL}}}Relationship")
    relaciones_grafico = [
        rel for rel in partes
        if rel.get("Type", "").endswith("/oleObject") or rel.get("Type", "").endswith("/package")
    ]
    if len(relaciones_grafico) != 1:
        raise ErrorGraficosDocx(f"{grafico} debe tener una relación de libro fuente; se encontraron {len(relaciones_grafico)}.")
    relacion = relaciones_grafico[0]
    relacion.set("Type", TIPO_PAQUETE)
    relacion.set("Target", f"../embeddings/{archivo_libro}")
    relacion.attrib.pop("TargetMode", None)
    return etree.tostring(raiz, xml_declaration=True, encoding="UTF-8", standalone=True)


def _actualizar_tipos_contenido(xml: bytes) -> bytes:
    raiz = etree.fromstring(xml)
    xlsx = raiz.find(f"{{{NS_CONTENT_TYPES}}}Default[@Extension='xlsx']")
    if xlsx is None:
        xlsx = etree.SubElement(raiz, f"{{{NS_CONTENT_TYPES}}}Default")
        xlsx.set("Extension", "xlsx")
    xlsx.set("ContentType", TIPO_XLSX)
    return etree.tostring(raiz, xml_declaration=True, encoding="UTF-8", standalone=True)


def _validar_salida(ruta: Path, series: Mapping[int, SerieGraficoDocx]) -> tuple[str, ...]:
    from docx import Document

    nombres_libros: list[str] = []
    with ZipFile(ruta, "r") as paquete:
        if paquete.testzip() is not None:
            raise ErrorGraficosDocx("El DOCX generado no es un ZIP válido.")
        nombres = set(paquete.namelist())
        for numero, serie in series.items():
            hoja, nombre_defecto = _GRAFICOS[numero]
            chart_path = f"word/charts/chart{numero}.xml"
            rel_path = f"word/charts/_rels/chart{numero}.xml.rels"
            raiz = etree.fromstring(paquete.read(chart_path))
            relaciones = etree.fromstring(paquete.read(rel_path))
            external = raiz.xpath("//c:externalData/@r:id", namespaces={"c": NS_CHART, "r": NS_REL})
            if len(external) != 1:
                raise ErrorGraficosDocx(f"chart{numero} no tiene una sola relación a datos.")
            relacion = next((r for r in relaciones if r.get("Id") == external[0]), None)
            if relacion is None or relacion.get("Type") != TIPO_PAQUETE or relacion.get("TargetMode"):
                raise ErrorGraficosDocx(f"chart{numero} conserva una relación externa o inválida.")
            target = posixpath.normpath(posixpath.join("word/charts", relacion.get("Target", "")))
            if target not in nombres or not target.startswith("word/embeddings/"):
                raise ErrorGraficosDocx(f"No se encontró el libro interno de chart{numero}.")
            nombres_libros.append(target)

            serie_xml = raiz.xpath("//c:pieChart/c:ser", namespaces={"c": NS_CHART})[0]
            categorias = serie_xml.xpath("./c:cat/c:strRef/c:strCache/c:pt/c:v/text()", namespaces={"c": NS_CHART})
            valores = serie_xml.xpath("./c:val/c:numRef/c:numCache/c:pt/c:v/text()", namespaces={"c": NS_CHART})
            if categorias != list(serie.categorias) or [Decimal(v) for v in valores] != [_decimal(v) for v in serie.valores]:
                raise ErrorGraficosDocx(f"La caché de chart{numero} no coincide con los datos recibidos.")
            libro = load_workbook(BytesIO(paquete.read(target)), read_only=True, data_only=True)
            try:
                if hoja not in libro.sheetnames:
                    raise ErrorGraficosDocx(f"Falta la hoja {hoja} dentro del libro del gráfico {numero}.")
                filas = list(libro[hoja].iter_rows(min_row=2, values_only=True))
                esperado = list(zip(serie.categorias, [float(_decimal(v)) for v in serie.valores]))
                if filas != esperado:
                    raise ErrorGraficosDocx(f"El libro interno de chart{numero} no coincide con su caché.")
            finally:
                libro.close()
    documento = Document(ruta)
    if len(documento.inline_shapes) != 4:
        raise ErrorGraficosDocx("La copia generada no conserva los cuatro gráficos inline.")
    return tuple(nombres_libros)


def actualizar_graficos_docx(
    ruta_entrada: str | Path,
    ruta_salida: str | Path,
    series_por_grafico: Mapping[int, SerieGraficoDocx],
) -> ResultadoGraficosDocx:
    """Copia un DOCX y sincroniza cachés más libros internos para sus cuatro gráficos.

    Nunca sobrescribe la entrada ni una salida existente. Recibe datos ya
    calculados; no define qué registros o áreas deben formar cada serie.
    """

    entrada = Path(ruta_entrada).resolve()
    salida = Path(ruta_salida).resolve()
    if entrada == salida:
        raise ErrorGraficosDocx("La salida debe ser una copia separada de la plantilla/documento de entrada.")
    if not entrada.is_file():
        raise ErrorGraficosDocx(f"No existe el DOCX de entrada: {entrada}.")
    if salida.exists():
        raise FileExistsError(f"La salida ya existe y no se sobrescribirá: {salida}.")
    if set(series_por_grafico) != set(_GRAFICOS):
        raise ErrorGraficosDocx("Se requieren datos para los cuatro gráficos (1, 2, 3 y 4).")
    for numero, serie in series_por_grafico.items():
        if len(serie.categorias) != len(serie.valores):
            raise ErrorGraficosDocx(f"chart{numero}: hay distinto número de categorías y valores.")
        if any(not isinstance(categoria, str) for categoria in serie.categorias):
            raise ErrorGraficosDocx(f"chart{numero}: todas las categorías deben ser texto.")
        for valor in serie.valores:
            _decimal(valor)

    hash_entrada = _sha256(entrada)
    salida.parent.mkdir(parents=True, exist_ok=True)
    fd, temporal_nombre = tempfile.mkstemp(prefix=".anexo5-graficos-", suffix=".docx", dir=salida.parent)
    os.close(fd)
    temporal = Path(temporal_nombre)
    nuevos: dict[str, bytes] = {}
    nombres_libros: list[str] = []
    try:
        with ZipFile(entrada, "r") as original:
            miembros = set(original.namelist())
            for numero, serie in series_por_grafico.items():
                chart_path = f"word/charts/chart{numero}.xml"
                rel_path = f"word/charts/_rels/chart{numero}.xml.rels"
                libro_nombre = f"Anexo5_grafico_{numero}.xlsx"
                if chart_path not in miembros or rel_path not in miembros:
                    raise ErrorGraficosDocx(f"Faltan partes OOXML del gráfico {numero}.")
                hoja, defecto = _GRAFICOS[numero]
                nombre_serie = serie.nombre_serie or defecto
                nuevos[chart_path] = _actualizar_parte_grafico(original.read(chart_path), numero, serie)
                nuevos[rel_path] = _actualizar_relacion(original.read(rel_path), numero, libro_nombre)
                nuevos[f"word/embeddings/{libro_nombre}"] = _crear_libro(hoja, nombre_serie, serie)
                nombres_libros.append(f"word/embeddings/{libro_nombre}")
            content_types = "[Content_Types].xml"
            if content_types not in miembros:
                raise ErrorGraficosDocx("El paquete DOCX no contiene [Content_Types].xml.")
            nuevos[content_types] = _actualizar_tipos_contenido(original.read(content_types))

            with ZipFile(temporal, "w", ZIP_DEFLATED) as generado:
                for info in original.infolist():
                    if info.filename in nuevos:
                        generado.writestr(info, nuevos.pop(info.filename))
                    else:
                        generado.writestr(info, original.read(info.filename))
                for nombre, contenido in nuevos.items():
                    generado.writestr(nombre, contenido)

        _validar_salida(temporal, series_por_grafico)
        if _sha256(entrada) != hash_entrada:
            raise ErrorGraficosDocx("El archivo de entrada cambió durante la generación.")
        # Reserve the final path exclusively before replacing the placeholder;
        # this prevents a concurrent run from overwriting someone else's file.
        descriptor_salida = os.open(salida, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor_salida)
        try:
            os.replace(temporal, salida)
        except Exception:
            salida.unlink(missing_ok=True)
            raise
    except Exception:
        temporal.unlink(missing_ok=True)
        raise

    return ResultadoGraficosDocx(
        ruta_salida=salida,
        puntos_por_grafico=tuple((n, len(serie.categorias)) for n, serie in sorted(series_por_grafico.items())),
        libros_incrustados=tuple(nombres_libros),
    )
