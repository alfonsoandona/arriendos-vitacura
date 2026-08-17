# Pendientes

Todo lo que falta para que el radar quede al 100%, en formato de campos por
rellenar. **Editas este archivo, me lo pasas, y yo lo aplico al código.**

No hace falta que lo llenes entero ni en orden. Cada bloque es independiente y
todos tienen un valor por omisión que ya funciona: lo que está pendiente
mejora el resultado, no lo desbloquea.

**Cómo leer las marcas:**

| | |
|---|---|
| 🔴 | Bloquea que lleguen los avisos. Sin esto el radar corre pero no avisa. |
| 🟠 | El radar funciona pero se está perdiendo inventario. |
| 🟡 | Mejora la calidad de lo que llega. |
| ⚪ | Decisión de gusto. Lo que hay ahora es una suposición mía. |

---

## 🔴 1. Telegram — el bot nuevo

Es lo único que impide que te lleguen los avisos. Toma 5 minutos desde el
teléfono y el paso a paso completo está en [`AVISOS.md`](AVISOS.md).

> ⚠️ **Bot NUEVO, distinto del radar de remates.** No reutilices el token de
> `claude-code`. Los secrets acá se llaman con sufijo `_ARRIENDOS` justamente
> para que no se crucen, y no hay respaldo al nombre genérico.

Estos dos van como **secrets del repositorio**, no acá (no pegues el token en
un archivo que se commitea):

```
Settings → Secrets and variables → Actions → New repository secret

  TELEGRAM_TOKEN_ARRIENDOS    = ______________________________
  TELEGRAM_CHAT_ID_ARRIENDOS  = ______________________________
```

Cuando estén, corre **Actions → Probar aviso de Telegram** y avísame si llegó.

- [ ] Bot creado con @BotFather
- [ ] Le mandé un "hola" al bot (sin esto falla con `chat not found`)
- [ ] Los dos secrets guardados
- [ ] La prueba llegó

**¿A dónde quieres que lleguen?**

- [ ] A mi chat personal
- [ ] A un grupo (dime quiénes y lo dejo documentado)

---

## 🟠 2. Las 19 fuentes "por calibrar"

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
por *arriendo + departamento + Vitacura*, y copias la URL. Con eso pasan a
confirmadas y dejan de generar ruido.

```yaml
propertypartners:    ______________________________________________
magnoliaproperty:    ______________________________________________
portillapropiedades: ______________________________________________
nativopropiedades:   ______________________________________________
maxrenta:            ______________________________________________
century21:           ______________________________________________
sothebys:            ______________________________________________
contempora:          ______________________________________________
colliers:            ______________________________________________
zentagroup:          ______________________________________________
inciti:              ______________________________________________
enlaceinmobiliario:  ______________________________________________
arriendoasegurado:   ______________________________________________
rentas_cl:           ______________________________________________
arriendos_cl:        ______________________________________________
propiedades_cl:      ______________________________________________
inmuebles_cl:        ______________________________________________
clasificados_cl:     ______________________________________________
capitalizarme:       ______________________________________________
```

No hace falta llenarlas todas: la calibración va a decir cuáles entregan algo
y cuáles no valen la pena. Si alguna resulta que no hace arriendo
residencial, dímelo y la saco.

**¿Se te ocurre alguna otra corredora del sector?** Las que publican solo en
su propio sitio son las que más valen — es la razón por la que este radar
existe.

```
______________________________________________
______________________________________________
______________________________________________
```

---

## 🟠 3. Calibración — el paso que solo se puede hacer con internet

**Actions → Calibrar fuentes → Run workflow.**

El entorno donde escribí esto tiene la red bloqueada hacia todos los portales,
así que **pude confirmar que las URLs existen pero no que el extractor las
entienda**. Son dos cosas distintas y esta es la que falta.

Cuando corras la calibración, pásame el reporte (sale en el resumen del run,
se lee del teléfono) y yo ajusto lo que haga falta. Lo que espero encontrar:

| Lo que puede salir | Qué significa | Qué hago yo |
|---|---|---|
| `✅ entrega` | Funciona | Nada |
| `⚠️ cero resultados` | La página cargó pero no reconocí los avisos | Le escribo el `selector_card`, o le pongo `motor: navegador` |
| `❌ HTTP 403` | El sitio bloquea clientes que no son navegador | `motor: navegador` |
| `❌ DNS` | La URL cambió | La corrijo |

- [ ] Corrí la calibración
- [ ] Te pasé el reporte

---

## ~~🟡 4. El presupuesto: ¿1,6 millones incluyen gastos comunes?~~ ✅ RESUELTO

Lo respondiste: **"solo de arriendo, no incluye ggcc"**. O sea que el tope se
compara contra el **canon solo**, que es como ya estaba configurado. No hubo
que cambiar nada.

```yaml
requisitos:
  comparar: arriendo          # el tope mira el canon, no canon + gastos comunes
```

Un departamento de $1.550.000 + $380.000 de gastos comunes **entra**, aunque en
la práctica cueste $1.930.000 al mes. Los gastos comunes igual se calculan, se
muestran en la alerta y puntúan —entre dos departamentos iguales gana el de
gastos comunes bajos— pero no descartan.

**Y el tope mismo**, también confirmado: *"que el tope sea 1.6 millones pero si
es 1.65 entra porque es cercano"*. Eso ya estaba implementado como una banda de
holgura del 12%:

```
Tope duro:        $1.600.000   → puntaje completo en precio
Con holgura:      hasta $1.792.000 → entra, con el puntaje penalizado
                                     y la alerta diciendo cuánto se pasa
```

Tu ejemplo, $1.650.000, entra y llega con la línea
*"⚠️ $50.000 sobre tu tope de $1.600.000 — hay que negociar"*.

Si algún día quieres mover cualquiera de los dos números:

```
Tope duro:        $1.600.000   (cámbialo si quieres: ____________)
Holgura:          12%          (cámbiala si quieres: ____________)
```

---

## 🟡 5. Datos tuyos que mejoran el filtro

Ninguno es obligatorio. Cada uno hace que el radar acierte más.

**El Sport Francés.** Usé estas coordenadas, sacadas del perfil del radar de
remates. Si el anillo de 1,2 km te está dejando fuera algo que sí te sirve,
dime y lo muevo.

```
lat: -33.381591    lon: -70.562037     ¿correcto? [ ] sí  [ ] no: ________
```

**¿Desde cuándo lo necesitas?** No lo estoy usando y podría: un departamento
disponible en marzo no sirve igual que uno disponible ya.

```
Fecha en que necesitas entrar: ____________________
```

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

**Corre 2 veces al día, 8:00 y 19:00 de Chile.**

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

### 5. Un resumen cuando hay muchas alertas 🟡

Si califican 8 departamentos, hoy llegan 8 mensajes seguidos. Podría llegar
uno con la tabla y los 3 mejores en detalle.

- [ ] Hazlo

### 6. Aviso de "se fue del mercado" 🟡 (medio hecho)

Ya se **detecta y se guarda**: el historial de búsquedas anota una baja cuando
un aviso falta tres corridas seguidas y su portal sí entregó en esas corridas
(esa segunda condición es la que evita dar por arrendados a cuarenta
departamentos cada vez que un sitio se cae una tarde). De ahí sale la línea
"días publicado antes de irse" del historial.

Lo que falta es **avisarlo por Telegram** cuando el que se fue es uno que te
habíamos mandado. Cierra el ciclo.

- [ ] Hazlo

### 7. Estimar los gastos comunes cuando no se publican ⚪

A partir del promedio de los que sí se publican en edificios parecidos. Hoy
digo "no publicados" y el costo mensual queda incompleto — que es honesto,
pero un rango estimado sería más útil que nada.

- [ ] Hazlo

### 8. Un segundo punto de referencia ⚪

Si hay otro lugar que importe (oficina, colegio), lo sumo como criterio junto
al Sport Francés.

```
Segundo punto: ______________________________________
```

```
Otras ideas tuyas:
______________________________________________
______________________________________________
```

---

## Estado actual, para que sepas qué está probado y qué no

| Pieza | Estado |
|---|---|
| Parser, scoring, deduplicación, alertas, fichas | ✅ 404 tests, todos sin red |
| URLs de los 20 portales activos | ✅ confirmadas una por una |
| Que el extractor entienda cada portal | ⚠️ **falta calibrar** (bloque 3) |
| Telegram | ⚠️ **falta el bot** (bloque 1) |
| Las 3 corredoras apagadas | ⚠️ **falta la URL** (bloque 2) |
| Barrido en paralelo y presupuesto de tiempo | ✅ probado con hilos de verdad |
| Historial de búsquedas | ✅ funciona, pero se llena solo con las corridas |

Todo lo demás corre solo.
