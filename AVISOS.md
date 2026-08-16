# Cómo hacer que lleguen los avisos

Todo esto se hace desde el teléfono y toma unos cinco minutos. Hay que hacerlo
**una sola vez**.

Sin estos dos pasos el radar funciona igual —busca, filtra, escribe las fichas
y el tablero— pero no manda nada, y lo dice en el log de cada corrida.

---

## Paso 1 — Crear el bot (2 minutos)

1. Abre Telegram y busca **@BotFather** (el que tiene el ✅ azul).
2. Mándale `/newbot`.
3. Te va a pedir dos cosas:
   - **Un nombre**: lo que quieras. Por ejemplo `Radar Arriendos Vitacura`.
   - **Un usuario**: tiene que terminar en `bot`. Por ejemplo
     `arriendos_vitacura_bot`. Si está tomado, prueba con otro.
4. BotFather te responde con un mensaje que incluye una línea así:

   ```
   123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw
   ```

   **Ese es el `TELEGRAM_TOKEN`.** Cópialo entero, con los dos puntos incluidos.

> ⚠️ Ese token es la llave del bot. Cualquiera que lo tenga puede mandar
> mensajes como él. No lo pegues en un chat ni en un issue: va como *secret*
> del repositorio, que es lo que hacemos en el paso 3.

---

## Paso 2 — Conseguir el chat_id (1 minuto)

El token dice **quién manda** el mensaje. El chat_id dice **a dónde llega**.

1. Búscalo en Telegram por el usuario que acabas de crear
   (`@arriendos_vitacura_bot`) y ábrelo.
2. Apriétale **Start**, o mándale cualquier cosa: un "hola" basta.

   > Este paso no se puede saltar. Un bot de Telegram no puede escribirle
   > primero a nadie: la conversación la tiene que abrir la persona. Si no le
   > escribes, el envío falla con `chat not found` aunque todo lo demás esté
   > bien.

3. Ahora busca **@userinfobot** y mándale `/start`. Te responde con tu id:

   ```
   Id: 987654321
   ```

   **Ese es el `TELEGRAM_CHAT_ID`.**

<details>
<summary>Si prefieres que los avisos lleguen a un grupo</summary>

Sirve para que le lleguen a más de una persona:

1. Crea un grupo de Telegram y agrega a tu bot como miembro.
2. Manda cualquier mensaje en el grupo.
3. Abre esta URL en el navegador, reemplazando `<TOKEN>` por el tuyo:

   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```

4. Busca `"chat":{"id":-1001234567890`. **Ese número, con el signo menos
   incluido**, es el chat_id del grupo.

Los id de grupo son negativos y los de persona positivos. El signo es parte
del número.

</details>

---

## Paso 3 — Guardarlos en el repositorio (2 minutos)

Los *secrets* de GitHub son valores cifrados que los workflows pueden usar
pero que nadie puede volver a leer, ni tú.

1. En este repositorio, anda a
   **Settings → Secrets and variables → Actions**.
2. Botón **New repository secret**. Dos veces:

   | Name | Secret |
   |---|---|
   | `TELEGRAM_TOKEN` | el token del paso 1 |
   | `TELEGRAM_CHAT_ID` | el id del paso 2 |

   El nombre tiene que ir **exactamente así**, en mayúsculas y con guión bajo.

---

## Paso 4 — Comprobar que llegó

**Actions → Probar aviso de Telegram → Run workflow.**

En unos segundos deberías recibir esto:

> ✅ **Radar de Arriendos**
>
> Prueba de conexión. Si estás leyendo esto, las alertas van a llegar a esta
> conversación.

Si llegó, estás listo. El radar corre solo dos veces al día.

---

## Si no llegó

El log del workflow trae el motivo exacto que devolvió Telegram. Los tres que
pasan siempre:

| Lo que dice el log | Qué pasó | Cómo se arregla |
|---|---|---|
| `chat not found` | No le escribiste al bot | Paso 2: ábrelo en Telegram y mándale un "hola" |
| `Unauthorized` | El token está mal copiado | Vuelve a copiarlo de BotFather, entero y con los dos puntos |
| `Telegram sin configurar` | Falta algún secret, o el nombre está mal escrito | Revisa que se llamen exactamente `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID` |

---

## Qué te va a llegar

Un mensaje por departamento, de ocho líneas, pensado para decidir **si vale la
pena abrirlo** desde la pantalla de bloqueo:

> 🏠 **Alonso de Córdova 4200, depto 802**
> 📍 Vitacura · 0,3 km — 4 min caminando 🚶
> 💰 $1.480.000 + GC $195.000 = **$1.675.000**
> 📐 134 m² tot · 3D · 3B · 2E · bodega
> 🏗 10 años · piso 11 · nororiente
> 📅 disponible ya
> ⭐ 91/100
>
> 📄 Ficha completa
> 🔗 Aviso original

El número que importa es el de la línea del dinero: **$1.675.000** es lo que
de verdad se paga al mes. El aviso publica $1.480.000.

Todo lo que no cabe en el mensaje —el desglose del puntaje, en qué otros
portales está publicado y qué preguntar por teléfono— está en la **ficha
completa**, que vive en este mismo repositorio y se abre desde el link.

### Cuándo NO te va a llegar nada

Por diseño. Un aviso que llega dos veces al día y nunca dice nada enseña a
ignorarlo, y entonces el que importa también se ignora.

Hay exactamente dos excepciones:

- **Algo se rompió.** Si ninguna fuente respondió, o si una que venía
  entregando pasó a cero, te avisa. Un radar ciego no se puede distinguir de
  un mercado sin novedades a menos que alguien lo diga.
- **Pasó una semana sin ninguna alerta.** Un "sigo acá" con los números, para
  que el silencio se pueda leer.

### Cuándo te va a llegar dos veces el mismo departamento

Solo cuando cambió algo que importa:

- **Bajó el canon 4% o más.** Es la mejor señal del mercado de arriendo: un
  aviso que baja de precio lleva semanas sin arrendarse, así que sigue
  disponible y hay margen para negociar.
- **Cruzó los 45 días publicado.** Se avisa una sola vez, no todos los días.
