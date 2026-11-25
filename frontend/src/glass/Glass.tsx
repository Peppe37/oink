/* The glass material: Apple-style "liquid glass".

   A pane is clear: what is behind it is not frosted, only slightly saturated, and it stays
   sharp in the middle. Near the rim the glass is a convex bezel, so the background bends
   (refraction computed with Snell's law, index 1.5) and the three colour channels bend by
   different amounts, a faint prismatic fringe. A thin specular rim catches the light, and a
   soft highlight follows the pointer.

   Structure: the element itself paints nothing behind its content. A clipped `.glass-layer`
   child carries the backdrop (so nothing is ever drawn outside the rounded shape, and nested
   panes still get their own backdrop), a `.glass-rim` child draws the light on the edge.

   - Chromium: the lens is an SVG filter inside `backdrop-filter`, so everything behind
     refracts: text, charts, the aurora.
   - Safari and Firefox can't run SVG filters in `backdrop-filter`. Panes marked `refract`
     draw their own copy of the aurora, aligned to the viewport and in phase with it, run the
     same lens on it and show it at the rim; the rest is clear glass with the same rim light. */

import { motion, type HTMLMotionProps } from "framer-motion";
import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from "react";

const backdropLens = (() => {
  if (typeof window === "undefined") return false;
  // Chromium (Chrome, Edge, Brave, Arc, Electron) says "Chrome/"; Safari never does, and
  // every browser on iOS is WebKit underneath ("CriOS" is not "Chrome/").
  const chromium = /\b(Chrome|Chromium|Edg)\//.test(navigator.userAgent) && !/iPhone|iPad/.test(navigator.userAgent);
  return chromium && CSS.supports("backdrop-filter", "url(#x)");
})();

/** Seconds since the page started: the aurora copies use it to stay in phase. */
const T0 = performance.now();
export const auroraPhase = () => (performance.now() - T0) / 1000;

/** Displacement across the bezel, 0 at the outer edge to 1 at the inner edge of the bezel:
 *  a ray going straight down through a convex squircle surface (n = 1.5) is bent by the
 *  slope of the surface; the offset is the height times the tangent of the bend. */
const PROFILE = (() => {
  const n = 1.5;
  const steps = 96;
  const height = (t: number) => Math.pow(1 - Math.pow(1 - t, 4), 1 / 4);
  const out: number[] = [];
  for (let i = 0; i <= steps; i++) {
    const t = Math.min(0.999, Math.max(0.001, i / steps));
    const dt = 0.001;
    const slope = (height(Math.min(1, t + dt)) - height(Math.max(0, t - dt))) / (2 * dt);
    const incidence = Math.atan(slope);
    const refracted = Math.asin(Math.sin(incidence) / n);
    out.push(height(t) * Math.tan(incidence - refracted));
  }
  const max = Math.max(...out);
  return out.map((v) => v / max);
})();
const profile = (t: number) => PROFILE[Math.round(Math.max(0, Math.min(1, t)) * (PROFILE.length - 1))];

type Maps = { displacement: string; rim: string };

/** Two images for a rounded rectangle: the displacement map (R/G = x/y offset, 128 = none,
 *  pixels near the rim pulled inward along the edge normal) and the rim mask (alpha: 1 on
 *  the bezel, fading to 0 over its inner half, so the middle comes straight from the page). */
function lensMaps(width: number, height: number, radius: number, bezel: number): Maps | null {
  const scale = width * height > 400_000 ? 0.35 : 0.5;
  const w = Math.max(2, Math.round(width * scale));
  const h = Math.max(2, Math.round(height * scale));
  const r = radius * scale;
  const b = Math.max(2, bezel * scale);
  const make = () => {
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    return ctx ? { canvas, ctx, img: ctx.createImageData(w, h) } : null;
  };
  const disp = make();
  const rim = make();
  if (!disp || !rim) return null;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const px = x + 0.5;
      const py = y + 0.5;
      const cx = Math.min(Math.max(px, r), w - r);
      const cy = Math.min(Math.max(py, r), h - r);
      let nx = 0;
      let ny = 0;
      let d: number;
      if ((px < r || px > w - r) && (py < r || py > h - r)) {
        const dx = px - cx;
        const dy = py - cy;
        const len = Math.hypot(dx, dy) || 1;
        d = r - len;
        nx = -dx / len;
        ny = -dy / len;
      } else {
        const dl = px, dr = w - px, dt = py, db = h - py;
        d = Math.min(dl, dr, dt, db);
        if (d === dl) nx = 1;
        else if (d === dr) nx = -1;
        else if (d === dt) ny = 1;
        else ny = -1;
      }
      const i = (y * w + x) * 4;
      const inside = d >= 0;
      const t = d / b; // 0 at the edge, 1 where the bezel ends
      const k = inside && t < 1 ? 1 - profile(t) : 0;
      disp.img.data[i] = 128 + nx * k * 127;
      disp.img.data[i + 1] = 128 + ny * k * 127;
      disp.img.data[i + 2] = 128;
      disp.img.data[i + 3] = 255;
      // rim mask: full on the outer half of the bezel, smooth fade over the inner half
      const fade = t <= 0.5 ? 1 : t >= 1.4 ? 0 : 1 - (t - 0.5) / 0.9;
      const a = inside ? fade * fade * (3 - 2 * fade) : 0;
      rim.img.data[i] = 255;
      rim.img.data[i + 1] = 255;
      rim.img.data[i + 2] = 255;
      rim.img.data[i + 3] = Math.round(a * 255);
    }
  }
  disp.ctx.putImageData(disp.img, 0, 0);
  rim.ctx.putImageData(rim.img, 0, 0);
  return { displacement: disp.canvas.toDataURL(), rim: rim.canvas.toDataURL() };
}

type Props = HTMLMotionProps<"div"> & {
  /** Also refract on Safari/Firefox (an aurora copy at the rim). Chromium always refracts. */
  refract?: boolean;
  /** How far the bezel bends the background, in px. */
  strength?: number;
  /** Corner radius in px; "pill" rounds the short side fully. */
  radius?: number;
  variant?: "pill" | "tight";
  /** Frosting (blur, px) for panes that hold long text, like sheets. 0 = clear glass. */
  frost?: number;
  press?: boolean;
};

function LensFilter({ id, maps, w, h, strength, edgeOnly }: { id: string; maps: Maps; w: number; h: number; strength: number; edgeOnly: boolean }) {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden focusable="false">
      <filter id={id} x="0" y="0" width={w} height={h} filterUnits="userSpaceOnUse" colorInterpolationFilters="sRGB">
        <feImage href={maps.displacement} x="0" y="0" width={w} height={h} preserveAspectRatio="none" result="map" />
        <feImage href={maps.rim} x="0" y="0" width={w} height={h} preserveAspectRatio="none" result="rim" />
        <feDisplacementMap in="SourceGraphic" in2="map" scale={strength} xChannelSelector="R" yChannelSelector="G" result="dr" />
        <feDisplacementMap in="SourceGraphic" in2="map" scale={strength * 0.92} xChannelSelector="R" yChannelSelector="G" result="dg" />
        <feDisplacementMap in="SourceGraphic" in2="map" scale={strength * 0.84} xChannelSelector="R" yChannelSelector="G" result="db" />
        <feColorMatrix in="dr" type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="r" />
        <feColorMatrix in="dg" type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0" result="g" />
        <feColorMatrix in="db" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" result="b" />
        <feBlend in="r" in2="g" mode="screen" result="rg" />
        <feBlend in="rg" in2="b" mode="screen" result="bent" />
        <feComposite in="bent" in2="rim" operator="in" result="edge" />
        {!edgeOnly && <feComposite in="edge" in2="SourceGraphic" operator="over" />}
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

export function Glass({ refract = false, strength = 34, radius = 26, variant, frost = 0, press, className = "", style, children, ...rest }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useId().replace(/:/g, "");
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [maps, setMaps] = useState<Maps | null>(null);
  // The lens costs a filter pass every frame the backdrop changes: only the panes that ask.
  const lens = refract;

  useLayoutEffect(() => {
    if (!lens || !ref.current) return;
    const el = ref.current;
    const observer = new ResizeObserver(([entry]) => {
      const w = Math.round(entry.borderBoxSize?.[0]?.inlineSize ?? entry.contentRect.width);
      const h = Math.round(entry.borderBoxSize?.[0]?.blockSize ?? entry.contentRect.height);
      setSize((old) => (old && Math.abs(old.w - w) < 2 && Math.abs(old.h - h) < 2 ? old : { w, h }));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [lens]);

  const corner = variant === "pill" ? 999 : variant === "tight" ? 22 : radius;
  useEffect(() => {
    if (!size || size.w < 8 || size.h < 8) return;
    const r = Math.min(corner, size.h / 2, size.w / 2);
    const bezel = Math.min(26, size.h / 2.2, size.w / 2.2);
    // Debounced: while the dock magnifies, its size changes every frame; the old map is
    // stretched meanwhile, which nobody can see.
    const timer = setTimeout(() => setMaps(lensMaps(size.w, size.h, r, bezel)), maps ? 160 : 0);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size, corner]);

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    event.currentTarget.style.setProperty("--mx", `${event.clientX - box.left}px`);
    event.currentTarget.style.setProperty("--my", `${event.clientY - box.top}px`);
    rest.onPointerMove?.(event);
  };

  const ready = lens && maps && size;
  const tail = `${frost ? ` blur(${frost}px)` : ""} saturate(${frost ? 180 : 150}%) brightness(1.05)`;
  // Plain panes get no backdrop filter at all: clear glass is a tint and a rim, and costs
  // nothing while the background moves or the page scrolls.
  const layerStyle: CSSProperties =
    ready && backdropLens
      ? { backdropFilter: `url(#lg-${id})${tail}`, WebkitBackdropFilter: `url(#lg-${id})${tail}` }
      : lens || frost
        ? { backdropFilter: tail.trim(), WebkitBackdropFilter: tail.trim() }
        : {};
  const classes = ["glass", frost ? "frosted" : "", variant ?? "", press ? "press" : "", className].filter(Boolean).join(" ");

  return (
    <motion.div ref={ref} className={classes} style={{ ["--r" as string]: `${corner}px`, ...style }} {...rest} onPointerMove={onPointerMove}>
      {ready && <LensFilter id={`lg-${id}`} maps={maps} w={size.w} h={size.h} strength={strength} edgeOnly={!backdropLens} />}
      <span className="glass-layer" aria-hidden style={layerStyle}>
        {ready && !backdropLens && refract && <AuroraCopy host={ref} filter={`url(#lg-${id}) saturate(160%)`} />}
      </span>
      <span className="glass-rim" aria-hidden />
      {children as React.ReactNode}
    </motion.div>
  );
}
