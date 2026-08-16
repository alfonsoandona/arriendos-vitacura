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

## 🟠 2. Las tres corredoras que quedaron apagadas

Busqué las URLs de los 20 portales activos y las confirmé una por una. Estas
tres no las encontré: sus fichas individuales están indexadas, pero no la URL
del buscador con los filtros puestos. Las dejé `activa: false` en vez de
inventar una URL, porque una fuente que reporta cero sin motivo ensucia el
diagnóstico de las que sí están rotas.

**Lo que necesito de cada una:** abres el sitio, filtras a mano por *arriendo
+ departamento + Vitacura*, y copias la URL que te queda en la barra.

```yaml
century21:   ______________________________________________
sothebys:    ______________________________________________
contempora:  ______________________________________________
```

Si alguna no tiene buscador con filtros, dímelo y la saco del catálogo en vez
de dejarla apagada para siempre.

**¿Se te ocurre alguna otra corredora del sector?** Las que publican solo en
su propio sitio son las que más valen — es literalmente la razón por la que
este radar existe.

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

## 🟡 4. El presupuesto: ¿1,6 millones incluyen gastos comunes?

Es la decisión que más cambia qué te llega, y la dejé en el valor que dice
literalmente tu pedido.

**Ahora mismo:** el tope de $1.600.000 se compara contra el **canon solo**. Un
departamento de $1.550.000 + $380.000 de gastos comunes entra, aunque en la
práctica cueste $1.930.000 al mes.

El costo total igual se calcula, se muestra en la alerta y puntúa —un
departamento con gastos comunes bajos gana puntos contra uno igual con gastos
altos— pero no descarta.

**Elige una:**

- [ ] **Dejarlo así.** El tope es sobre el canon. Es lo que dice el pedido.
- [ ] **Cambiarlo al costo total.** El tope de $1.600.000 aplica sobre canon +
      gastos comunes. Va a llegar bastante menos, y todo lo que llegue va a
      caber de verdad en el presupuesto.

> Es un cambio de una línea (`requisitos.comparar: total` en `perfil.yml`) y
> ya está probado en los dos modos. Dime cuál y lo dejo.

**Y el tope mismo:** ahora acepta hasta 12% sobre $1.600.000 —o sea
$1.792.000— penalizando el puntaje, con la lógica de que eso se negocia.

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

## ⚪ 7. Cosas que puedo construir si las quieres

No están hechas. Las anoto porque salieron mientras armaba esto y me parecen
las siguientes en valor.

- [ ] **Historial de precios por departamento.** Guardar cada canon visto y
      mostrar la curva en la ficha. Hoy solo detecto la baja contra el precio
      con el que te avisé; con el historial completo se vería "lleva 3 bajas
      en 2 meses", que es una señal mucho más fuerte.
- [ ] **Comparador lado a lado.** Una tabla con los 5 mejores y sus
      diferencias resaltadas, para decidir cuál visitar primero.
- [ ] **Aviso de "se fue del mercado".** Cuando un aviso que venías siguiendo
      desaparece de todos los portales, decirlo: cierra el ciclo y te dice a
      qué velocidad se mueve el mercado en tu rango.
- [ ] **Estimación de gastos comunes cuando no se publican.** A partir del
      promedio de los que sí se publican en edificios parecidos. Hoy digo "no
      publicados" y dejo el costo incompleto.
- [ ] **Filtro por línea de metro / colegios / tiempo a un punto B.** Si hay
      un segundo lugar que importa (oficina, colegio), lo puedo sumar como
      criterio junto al Sport Francés.
- [ ] **Reporte semanal.** Un resumen de qué apareció, qué bajó de precio y
      qué se arrendó, una vez por semana.

```
Otras ideas tuyas:
______________________________________________
______________________________________________
```

---

## Estado actual, para que sepas qué está probado y qué no

| Pieza | Estado |
|---|---|
| Parser, scoring, deduplicación, alertas, fichas | ✅ 301 tests, todos sin red |
| URLs de los 20 portales activos | ✅ confirmadas una por una |
| Que el extractor entienda cada portal | ⚠️ **falta calibrar** (bloque 3) |
| Telegram | ⚠️ **falta el bot** (bloque 1) |
| Las 3 corredoras apagadas | ⚠️ **falta la URL** (bloque 2) |

Todo lo demás corre solo.
