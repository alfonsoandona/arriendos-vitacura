# Pendientes

**Estado al 27-09-2026.** El radar corre solo 3 veces al día, avisa por
Telegram y publica el dashboard. Un mes de corridas sin un solo error.

Se trabaja **conversando en el chat**: tú contestas, yo edito y pusheo.

---

## 📬 Qué te llega al teléfono

**Publicaciones nuevas, y nada más.** Un departamento suena una sola vez: la
primera. No vuelve a sonar nunca — ni si baja de precio, ni si lleva meses
publicado, ni si se va del mercado. Tampoco hay resúmenes, ni latidos
semanales, ni avisos de que el radar se cayó.

Nada de eso se dejó de medir; dejó de interrumpir:

| Lo que ya no suena | Dónde se mira |
|---|---|
| bajas de canon | filtro **Bajaron** del dashboard, tabla de precios de la ficha |
| días publicado | columna **Días** del tablero y del dashboard |
| se fueron del mercado | tablero y `state/inventario.jsonl` |
| cómo salió cada corrida | `logs/corridas/`, una por corrida, completas |
| el radar se cayó | el job en rojo en Actions, que GitHub te notifica |

El silencio significa una cosa sola: **no hay publicaciones nuevas que
cumplan tu búsqueda.** Para confirmar que el canal sigue vivo:
**Actions → Probar aviso de Telegram → Run workflow.**

---

## 📊 Dónde está parado

| | |
|---|---|
| Corridas | 79 en septiembre, sin errores · 5-7 min cada una |
| Inventario | ~1.300 avisos crudos → ~500 únicos → **55 candidatos** |
| Fuentes | 28 activas de 45 registradas |
| Tests | **692**, sin red, corren en 7 segundos |

**Cobertura en los 55 candidatos:** precio 83% · dirección 63% · mapa 63% ·
**año de construcción 14%**.

---

## 🔴 LO ÚNICO ABIERTO

### El año de construcción — 14%, y ya no es culpa del lector

Se auditó ficha por ficha contra el HTML real: toctoc y mitula lo publican
rotulado y se lee bien; engelvoelkers lo escribía en prosa y se arregló;
chilepropiedades y houm **no lo publican**. El hueco es de los portales.

La apuesta para cerrarlo es **la libreta de edificios**
(`state/edificios.json`): lo que un aviso enseña sobre una dirección le sirve
a todos los avisos de esa dirección, para siempre.

**Medición del 27-09 — conoce 42 edificios (venía de 10 el 25-08) y rescató
cero.** Y el porqué vale la pena anotarlo, porque no es lo que parecía:

De los 47 candidatos sin año, 26 tienen dirección de edificio. Coinciden con
la libreta **15 avisos… y los 15 son la misma dirección: Vitacura 9976**, que
es justo la entrada anulada. Anulada con razón: entre los avisos históricos y
los de hoy, esa dirección tiene **cuatro años distintos declarados** (2015,
2003, 2018, 2003). No es un portal equivocándose — es que Vitacura 9976 no es
un solo edificio. La libreta se calla, que es exactamente para lo que está esa
regla.

O sea: el mecanismo funciona, la coincidencia existe, y le tocó la única
dirección del barrio donde la respuesta es legítimamente "no se sabe". Lo que
falta es que la libreta acumule más direcciones DISTINTAS, y eso es tiempo.

**Próxima medición: 11-10.** Si a esa fecha sigue en cero con >60 edificios,
la apuesta no está rindiendo y el paso siguiente es poblarla a la fuerza:
subir el presupuesto de fichas unas corridas para visitar inventario
completo, no solo candidatos.

---

## 👁 VIGILAR (no hay nada que arreglar)

Cuatro fuentes alternan entre entregar y dar cero, sin patrón: es su anti-bot,
no muerte. Están marcadas `entrega_variable` para que sus ceros no disparen
falsas alarmas. En septiembre:

| Fuente | Entregó en | Última entrega |
|---|---|---|
| mitula | 67 de 79 corridas | hoy |
| doomos | 60 de 79 | hoy |
| remax | 21 de 79 | 23-09 |
| economicos | 14 de 79 | 26-09 |

**El umbral es una semana completa en cero.** Ninguna lo cruza. remax es la
más floja y la que hay que mirar primero si alguna se muere de verdad.

---

## ✅ Cerrado el 27-09

- **El canal quedó en publicaciones nuevas y nada más.** Se borraron los
  cuatro mensajes que no lo eran (índice de sobrantes, despedidas, latido,
  corrida caída — este último también en el workflow, que mandaba su propio
  curl) y las dos razones de reaviso (baja de canon, días publicado).
- **El tope por corrida pasó a ser un ritmo, no un filtro.** Antes los
  sobrantes se registraban como vistos y su única aparición era el mensaje
  índice; al quitar ese índice el recorte habría sido una pérdida silenciosa
  de publicaciones nuevas. Ahora quedan como entrega pendiente y suenan en la
  corrida siguiente.
- **La limpieza es de verdad, no un `if` que apaga las llamadas**: 269 líneas
  borradas entre `telegram.py` y `store.py`, más la config `reavisar` y el
  estado `ultimo_aviso.json`. Dos guardias en `tests/test_estatico.py` — uno
  cuenta leyendo el código que solo existan dos puntos capaces de mandar, y
  está probado que muerde.
- **Código muerto fuera**: el banner de reaviso de la ficha, `hoy_utc()`, y un
  marcador histórico que era un comentario disfrazado de función invocable.
- **Cuatro llamadores abrían `perfil["comunas"]["nucleo"]` a mano**, cada uno
  con su propio manejo del caso vacío. Ahora usan el helper que ya existía.

## ✅ Antes (resumen)

toctoc recuperada · la ficha técnica que vivía ARRIBA del título · "Mts" ya no
es m² · el canon de doomos (55%→93%) · 17 direcciones que no eran direcciones
· la limpieza unificada en `limpiar_direccion` · `es_nuevo` con la URL de red
· el paso de tests de 5 min 53 s → 15 s · busconido confirmada · assetplan,
comunavitacura, zentagroup, trovit y nestoria apagadas con su motivo medido ·
libreta de edificios en producción.

---

## 🙋 TU LISTA

### Paso 1 · Estrenar la gestión (2 min)

```
"descarta el #FX6GA, ya se arrendó"
"llamé por el #BB6M4, visita el jueves"
"el #VQ3SD en realidad son 95 m²"
```

Un `descartado` no vuelve a sonar nunca; lo que corrijas **pisa** al aviso y
lo re-puntúa.

### Paso 2 · Borrar una rama que sobró (10 seg)

Existe `claude/compra-vitacura-scoring-bm4n1u` (SHA `68dbfba`), que resolvía lo
mismo del 27-09 como interruptor de perfil en vez de borrar el código. Se
revisó archivo por archivo: no tiene nada que la rama de producción no tenga,
y le faltan ~100 commits. **Yo no la puedo borrar** —el proxy de este entorno
rechaza el borrado de ramas con un 403— así que te toca:

**GitHub → branches → la papelera 🗑 al lado de esa rama.**

El SHA queda anotado acá arriba: mientras exista en el reflog de GitHub es
recuperable, así que borrarla no es definitivo.

### Paso 3 · URLs de corredoras (2 min c/u)

Entras → filtras **arriendo + departamento + Vitacura** → me pegas la URL.
Como hiciste con nuroa: eso solo ya subió sus m² de 0% a 100%.

```
century21.cl           ______________________________________
enlaceinmobiliario.cl  ______________________________________
arriendos.cl           ______________________________________
clasificados.cl        ______________________________________
rentas.cl              ______________________________________
```

**Y si ves cualquier portal que "llegue sin info", mándamelo con el link.**
Los cuatro arreglos más grandes del radar salieron de un link tuyo.

### Paso 4 · Tres respuestas de perfil (1 min)

```
Mascotas:           tengo / no aplica
Amoblado:           sin amoblar / amoblado / da lo mismo
Estacionamientos:   mínimo ____ , ideal ____
```

---

## 📁 Dónde queda el rastro

| Archivo | Qué guarda |
|---|---|
| `logs/corridas/AAAA-MM-DD-HHMM.log` | el log **completo** de cada corrida, para siempre |
| `logs/historial.jsonl` | una línea por corrida desde el día uno |
| `alertas/casos/` | la ficha de cada aviso, aunque el aviso ya no exista |
| `state/arriendos.json` | cada aviso con su **texto crudo** |
| `state/edificios.json` | la libreta: qué año se construyó cada edificio conocido |
| rama `diagnostico-datos` | el HTML real de cada portal, para depurar sin visitarlos |
