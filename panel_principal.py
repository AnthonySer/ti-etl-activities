"""Panel principal visual del Sistema Integral de Tickets TI - INKACROPS.

Este módulo es **solo interfaz**: no importa ``core`` ni toca la lógica de
negocio. ``PanelPrincipal`` se incrusta en ``interfaz.AplicacionTk`` y recibe
los datos mediante ``actualizar_kpis`` y ``actualizar_detalle``.

También puede ejecutarse de forma independiente para revisar el diseño:

    python panel_principal.py

La clase ``DemoApp`` al final del archivo es solo el shell de demostración.

Diseño
------
* Fondo claro ``#f8fafc`` y superficies blancas con borde sutil.
* Azul corporativo ``#2563eb`` como acción primaria.
* Estados con color semántico (verde/ámbar/rojo) mediante *pills*.
* Tarjetas KPI con jerarquía título → valor → detalle → sparkline.
* Rejilla responsive: 3→2→1 columnas para tarjetas y 4→2→1 para acciones.
* Navegación por teclado y foco visible en todos los botones.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, Sequence

# ---------------------------------------------------------------------------
# Design tokens (identidad corporativa compartida)
# ---------------------------------------------------------------------------
from ui_tema import (  # noqa: E402
    AZUL_HOVER as COLOR_PRIMARIO_HOVER,
    AZUL_PRINCIPAL as COLOR_PRIMARIO,
    AZUL_SUAVE as COLOR_PRIMARIO_SUAVE,
    BORDE as COLOR_BORDE,
    BORDE_FUERTE as COLOR_BORDE_FUERTE,
    FONDO as COLOR_FONDO,
    SEMANTICA,
    SUPERFICIE as COLOR_SUPERFICIE,
    TEXTO as COLOR_TEXTO,
    TEXTO_SECUNDARIO as COLOR_TEXTO_SECUNDARIO,
    TEXTO_TENUE as COLOR_TEXTO_TENUE,
)

_FUENTE: str | None = None


def _fuente_ui(widget: tk.Misc) -> str:
    """Devuelve una sans-serif moderna disponible en el sistema."""

    global _FUENTE
    if _FUENTE is None:
        familias = set(tkfont.families(widget))
        for candidata in ("Segoe UI", "Inter", "Helvetica Neue", "Arial"):
            if candidata in familias:
                _FUENTE = candidata
                break
        else:  # pragma: no cover - depende del sistema
            _FUENTE = "TkDefaultFont"
    return _FUENTE


def _entero(valor: int | float) -> str:
    """Formato 12 847 (separador de miles visible y legible)."""

    return f"{valor:,.0f}".replace(",", " ")


def _decimal(valor: float) -> str:
    """Formato 4 320,50."""

    return (
        f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
    )


# ---------------------------------------------------------------------------
# Componentes reutilizables
# ---------------------------------------------------------------------------
class Pill(tk.Frame):
    """Etiqueta compacta de estado con color semántico y punto opcional."""

    def __init__(
        self,
        master: tk.Misc,
        texto: str,
        *,
        estilo: str = "neutral",
        punto: bool = False,
        fuente: str = "TkDefaultFont",
    ) -> None:
        fondo, color, borde = SEMANTICA[estilo]
        super().__init__(
            master,
            bg=fondo,
            highlightbackground=borde,
            highlightthickness=1,
            bd=0,
        )
        self._punto: tk.Canvas | None = None
        self._punto_item: int | None = None
        if punto:
            self._punto = tk.Canvas(
                self,
                width=8,
                height=8,
                bg=fondo,
                highlightthickness=0,
                bd=0,
            )
            self._punto_item = self._punto.create_oval(
                1, 1, 7, 7, fill=color, outline=""
            )
            self._punto.pack(side="left", padx=(9, 0))
        self._texto = tk.Label(
            self,
            text=texto,
            bg=fondo,
            fg=color,
            font=(fuente, 9, "bold"),
            pady=3,
        )
        self._texto.pack(
            side="left", padx=(5 if punto else 9, 9)
        )

    def actualizar(self, texto: str, estilo: str = "neutral") -> None:
        fondo, color, borde = SEMANTICA[estilo]
        self.configure(bg=fondo, highlightbackground=borde)
        self._texto.configure(text=texto, bg=fondo, fg=color)
        if self._punto is not None and self._punto_item is not None:
            self._punto.configure(bg=fondo)
            self._punto.itemconfigure(self._punto_item, fill=color)


class Sparkline(tk.Canvas):
    """Micrográfico sin dependencias externas, redibujado al redimensionar."""

    def __init__(
        self,
        master: tk.Misc,
        datos: Sequence[float],
        *,
        color: str,
        relleno: str,
        alto: int = 38,
    ) -> None:
        super().__init__(
            master,
            height=alto,
            bg=COLOR_SUPERFICIE,
            highlightthickness=0,
            bd=0,
        )
        self._datos = list(datos)
        self._color = color
        self._relleno = relleno
        self.bind("<Configure>", lambda _e: self._dibujar())

    def set_datos(self, datos: Sequence[float]) -> None:
        self._datos = list(datos)
        self._dibujar()

    def _dibujar(self) -> None:
        self.delete("all")
        ancho = self.winfo_width()
        alto = self.winfo_height()
        if ancho < 20 or alto < 12 or len(self._datos) < 2:
            return

        # Líneas guía muy sutiles
        for fraccion in (0.33, 0.66):
            y = alto * fraccion
            self.create_line(0, y, ancho, y, fill="#f1f5f9", width=1)

        minimo, maximo = min(self._datos), max(self._datos)
        rango = (maximo - minimo) or 1.0
        margen_x, margen_y = 3, 6
        puntos: list[float] = []
        for indice, valor in enumerate(self._datos):
            x = margen_x + indice * (ancho - 2 * margen_x) / (len(self._datos) - 1)
            y = alto - margen_y - (valor - minimo) / rango * (alto - 2 * margen_y)
            puntos.extend((x, y))

        area = [puntos[0], alto, *puntos, puntos[-2], alto]
        self.create_polygon(area, fill=self._relleno, outline="")
        self.create_line(
            *puntos,
            fill=self._color,
            width=2,
            smooth=True,
            splinesteps=18,
            capstyle="round",
            joinstyle="round",
        )
        self.create_oval(
            puntos[-2] - 3,
            puntos[-1] - 3,
            puntos[-2] + 3,
            puntos[-1] + 3,
            fill=self._color,
            outline=COLOR_SUPERFICIE,
            width=2,
        )


class TarjetaKPI(tk.Frame):
    """Tarjeta de métrica: título, chip, valor grande, detalle y sparkline."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        titulo: str,
        valor: str,
        unidad: str,
        detalle: str,
        acento: str,
        relleno: str,
        chip: str = "",
        chip_estilo: str = "neutral",
        datos: Sequence[float] = (),
        fuente: str = "TkDefaultFont",
    ) -> None:
        super().__init__(
            master,
            bg=COLOR_SUPERFICIE,
            highlightbackground=COLOR_BORDE,
            highlightthickness=1,
            bd=0,
        )
        self._fuente = fuente

        # Barra de acento lateral: identifica el KPI sin recargar la tarjeta.
        tk.Frame(self, bg=acento, width=4).pack(side="left", fill="y")

        contenido = tk.Frame(self, bg=COLOR_SUPERFICIE)
        contenido.pack(side="left", fill="both", expand=True, padx=18, pady=16)

        fila_titulo = tk.Frame(contenido, bg=COLOR_SUPERFICIE)
        fila_titulo.pack(fill="x")
        tk.Label(
            fila_titulo,
            text=titulo.upper(),
            bg=COLOR_SUPERFICIE,
            fg=COLOR_TEXTO_SECUNDARIO,
            font=(fuente, 9, "bold"),
            anchor="w",
        ).pack(side="left")
        self._chip = Pill(fila_titulo, chip, estilo=chip_estilo, fuente=fuente)
        if chip:
            self._chip.pack(side="right")
        else:
            self._chip.pack_forget()

        fila_valor = tk.Frame(contenido, bg=COLOR_SUPERFICIE)
        fila_valor.pack(fill="x", pady=(10, 0))
        self._valor = tk.Label(
            fila_valor,
            text=valor,
            bg=COLOR_SUPERFICIE,
            fg=COLOR_TEXTO,
            font=(fuente, 28, "bold"),
            anchor="w",
        )
        self._valor.pack(side="left")
        tk.Label(
            fila_valor,
            text=unidad,
            bg=COLOR_SUPERFICIE,
            fg=COLOR_TEXTO_SECUNDARIO,
            font=(fuente, 10),
            anchor="w",
        ).pack(side="left", anchor="s", padx=(8, 0), pady=(0, 6))

        self._detalle = tk.Label(
            contenido,
            text=detalle,
            bg=COLOR_SUPERFICIE,
            fg=COLOR_TEXTO_SECUNDARIO,
            font=(fuente, 9),
            anchor="w",
            justify="left",
        )
        self._detalle.pack(fill="x", pady=(4, 0))

        self._sparkline = Sparkline(
            contenido, datos, color=acento, relleno=relleno
        )
        self._sparkline.pack(fill="x", pady=(14, 0))

        # Ajusta el texto secundario al ancho real de la tarjeta.
        self.bind("<Configure>", self._al_redimensionar)

    def _al_redimensionar(self, evento: tk.Event) -> None:
        self._detalle.configure(wraplength=max(120, evento.width - 52))

    def actualizar(
        self,
        *,
        valor: str | None = None,
        detalle: str | None = None,
        chip: str | None = None,
        chip_estilo: str = "neutral",
        datos: Sequence[float] | None = None,
    ) -> None:
        if valor is not None:
            self._valor.configure(text=str(valor))
        if detalle is not None:
            self._detalle.configure(text=str(detalle))
        if chip is not None:
            if chip:
                self._chip.actualizar(chip, chip_estilo)
                self._chip.pack(side="right")
            else:
                self._chip.pack_forget()
        if datos is not None:
            self._sparkline.set_datos(datos)


class Boton(tk.Button):
    """Botón plano con jerarquía primary / secondary / ghost y hover."""

    _VARIANTES = {
        "primary": {
            "bg": COLOR_PRIMARIO,
            "fg": "#ffffff",
            "activebackground": COLOR_PRIMARIO_HOVER,
            "activeforeground": "#ffffff",
            "highlightbackground": COLOR_PRIMARIO,
            "highlightcolor": "#93c5fd",
        },
        "secondary": {
            "bg": COLOR_SUPERFICIE,
            "fg": COLOR_TEXTO,
            "activebackground": "#f1f5f9",
            "activeforeground": COLOR_TEXTO,
            "highlightbackground": COLOR_BORDE_FUERTE,
            "highlightcolor": COLOR_PRIMARIO,
        },
        "ghost": {
            "bg": COLOR_FONDO,
            "fg": COLOR_PRIMARIO,
            "activebackground": COLOR_PRIMARIO_SUAVE,
            "activeforeground": COLOR_PRIMARIO_HOVER,
            "highlightbackground": COLOR_FONDO,
            "highlightcolor": COLOR_PRIMARIO,
        },
    }

    def __init__(
        self,
        master: tk.Misc,
        *,
        texto: str,
        comando: Callable[[], None],
        variante: str = "secondary",
        fuente: str = "TkDefaultFont",
    ) -> None:
        opciones = dict(self._VARIANTES[variante])
        self._bg_normal = opciones["bg"]
        self._bg_hover = "#1e40af" if variante == "primary" else (
            COLOR_PRIMARIO_SUAVE if variante == "ghost" else "#f1f5f9"
        )
        super().__init__(
            master,
            text=texto,
            command=comando,
            relief="flat",
            overrelief="flat",
            bd=0,
            highlightthickness=1,
            font=(fuente, 10, "bold"),
            cursor="hand2",
            takefocus=True,
            padx=16,
            pady=10,
            **opciones,
        )
        self.bind("<Enter>", lambda _e: self.configure(bg=self._bg_hover))
        self.bind("<Leave>", lambda _e: self.configure(bg=self._bg_normal))
        self.bind("<Return>", lambda _e: self.invoke())
        self.bind("<KP_Enter>", lambda _e: self.invoke())


class CampoDetalle(tk.Frame):
    """Par clave/valor usado en el resumen inferior."""

    def __init__(
        self,
        master: tk.Misc,
        clave: str,
        valor: str,
        *,
        fuente: str = "TkDefaultFont",
    ) -> None:
        super().__init__(master, bg=COLOR_SUPERFICIE)
        tk.Label(
            self,
            text=clave.upper(),
            bg=COLOR_SUPERFICIE,
            fg=COLOR_TEXTO_TENUE,
            font=(fuente, 8, "bold"),
            anchor="w",
        ).pack(fill="x")
        self._valor = tk.Label(
            self,
            text=valor,
            bg=COLOR_SUPERFICIE,
            fg=COLOR_TEXTO,
            font=(fuente, 10),
            anchor="w",
            justify="left",
        )
        self._valor.pack(fill="x", pady=(4, 0))
        self.bind(
            "<Configure>",
            lambda e: self._valor.configure(wraplength=max(120, e.width - 4)),
        )

    def actualizar(self, valor: str) -> None:
        self._valor.configure(text=str(valor))


# ---------------------------------------------------------------------------
# Panel principal
# ---------------------------------------------------------------------------
class PanelPrincipal(tk.Frame):
    """Prueba de concepto del Panel principal, lista para incrustarse."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        on_cargar: Callable[[], None] | None = None,
        on_abrir_procesado: Callable[[], None] | None = None,
        on_abrir_salida: Callable[[], None] | None = None,
        on_actualizar: Callable[[], None] | None = None,
        on_auditoria: Callable[[], None] | None = None,
        texto_pie: str | None = None,
    ) -> None:
        super().__init__(master, bg=COLOR_FONDO)
        self._fuente = _fuente_ui(self)
        self._on_cargar = on_cargar or (lambda: None)
        self._on_abrir_procesado = on_abrir_procesado or (lambda: None)
        self._on_abrir_salida = on_abrir_salida or (lambda: None)
        self._on_actualizar = on_actualizar or (lambda: None)
        self._on_auditoria = on_auditoria or (lambda: None)
        self._texto_pie = texto_pie

        self._modo: tuple[int, int, int, int, int, int] | None = None
        self._tarjetas: list[TarjetaKPI] = []
        self._botones: list[Boton] = []
        self._campos: list[CampoDetalle] = []

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # Lienzo desplazable: el panel respira a cualquier alto de ventana.
        self._lienzo = tk.Canvas(
            self, bg=COLOR_FONDO, highlightthickness=0, bd=0
        )
        self._lienzo.grid(row=0, column=0, sticky="nsew")
        self._barra = tk.Scrollbar(
            self,
            orient="vertical",
            command=self._lienzo.yview,
            width=10,
            bg="#94a3b8",
            activebackground="#64748b",
            troughcolor=COLOR_FONDO,
            relief="flat",
            bd=0,
            elementborderwidth=0,
            highlightthickness=0,
        )
        self._barra.grid(row=0, column=1, sticky="ns")
        self._lienzo.configure(yscrollcommand=self._barra.set)

        self._marco = tk.Frame(self._lienzo, bg=COLOR_FONDO)
        self._ventana = self._lienzo.create_window(
            (0, 0), window=self._marco, anchor="nw"
        )
        self._pagina = tk.Frame(self._marco, bg=COLOR_FONDO)
        self._pagina.pack(fill="both", expand=True, padx=28, pady=24)
        for columna in range(4):
            self._pagina.columnconfigure(columna, weight=1)
        self._marco.bind("<Configure>", self._actualizar_region)
        self._lienzo.bind("<Configure>", self._al_redimensionar)

        self._construir_encabezado()
        self._construir_metricas()
        self._construir_acciones()
        self._construir_detalle()
        self._construir_pie()
        if self._texto_pie:
            self._lbl_pie.configure(text=self._texto_pie)
        self._lienzo.bind("<MouseWheel>", self._rueda, add="+")
        self._vincular_rueda(self._pagina)

    # -- construcción ------------------------------------------------------
    def _construir_encabezado(self) -> None:
        self._encabezado = tk.Frame(self._pagina, bg=COLOR_FONDO)
        self._encabezado.grid(row=0, column=0, columnspan=4, sticky="ew")
        self._encabezado.columnconfigure(0, weight=1)

        bloque = tk.Frame(self._encabezado, bg=COLOR_FONDO)
        bloque.grid(row=0, column=0, sticky="w")
        tk.Label(
            bloque,
            text="Panel principal",
            bg=COLOR_FONDO,
            fg=COLOR_TEXTO,
            font=(self._fuente, 22, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            bloque,
            text="Resumen operativo del motor ETL y del maestro histórico.",
            bg=COLOR_FONDO,
            fg=COLOR_TEXTO_SECUNDARIO,
            font=(self._fuente, 10),
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        self._estado_cabecera = tk.Frame(self._encabezado, bg=COLOR_FONDO)
        self._estado_cabecera.grid(row=0, column=1, sticky="e")
        self._pill_estado = Pill(
            self._estado_cabecera, "SISTEMA OPERATIVO", estilo="success",
            punto=True, fuente=self._fuente,
        )
        self._pill_estado.pack(anchor="e")
        self._lbl_actualizado = tk.Label(
            self._estado_cabecera,
            text="Actualizado hace 2 min",
            bg=COLOR_FONDO,
            fg=COLOR_TEXTO_TENUE,
            font=(self._fuente, 9),
            anchor="e",
        )
        self._lbl_actualizado.pack(anchor="e", pady=(5, 0))

    def _construir_metricas(self) -> None:
        seccion = tk.Frame(self._pagina, bg=COLOR_FONDO)
        seccion.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(20, 0))
        tk.Label(
            seccion,
            text="Métricas del ETL",
            bg=COLOR_FONDO,
            fg=COLOR_TEXTO,
            font=(self._fuente, 12, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            seccion,
            text="Indicadores de la última ejecución validada.",
            bg=COLOR_FONDO,
            fg=COLOR_TEXTO_SECUNDARIO,
            font=(self._fuente, 9),
            anchor="w",
        ).pack(anchor="w", pady=(1, 0))

        self._contenedor_tarjetas = tk.Frame(self._pagina, bg=COLOR_FONDO)
        self._contenedor_tarjetas.grid(
            row=2, column=0, columnspan=4, sticky="nsew", pady=(12, 0)
        )
        for columna in range(4):
            self._contenedor_tarjetas.columnconfigure(columna, weight=1)
        for fila in range(3):
            self._contenedor_tarjetas.rowconfigure(fila, weight=1)

        definiciones = [
            dict(
                titulo="Registros válidos",
                valor=_entero(12847),
                unidad="registros",
                detalle="98,2 % del total · 184 observados para revisión.",
                acento="#16a34a",
                relleno="#dcfce7",
                chip="+4,3 %",
                chip_estilo="success",
                datos=(48, 54, 51, 63, 60, 72, 69, 78, 75, 86, 83, 94),
            ),
            dict(
                titulo="Horas totales",
                valor=_decimal(4320.5),
                unidad="horas",
                detalle="Promedio de 2,1 h por ticket en 6 áreas.",
                acento=COLOR_PRIMARIO,
                relleno="#dbeafe",
                chip="AL DÍA",
                chip_estilo="success",
                datos=(30, 36, 34, 43, 47, 45, 53, 51, 58, 62, 59, 67),
            ),
            dict(
                titulo="Pendientes de área",
                valor=_entero(327),
                unidad="tickets",
                detalle="27 sin área asignada · 3 casos críticos.",
                acento="#d97706",
                relleno="#fef3c7",
                chip="REVISAR",
                chip_estilo="warning",
                datos=(82, 78, 80, 71, 74, 67, 63, 66, 58, 55, 51, 46),
            ),
        ]
        for indice, definicion in enumerate(definiciones):
            tarjeta = TarjetaKPI(
                self._contenedor_tarjetas, fuente=self._fuente, **definicion
            )
            tarjeta.grid(row=0, column=indice, sticky="nsew", padx=6, pady=6)
            self._tarjetas.append(tarjeta)

    def _construir_acciones(self) -> None:
        seccion = tk.Frame(self._pagina, bg=COLOR_FONDO)
        seccion.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(18, 0))
        tk.Label(
            seccion,
            text="Acciones rápidas",
            bg=COLOR_FONDO,
            fg=COLOR_TEXTO,
            font=(self._fuente, 12, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            seccion,
            text="Atajos a las tareas más frecuentes del flujo de tickets.",
            bg=COLOR_FONDO,
            fg=COLOR_TEXTO_SECUNDARIO,
            font=(self._fuente, 9),
            anchor="w",
        ).pack(anchor="w", pady=(1, 0))

        self._contenedor_acciones = tk.Frame(self._pagina, bg=COLOR_FONDO)
        self._contenedor_acciones.grid(
            row=4, column=0, columnspan=4, sticky="ew", pady=(10, 0)
        )
        for columna in range(4):
            self._contenedor_acciones.columnconfigure(columna, weight=1)

        acciones = [
            ("Cargar archivo Excel", self._on_cargar, "primary"),
            ("Abrir Excel procesado", self._on_abrir_procesado, "secondary"),
            ("Abrir carpeta de salida", self._on_abrir_salida, "secondary"),
            ("Actualizar estado", self._on_actualizar, "ghost"),
        ]
        for indice, (texto, comando, variante) in enumerate(acciones):
            boton = Boton(
                self._contenedor_acciones,
                texto=texto,
                comando=comando,
                variante=variante,
                fuente=self._fuente,
            )
            boton.grid(row=0, column=indice, sticky="ew", padx=5, pady=5)
            self._botones.append(boton)

    def _construir_detalle(self) -> None:
        self._detalle_card = tk.Frame(
            self._pagina,
            bg=COLOR_SUPERFICIE,
            highlightbackground=COLOR_BORDE,
            highlightthickness=1,
            bd=0,
        )
        self._detalle_card.grid(
            row=5, column=0, columnspan=4, sticky="nsew", pady=(16, 0)
        )
        self._detalle_card.columnconfigure(0, weight=1)
        contenido = tk.Frame(self._detalle_card, bg=COLOR_SUPERFICIE)
        contenido.grid(row=0, column=0, sticky="nsew", padx=20, pady=14)

        cabecera = tk.Frame(contenido, bg=COLOR_SUPERFICIE)
        cabecera.pack(fill="x")
        tk.Label(
            cabecera,
            text="Último procesamiento",
            bg=COLOR_SUPERFICIE,
            fg=COLOR_TEXTO,
            font=(self._fuente, 12, "bold"),
            anchor="w",
        ).pack(side="left")
        enlace = tk.Label(
            cabecera,
            text="Ver auditoría  →",
            bg=COLOR_SUPERFICIE,
            fg=COLOR_PRIMARIO,
            font=(self._fuente, 9, "bold"),
            cursor="hand2",
            takefocus=True,
        )
        enlace.pack(side="right")
        enlace.bind("<Button-1>", lambda _e: self._on_auditoria())
        enlace.bind("<Return>", lambda _e: self._on_auditoria())
        enlace.bind("<space>", lambda _e: self._on_auditoria())
        enlace.bind("<Enter>", lambda _e: enlace.configure(fg=COLOR_PRIMARIO_HOVER))
        enlace.bind("<Leave>", lambda _e: enlace.configure(fg=COLOR_PRIMARIO))
        enlace.bind(
            "<FocusIn>",
            lambda _e: enlace.configure(
                font=(self._fuente, 9, "bold", "underline")
            ),
        )
        enlace.bind(
            "<FocusOut>",
            lambda _e: enlace.configure(font=(self._fuente, 9, "bold")),
        )

        tk.Frame(contenido, bg="#f1f5f9", height=1).pack(fill="x", pady=(9, 10))

        self._campos_grid = tk.Frame(contenido, bg=COLOR_SUPERFICIE)
        self._campos_grid.pack(fill="x")
        for columna in range(4):
            self._campos_grid.columnconfigure(columna, weight=1)

        campos = [
            ("Archivo procesado", "tickets_procesados_2026_11_06.xlsx"),
            ("Hoja", "Detalle"),
            ("Periodo", "Noviembre 2026"),
            ("ID de ejecución", "20261106-1042-8F3A"),
        ]
        for indice, (clave, valor) in enumerate(campos):
            campo = CampoDetalle(
                self._campos_grid, clave, valor, fuente=self._fuente
            )
            campo.grid(row=0, column=indice, sticky="nsew", padx=(0, 16), pady=2)
            self._campos.append(campo)

        aviso = tk.Frame(
            contenido,
            bg="#f0fdf4",
            highlightbackground="#bbf7d0",
            highlightthickness=1,
            bd=0,
        )
        aviso.pack(fill="x", pady=(12, 0))
        tk.Label(
            aviso,
            text="✓",
            bg="#f0fdf4",
            fg="#15803d",
            font=(self._fuente, 10, "bold"),
        ).pack(side="left", padx=(12, 6), pady=6)
        self._lbl_aviso = tk.Label(
            aviso,
            text=(
                "Procesamiento completado sin incidencias · "
                "12 847 registros · 00:01:42"
            ),
            bg="#f0fdf4",
            fg="#15803d",
            font=(self._fuente, 9),
            anchor="w",
        )
        self._lbl_aviso.pack(side="left", fill="x", expand=True, pady=6)
        self._lbl_maestro = tk.Label(
            aviso,
            text="Maestro: 8 432 tickets",
            bg="#f0fdf4",
            fg="#166534",
            font=(self._fuente, 9, "bold"),
        )
        self._lbl_maestro.pack(side="right", padx=12, pady=6)

    def _construir_pie(self) -> None:
        pie = tk.Frame(self._pagina, bg=COLOR_FONDO)
        pie.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        self._lbl_pie = tk.Label(
            pie,
            text=(
                "Indicadores sincronizados con el estado del sistema · "
                "Tab para navegar · Espacio/Enter para activar"
            ),
            bg=COLOR_FONDO,
            fg=COLOR_TEXTO_TENUE,
            font=(self._fuente, 8),
            anchor="w",
            justify="left",
        )
        self._lbl_pie.pack(fill="x")

    # -- comportamiento responsive ----------------------------------------
    def _al_redimensionar(self, evento: tk.Event) -> None:
        ancho = evento.width
        self._lienzo.itemconfigure(self._ventana, width=ancho)

        modo = (
            3 if ancho >= 900 else 2 if ancho >= 620 else 1,
            4 if ancho >= 980 else 2 if ancho >= 560 else 1,
            4 if ancho >= 980 else 2 if ancho >= 560 else 1,
            28 if ancho >= 1024 else 22 if ancho >= 640 else 16,
            24 if ancho >= 1024 else 20 if ancho >= 640 else 16,
            1 if ancho >= 720 else 0,
        )
        if modo == self._modo:
            return
        self._modo = modo
        tarjetas, acciones, campos, padx, pady, encabezado_en_linea = modo
        self._pagina.pack_configure(padx=padx, pady=pady)
        self._reflow_tarjetas(tarjetas)
        self._reflow_botones(acciones)
        self._reflow_campos(campos)
        self._reflow_encabezado(encabezado_en_linea)
        self.after_idle(self._actualizar_region)

    def _actualizar_region(self, _evento: tk.Event | None = None) -> None:
        caja = self._lienzo.bbox("all")
        if caja:
            self._lienzo.configure(scrollregion=caja)

    def _vincular_rueda(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._rueda, add="+")
        widget.bind("<Button-4>", self._rueda_linux, add="+")
        widget.bind("<Button-5>", self._rueda_linux, add="+")
        for hijo in widget.winfo_children():
            self._vincular_rueda(hijo)

    def _puede_desplazar(self) -> bool:
        primero, ultimo = self._lienzo.yview()
        return not (primero <= 0.0 and ultimo >= 1.0)

    def _rueda(self, evento: tk.Event) -> str:
        if not self._puede_desplazar():
            return "break"
        pasos = -2 if evento.delta > 0 else 2
        self._lienzo.yview_scroll(pasos, "units")
        return "break"

    def _rueda_linux(self, evento: tk.Event) -> str:
        if not self._puede_desplazar():
            return "break"
        self._lienzo.yview_scroll(-2 if evento.num == 4 else 2, "units")
        return "break"

    def _reflow_tarjetas(self, columnas: int) -> None:
        for columna in range(4):
            self._contenedor_tarjetas.columnconfigure(columna, weight=0)
        for fila in range(3):
            self._contenedor_tarjetas.rowconfigure(fila, weight=0)
        for columna in range(columnas):
            self._contenedor_tarjetas.columnconfigure(columna, weight=1)

        fila = 0
        columna = 0
        for indice, tarjeta in enumerate(self._tarjetas):
            if columna >= columnas:
                fila += 1
                columna = 0
            # Si queda sola en la fila, ocupa todo el ancho disponible.
            restantes = len(self._tarjetas) - indice
            span = columnas - columna if restantes < columnas - columna else 1
            tarjeta.grid_configure(
                row=fila, column=columna, columnspan=span, sticky="nsew",
                padx=6, pady=6,
            )
            columna += span
        for indice in range(fila + 1):
            self._contenedor_tarjetas.rowconfigure(indice, weight=1)

    def _reflow_botones(self, columnas: int) -> None:
        for columna in range(4):
            self._contenedor_acciones.columnconfigure(columna, weight=0)
        for columna in range(columnas):
            self._contenedor_acciones.columnconfigure(columna, weight=1)
        for indice, boton in enumerate(self._botones):
            boton.grid_configure(
                row=indice // columnas,
                column=indice % columnas,
                columnspan=1,
                sticky="ew",
                padx=5,
                pady=5,
            )

    def _reflow_campos(self, columnas: int) -> None:
        for columna in range(4):
            self._campos_grid.columnconfigure(columna, weight=0)
        for columna in range(columnas):
            self._campos_grid.columnconfigure(columna, weight=1)
        for indice, campo in enumerate(self._campos):
            campo.grid_configure(
                row=indice // columnas,
                column=indice % columnas,
                sticky="nsew",
                padx=(0, 16 if indice % columnas != columnas - 1 else 0),
                pady=(0, 10) if indice // columnas < 1 else 0,
            )

    def _reflow_encabezado(self, en_linea: int) -> None:
        self._encabezado.columnconfigure(0, weight=1)
        self._encabezado.columnconfigure(1, weight=0)
        if en_linea:
            self._estado_cabecera.grid_configure(
                row=0, column=1, sticky="e", pady=0
            )
        else:
            self._estado_cabecera.grid_configure(
                row=1, column=0, sticky="w", pady=(12, 0)
            )

    # -- API pública para conectar con la lógica existente -----------------
    def actualizar_kpis(
        self,
        *,
        validos: int,
        horas: float,
        pendientes: int,
        detalle_validos: str | None = None,
        detalle_horas: str | None = None,
        detalle_pendientes: str | None = None,
        chip_validos: tuple[str, str] | None = None,
        chip_horas: tuple[str, str] | None = None,
        chip_pendientes: tuple[str, str] | None = None,
    ) -> None:
        """Actualiza las tres tarjetas desde ``core.estado``.

        ``chip_*`` recibe ``(texto, estilo_semantico)``; por ejemplo
        ``("+4,3 %", "success")``.
        """

        tarjeta_validos, tarjeta_horas, tarjeta_pendientes = self._tarjetas
        tarjeta_validos.actualizar(
            valor=_entero(validos),
            detalle=detalle_validos,
            chip=chip_validos[0] if chip_validos else None,
            chip_estilo=chip_validos[1] if chip_validos else "success",
        )
        tarjeta_horas.actualizar(
            valor=_decimal(horas),
            detalle=detalle_horas,
            chip=chip_horas[0] if chip_horas else None,
            chip_estilo=chip_horas[1] if chip_horas else "success",
        )
        tarjeta_pendientes.actualizar(
            valor=_entero(pendientes),
            detalle=detalle_pendientes,
            chip=chip_pendientes[0] if chip_pendientes else None,
            chip_estilo=chip_pendientes[1] if chip_pendientes else "warning",
        )

    def actualizar_detalle(
        self,
        *,
        archivo: str,
        hoja: str,
        periodo: str,
        id_ejecucion: str,
        aviso: str | None = None,
        maestro: str | None = None,
    ) -> None:
        valores = (archivo, hoja, periodo, id_ejecucion)
        for campo, valor in zip(self._campos, valores):
            campo.actualizar(valor)
        if aviso is not None:
            self._lbl_aviso.configure(text=aviso)
        if maestro is not None:
            self._lbl_maestro.configure(text=maestro)

    def marcar_actualizado(self, texto: str = "Ahora mismo") -> None:
        self._lbl_actualizado.configure(text=f"Actualizado {texto.lower()}")

    def set_estado_sistema(self, texto: str, estilo: str = "success") -> None:
        self._pill_estado.actualizar(texto.upper(), estilo)

    def notificar(self, texto: str) -> None:
        """Feedback no bloqueante en el pie (sustituye a un messagebox)."""

        self._lbl_pie.configure(text=texto, fg=COLOR_TEXTO_SECUNDARIO)


# ---------------------------------------------------------------------------
# Shell de demostración
# ---------------------------------------------------------------------------
class DemoApp(tk.Tk):
    """Ventana de demostración; en la app real el panel se incrusta en el Notebook."""

    def __init__(self) -> None:
        super().__init__()
        self.title("INKACROPS · Panel principal (prueba de concepto)")
        self.geometry("1240x800")
        self.minsize(820, 680)
        self.configure(bg=COLOR_FONDO)

        fuente = _fuente_ui(self)
        self._construir_barra_marca(fuente)

        self.panel = PanelPrincipal(
            self,
            on_cargar=lambda: self._accion("Cargar archivo Excel"),
            on_abrir_procesado=lambda: self._accion("Abrir Excel procesado"),
            on_abrir_salida=lambda: self._accion("Abrir carpeta de salida"),
            on_actualizar=self._actualizar,
            on_auditoria=lambda: self._accion("Ver auditoría"),
            texto_pie=(
                "Vista de demostración · Botones conectados a callbacks de ejemplo · "
                "Tab para navegar · Espacio/Enter para activar"
            ),
        )
        self.panel.pack(fill="both", expand=True)

    def _construir_barra_marca(self, fuente: str) -> None:
        barra = tk.Frame(self, bg=COLOR_SUPERFICIE, height=58)
        barra.pack(side="top", fill="x")
        barra.pack_propagate(False)

        marca = tk.Frame(barra, bg=COLOR_PRIMARIO, width=30, height=30)
        marca.pack(side="left", padx=(22, 10), pady=14)
        marca.pack_propagate(False)
        tk.Label(
            marca,
            text="TI",
            bg=COLOR_PRIMARIO,
            fg="#ffffff",
            font=(fuente, 10, "bold"),
        ).pack(expand=True)

        bloque = tk.Frame(barra, bg=COLOR_SUPERFICIE)
        bloque.pack(side="left", pady=10)
        tk.Label(
            bloque,
            text="INKACROPS",
            bg=COLOR_SUPERFICIE,
            fg=COLOR_TEXTO,
            font=(fuente, 11, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            bloque,
            text="Sistema Integral de Tickets TI",
            bg=COLOR_SUPERFICIE,
            fg=COLOR_TEXTO_SECUNDARIO,
            font=(fuente, 9),
            anchor="w",
        ).pack(anchor="w")

        operador = tk.Frame(barra, bg=COLOR_SUPERFICIE)
        operador.pack(side="right", padx=22)
        avatar = tk.Frame(operador, bg="#e0e7ff", width=28, height=28)
        avatar.pack(side="right")
        avatar.pack_propagate(False)
        tk.Label(
            avatar,
            text="AL",
            bg="#e0e7ff",
            fg="#3730a3",
            font=(fuente, 8, "bold"),
        ).pack(expand=True)
        tk.Label(
            operador,
            text="Analista TI · Administrador",
            bg=COLOR_SUPERFICIE,
            fg=COLOR_TEXTO_SECUNDARIO,
            font=(fuente, 9),
        ).pack(side="right", padx=(0, 8))

        tk.Frame(self, bg=COLOR_BORDE, height=1).pack(side="top", fill="x")

    def _accion(self, nombre: str) -> None:
        print(f"[demo] {nombre}")
        self.panel.notificar(f"Acción de ejemplo: {nombre}.")

    def _actualizar(self) -> None:
        self.panel.actualizar_kpis(
            validos=12847,
            horas=4320.5,
            pendientes=327,
            detalle_validos="98,2 % del total · 184 observados para revisión.",
            detalle_horas="Promedio de 2,1 h por ticket en 6 áreas.",
            detalle_pendientes="27 sin área asignada · 3 casos críticos.",
            chip_validos=("+4,3 %", "success"),
            chip_horas=("AL DÍA", "success"),
            chip_pendientes=("REVISAR", "warning"),
        )
        self.panel.marcar_actualizado("Ahora mismo")
        self.panel.notificar("Estado actualizado correctamente.")


def main() -> int:
    app = DemoApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
