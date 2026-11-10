# Gasolineras Pamplona y comarca

Web con los precios de diésel y gasolina 95 de las gasolineras de Pamplona y su comarca, ordenadas de más barata a más cara.

- **Web:** https://krumkk.github.io/gasolineras-pamplona/
- **Datos:** API oficial del Ministerio para la Transición Ecológica.
- **Actualización:** la GitHub Action `Actualizar precios` comprueba los precios cada hora y, solo cuando alguna gasolinera ha cambiado de precio, guarda `data/precios.json` y `data/historial.json`. También se puede lanzar a mano desde la pestaña *Actions → Actualizar precios → Run workflow*.

Municipios incluidos: Pamplona/Iruña, Burlada, Villava, Ansoáin, Berriozar, Berrioplano, Huarte, Valle de Egüés, Aranguren, Barañáin, Zizur Mayor, Cendea de Cizur, Orkoien, Cendea de Olza, Galar, Noáin (Valle de Elorz) y Beriáin. Se cambian en `scripts/actualizar.py` (`MUNICIPIOS`).
