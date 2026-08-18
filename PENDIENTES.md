# Pendientes

Todo lo que falta para que el radar quede al 100%, en formato de campos por
rellenar. **Editas este archivo, me lo pasas, y yo lo aplico al código.**

No hace falta que lo llenes entero ni en orden. Cada bloque es independiente y
todos tienen un valor por omisión que ya funciona: lo que está pendiente
mejora el resultado, no lo desbloquea.

---

## ⚡ Lo que tienes que hacer tú, en orden

Ninguno es obligatorio: el radar ya corre y avisa solo. Cada paso mejora lo
que llega.

| # | Qué | Dónde | Cuánto demora | Sin esto… |
|---|---|---|---|---|
| **1** | **Probar [`gestion.yml`](gestion.yml)** con el primer aviso que mires: su código `#ABC12` + `estado: visita` o `descartado` | Lápiz ✏️ en GitHub | 2 min | Los que ya viste siguen compitiendo por tu atención en el tablero. |
| 2 | Pegar la URL de las corredoras que apuntan a su portada — lista con su estado real más abajo (bloque 2) | Navegador | 2 min c/u | Traen lo que muestre su home, no arriendos de Vitacura. |
| ~~3~~ | ~~El Sport Francés~~ | — | — | ✅ Confirmado por ti: es ese punto. |
| ~~4~~ | ~~Fecha de entrada y segundo punto~~ | — | — | ✅ Contestado: fecha indiferente, sin segundo punto por ahora. |

### Cómo pegar una URL de portal (el paso 1)

Entras al sitio, filtras **arriendo + departamento + Vitacura**, y me pasas la
URL que queda en la barra de direcciones. Eso es todo. Cada una que pegues
suma inventario que hoy no se está mirando.

<details><summary>El paso a paso del bot de Telegram, por si hay que rehacerlo</summary>

1. En el teléfono, abre Telegram y busca **@BotFather**.
2. Mándale `/newbot`. Te va a pedir dos cosas:
   - un **nombre** (lo que se ve): por ejemplo `Radar Arriendos Vitacura`
   - un **usuario** que termine en `bot`: por ejemplo `arriendos_vitacura_bot`
3. Te responde con un **token** largo, con esta forma:
   `8123456789:AAH8x...`. Ese es `TELEGRAM_TOKEN_ARRIENDOS`.
4. **Búscalo por su usuario y mándale un "hola".** Este paso se salta siempre
   y es obligatorio: un bot no puede escribirle primero a nadie en Telegram,
   así que sin tu mensaje el envío falla con `chat not found`.
5. Para el chat id, abre en el navegador del teléfono:
   `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
   y busca `"chat":{"id":123456789`. Ese número es
   `TELEGRAM_CHAT_ID_ARRIENDOS`.
6. En GitHub: **Settings → Secrets and variables → Actions → New repository
   secret**, y guarda los dos con esos nombres exactos.
7. **Actions → Probar aviso de Telegram → Run workflow.** Si llega el mensaje,
   listo.

> ⚠️ **Bot NUEVO, distinto del de remates.** Los secrets llevan sufijo
> `_ARRIENDOS` justamente para que no se crucen, y el código **no** cae al
> nombre genérico si faltan: prefiere fallar con un mensaje claro antes que
> mandarle tus arriendos al chat equivocado.

</details>

### Lo que YA no tienes que hacer

Dos cosas que estaban en esta lista y se resolvieron solas:

- ~~Crear el bot de Telegram~~ ✅ **Listo**, y la prueba de envío pasó.
- ~~Correr la calibración~~ ✅ **Ya corrió.** El radar se ejecutó contra los 39
  portales el 16 de agosto: 633 avisos, 328 únicos, 91 candidatos. El detalle
  fuente por fuente, con cuáles tienen resultados de Vitacura y cuáles no,
  está en **[`FUENTES.md`](FUENTES.md)**.
- ~~Decidir si el tope incluye gastos comunes~~ ✅ **Ya lo contestaste**
  ("solo de arriendo, no incluye ggcc"). Ver el bloque 4.

**Cómo leer las marcas:**

| | |
|---|---|
| 🔴 | Bloquea que lleguen los avisos. Sin esto el radar corre pero no avisa. |
| 🟠 | El radar funciona pero se está perdiendo inventario. |
| 🟡 | Mejora la calidad de lo que llega. |
| ⚪ | Decisión de gusto. Lo que hay ahora es una suposición mía. |

---

## ~~🔴 1. Telegram~~ ✅ FUNCIONANDO

Los dos secrets están guardados y **la prueba de envío pasó**
(`Actions → Probar aviso de Telegram`, 17-08-2026). El radar ya puede avisarte.

```
TELEGRAM_TOKEN_ARRIENDOS    ✅ configurado
TELEGRAM_CHAT_ID_ARRIENDOS  ✅ configurado
```

Si alguna vez dejan de llegar avisos, el orden para revisar es:

1. `Actions → Probar aviso de Telegram`. Si llega, el canal está bien y el
   problema es que no hay inventario nuevo — mira `logs/ultima-corrida.md`.
2. Si no llega, mira el log del job: el radar imprime el motivo exacto que
   devuelve Telegram ("chat not found", "bot was blocked by the user"), que es
   la diferencia entre arreglarlo y adivinar.
3. Si bloqueaste el bot sin querer, basta con volver a escribirle.

---

## 🟠 2. Las corredoras que apuntan a su portada

El catálogo tiene ahora **41 portales, 39 activos**. Se dividen en dos grupos
y la diferencia importa:

| | Cuántas | Qué se verificó |
|---|---|---|
| ✔︎ **Confirmadas** | 20 | Se vio la URL exacta del listado filtrado |
| ? **Por calibrar** | 19 | Solo que **el dominio existe** (resuelve por DNS) |

Las 19 apuntan a la **raíz** del sitio, no a una ruta inventada. Es
deliberado: la raíz siempre carga y deja el HTML guardado, así que después se
puede leer y escribir la ruta buena. Una ruta inventada da 404, que en el
reporte se ve igual que un sitio caído y no deja nada con qué trabajar.

Entre ellas van **Property Partners** (la que pediste) y cuatro que ya vi
publicando avisos reales de Vitacura en Economicos, Mitula y GoPlaceIt:
Magnolia Property, Portilla Propiedades, Nativo Propiedades y MaxRenta.

**Lo que necesito:** para las que te importen, abres el sitio, filtras a mano
por *arriendo + departamento + Vitacura*, y copias la URL.

**El diagnóstico del 18-08 (bajó la página real de cada una desde el runner)
ya midió cuáles vale la pena pedir y cuáles no.** Prioriza así:

**Sí vale la pena pegar su URL** — el sitio responde, solo le falta la ruta:

```yaml
century21:           ______________________________________________
magnoliaproperty:    ______________________________________________
zentagroup:          ______________________________________________
colliers:            ______________________________________________
enlaceinmobiliario:  ______________________________________________
rentas_cl:           ______________________________________________
arriendos_cl:        ______________________________________________
clasificados_cl:     ______________________________________________
busconido:           _____________________________ (¿/departamentos/vitacura?)
```

**Con URL y todo, algo ya entregan** (poco, pero real): propertypartners (4),
propiedades_cl (6), nativopropiedades (8), portillapropiedades (2) — la ruta
filtrada les multiplicaría el rendimiento.

**No va a servir pegarles URL** — problema del sitio, no de la ruta:

| Fuente | Qué pasa |
|---|---|
| contempora | no responde (timeout, también desde el runner) |
| arriendoasegurado | corta la conexión: bloqueo anti-bot |
| sothebys | responde 403 a todo |
| inciti | responde una página de 2 KB (cascarón) |
| maxrenta | no respondió a tiempo en el diagnóstico |
| inmuebles_cl, capitalizarme, emol | su robots.txt no permite el acceso — se respeta |
| zoominmobiliario | el dominio no resuelve en DNS: **apagada**. Si te abre a ti, dime con qué dominio |

Si alguna de las muertas te importa mucho, dímelo: se puede probar con
navegador o revisar si cambiaron de dominio.

**¿Se te ocurre alguna otra corredora del sector?** Las que publican solo en
su propio sitio son las que más valen — es la razón por la que este radar
existe.

```
______________________________________________
______________________________________________
______________________________________________
```

---

## ~~🟠 3. Calibración~~ ✅ YA CORRIÓ

El radar se ejecutó contra los 39 portales el 16-08-2026 y entregó: **633
avisos leídos, 328 únicos después de deduplicar, 91 que pasan tus filtros.**

El resultado fuente por fuente —cuáles tienen resultados de Vitacura, cuántos,
y por qué las que no— está en **[`FUENTES.md`](FUENTES.md)**. En resumen:

| | |
|---|---|
| ✅ Entregan Vitacura | 15 portales |
| ⚠️ Entregan, pero nada de Vitacura | 7 (la URL o el filtro está mal) |
| ❌ No entregaron nada | 17 (14 son las corredoras del bloque 2) |

Revisar esos 633 avisos reales encontró tres errores que ningún test escrito a
mano iba a mostrar —51 departamentos descartados en silencio, los avisos sin
precio quedándose con los primeros lugares, y gastos comunes leídos como
arriendo— y los tres ya están corregidos. El mismo corpus pasa de 68 a 91
candidatos.

Para volver a correrla cuando quieras: **Actions → Calibrar fuentes**.

---

## ~~🟡 4. El presupuesto~~ ✅ RESUELTO Y ACTUALIZADO

**Tope: $1.700.000** (subido de 1,6 el 17-08-2026, a pedido tuyo).

**Sobre el canon solo, sin gastos comunes**, como dijiste: *"solo de arriendo,
no incluye ggcc"*.

```yaml
requisitos:
  arriendo_clp: {max: 1700000, holgura_pct: 12}
  comparar: arriendo          # el tope mira el canon, no canon + gastos comunes
```

Cómo funcionan los dos números juntos:

```
Hasta $1.700.000    puntaje completo en el rubro precio
Hasta $1.904.000    entra igual, con el puntaje penalizado y la alerta
                    diciendo cuánto se pasa
Sobre $1.904.000    se descarta
```

Un departamento de $1.650.000 + $380.000 de gastos comunes **entra**, aunque en
la práctica cueste $2.030.000 al mes. Los gastos comunes igual se calculan, se
muestran en la alerta y puntúan —entre dos departamentos iguales gana el de
gastos comunes bajos— pero no descartan.

**Para calibrar esto tienes un dato nuevo que antes no existía:** el canon
mediano de lo que el radar ha visto en Vitacura. Sale de `alertas/historial.md`
y ahora también aparece en cada aviso ("12% bajo la mediana del mercado"). Con
la mediana cerca de $1,5 millones, un tope de 1,7 es cómodo; si empieza a
llegar ruido caro, lo primero que conviene bajar es la holgura, no el tope.

Si quieres moverlos:

```
Tope duro:        $1.700.000   (cámbialo si quieres: ____________)
Holgura:          12%          (cámbiala si quieres: ____________)
```

---

## 🟡 5. Datos tuyos que mejoran el filtro

Ninguno es obligatorio. Cada uno hace que el radar acierte más.

**~~El Sport Francés~~** ✅ **Confirmado por ti el 18-08**: "el sport es ese
punto". El ancla queda en `lat: -33.381591, lon: -70.562037`.

**~~¿Desde cuándo lo necesitas?~~** ✅ **Contestado el 18-08**: "desde cuando
sea la fecha" — la disponibilidad no filtra ni puntúa, cualquier fecha sirve.

**Mascotas.** Lo detecto y lo muestro, pero no filtra ni puntúa.

- [ ] Tengo mascota → conviene penalizar los que dicen "no acepta"
- [ ] No aplica

**Amoblado.** Ahora no prefiere ninguno de los dos, así que compiten en
igualdad. Si tienes preferencia lo puntúo.

- [ ] Sin amoblar
- [ ] Amoblado
- [ ] Me da lo mismo

**Estacionamientos.** Ahora prefiere 2 o más. ¿Cuántos necesitas de verdad?

```
Mínimo aceptable: ______    Ideal: ______
```

---

## ⚪ 6. Cosas que decidí yo y quizás no comparten

Todas están funcionando así hoy. Ninguna es difícil de cambiar; las listo
porque son suposiciones mías, no cosas que hayas pedido.

**"3 piezas" = 3 dormitorios, sin contar la pieza de servicio.** Un
departamento de 2 dormitorios + pieza de servicio se descarta. Me pareció lo
correcto —cuando alguien dice 3 piezas está pensando en dormitorios de la
familia, no en los 6 m² detrás de la cocina— pero es una interpretación.

- [ ] De acuerdo
- [ ] No: la pieza de servicio sí debería contar

**"Más de 100 m²" es estricto y sin tolerancia.** 100,0 exactos no pasan;
96 m² tampoco. Fue por el "sí o sí" del pedido.

- [ ] De acuerdo
- [ ] Aflojar a: ______ m², o aceptar hasta ______ m² de tolerancia

**Casas no, solo departamentos.** El pedido decía "arriendos de departamento".

- [ ] De acuerdo
- [ ] Incluir casas también

**Máximo 8 alertas por corrida.** Para que la primera corrida no te mande
cuarenta mensajes seguidos a las 8 de la mañana. El resto queda en el tablero
y alerta en la corrida siguiente.

- [ ] De acuerdo
- [ ] Cambiar a: ______

**Corre 3 veces al día, 8:00, 13:00 y 19:00 de Chile** (la del almuerzo la
pediste el 17-08).

- [ ] De acuerdo
- [ ] Cambiar a: ______________________

**Los metabuscadores están encendidos** (Trovit, Nuroa, Mitula, Nestoria). No
tienen inventario propio: agregan el de todos. Los dejé porque pescan
corredoras chicas, y filtro los avisos que apuntan a Portal Inmobiliario para
no duplicarte lo que ya tienes.

- [ ] De acuerdo
- [ ] Apágalos, prefiero solo portales con inventario propio

**Hey, Engel & Völkers y Houm están INCLUIDOS.** Leí tu mensaje como
"complementar con todas las páginas que no sean Portal Inmobiliario, como
hey, engel volkers, houm". Si querías lo contrario —excluir esos tres— es
cambiar tres líneas.

- [ ] Bien, inclúyelos
- [ ] No: sácalos

---

## ⚪ 7. Lo que yo mejoraría ahora, en orden

No están hechas. Van ordenadas por cuánto cambian el resultado, no por
cuánto cuestan.

### ~~1. Traer el valor de la UF del día~~ ✅ HECHO

Cascada: variable `VALOR_UF` → API pública del día → caché de la última
corrida → constante. La bitácora dice cuál se usó, porque eso explica que un
aviso quede justo a un lado u otro del tope.

### ~~2. Que la corrida no muera por tiempo~~ ✅ HECHO

Con 39 fuentes el peor caso eran 54 minutos contra un job de 30. Ahora se
corta sola a los 18 y avisa qué quedó sin mirar.

### ~~2b. Paralelizar las fuentes~~ ✅ HECHO

Cuatro fuentes a la vez. El caso normal baja de unos 12 minutos a unos 4, y el
peor caso deja de acercarse al techo del job: una fuente colgada ya no le come
el turno a las otras 38.

Cada portal sigue recibiendo un request a la vez y espaciado igual que antes
—el límite de velocidad es por sitio— así que no se le carga más la mano a
nadie. Se puede volver al modo serie con `--hilos 1` si algún portal se pone
difícil.

### ~~3. Historial de precios por departamento~~ ✅ HECHO

El aviso ahora dice "2 bajas: -10% desde $1.650.000" bajo la línea del
precio, y la ficha trae la tabla con fechas. Solo se anota cuando el precio
cambia, así que el estado no se infla.

### 4. Polígono de Vitacura en vez del nombre de la comuna 🟡

Hoy la zona se decide por el texto "Vitacura" en el aviso, con las
coordenadas como apoyo. Con el polígono real de la comuna, las coordenadas
decidirían solas y dejaría de importar si el portal escribió bien la comuna.
Quita toda una clase de errores.

- [ ] Hazlo

### ~~5. Un resumen cuando hay muchas alertas~~ ✅ HECHO

Los 8 mejores llegan en detalle y el resto en UN mensaje índice ("📋 Además
calificaron N más"), una línea por departamento. Antes el tope era un recorte
silencioso: si el noveno era justo el bueno, la única forma de saberlo era
abrir el tablero por iniciativa propia.

### ~~6. Aviso de "se fue del mercado"~~ ✅ HECHO

Ya se **detecta y se guarda**: el historial de búsquedas anota una baja cuando
un aviso falta tres corridas seguidas y su portal sí entregó en esas corridas
(esa segunda condición es la que evita dar por arrendados a cuarenta
departamentos cada vez que un sitio se cae una tarde). De ahí sale la línea
"días publicado antes de irse" del historial.

Y ahora también **se avisa por Telegram**: cuando un departamento que te
habíamos mandado deja de aparecer, llega un mensaje "📤 Se fueron del mercado"
con cuántos días estuvo publicado y a qué precio. Solo para los avisados: de
los demás nadie está esperando noticias.

### ~~7. Estimar los gastos comunes cuando no se publican~~ ✅ HECHO

Con la mediana de $/m² de los avisos del historial que SÍ los publican:
"GC no publicados, típico en la zona ≈$180.000". Marcado como estimación con
≈ — un dato deducido presentado como publicado es peor que uno ausente — y
solo con 4+ datos, como todas las medianas del radar.

### ~~8. Un segundo punto de referencia~~ ✅ NO HAY, POR AHORA

Contestado el 18-08: "no hay otro punto de referencia aún". El Sport Francés
queda como único ancla. Si algún día aparece uno (oficina, colegio), se suma
como criterio en un rato — basta con decirlo.

```
Otras ideas tuyas:
______________________________________________
______________________________________________
```

---

## Estado actual, para que sepas qué está probado y qué no

| Pieza | Estado |
|---|---|
| Parser, scoring, deduplicación, alertas, fichas | ✅ 513 tests, todos sin red |
| Extractores auditados contra las páginas reales | ✅ diagnóstico del 17-08 (`Actions → Diagnóstico de cobertura`) |
| URLs de los 20 portales activos | ✅ confirmadas una por una |
| Que el extractor entienda cada portal | ✅ 15 fuentes entregando Vitacura ([FUENTES.md](FUENTES.md)) |
| Telegram | ✅ **funcionando**, prueba de envío OK |
| Las 3 corredoras apagadas | ⚠️ **falta la URL** (bloque 2) |
| Calibración contra los portales reales | ✅ corrió: 633 avisos, 91 candidatos |
| Barrido en paralelo y presupuesto de tiempo | ✅ **probado en producción: 203s vs 536s en serie** |
| Primeros avisos por Telegram | ✅ **8 enviados en la corrida del 17-08** |
| Historial de búsquedas | ✅ funciona, pero se llena solo con las corridas |

Todo lo demás corre solo.
