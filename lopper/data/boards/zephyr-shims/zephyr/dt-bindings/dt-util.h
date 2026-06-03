/* Minimal stub of Zephyr's <zephyr/dt-bindings/dt-util.h> for the
 * sdt-from-linux pipeline.
 *
 * Upstream dt-util.h pulls in <zephyr/sys/util_macro.h> which
 * transitively vendors a large chunk of Zephyr's preprocessor utility
 * library. We don't need any of that for device-inventory extraction;
 * what we DO need is the small handful of macros that dt-bindings
 * headers under <zephyr/dt-bindings/> use when expanding into property
 * values. This shim provides exactly those, as bare integer-arithmetic
 * defines.
 *
 * Added to the cpp include path BEFORE the vendored zephyr/include/
 * tree so we resolve to this file instead of the upstream chain.
 *
 * Add new macros here as new dt-bindings headers surface needs.
 */
#ifndef ZEPHYR_INCLUDE_DT_BINDINGS_DT_UTIL_H_
#define ZEPHYR_INCLUDE_DT_BINDINGS_DT_UTIL_H_

/* From <zephyr/sys/util_macro.h>. Used by arm-gic.h's IRQ_TYPE_*. */
#ifndef BIT
#define BIT(n)  (1UL << (n))
#endif

#endif
