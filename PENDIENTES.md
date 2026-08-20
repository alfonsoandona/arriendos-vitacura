# Pendientes

**Estado al 20-08-2026.** El radar corre solo 3 veces al día y avisa por
Telegram. Nada de esta lista lo desbloquea: todo lo que está acá lo mejora.

Se trabaja **conversando en el chat**: tú contestas, yo edito el código y
pusheo. No necesitas editar archivos salvo que quieras.

---

## 🟢 Lo que ya está funcionando

| | |
|---|---|
| Telegram | ✅ avisa 3 veces al día (8:00, 13:00 y 19:00 de Chile) |
| Dashboard | ✅ https://alfonsoandona.github.io/arriendos-vitacura/ · mapa interactivo, se actualiza solo |
| Fuentes | ✅ 42 consultadas, ~35 entregando · 1.400 avisos crudos por corrida |
| Registro | ✅ cada corrida guarda su log completo en `logs/corridas/` |
| Gestión | ✅ `gestion.yml` listo (falta estrenarlo) |

---

## 🔴 TU LISTA — 4 cosas, ninguna toma más de 5 minutos

### Paso 1 · Estrenar la gestión (2 min) — *lo más útil que puedes hacer hoy*

Cuando mires un aviso del dashboard o de Telegram, escríbeme acá en el chat
cualquiera de estas frases. Yo la traduzco a `gestion.yml`:

```
"descarta el #FX6GA, ya se arrendó"
"llamé por el #BB6M4, visita el jueves"
"el #VQ3SD en realidad son 95 m², no 120"
"el #JUHQH lo vi, no me gustó"
```

El código lo copias tocándolo en la tabla del dashboard.

**Qué gana el radar:** un `descartado` no vuelve a sonar NUNCA; los
contactados salen marcados 📞📅 en tabla y mapa; y los datos que corrijas
**pisan** lo que dice el aviso y lo re-puntúan.

### Paso 2 · URLs de corredoras (2 min cada una)

Entras al sitio → filtras **arriendo + departamento + Vitacura** → me pegas
la URL de la barra de direcciones. Pega las que quieras, de a una o todas.

```
century21.cl           ______________________________________
zentagroup.com         ______________________________________
enlaceinmobiliario.cl  ______________________________________
arriendos.cl           ______________________________________
clasificados.cl        ______________________________________  (hoy entrega 1)
rentas.cl              ______________________________________  (hoy entrega 1)
busconido.cl           ______________________________________
```

Estas ya NO las necesitas pegar: **magnoliaproperty** entrega sola desde su
raíz, y **colliers** tiene el certificado TLS roto (es problema de ellos).

**¿Conoces otra corredora del sector?** Las que publican solo en su propio
sitio son las que más valen — es la razón por la que este radar existe.

### Paso 3 · Cuatro respuestas de perfil (1 min) — copia y contesta

```
Mascotas:           tengo / no aplica
Amoblado:           sin amoblar / amoblado / da lo mismo
Estacionamientos:   mínimo ____ , ideal ____
Pieza de servicio:  ¿cuenta como dormitorio? sí / no      (hoy: NO cuenta)
```

Hoy el radar supone: sin preferencia de amoblado, 2+ estacionamientos ideal,
pieza de servicio no cuenta. Cada respuesta afina el puntaje.

### Paso 4 · Una decisión sobre las alertas

Hoy alertan: **nuevos**, **bajas de precio ≥4%**, **"lleva N días
publicado"**, **reintentos** y **"se fueron del mercado"**.

Pero si en una corrida califican más de 8, los que no caben **no vuelven a
sonar** (solo aparecen en el mensaje "👉 Ver la lista completa"). Elige:

- [ ] Déjalo así — el click-through me sirve
- [ ] Que el que no cupo vuelva a intentar sonar en la corrida siguiente
- [ ] Sube el tope de 8 a ____ (hoy sobra 1 o 2 por corrida, no 40 como al principio)

---

## 🔧 MI LISTA — dime "dale" y las tomo, en este orden

### A · Arreglar el criterio "sí o sí" de antigüedad ⚠️ *la más importante*

**El problema, medido hoy:** de 72 candidatos vivos, **solo 5 tienen el año
de construcción (6%)**. Tu filtro duro de "menos de 30 años" no está
filtrando casi nada — los avisos sin año no se descartan, solo puntúan más
bajo. Podrías estar viendo edificios de 1975 arriba en la lista.

**La solución, ya medida:** de los 67 que no lo traen, **55 tienen link
directo a su ficha**, que es donde el año casi siempre está publicado. Hoy
el radar solo visita las fichas de los 8 que va a alertar. Si visita las de
todos los candidatos, el criterio empieza a funcionar de verdad.

**Costo:** ~1 minuto más por corrida (hoy tarda 4, con techo de 18).

### B · Polígono real de Vitacura

Hoy la comuna se decide por el texto del aviso ("Vitacura" escrito en
alguna parte) con las coordenadas de apoyo. Con el polígono real, las
coordenadas deciden solas y desaparece toda una clase de errores: avisos de
Las Condes etiquetados como Vitacura y viceversa.

### C · Fuentes que cargan pero extraen cero

Bajan su página con contenido y el extractor no reconoce nada. Ya tengo sus
HTML reales guardados en la rama `diagnostico-datos` para calibrarlas sin
volver a visitarlas:

`assetplan` · `enlaceinmobiliario` · `busconido` · `comunavitacura` ·
`zentagroup` · `century21` · `propiedades_cl`

### D · Dos fuentes que se cayeron y antes funcionaban

- **economicos** (El Mercurio): empezó a responder **403**; el reintento con
  navegador tampoco pasa. Entregaba 20 avisos.
- **doomos**: **timeouts** (3 intentos × 25s = 78 segundos perdidos por
  corrida). Entregaba 33 avisos.

### E · Apagar las fuentes muertas para no gastar tiempo

`contempora` (78s), `maxrenta` (78s) y `arriendoasegurado` (80s) se comen
casi 4 minutos de reloj entre las tres y nunca han entregado nada. Apagarlas
acorta la corrida sin perder un solo aviso.

### F · Subir la cobertura de dirección y m²

Hoy: dirección 75%, m² totales 50%, gastos comunes 30%. Los huecos están
concentrados en yapo, economicos y doomos, y se cierran leyendo sus fichas
(mismo mecanismo del punto A).

---

## 📁 Dónde queda el rastro de todo

Para cuando encontremos algo raro y haya que corregir:

| Archivo | Qué guarda |
|---|---|
| `logs/corridas/AAAA-MM-DD-HHMM.log` | el log **completo** de cada corrida, para siempre |
| `logs/corridas/AAAA-MM-DD-HHMM.md` | el resumen: qué entregó cada fuente |
| `logs/historial.jsonl` | una línea por corrida desde el día uno |
| `alertas/historial.md` | qué se avisó y cuándo |
| `alertas/casos/` | la ficha de cada aviso, aunque el aviso ya no exista |
| `state/arriendos.json` | cada aviso con su **texto crudo** — la materia prima para reproducir cualquier error |
| rama `diagnostico-datos` | el HTML real de cada portal, para depurar sin visitarlos |
