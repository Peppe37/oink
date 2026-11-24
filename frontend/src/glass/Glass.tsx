/* The glass material.

   Every <Glass> blurs and saturates what is behind it, with a specular highlight that follows
   the pointer and a rim light. With `refract` it also BENDS the background like a lens: an SVG
   displacement map (strong near the edges, flat in the middle — how Apple's Liquid Glass
   behaves) applied to the backdrop. Browsers that cannot run SVG filters in backdrop-filter
   (Safari, Firefox) get the frosted version instead of a broken one. */

import { motion, type HTMLMotionProps, type MotionStyle } from "framer-motion";
import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from "react";

const canRefract = (() => {
  if (typeof window === "undefined") return false;
  const chromium = "chrome" in window && !/iPhone|iPad|Macintosh.*Safari(?!.*Chrome)/.test(navigator.userAgent);
  return chromium && CSS.supports("backdrop-filter", "url(#x)");
})();

/** A displacement map for a rounded rectangle: R/G = x/y offset (128 = none). Near the rim the
 *  pixels are pulled inward along the edge normal, so the backdrop looks magnified there. */
function lensMap(width: number, height: number, radius: number, bezel: number): string {
  const scale = 0.5; // half resolution is enough: the filter interpolates
  const w = Math.max(2, Math.round(width * scale));
  const h = Math.max(2, Math.round(height * scale));
  const r = radius * scale;
  const b = Math.max(2, bezel * scale);
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return "";
  const img = ctx.createImageData(w, h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      // signed distance to the rounded rectangle's edge, and the inward normal
      const cx = Math.min(Math.max(x, r), w - r);
      const cy = Math.min(Math.max(y, r), h - r);
      let nx = 0;
      let ny = 0;
      let d: number;
      if (x < r || x > w - r) {
        if (y < r || y > h - r) {
          const dx = x - cx;
          const dy = y - cy;
          const len = Math.hypot(dx, dy) || 1;
          d = r - len;
          nx = -dx / len;
          ny = -dy / len;
        } else {
          d = Math.min(x, w - x);
          nx = x < w / 2 ? 1 : -1;
        }
      } else if (y < r || y > h - r) {
        d = Math.min(y, h - y);
        ny = y < h / 2 ? 1 : -1;
      } else {
        d = Math.min(x, w - x, y, h - y);
        if (d === x) nx = 1;
        else if (d === w - x) nx = -1;
        else if (d === y) ny = 1;
        else ny = -1;
      }
      const t = Math.max(0, Math.min(1, 1 - d / b));
      const k = t * t * (3 - 2 * t); // smoothstep: soft lens, no hard ring
      const i = (y * w + x) * 4;
      img.data[i] = 128 + nx * k * 127;
      img.data[i + 1] = 128 + ny * k * 127;
      img.data[i + 2] = 128;
      img.data[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return canvas.toDataURL();
}

type Props = HTMLMotionProps<"div"> & {
  refract?: boolean;
  strength?: number;
  radius?: number;
  variant?: "pill" | "tight";
  press?: boolean;
};

export function Glass({ refract = false, strength = 38, radius = 28, variant, press, className = "", style, children, ...rest }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useId().replace(/:/g, "");
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [map, setMap] = useState("");
  const lens = refract && canRefract;

  useLayoutEffect(() => {
    if (!lens || !ref.current) return;
    const el = ref.current;
    const observer = new ResizeObserver(([entry]) => {
      const w = Math.round(entry.contentRect.width + 0.5);
      const h = Math.round(entry.contentRect.height + 0.5);
      setSize((old) => (old && Math.abs(old.w - w) < 4 && Math.abs(old.h - h) < 4 ? old : { w, h }));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [lens]);

  useEffect(() => {
    if (!size) return;
    const r = variant === "pill" ? size.h / 2 : radius;
    setMap(lensMap(size.w, size.h, Math.min(r, size.h / 2, size.w / 2), Math.min(28, size.h / 3)));
  }, [size, radius, variant]);

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    event.currentTarget.style.setProperty("--mx", `${event.clientX - box.left}px`);
    event.currentTarget.style.setProperty("--my", `${event.clientY - box.top}px`);
  };

  const filterStyle: CSSProperties =
    lens && map
      ? { backdropFilter: `url(#lg-${id}) blur(6px) saturate(185%) brightness(1.06)` }
      : {};
  const classes = ["glass", variant ?? "", press ? "press" : "", className].filter(Boolean).join(" ");

  return (
    <motion.div ref={ref} className={classes} style={{ ...(filterStyle as MotionStyle), ...style }} onPointerMove={onPointerMove} {...rest}>
      {lens && map && size && (
        <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden>
          <filter id={`lg-${id}`} x="0" y="0" width={size.w} height={size.h} filterUnits="userSpaceOnUse" colorInterpolationFilters="sRGB">
            <feImage href={map} x="0" y="0" width={size.w} height={size.h} preserveAspectRatio="none" result="map" />
            {/* Chromatic dispersion: red, green and blue bend by slightly different amounts,
                so the rim shows the faint colour fringe of real glass. */}
            <feDisplacementMap in="SourceGraphic" in2="map" scale={strength} xChannelSelector="R" yChannelSelector="G" result="dr" />
            <feDisplacementMap in="SourceGraphic" in2="map" scale={strength * 0.93} xChannelSelector="R" yChannelSelector="G" result="dg" />
            <feDisplacementMap in="SourceGraphic" in2="map" scale={strength * 0.86} xChannelSelector="R" yChannelSelector="G" result="db" />
            <feColorMatrix in="dr" type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="r" />
            <feColorMatrix in="dg" type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0" result="g" />
            <feColorMatrix in="db" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" result="b" />
            <feComposite in="r" in2="g" operator="arithmetic" k2="1" k3="1" result="rg" />
            <feComposite in="rg" in2="b" operator="arithmetic" k2="1" k3="1" />
          </filter>
        </svg>
      )}
      {children as React.ReactNode}
    </motion.div>
  );
}
