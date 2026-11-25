/* The glass material: Apple-style "liquid glass", not just a blurred transparent box.

   Every <Glass> lightly blurs and saturates what is behind it, with a specular highlight
   that follows the pointer and a lit rim. With `refract` it also BENDS the background like a
   lens: an SVG displacement map (strong at the rim, flat in the middle) with chromatic
   dispersion (red, green and blue bend by different amounts).

   - Chromium: the lens runs inside `backdrop-filter`, so everything behind refracts.
   - Safari and Firefox can't put SVG filters in `backdrop-filter`, but they can on normal
     elements: the glass draws its own copy of the aurora, aligned to the viewport and in
     phase with it, runs the lens on that copy and shows it at the rim. The centre stays a
     clear, lightly blurred window onto the content, the edges magnify and fringe. */

import { motion, type HTMLMotionProps, type MotionStyle } from "framer-motion";
import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from "react";

const backdropLens = (() => {
  if (typeof window === "undefined") return false;
  const chromium = "chrome" in window && !/iPhone|iPad|Macintosh.*Safari(?!.*Chrome)/.test(navigator.userAgent);
  return chromium && CSS.supports("backdrop-filter", "url(#x)");
})();

/** Seconds since the page started: the aurora copies use it to stay in phase. */
const T0 = performance.now();
export const auroraPhase = () => (performance.now() - T0) / 1000;

/** A displacement map for a rounded rectangle: R/G = x/y offset (128 = none). Near the rim
 *  pixels are pulled inward along the edge normal, so the background looks magnified there,
 *  as it does at the curved edge of a real slab of glass. */
function lensMap(width: number, height: number, radius: number, bezel: number): string {
  const scale = 0.5;
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
      const cx = Math.min(Math.max(x, r), w - r);
      const cy = Math.min(Math.max(y, r), h - r);
      let nx = 0;
      let ny = 0;
      let d: number;
      if ((x < r || x > w - r) && (y < r || y > h - r)) {
        const dx = x - cx;
        const dy = y - cy;
        const len = Math.hypot(dx, dy) || 1;
        d = r - len;
        nx = -dx / len;
        ny = -dy / len;
      } else {
        const dl = x, dr = w - x, dt = y, db = h - y;
        d = Math.min(dl, dr, dt, db);
        if (d === dl) nx = 1;
        else if (d === dr) nx = -1;
        else if (d === dt) ny = 1;
        else ny = -1;
      }
      // convex profile: displacement grows like a lens toward the rim (circular bezel)
      const t = Math.max(0, Math.min(1, 1 - d / b));
      const k = 1 - Math.sqrt(1 - t * t);
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

function LensFilter({ id, map, w, h, strength }: { id: string; map: string; w: number; h: number; strength: number }) {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden>
      <filter id={id} x="0" y="0" width={w} height={h} filterUnits="userSpaceOnUse" colorInterpolationFilters="sRGB">
        <feImage href={map} x="0" y="0" width={w} height={h} preserveAspectRatio="none" result="map" />
        <feDisplacementMap in="SourceGraphic" in2="map" scale={strength} xChannelSelector="R" yChannelSelector="G" result="dr" />
        <feDisplacementMap in="SourceGraphic" in2="map" scale={strength * 0.9} xChannelSelector="R" yChannelSelector="G" result="dg" />
        <feDisplacementMap in="SourceGraphic" in2="map" scale={strength * 0.8} xChannelSelector="R" yChannelSelector="G" result="db" />
        <feColorMatrix in="dr" type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="r" />
        <feColorMatrix in="dg" type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0" result="g" />
        <feColorMatrix in="db" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" result="b" />
        <feComposite in="r" in2="g" operator="arithmetic" k2="1" k3="1" result="rg" />
        <feComposite in="rg" in2="b" operator="arithmetic" k2="1" k3="1" />
      </filter>
    </svg>
  );
}

/** Safari/Firefox: a copy of the aurora behind this glass, aligned to the viewport. */
function AuroraCopy({ host, filter }: { host: React.RefObject<HTMLDivElement>; filter: string }) {
  const layer = useRef<HTMLDivElement>(null);
  const [phase] = useState(auroraPhase);
  useEffect(() => {
    let frame = 0;
    const follow = () => {
      const el = host.current;
      const l = layer.current;
      if (el && l) {
        const box = el.getBoundingClientRect();
        l.style.transform = `translate3d(${-box.left}px, ${-box.top}px, 0)`;
        l.style.width = `${window.innerWidth}px`;
        l.style.height = `${window.innerHeight}px`;
      }
      frame = requestAnimationFrame(follow);
    };
    follow();
    return () => cancelAnimationFrame(frame);
  }, [host]);
  return (
    <div className="lens-copy" aria-hidden style={{ filter }}>
      <div ref={layer} className="lens-copy-inner">
        <div className="aurora copy" style={{ ["--phase" as string]: `-${phase}s` }}><i /><i /><i /><i /></div>
      </div>
    </div>
  );
}

export function Glass({ refract = false, strength = 40, radius = 26, variant, press, className = "", style, children, ...rest }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useId().replace(/:/g, "");
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [map, setMap] = useState("");

  useLayoutEffect(() => {
    if (!refract || !ref.current) return;
    const el = ref.current;
    const observer = new ResizeObserver(([entry]) => {
      const w = Math.round(entry.borderBoxSize?.[0]?.inlineSize ?? entry.contentRect.width);
      const h = Math.round(entry.borderBoxSize?.[0]?.blockSize ?? entry.contentRect.height);
      setSize((old) => (old && Math.abs(old.w - w) < 3 && Math.abs(old.h - h) < 3 ? old : { w, h }));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [refract]);

  useEffect(() => {
    if (!size || size.w < 4 || size.h < 4) return;
    const r = variant === "pill" ? size.h / 2 : radius;
    setMap(lensMap(size.w, size.h, Math.min(r, size.h / 2, size.w / 2), Math.min(30, size.h / 2.4, size.w / 2.4)));
  }, [size, radius, variant]);

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    event.currentTarget.style.setProperty("--mx", `${event.clientX - box.left}px`);
    event.currentTarget.style.setProperty("--my", `${event.clientY - box.top}px`);
  };

  const ready = refract && map && size;
  const filterStyle: CSSProperties =
    ready && backdropLens ? { backdropFilter: `url(#lg-${id}) blur(3px) saturate(180%) brightness(1.06)` } : {};
  const classes = ["glass", refract ? "liquid" : "", variant ?? "", press ? "press" : "", className].filter(Boolean).join(" ");

  return (
    <motion.div ref={ref} className={classes} style={{ ...(filterStyle as MotionStyle), ...style }} onPointerMove={onPointerMove} {...rest}>
      {ready && <LensFilter id={`lg-${id}`} map={map} w={size.w} h={size.h} strength={strength} />}
      {ready && !backdropLens && <AuroraCopy host={ref} filter={`url(#lg-${id}) saturate(160%)`} />}
      {children as React.ReactNode}
    </motion.div>
  );
}
