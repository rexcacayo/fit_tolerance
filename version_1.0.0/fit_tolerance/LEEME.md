# Fit Tolerance 1.0.0

Complemento para Blender 4.2 a 5.x (licencia GPL-3.0-or-later). Deja **agujeros y ejes a la medida que necesitas según el material**, abre **huecos para insertos de rosca en caliente** y bisela la base contra la **pata de elefante**. Todo en modo Objeto, sobre una copia `<modelo>_ajustado`; el original queda oculto.

## Uso (pestaña Fit Tolerance)

1. **Material**: elige PLA, PETG, ASA, ABS, TPU o PA-CF. Debajo ves cuánto pierden los agujeros y cuánto ganan los ejes al imprimir (diámetro, mm). Puedes cambiarlos y pulsar **Guardar**.
2. **Agujeros y ejes**: elige el ajuste (apretado, deslizante u holgado), pulsa **Clic en agujero o eje** y haz clic en la *pared* del cilindro. Detecta el diámetro, lo redondea a la medida nominal (o usa la que escribas) y lo compensa:
   - agujero = nominal + lo que pierde el material + holgura del ajuste (0 / 0,2 / 0,4 mm)
   - eje = nominal − lo que gana el material
   Funciona con agujeros pasantes y ciegos (el fondo no se toca) y con pivotes que salen de una cara.
3. **Insertos de rosca**: elige M2 a M5, pulsa el botón y haz clic en la cara. Abre el agujero (Ø y profundidad para insertos tipo Ruthex) con un chaflán de entrada. Avisa si no hay material suficiente debajo.
4. **Pata de elefante**: bisela hacia dentro el borde de la cara que apoya en la cama (0,3 a 0,5 mm). También abre un poco los agujeros de la base. Necesita una base plana (si no la tiene, usa Base Support).

## Calibrar tu impresora

Despliega **Calibrar con la pieza de prueba** y pulsa **Crear pieza de prueba**: una placa con agujeros y pivotes de 3, 4, 5, 6, 8 y 10 mm. Imprímela **sin compensación** con cada material, mide un agujero y su pivote con el calibre, escribe las medidas y pulsa **Guardar medidas en la tabla**.

La tabla se guarda en la carpeta de configuración de Blender (`config/fit_tolerance/materiales.json`), así que **no se pierde al actualizar** el complemento.

Valores de partida: PLA +0,14 / +0,14 mm (medidos con PLA HD Winkle); el resto son estimaciones hasta que los midas.

Orden recomendado: Mesh Doctor → Print Scale → **Fit Tolerance** → Base Support → resto.
