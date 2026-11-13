"""Reemplazo seguro de marcadores en párrafos Word conservando sus runs."""

from __future__ import annotations

from collections.abc import Mapping

from docx.document import Document as DocumentType
from docx.table import Table
from docx.text.paragraph import Paragraph


class ErrorMarcadorDocx(ValueError):
    """Falta un marcador requerido o el reemplazo es ambiguo."""


TITULOS_GRAFICOS_SIN_SUBRAYADO = (
    "Atenciones por Proyectos",
    "Tickets por Tipo de Actividad",
    "Tickets por Área",
)

# Marcadores con etiquetas especiales que no requieren reemplazo completo
MARCADORES_ETIQUETADOS_PASIVOS = (
    "#TAG_1",
    "#TAG_2",
    "#PROYECTO_X",
)


def _parrafos_contenedor(contenedor):
    yield from contenedor.paragraphs
    for tabla in contenedor.tables:
        yield from _parrafos_tabla(tabla)


def _parrafos_tabla(tabla: Table):
    for fila in tabla.rows:
        for celda in fila.cells:
            yield from celda.paragraphs
            for tabla_anidada in celda.tables:
                yield from _parrafos_tabla(tabla_anidada)


def iterar_parrafos(documento: DocumentType):
    """Recorre párrafos del cuerpo, tablas, encabezados y pies una sola vez."""

    vistos: set[int] = set()
    contenedores = [documento]
    for seccion in documento.sections:
        contenedores.extend((seccion.header, seccion.footer))
    for contenedor in contenedores:
        for parrafo in _parrafos_contenedor(contenedor):
            identidad = id(parrafo._p)
            if identidad not in vistos:
                vistos.add(identidad)
                yield parrafo


def _reemplazar_primera_coincidencia(
    parrafo: Paragraph,
    original: str,
    nuevo: str,
) -> bool:
    runs = list(parrafo.runs)
    textos = [run.text for run in runs]
    texto = "".join(textos)
    inicio = texto.find(original)
    if inicio < 0:
        return False
    if original == nuevo:
        return True

    fin = inicio + len(original)
    cursor = 0
    inicio_run = fin_run = -1
    inicio_local = fin_local = 0
    for indice, texto_run in enumerate(textos):
        siguiente = cursor + len(texto_run)
        if inicio_run < 0 and cursor <= inicio < siguiente:
            inicio_run = indice
            inicio_local = inicio - cursor
        if cursor < fin <= siguiente:
            fin_run = indice
            fin_local = fin - cursor
            break
        cursor = siguiente

    if inicio_run < 0 or fin_run < 0:
        raise ErrorMarcadorDocx("El marcador cruza contenido Word que no está en runs de texto simples.")
    if inicio_run == fin_run:
        runs[inicio_run].text = (
            textos[inicio_run][:inicio_local]
            + nuevo
            + textos[fin_run][fin_local:]
        )
    else:
        runs[inicio_run].text = textos[inicio_run][:inicio_local] + nuevo
        for indice in range(inicio_run + 1, fin_run):
            runs[indice].text = ""
        runs[fin_run].text = textos[fin_run][fin_local:]
    return True


def reemplazar_marcadores_docx(
    documento: DocumentType,
    reemplazos: Mapping[str, str],
    *,
    exigir_todos: bool = True,
) -> dict[str, int]:
    """Sustituye cadenas en runs sin reemplazar el párrafo ni sus propiedades.

    Cada valor es presentación ya decidida por la capa llamadora; esta función
    no deriva meses, importes, conteos ni políticas de negocio.
    """

    if any(not marcador for marcador in reemplazos):
        raise ErrorMarcadorDocx("Los marcadores de origen no pueden estar vacíos.")
    for marcador, valor in reemplazos.items():
        nuevo = str(valor)
        if marcador != nuevo and marcador in nuevo:
            raise ErrorMarcadorDocx(
                f"El reemplazo de '{marcador}' vuelve a contener el marcador original."
            )

    conteos = {marcador: 0 for marcador in reemplazos}
    parrafos = tuple(iterar_parrafos(documento))
    for marcador in sorted(reemplazos, key=len, reverse=True):
        nuevo = str(reemplazos[marcador])
        if marcador == nuevo:
            # Un periodo idéntico es válido. Se cuenta como encontrado para
            # mantener la validación de marcadores obligatorios, sin mutar runs.
            conteos[marcador] = sum(
                "".join(run.text for run in parrafo.runs).count(marcador)
                for parrafo in parrafos
            )
            continue
        for parrafo in parrafos:
            while _reemplazar_primera_coincidencia(parrafo, marcador, nuevo):
                conteos[marcador] += 1
    faltantes = [marcador for marcador, cantidad in conteos.items() if cantidad == 0]
    if exigir_todos and faltantes:
        raise ErrorMarcadorDocx(
            "No se encontraron los marcadores requeridos: " + ", ".join(faltantes)
        )
    return conteos


def quitar_subrayado_titulos_graficos_docx(documento: DocumentType) -> tuple[str, ...]:
    """Quita el subrayado directo de los tres títulos acordados.

    Solo cambia la propiedad de subrayado de los runs de esos párrafos; conserva
    fuente, tamaño, color, negrita, resaltado y estructura del documento.
    """

    quitados: list[str] = []
    titulos = set(TITULOS_GRAFICOS_SIN_SUBRAYADO)
    for parrafo in iterar_parrafos(documento):
        titulo = parrafo.text.strip()
        if titulo not in titulos:
            continue
        for run in parrafo.runs:
            run.font.underline = False
        quitados.append(titulo)
    faltantes = titulos - set(quitados)
    if faltantes:
        raise ErrorMarcadorDocx(
            "No se encontraron los títulos de gráfico esperados: "
            + ", ".join(sorted(faltantes))
        )
    return tuple(quitados)
