"""Identidad visual compartida del Sistema Integral de Tickets TI - INKACROPS.

Este módulo concentra la paleta, la tipografía y los componentes visuales
reutilizables (encabezado con degradado, tarjetas, indicadores y botón de
navegación). No contienen lógica de negocio: solo presentación.

La paleta sigue la referencia corporativa:

* Azul marino ``#102D68`` para la barra superior y el menú lateral.
* Azul principal ``#1769E0`` para acciones y selección.
* Fondo gris azulado muy claro ``#F4F7FC`` y superficies blancas.
* Verde, naranja y rojo para estados semánticos.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

# ---------------------------------------------------------------------------
# Tokens de diseño
# ---------------------------------------------------------------------------
FONDO = "#F4F7FC"
SUPERFICIE = "#FFFFFF"
SUPERFICIE_ALT = "#EAF2FF"
BORDE = "#DCE5F2"
BORDE_FUERTE = "#C3D2EA"

AZUL_MARINO = "#102D68"
AZUL_OSCURO = "#153A80"
AZUL_PRINCIPAL = "#1769E0"
AZUL_HOVER = "#0F55BE"
AZUL_SUAVE = "#EAF2FF"

TEXTO = "#14213D"
TEXTO_SECUNDARIO = "#64748B"
TEXTO_TENUE = "#7C8AA5"
BLANCO = "#FFFFFF"

VERDE = "#16A36A"
VERDE_SUAVE = "#E7F7F0"
NARANJA = "#F59E0B"
NARANJA_SUAVE = "#FEF3E2"
ROJO = "#DC4545"
ROJO_SUAVE = "#FDECEC"
MORADO = "#7C5CE0"
MORADO_SUAVE = "#F0ECFD"

FUENTE = "Segoe UI"
FUENTE_MONO = "Consolas"

SEMANTICA: dict[str, tuple[str, str, str]] = {
    # (fondo, texto, borde)
    "success": (VERDE_SUAVE, "#0F7A4F", "#BDEBD9"),
    "warning": (NARANJA_SUAVE, "#A96500", "#F8D9A8"),
    "danger": (ROJO_SUAVE, "#B52F2F", "#F7C5C5"),
    "info": (AZUL_SUAVE, AZUL_PRINCIPAL, "#BFD8FB"),
    "neutral": ("#F1F5F9", "#475569", "#E2E8F0"),
    "purple": (MORADO_SUAVE, "#5D40BC", "#D9CFFA"),
}


def _color_mezcla(inicio: str, fin: str, fraccion: float) -> str:
    """Interpola dos colores hexadecimales (0.0 = inicio, 1.0 = fin)."""

    fraccion = max(0.0, min(1.0, fraccion))
    a = tuple(int(inicio[i : i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(fin[i : i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(
        f"{round(x + (y - x) * fraccion):02X}" for x, y in zip(a, b)
    )


# ---------------------------------------------------------------------------
# Estilos ttk
# ---------------------------------------------------------------------------
def aplicar_estilos(raiz: tk.Misc) -> ttk.Style:
    """Configura todos los estilos ttk con la identidad corporativa."""

    estilo = ttk.Style(raiz)
    try:
        estilo.theme_use("clam")
    except tk.TclError:  # pragma: no cover - depende del sistema
        pass

    estilo.configure(
        ".",
        font=(FUENTE, 10),
        background=FONDO,
        foreground=TEXTO,
    )
    estilo.configure("TFrame", background=FONDO)
    estilo.configure("Superficie.TFrame", background=SUPERFICIE)
    estilo.configure("TLabel", background=FONDO, foreground=TEXTO)
    estilo.configure(
        "Titulo.TLabel",
        background=FONDO,
        foreground=TEXTO,
        font=(FUENTE, 18, "bold"),
    )
    estilo.configure(
        "Subtitulo.TLabel",
        background=FONDO,
        foreground=TEXTO_SECUNDARIO,
        font=(FUENTE, 9),
    )
    estilo.configure(
        "Seccion.TLabel",
        background=FONDO,
        foreground=TEXTO,
        font=(FUENTE, 13, "bold"),
    )
    estilo.configure(
        "SeccionBlanca.TLabel",
        background=SUPERFICIE,
        foreground=TEXTO,
        font=(FUENTE, 12, "bold"),
    )
    estilo.configure(
        "Tarjeta.TLabel",
        background=SUPERFICIE,
        foreground=TEXTO,
    )
    estilo.configure(
        "TarjetaSecundaria.TLabel",
        background=SUPERFICIE,
        foreground=TEXTO_SECUNDARIO,
        font=(FUENTE, 9),
    )
    estilo.configure(
        "Valor.TLabel",
        background=SUPERFICIE,
        foreground=AZUL_PRINCIPAL,
        font=(FUENTE, 20, "bold"),
    )
    estilo.configure(
        "Clave.TLabel",
        background=FONDO,
        foreground=TEXTO_SECUNDARIO,
        font=(FUENTE, 9),
    )
    estilo.configure(
        "Cabecera.TLabel",
        background=AZUL_MARINO,
        foreground=BLANCO,
        font=(FUENTE, 13, "bold"),
    )
    estilo.configure(
        "CabeceraSecundaria.TLabel",
        background=AZUL_MARINO,
        foreground="#BFD3F5",
        font=(FUENTE, 9),
    )

    # Botones ---------------------------------------------------------------
    estilo.configure(
        "TButton",
        font=(FUENTE, 10),
        padding=(12, 7),
        background=SUPERFICIE,
        foreground=TEXTO,
        bordercolor=BORDE,
        lightcolor=BORDE,
        darkcolor=BORDE,
        focuscolor=AZUL_PRINCIPAL,
    )
    estilo.map(
        "TButton",
        background=[("active", AZUL_SUAVE), ("pressed", "#DCE9FE")],
        foreground=[("disabled", "#9AA8BF")],
    )
    estilo.configure(
        "Principal.TButton",
        font=(FUENTE, 10, "bold"),
        padding=(15, 8),
        background=AZUL_PRINCIPAL,
        foreground=BLANCO,
        bordercolor=AZUL_PRINCIPAL,
        lightcolor=AZUL_PRINCIPAL,
        darkcolor=AZUL_PRINCIPAL,
        focuscolor="#93C5FD",
    )
    estilo.map(
        "Principal.TButton",
        background=[("active", AZUL_HOVER), ("pressed", "#0B479F")],
        foreground=[("active", BLANCO), ("disabled", "#D7E2F4")],
    )
    estilo.configure(
        "Verde.TButton",
        font=(FUENTE, 10, "bold"),
        padding=(15, 8),
        background=VERDE,
        foreground=BLANCO,
        bordercolor=VERDE,
        lightcolor=VERDE,
        darkcolor=VERDE,
        focuscolor="#A7F3D0",
    )
    estilo.map(
        "Verde.TButton",
        background=[("active", "#118A59"), ("pressed", "#0D7049")],
        foreground=[("active", BLANCO), ("disabled", "#D7E2F4")],
    )
    estilo.configure(
        "Naranja.TButton",
        font=(FUENTE, 10, "bold"),
        padding=(15, 8),
        background=NARANJA,
        foreground=BLANCO,
        bordercolor=NARANJA,
        lightcolor=NARANJA,
        darkcolor=NARANJA,
        focuscolor="#FDE68A",
    )
    estilo.map(
        "Naranja.TButton",
        background=[("active", "#D98A06"), ("pressed", "#B87305")],
        foreground=[("active", BLANCO), ("disabled", "#E3E9F4")],
    )
    estilo.configure(
        "Cabecera.TButton",
        font=(FUENTE, 9, "bold"),
        padding=(12, 6),
        background=AZUL_OSCURO,
        foreground=BLANCO,
        bordercolor=AZUL_OSCURO,
        lightcolor=AZUL_OSCURO,
        darkcolor=AZUL_OSCURO,
        focuscolor="#93C5FD",
    )
    estilo.map(
        "Cabecera.TButton",
        background=[("active", "#1D4B9E"), ("pressed", "#2557B0")],
        foreground=[("active", BLANCO)],
    )
    estilo.configure(
        "Enlace.TButton",
        font=(FUENTE, 9),
        padding=(4, 2),
        background=SUPERFICIE,
        foreground=AZUL_PRINCIPAL,
        bordercolor=SUPERFICIE,
        lightcolor=SUPERFICIE,
        darkcolor=SUPERFICIE,
        relief="flat",
    )
    estilo.map(
        "Enlace.TButton",
        background=[("active", AZUL_SUAVE)],
        foreground=[("active", AZUL_HOVER)],
    )

    # Pestañas: la navegación visible es la barra lateral. Las pestañas ttk
    # siguen existiendo para conservar el estado y la API interna de la app.
    estilo.configure("TNotebook", background=FONDO, borderwidth=0, tabmargins=0)
    estilo.configure("TNotebook.Tab", padding=0, borderwidth=0)
    try:
        estilo.layout("TNotebook.Tab", [])
    except tk.TclError:  # pragma: no cover
        pass

    # Tablas ---------------------------------------------------------------
    estilo.configure(
        "Treeview",
        font=(FUENTE, 10),
        rowheight=27,
        background=SUPERFICIE,
        fieldbackground=SUPERFICIE,
        foreground=TEXTO,
        bordercolor=BORDE,
        lightcolor=BORDE,
        darkcolor=BORDE,
        borderwidth=0,
    )
    estilo.configure(
        "Treeview.Heading",
        font=(FUENTE, 9, "bold"),
        background="#E8EEF8",
        foreground="#33415C",
        relief="flat",
        padding=(8, 8),
        bordercolor=BORDE,
        lightcolor=BORDE,
        darkcolor=BORDE,
    )
    estilo.map("Treeview.Heading", background=[("active", "#DDE7F5")])
    estilo.map(
        "Treeview",
        background=[("selected", "#D9E8FF")],
        foreground=[("selected", TEXTO)],
    )

    # Contenedores y campos ------------------------------------------------
    estilo.configure(
        "TLabelframe",
        background=SUPERFICIE,
        bordercolor=BORDE,
        lightcolor=BORDE,
        darkcolor=BORDE,
        relief="solid",
        borderwidth=1,
        padding=8,
    )
    estilo.configure(
        "TLabelframe.Label",
        background=SUPERFICIE,
        foreground=TEXTO,
        font=(FUENTE, 10, "bold"),
    )
    estilo.configure(
        "TEntry",
        fieldbackground=SUPERFICIE,
        foreground=TEXTO,
        bordercolor=BORDE,
        lightcolor=BORDE,
        darkcolor=BORDE,
        padding=6,
    )
    estilo.map(
        "TEntry",
        bordercolor=[("focus", AZUL_PRINCIPAL)],
        lightcolor=[("focus", AZUL_PRINCIPAL)],
        darkcolor=[("focus", AZUL_PRINCIPAL)],
    )
    estilo.configure(
        "TCombobox",
        fieldbackground=SUPERFICIE,
        background=SUPERFICIE,
        foreground=TEXTO,
        bordercolor=BORDE,
        lightcolor=BORDE,
        darkcolor=BORDE,
        arrowcolor=TEXTO_SECUNDARIO,
        padding=5,
    )
    estilo.map(
        "TCombobox",
        fieldbackground=[("readonly", SUPERFICIE)],
        bordercolor=[("focus", AZUL_PRINCIPAL)],
        lightcolor=[("focus", AZUL_PRINCIPAL)],
        darkcolor=[("focus", AZUL_PRINCIPAL)],
    )
    estilo.configure(
        "Horizontal.TProgressbar",
        troughcolor="#E1E8F4",
        background=AZUL_PRINCIPAL,
        bordercolor=BORDE,
        lightcolor=AZUL_PRINCIPAL,
        darkcolor=AZUL_PRINCIPAL,
        thickness=8,
    )
    estilo.configure(
        "TCheckbutton",
        background=SUPERFICIE,
        foreground=TEXTO,
        font=(FUENTE, 9),
        focuscolor=AZUL_PRINCIPAL,
    )
    estilo.map(
        "TCheckbutton",
        background=[("active", SUPERFICIE)],
        foreground=[("disabled", "#9AA8BF")],
    )
    estilo.configure("TPanedwindow", background=FONDO)
    estilo.configure(
        "TScrollbar",
        background="#BFCBE0",
        troughcolor=FONDO,
        bordercolor=FONDO,
        arrowcolor=TEXTO_SECUNDARIO,
        relief="flat",
    )
    return estilo


# ---------------------------------------------------------------------------
# Componentes específicos de tk (degradados y navegación)
# ---------------------------------------------------------------------------
class EncabezadoDegradado(tk.Canvas):
    """Barra superior azul marino con degradado y contenido superpuesto."""

    def __init__(self, master: tk.Misc, *, alto: int = 66) -> None:
        super().__init__(
            master,
            height=alto,
            highlightthickness=0,
            bd=0,
            bg=AZUL_MARINO,
        )
        self._alto = alto
        self._contenido = tk.Frame(self, bg=AZUL_MARINO)
        self._ventana = self.create_window(
            (0, 0), window=self._contenido, anchor="nw"
        )
        self.bind("<Configure>", self._redibujar)

    @property
    def contenido(self) -> tk.Frame:
        return self._contenido

    def _redibujar(self, _evento: tk.Event | None = None) -> None:
        ancho = max(self.winfo_width(), 1)
        alto = max(self.winfo_height(), 1)
        self.delete("degradado")
        pasos = max(24, ancho // 12)
        for indice in range(pasos):
            x0 = ancho * indice / pasos
            x1 = ancho * (indice + 1) / pasos
            color = _color_mezcla(AZUL_MARINO, "#1B4BA3", indice / pasos)
            self.create_rectangle(
                x0, 0, x1 + 1, alto, fill=color, outline="", tags="degradado"
            )
        self.tag_lower("degradado")
        self.coords(self._ventana, 0, 0)
        self.itemconfigure(self._ventana, width=ancho, height=alto)


class BotonNavegacion(tk.Frame):
    """Botón del menú lateral con icono, texto y estado activo."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        icono: str,
        texto: str,
        comando: Callable[[], None],
        fuente: str = FUENTE,
    ) -> None:
        super().__init__(master, bg=AZUL_MARINO, cursor="hand2")
        self._comando = comando
        self._activo = False
        self._icono = tk.Label(
            self,
            text=icono,
            bg=AZUL_MARINO,
            fg="#BFD3F5",
            font=(fuente, 14),
            width=3,
            anchor="center",
        )
        self._icono.pack(side="left", padx=(12, 2), pady=10)
        self._texto = tk.Label(
            self,
            text=texto,
            bg=AZUL_MARINO,
            fg="#DCE7FA",
            font=(fuente, 10),
            anchor="w",
        )
        self._texto.pack(side="left", fill="x", expand=True, padx=(4, 12))
        self._barra = tk.Frame(self, bg=AZUL_MARINO, width=4)
        self._barra.pack(side="left", fill="y")
        for widget in (self, self._icono, self._texto):
            widget.bind("<Button-1>", self._al_clic)
            widget.bind("<Enter>", self._al_entrar)
            widget.bind("<Leave>", self._al_salir)

    def _al_clic(self, _evento=None) -> None:
        self._comando()

    def _al_entrar(self, _evento=None) -> None:
        if not self._activo:
            self._pintar("#1A4388", "#FFFFFF")

    def _al_salir(self, _evento=None) -> None:
        if not self._activo:
            self._pintar(AZUL_MARINO, "#DCE7FA")

    def _pintar(self, fondo: str, color: str) -> None:
        self.configure(bg=fondo)
        self._icono.configure(bg=fondo, fg=color)
        self._texto.configure(bg=fondo, fg=color)
        self._barra.configure(bg=fondo)

    def marcar_activo(self, activo: bool) -> None:
        self._activo = activo
        if activo:
            self.configure(bg=AZUL_PRINCIPAL)
            self._icono.configure(bg=AZUL_PRINCIPAL, fg=BLANCO)
            self._texto.configure(bg=AZUL_PRINCIPAL, fg=BLANCO)
            self._barra.configure(bg="#8FC0FF")
        else:
            self._pintar(AZUL_MARINO, "#DCE7FA")
            self._barra.configure(bg=AZUL_MARINO)


class TarjetaKPI(tk.Frame):
    """Tarjeta compacta de indicador para encabezados y validaciones."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        titulo: str,
        valor: str,
        detalle: str = "",
        acento: str = AZUL_PRINCIPAL,
        fondo: str = SUPERFICIE,
        icono: str = "",
        fuente: str = FUENTE,
    ) -> None:
        super().__init__(
            master,
            bg=fondo,
            highlightbackground=BORDE,
            highlightthickness=1,
            bd=0,
        )
        tk.Frame(self, bg=acento, width=4).pack(side="left", fill="y")
        contenido = tk.Frame(self, bg=fondo)
        contenido.pack(side="left", fill="both", expand=True, padx=14, pady=10)
        fila = tk.Frame(contenido, bg=fondo)
        fila.pack(fill="x")
        if icono:
            tk.Label(
                fila,
                text=icono,
                bg=fondo,
                fg=acento,
                font=(fuente, 16),
            ).pack(side="left", padx=(0, 8))
        bloque = tk.Frame(fila, bg=fondo)
        bloque.pack(side="left", fill="x", expand=True)
        tk.Label(
            bloque,
            text=titulo.upper(),
            bg=fondo,
            fg=TEXTO_SECUNDARIO,
            font=(fuente, 8, "bold"),
            anchor="w",
        ).pack(anchor="w")
        self._valor = tk.Label(
            bloque,
            text=valor,
            bg=fondo,
            fg=TEXTO,
            font=(fuente, 17, "bold"),
            anchor="w",
        )
        self._valor.pack(anchor="w")
        self._detalle = tk.Label(
            contenido,
            text=detalle,
            bg=fondo,
            fg=TEXTO_SECUNDARIO,
            font=(fuente, 9),
            anchor="w",
            justify="left",
        )
        self._detalle.pack(fill="x", pady=(2, 0))
        self.bind(
            "<Configure>",
            lambda e: self._detalle.configure(wraplength=max(120, e.width - 50)),
        )

    def actualizar(
        self,
        *,
        valor: str | None = None,
        detalle: str | None = None,
    ) -> None:
        if valor is not None:
            self._valor.configure(text=str(valor))
        if detalle is not None:
            self._detalle.configure(text=str(detalle))


class Pill(tk.Frame):
    """Etiqueta de estado con color semántico."""

    def __init__(
        self,
        master: tk.Misc,
        texto: str,
        *,
        estilo: str = "neutral",
        punto: bool = False,
        fuente: str = FUENTE,
    ) -> None:
        fondo, color, borde = SEMANTICA.get(estilo, SEMANTICA["neutral"])
        super().__init__(
            master,
            bg=fondo,
            highlightbackground=borde,
            highlightthickness=1,
            bd=0,
        )
        self._punto = None
        if punto:
            self._punto = tk.Canvas(
                self, width=8, height=8, bg=fondo, highlightthickness=0, bd=0
            )
            self._punto.create_oval(1, 1, 7, 7, fill=color, outline="")
            self._punto.pack(side="left", padx=(8, 0))
        self._label = tk.Label(
            self,
            text=texto,
            bg=fondo,
            fg=color,
            font=(fuente, 9, "bold"),
            pady=3,
        )
        self._label.pack(side="left", padx=(4 if punto else 9, 9))

    def actualizar(self, texto: str, estilo: str = "neutral") -> None:
        fondo, color, borde = SEMANTICA.get(estilo, SEMANTICA["neutral"])
        self.configure(bg=fondo, highlightbackground=borde)
        self._label.configure(text=texto, bg=fondo, fg=color)
        if self._punto is not None:
            self._punto.configure(bg=fondo)
            self._punto.itemconfigure(1, fill=color)
