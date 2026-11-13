# IMPLEMENTACIÓN FASE 2 — Motor de normalización y búsqueda de usuarios

## Objetivo

Mejorar la identificación de usuarios con datos incorrectos, incompletos o en
distinto orden, sin fusionar automáticamente identidades distintas.

## Archivos modificados

- `core/similitud.py` (nuevo).
- `core/asignaciones.py`.
- `database/resolucion.py`.
- `config/configuracion.py`.
- `AUDITORIA_SISTEMA_ACTUAL.md` (referencia de problemas detectados).

## Componentes creados

- `core.similitud`:
  - Jaro.
  - Jaro-Winkler.
  - Levenshtein y Levenshtein normalizado.
  - Similitud por tokens sensible al orden y a nombres incompletos.
  - Contención controlada para nombres parciales.
  - `UmbralesSimilitud` configurable.
  - Clasificación `exacta`, `alta`, `intermedia`, `sin_coincidencia`.
  - `buscar_similares` con límite y umbral.

## Funcionalidades implementadas

- Normalización de mayúsculas, tildes, puntuación y espacios reutilizando el
  normalizador existente.
- Comparación combinada: se toma el mejor indicador controlado, pero una
  igualdad exacta de tokens en distinto orden devuelve `1.0` y se clasifica como
  exacta.
- Un nombre parecido pero distinto (`ANA TORRE` frente a `ANA TORRES`) se
  clasifica como `alta`, no como exacta.
- Nombres claramente distintos no se fusionan.
- Integración en `sugerencias_usuario` y en la resolución SQL para añadir el
  campo adicional `tipo` sin eliminar las claves existentes (`usuario`, `area`,
  `similitud`).
- Umbrales configurables: `UMBRAL_SIMILITUD_EXACTA`, `UMBRAL_SIMILITUD_ALTA` y
  `UMBRAL_SIMILITUD_INTERMEDIA`.

## Pruebas ejecutadas

- `PruebasSimilitud`:
  - tildes, mayúsculas y espacios;
  - orden de nombres;
  - error de escritura;
  - Levenshtein normalizada;
  - nombres distintos no se fusionan;
  - orden y límite de `buscar_similares`.
- Suite completa del proyecto.

## Resultados obtenidos

- 6 pruebas específicas de similitud: **OK**.
- Suite completa: **212 pruebas, 4 omitidas, 0 fallos**.
- `comparar("ANA TORRE", "ANA TORRES")` devuelve aproximadamente `0.983` y se
  clasifica como `alta`.
- La comparación de nombres en distinto orden devuelve `1.0`.

## Problemas encontrados

- Fue necesario ajustar expectativas de prueba: un nombre parecido no es una
  coincidencia exacta.
- El cálculo de similitud tiene un coste mayor que la igualdad exacta; se
  añadió un descarte temprano cuando las métricas de caracteres son bajas.

## Pendientes

- Ajustar los umbrales con registros reales anonimizados si el negocio lo
  requiere. Los valores actuales son configurables y no bloquean la operación.

## Compatibilidad con fases anteriores

Se conservan las claves y el formato de retorno existentes. La fase 1 sigue
funcionando porque la similitud no publica ni consume eventos. No se modifican
los datos originales de SQL ni del Excel maestro.
