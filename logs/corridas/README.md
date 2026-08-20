# Archivo de corridas

Una carpeta por si acaso, y el "por si acaso" es el trabajo de corregir el
radar: cada corrida deja acá su **log completo** (`AAAA-MM-DD-HHMM.log`, sin
recortar) y su **resumen** (`.md`, con la tabla de qué entregó cada fuente).

Existe porque `logs/ultima-corrida.log` se pisa en cada corrida. Se podía
recuperar del historial de git, pero eso es arqueología: acá la pregunta
"¿por qué este aviso salió así el martes a las 17:00?" se contesta abriendo
un archivo.

Lo demás del rastro vive en:

| Dónde | Qué guarda |
|---|---|
| `logs/historial.jsonl` | una línea por corrida, para siempre: duración, fuentes, avisos, candidatos |
| `logs/ultima-corrida.md` | el resumen de la ÚLTIMA corrida, para mirar rápido |
| `alertas/historial.md` | qué se avisó y cuándo |
| `alertas/casos/` | la ficha de cada aviso, aunque el aviso ya no exista |
| `state/arriendos.json` | cada aviso con su TEXTO CRUDO — la materia prima para reproducir cualquier error de lectura |
