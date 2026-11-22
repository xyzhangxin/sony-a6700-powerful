[简体中文](README.md) | [English](README.en.md) | **Español**

> Traducción comunitaria de [README.md](README.md) correspondiente a Powerful6700 0.5.2. Si algo aquí difiere del original en chino, prevalece el original. Salvo el [aviso legal](声明与使用边界.es.md), los documentos enlazados por ahora solo están disponibles en chino.

Registro personal de aprendizaje sobre el firmware y el protocolo de mantenimiento USB de la Sony A6700. Comparte tutoriales que ya se han probado de principio a fin y la herramienta experimental **Powerful6700**. Sin relación con Sony.

Por ahora solo es compatible con la **A6700 con firmware 2.00**. Estas operaciones pueden afectar la garantía y conllevan riesgo de que la cámara no arranque y de pérdida de datos. Lee primero el [Aviso legal y límites de uso](声明与使用边界.es.md).

## Por dónde empezar

| Qué quieres hacer | Dónde ir |
|---|---|
| Usar la app de desbloqueo con un clic | [Descargar la versión de prueba](https://github.com/xyzhangxin/sony-a6700-powerful/releases) · [Guía de uso](Powerful6700使用说明.md) |
| Entender cómo una conexión USB normal obtiene ejecución root temporal | [Tutorial de root por USB](USB进入root教程.md) |
| Activar o desactivar funciones desde el código fuente | [Tutorial de escritura de funciones por USB](USB功能刷写教程.md) |
| Usar una Pico para entrar en Fastboot y leer una copia de seguridad | [Guía de hardware](硬件说明.md) · [Manual de operación](操作手册.md) |

**Powerful6700 0.5.2** se distribuye como dos instaladores independientes compilados a partir del mismo código fuente congelado: macOS (Apple Silicon, macOS 13+) y Windows x64. Ambos incluyen su propio entorno de ejecución y conservan un modo de demostración. Actualmente hay **7 funciones** disponibles (FTP, cortinilla del obturador cerrada al apagar, exposición flexible, sonido del obturador electrónico, FL2/FL3, nombres en chino para Creative Looks y **sonidos de obturador personalizados en 10 ranuras**). La app también incluye un **Mercado de sonidos de obturador** (7 sonidos de obturador predeterminados o de ejemplo, con vista previa, favoritos, importación en varios formatos, recorte de forma de onda de `<=350 ms` y reemplazo en 10 ranuras), la base unificada `flags=4..7 rev=6` que protege contra bloqueos permanentes, y una opción de "Restauración sin pérdidas" que conserva tus 10 ranuras de audio. Pixel Shift Multi Shooting y la estabilización Dynamic Active están desactivados temporalmente.

<img width="1100" alt="Pantalla de gestión de funciones de Powerful6700: interruptores independientes para FL2/FL3 y los nombres en chino de Creative Looks" src="图片/Powerful6700功能管理.png" />


Sobre la base de 0.5.1, la versión 0.5.2 actualiza la base de arranque unificada de los sonidos de obturador personalizados a `rev=6` (un fallo aislado se pone en cuarentena automáticamente y la recuperación se reintenta en el siguiente arranque en frío normal; se mantiene la compatibilidad con los recibos `rev=5` y con ambas versiones de `probe.elf`). Añade un diagnóstico que desglosa el estado nativo 8 en casos concretos y un flujo de "Restauración sin pérdidas (conservar audio)" que preserva tus 10 ranuras de audio y tus ajustes. Mantiene las estadísticas de uso anónimas y voluntarias (activadas por defecto), el envío automático de informes de fallos (sin correo de contacto), las actualizaciones dentro de la app verificadas con Ed25519 en Mac y la reversión por estado de salud. Consulta el [registro de cambios](应用/CHANGELOG.md) para más detalles.

La versión Windows x64 0.5.2 se compila junto con la de Mac a partir del mismo paquete de código fuente congelado. Incorpora el Mercado de sonidos de obturador (7 sonidos predeterminados, confirmación antes de sobrescribir las 10 ranuras, recorte de forma de onda en 64 segmentos, vista previa con `winsound` y decodificación de varios formatos con Media Foundation), el botón de Restauración sin pérdidas y la guía para el estado 8, la lista de funciones alineada a la derecha con un botón "Ir al Mercado de sonidos de obturador", y el menú y la cadena de diagnóstico unificados. La primera conexión sigue instalando WinUSB mediante un asistente de controladores de un solo uso; la app principal y las operaciones de firmware no se ejecutan con privilegios elevados. Para la instalación y los límites probados, consulta las [notas de Windows](应用/docs/WINDOWS.md) y el [registro de validación](应用/docs/VALIDATION.md).

## Plan de desarrollo y avances

- **Desarrollo de funciones:** primero, mejorar la compatibilidad y las pruebas de las cuatro funciones existentes. FL2/FL3 ya superó las pruebas de aceptación en la cámara y está integrada en la app; la estabilización Dynamic Active y otras siguen en investigación. Lo que no está verificado no se considera una función disponible.
- **Código abierto y herramientas:** publicar los tutoriales, la app y su código fuente correspondiente, y mejorar poco a poco la compatibilidad entre distintas unidades de cámara y la experiencia de conexión. La copia de seguridad y restauración completas, más plataformas y las actualizaciones de paquetes de funciones llegarán más adelante.

Consulta el [Plan de desarrollo y avances](开发计划与进展.md) para más detalles. El código fuente de la app está en [应用/](应用/) y el de la Pico en [源码/](源码/). Para ayudar a mejorar el proyecto, consulta [Cómo contribuir](参与开发.md) y [AGENTS.md](AGENTS.md).

### Nombres de carpetas

| Carpeta | Significado |
|---|---|
| `应用/` | App (código fuente, pruebas y documentación de Powerful6700) |
| `源码/` | Código fuente (protocolo del host de la Pico, scripts y código de la placa) |
| `已验证文件/` | Archivos verificados (archivos fijos de arranque Pico/RAM con fuentes, hashes y licencias) |
| `图片/` | Imágenes |

## Apoya el proyecto

Si este proyecto te resulta útil, puedes aportar voluntariamente un poco para cubrir los costos de tokens de IA y así acelerar el desarrollo y la validación.

<p align="center">
  <img src="图片/支持项目.png" alt="Apoya el proyecto: códigos QR de WeChat Pay y Alipay" width="760">
</p>

## Proyectos de referencia

- [ILCE-7M4-RE](https://github.com/DavidBuchanan314/ILCE-7M4-RE): referencia para la BootROM, el arranque con Pico y RAM Fastboot.
- [Arduino-Pico](https://github.com/earlephilhower/arduino-pico): entorno de compilación para RP2040.
- [Sony-PMCA-RE](https://github.com/ma1co/Sony-PMCA-RE): referencia para el protocolo de mantenimiento USB y la autenticación.

El código y la documentación propios del proyecto usan la licencia GPL-3.0-only. Para las fuentes de terceros y el estado de sus licencias, consulta [NOTICE](NOTICE) y los [avisos de terceros de la app](应用/THIRD_PARTY_NOTICES.md). No subas firmware, copias de seguridad privadas, datos de la cámara ni credenciales.

## Colaboradores y agradecimientos

- [@zhuorantiancheng](https://github.com/zhuorantiancheng): corrigió, en el [PR #7](https://github.com/xyzhangxin/sony-a6700-powerful/pull/7), los falsos positivos de la comprobación de compatibilidad causados por la deriva de las marcas de tiempo de inodos ext2.
- [@wanghm105](https://github.com/wanghm105): informó, desde una cámara real, que la comprobación de compatibilidad fallaba tras reiniciar la cámara, lo que ayudó a localizar el problema anterior.
